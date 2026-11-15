# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Host-side Actor lifecycle. No kubeconfig or control credentials enter the Actor."""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from .proxy import ActorProxy


class ActorCapacityError(RuntimeError):
    """Substrate refused to run an Actor because no worker has room; retrying later can succeed."""


class ActorAlreadyExistsError(RuntimeError):
    """Substrate already has an Actor with this name in the atespace."""


class ActorNotFoundError(RuntimeError):
    """The requested Actor no longer exists in the atespace."""


class ActorOperationUncertainError(RuntimeError):
    """The control plane could have applied a lifecycle operation before its reply was lost."""


class ActorCreateUncertainError(ActorOperationUncertainError):
    """The caller can reconcile an unresolved allocation through its exact Actor reference."""

    def __init__(self, atespace: str, actor_name: str):
        self.actor_ref = f"{atespace}/{actor_name}"
        super().__init__(f"Creation outcome is uncertain for Actor {self.actor_ref}")


class ActorResumeInProgressError(RuntimeError):
    """Substrate cannot suspend an Actor while a prior resume is still incomplete."""


# Substrate's AssignWorker step reports a full pool with this text and gRPC RESOURCE_EXHAUSTED.
_RPC_CODE = re.compile(r"\brpc error:\s*code\s*=\s*(\w+)\b", re.IGNORECASE)
_NO_ROOM = re.compile(r"no worker has room|RESOURCE_EXHAUSTED", re.IGNORECASE)
_INCOMPLETE_RESUME = re.compile(r"\bMarkSuspending\b.*\bgot:\s*ACTOR_STATE_RESUMING\b", re.IGNORECASE)
_STATUS_ERRORS = {
    "alreadyexists": ActorAlreadyExistsError,
    "notfound": ActorNotFoundError,
    "resourceexhausted": ActorCapacityError,
    "aborted": ActorOperationUncertainError,
    "canceled": ActorOperationUncertainError,
    "deadlineexceeded": ActorOperationUncertainError,
    "unavailable": ActorOperationUncertainError,
    "unknown": ActorOperationUncertainError,
    "internal": ActorOperationUncertainError,
}


def resource_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Actor, template, and atespace names must be DNS labels (1–63 characters)")
    return value


class ActorControl(Protocol):
    """Control implementations report unknown mutation outcomes with ActorOperationUncertainError."""

    async def create(self, atespace: str, name: str, template: str) -> None: ...
    async def resume(self, atespace: str, name: str) -> None: ...
    async def suspend(self, atespace: str, name: str) -> None: ...
    async def delete(self, atespace: str, name: str) -> None: ...


class KubectlActorControl:
    """Use the upstream kubectl-ate plugin, always with an explicit cluster context.

    Without ``token_file``, kubectl-ate mints a token for Substrate's shared ``ate-client``
    ServiceAccount, which a default install makes a global owner. In the cluster, pass the
    pod's own ServiceAccount token (audience ``api.ate-system.svc``) and the API ``endpoint``
    so calls run as the harness, limited to what its atespace access policy grants.
    """

    def __init__(self, *, context: str, endpoint: str | None = None, kubeconfig: str | Path | None = None,
                 token_file: str | Path | None = None, timeout: float = 120):
        if not context or context.startswith("-"):
            raise ValueError("An explicit Kubernetes context is required")
        if timeout <= 0:
            raise ValueError("Lifecycle timeout must be positive")
        self.context = context
        self.endpoint = endpoint
        self.kubeconfig = kubeconfig
        self.token_file = token_file
        self.timeout = timeout

    def base_command(self) -> list[str]:
        argv = ["kubectl", "ate", "--context", self.context]
        if self.kubeconfig is not None:
            argv += ["--kubeconfig", str(self.kubeconfig)]
        if self.endpoint:
            argv += ["--endpoint", self.endpoint]
        if self.token_file is not None:
            argv += ["--token-file", str(self.token_file)]
        return argv

    def command(self, verb: str, atespace: str, name: str, *extra: str) -> list[str]:
        return self.base_command() + [verb, "actor", resource_name(name), "--atespace", resource_name(atespace), *extra]

    async def _run(self, argv: list[str]) -> None:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except BaseException as error:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.communicate()
            if isinstance(error, TimeoutError):
                raise ActorOperationUncertainError(
                    f"Actor lifecycle command did not complete within {self.timeout:g} seconds"
                ) from error
            raise
        if process.returncode:
            detail = (stderr or stdout).decode(errors="replace")[:2000]
            status = _RPC_CODE.search(detail)
            code = status.group(1).lower() if status else ""
            error_type: type[RuntimeError]
            if code == "failedprecondition" and _INCOMPLETE_RESUME.search(detail):
                error_type = ActorResumeInProgressError
            elif not code and _NO_ROOM.search(detail):
                error_type = ActorCapacityError
            else:
                error_type = _STATUS_ERRORS.get(code, RuntimeError)
            raise error_type(f"Actor lifecycle command failed (exit {process.returncode}): {detail}")

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
