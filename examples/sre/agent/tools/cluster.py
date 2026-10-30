# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only Kubernetes tools. They run in the harness with a reader token, never in the sandbox.

Every call is checked against kind and namespace allowlists here, and the
reader ServiceAccount's Roles enforce the same limits in the cluster. Each
output is saved as evidence: a host copy for checks and a workspace file for
the sandbox to analyze.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agents import function_tool

LIST_KINDS = {"pods", "deployments", "replicasets", "statefulsets", "daemonsets", "jobs", "services", "endpoints"}
DESCRIBE_KINDS = {"pod", "deployment", "replicaset", "statefulset", "daemonset", "job", "service"}
_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
PREVIEW = 4000


class Refused(ValueError):
    """A call outside the allowlists; the message goes back to the model."""


@dataclass
class ClusterAccess:
    """The reader's kubeconfig (a short-lived token, never the admin credentials) and what it may read."""

    kubeconfig: Path
    namespaces: frozenset[str]
    refresh: Callable[[], float] | None = None  # mints a new token; returns its expiry (epoch seconds)
    expires_at: float = 0
    timeout: float = 20
    max_bytes: int = 512 * 1024
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def namespace(self, value: str) -> str:
        if value not in self.namespaces:
            raise Refused(f"Namespace {value!r} is outside this agent's scope: {', '.join(sorted(self.namespaces))}.")
        return value

    @staticmethod
    def name(value: str) -> str:
        if not _NAME.fullmatch(value or ""):
            raise Refused(f"{value!r} is not a valid Kubernetes object name.")
        return value

    async def kubectl(self, *args: str) -> str:
        async with self._lock:
            # Renew the token five minutes before it expires.
            if self.refresh is not None and time.time() > self.expires_at - 300:
                self.expires_at = await asyncio.to_thread(self.refresh)
        process = await asyncio.create_subprocess_exec(
            "kubectl", "--kubeconfig", str(self.kubeconfig), "--request-timeout", f"{int(self.timeout)}s", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout + 5)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return f"kubectl {' '.join(args[:2])} timed out after {self.timeout:.0f} s."
        text = stdout.decode(errors="replace")[: self.max_bytes]
        if process.returncode:
            text += ("\n" if text else "") + stderr.decode(errors="replace")[:4000]
        return text or "(no output)"


def argv_list(kind: str, namespace: str) -> list[str]:
    if kind not in LIST_KINDS:
        raise Refused(f"list_resources reads {', '.join(sorted(LIST_KINDS))}.")
    return ["get", kind, "--namespace", namespace, "--output", "wide"]


def argv_describe(kind: str, name: str, namespace: str) -> list[str]:
    if kind not in DESCRIBE_KINDS:
        raise Refused(f"describe reads {', '.join(sorted(DESCRIBE_KINDS))}.")
    return ["describe", kind, ClusterAccess.name(name), "--namespace", namespace]


def argv_logs(pod: str, namespace: str, container: str | None, previous: bool, tail: int) -> list[str]:
    argv = ["logs", ClusterAccess.name(pod), "--namespace", namespace, "--tail", str(max(1, min(int(tail), 2000)))]
    if container:
        argv += ["--container", ClusterAccess.name(container)]
    if previous:
        argv.append("--previous")
    return argv


def argv_events(namespace: str) -> list[str]:
    return ["get", "events", "--namespace", namespace, "--sort-by", ".lastTimestamp"]


def tools(access: ClusterAccess, turn) -> list:
    """This turn's cluster tools; ``turn`` is the harness's TurnContext."""

    async def read(label: str, build: Callable[[], list[str]], namespace: str) -> str:
        try:
            access.namespace(namespace)
            argv = build()
        except Refused as error:
            return f"Refused: {error}"
        text = await access.kubectl(*argv)
        path = await turn.record(label, text)
        preview = text if len(text) <= PREVIEW else text[:PREVIEW] + f"\n… {len(text) - PREVIEW} more characters in the file"
        return f"Saved to {path} ({len(text.splitlines())} lines).\n\n{preview}"

    @function_tool
    async def list_resources(kind: str, namespace: str) -> str:
        """List one kind of resource in a namespace with status columns (kubectl get -o wide).

        Args:
            kind: pods, deployments, replicasets, statefulsets, daemonsets, jobs, services or endpoints.
            namespace: A namespace in this agent's scope.
        """
        return await read(f"{kind}-{namespace}", lambda: argv_list(kind, namespace), namespace)

    @function_tool
    async def describe(kind: str, name: str, namespace: str) -> str:
        """Describe one object: spec, status, conditions and recent events (kubectl describe).

        Args:
            kind: pod, deployment, replicaset, statefulset, daemonset, job or service.
            name: The object's name.
            namespace: A namespace in this agent's scope.
        """
        return await read(f"describe-{kind}-{name}", lambda: argv_describe(kind, name, namespace), namespace)

    @function_tool
    async def pod_logs(pod: str, namespace: str, container: str | None = None, previous: bool = False,
                       tail: int = 200) -> str:
        """Read a pod's recent logs.

        Args:
            pod: The pod's name.
            namespace: A namespace in this agent's scope.
            container: The container, when the pod has more than one.
            previous: Read the last terminated container instead; use this for crash loops.
            tail: How many lines to read, up to 2000.
        """
        label = f"logs-{pod}" + ("-previous" if previous else "")
        return await read(label, lambda: argv_logs(pod, namespace, container, previous, tail), namespace)

    @function_tool
    async def namespace_events(namespace: str) -> str:
        """Recent events in a namespace, oldest first (kubectl get events).

        Args:
            namespace: A namespace in this agent's scope.
        """
        return await read(f"events-{namespace}", lambda: argv_events(namespace), namespace)

    return [list_resources, describe, pod_logs, namespace_events]


TOOL_NAMES = ("list_resources", "describe", "pod_logs", "namespace_events")
