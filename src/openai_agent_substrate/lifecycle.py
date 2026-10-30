# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Host-side Actor lifecycle. No kubeconfig or control credentials enter the Actor."""

from __future__ import annotations

import asyncio
import re
from typing import Protocol
from pathlib import Path

from .proxy import ActorProxy


class ActorCapacityError(RuntimeError):
    """Substrate refused to run an Actor because no worker has room; retrying later can succeed."""


# Substrate's AssignWorker step reports a full pool with this text and gRPC RESOURCE_EXHAUSTED.
_NO_ROOM = re.compile(r"no worker has room|RESOURCE_EXHAUSTED", re.IGNORECASE)


def resource_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Actor, template, and atespace names must be DNS labels (1–63 characters)")
    return value


class ActorControl(Protocol):
    async def create(self, atespace: str, name: str, template: str) -> None: ...
    async def resume(self, atespace: str, name: str) -> None: ...
    async def suspend(self, atespace: str, name: str) -> None: ...
    async def delete(self, atespace: str, name: str) -> None: ...


class KubectlActorControl:
    """Use the upstream kubectl-ate plugin, always with an explicit cluster context."""

    def __init__(self, *, context: str, endpoint: str | None = None, kubeconfig: str | Path | None = None, timeout: float = 120):
        if not context or context.startswith("-"):
            raise ValueError("An explicit Kubernetes context is required")
        if timeout <= 0:
            raise ValueError("Lifecycle timeout must be positive")
        self.context = context
        self.endpoint = endpoint
        self.kubeconfig = kubeconfig
        self.timeout = timeout

    def command(self, verb: str, atespace: str, name: str, *extra: str) -> list[str]:
        argv = ["kubectl", "ate", "--context", self.context]
        if self.kubeconfig is not None:
            argv += ["--kubeconfig", str(self.kubeconfig)]
        if self.endpoint:
            argv += ["--endpoint", self.endpoint]
        return argv + [verb, "actor", resource_name(name), "--atespace", resource_name(atespace), *extra]

    async def _run(self, argv: list[str]) -> None:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            detail = (stderr or stdout).decode(errors="replace")[:2000]
            error = ActorCapacityError if _NO_ROOM.search(detail) else RuntimeError
            raise error(f"Actor lifecycle command failed (exit {process.returncode}): {detail}")

    async def create(self, atespace: str, name: str, template: str) -> None:
        await self._run(self.command("create", atespace, name, "--template", resource_name(template)))

    async def resume(self, atespace: str, name: str) -> None:
        await self._run(self.command("resume", atespace, name))

    async def suspend(self, atespace: str, name: str) -> None:
        # Wait for durable object-storage persistence before claiming the session is closed.
        await self._run(self.command("suspend", atespace, name, "--mode", "sync"))

    async def delete(self, atespace: str, name: str) -> None:
        await self._run(self.command("delete", atespace, name))

    async def start_proxy(self, atespace: str, name: str) -> ActorProxy:
        return await ActorProxy.start(
            self.command("proxy", atespace, name, "0:80", "--address", "127.0.0.1"),
            timeout=self.timeout,
        )
