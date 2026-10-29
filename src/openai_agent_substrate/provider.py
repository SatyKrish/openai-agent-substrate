# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Pinned SDK adapter: real SandboxSession wrapping an Actor-backed BaseSandboxSession."""

from __future__ import annotations

import base64
import io
import uuid
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import field_validator

from agents.sandbox import Manifest
from agents.sandbox.entries import BaseEntry, Dir, File, LocalDir, LocalFile
from agents.sandbox.errors import ExecNonZeroError, ExecTransportError
from agents.sandbox.session import SandboxSession, SandboxSessionState
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.sandbox_client import BaseSandboxClient, BaseSandboxClientOptions
from agents.sandbox.snapshot import NoopSnapshot, SnapshotBase, SnapshotSpec
from agents.sandbox.types import ExecResult, User

from .lifecycle import ActorControl, resource_name
from .transport import ProcessTransport, ProcessTransportError

MAX_FILE_BYTES = 1024 * 1024
_WRITE_CHUNK_BYTES = 48 * 1024


class SubstrateSandboxOptions(BaseSandboxClientOptions):
    type: Literal["agent-substrate-poc"] = "agent-substrate-poc"
    template: str
    actor_name: str | None = None

    @field_validator("template", "actor_name")
    @classmethod
    def _names(cls, value: str | None) -> str | None:
        return resource_name(value) if value is not None else None


class SubstrateSessionState(SandboxSessionState):
    type: Literal["agent-substrate-poc"] = "agent-substrate-poc"
    actor_name: str
    atespace: str

    @field_validator("actor_name", "atespace")
    @classmethod
    def _names(cls, value: str) -> str:
        return resource_name(value)


def validate_manifest(manifest: Manifest) -> None:
    root = PurePosixPath(manifest.root)
    if not root.is_absolute() or str(root) == "/" or ".." in root.parts:
        raise ValueError("Use an absolute workspace root below /, without '..'")
    if manifest.users or manifest.groups or manifest.extra_path_grants or manifest.environment.value:
        raise NotImplementedError("This reference implementation supports files/directories, without accounts, environment, or path grants")

    def validate(entry: BaseEntry) -> None:
        if not isinstance(entry, (File, Dir, LocalFile, LocalDir)) or entry.group is not None:
            raise NotImplementedError("This reference implementation supports File, Dir, LocalFile, and LocalDir without ownership changes")
        if isinstance(entry, Dir):
            for child in entry.children.values():
                validate(child)

    for entry in manifest.validated_entries().values():
        validate(entry)


class SubstrateSandboxClient(BaseSandboxClient[SubstrateSandboxOptions]):
    """One client is bound to a trusted router, control implementation, and atespace.

    Create owns a fresh Actor. Closing suspends it; delete is explicit. Resume never
    silently creates a replacement if the Actor is lost. Portable SDK tar snapshots
    are intentionally unsupported; Substrate owns memory/filesystem persistence.
    """

    backend_id = "agent-substrate-poc"

    def __init__(self, *, atespace: str, control: ActorControl, router_url: str | None = None, exec_timeout: float = 30):
        self.atespace = resource_name(atespace)
        self.control = control
        self.exec_timeout = exec_timeout
        if router_url is None and not callable(getattr(control, "start_proxy", None)):
            raise ValueError("Provide router_url or a control implementation with authenticated start_proxy support")
        self.transport = ProcessTransport(router_url, default_timeout=exec_timeout) if router_url is not None else None

    async def create(self, *, options: SubstrateSandboxOptions, manifest: Manifest | None = None,
                     snapshot: SnapshotSpec | SnapshotBase | None = None) -> SandboxSession:
        if snapshot is not None:
            raise NotImplementedError("Use Substrate Actor suspension/resumption, not portable SDK snapshots")
        manifest = manifest if manifest is not None else Manifest()
        self._validate_manifest_for_create(manifest)
        validate_manifest(manifest)
        actor_name = options.actor_name or f"oas-{uuid.uuid4().hex[:16]}"
        await self.control.create(self.atespace, actor_name, options.template)
        state = SubstrateSessionState(
            actor_name=actor_name, atespace=self.atespace, manifest=manifest,
            snapshot=NoopSnapshot(id=uuid.uuid4().hex),
        )
        return self._wrap_session(_SubstrateSession(state, self, preserved=False))

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        if not isinstance(state, SubstrateSessionState) or state.atespace != self.atespace:
            raise ValueError("Resume requires Substrate state in this client's configured atespace")
        state.assert_path_grants_rebound()
        validate_manifest(state.manifest)
        if not isinstance(state.snapshot, NoopSnapshot):
            raise NotImplementedError("Portable SDK snapshots are unsupported")
        return self._wrap_session(_SubstrateSession(state.model_copy(deep=True), self, preserved=True))

    def deserialize_session_state(self, payload: dict[str, object]) -> SubstrateSessionState:
        state = self._deserialize_session_state_payload(payload, SubstrateSessionState)
        if not isinstance(state, SubstrateSessionState) or state.atespace != self.atespace:
            raise ValueError("Session state belongs to a different atespace")
        return state

    async def delete(self, session: SandboxSession) -> SandboxSession:
        inner = session._inner
        if not isinstance(inner, _SubstrateSession) or inner.client is not self:
            raise ValueError("Delete requires a session owned by this client")
        if not inner.deleted:
            # Deletion must remain possible after failed startup/suspension.
            # It also bypasses suspension when the workspace is disposable.
            await inner.close_proxy()
            await self.control.delete(inner.state.atespace, inner.state.actor_name)
            inner.deleted = True
            inner.active = False
            await session.aclose()
        return session


class _SubstrateSession(BaseSandboxSession):
    def __init__(self, state: SubstrateSessionState, client: SubstrateSandboxClient, *, preserved: bool):
        self.state = state
        self.client = client
        self.active = False
        self.deleted = False
        self._cwd_ready = False
        self._proxy = None
        self._transport = client.transport
        self._set_start_state_preserved(preserved)

    @property
    def actor_ref(self) -> str:
        return f"{self.state.atespace}/{self.state.actor_name}"

    async def _ensure_backend_started(self) -> None:
        if self.deleted:
            raise RuntimeError("This Actor has been deleted")
        validate_manifest(self.state.manifest)
        if not self.active:
            await self.client.control.resume(self.state.atespace, self.state.actor_name)
            self.active = True
        if self.client.transport is None and self._proxy is None:
            start_proxy = getattr(self.client.control, "start_proxy")
            self._proxy = await start_proxy(self.state.atespace, self.state.actor_name)
            self._transport = ProcessTransport(self._proxy.url, default_timeout=self.client.exec_timeout)

    async def close_proxy(self) -> None:
        if self._proxy is not None:
            try:
                await self._proxy.aclose()
            finally:
                self._proxy = None
                self._transport = None

    async def _prepare_backend_workspace(self) -> None:
        await self._checked("mkdir", "-p", self.state.manifest.root)
        self._cwd_ready = True

    async def _shutdown_backend(self) -> None:
        try:
            if self.active and not self.deleted:
                await self.client.control.suspend(self.state.atespace, self.state.actor_name)
                self.active = False
                self._set_start_state_preserved(True)
        finally:
            await self.close_proxy()

    async def _after_start_failed(self) -> None:
        await self._shutdown_backend()

    async def _exec_internal(self, *command: str | Path, timeout: float | None = None) -> ExecResult:
        if self.deleted or not self.active:
            raise RuntimeError("Enter/start the sandbox session before executing commands")
        if self._transport is None:
            raise RuntimeError("The Actor proxy is not running")
        try:
            result = await self._transport.execute(
                self.actor_ref, [str(arg) for arg in command],
                cwd=self.state.manifest.root if self._cwd_ready else "/", timeout=timeout,
            )
        except ProcessTransportError as error:
            raise ExecTransportError(
                command=tuple(str(c) for c in command), cause=error, retryable=False,
                context={"backend": self.state.type, "retry_safe": False},
            ) from error
        return ExecResult(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)

    async def _checked(self, *command: str) -> ExecResult:
        result = await self.exec(*command, shell=False)
        if not result.ok():
            raise ExecNonZeroError(result, command=command)
        return result

    async def running(self) -> bool:
        if not self.active or self.deleted:
            return False
        return (await self.exec("true", shell=False)).ok()

    async def read(self, path: Path, *, user: str | User | None = None) -> io.BytesIO:
        return io.BytesIO(await self._read_file(path, MAX_FILE_BYTES + 1, user=user, require_complete=True))

    async def _read_bounded(self, path: Path, *, max_bytes: int) -> bytes:
        if max_bytes > MAX_FILE_BYTES:
            raise ValueError("Bounded read exceeds the reference implementation's 1 MiB file limit")
        return await self._read_file(path, max_bytes, require_complete=False)

    async def _read_file(self, path: Path, limit: int, *, user: str | User | None = None,
                         require_complete: bool) -> bytes:
        if user is not None:
            raise NotImplementedError("Per-user execution is unsupported")
        target = str(self.normalize_path(path))
        result = await self.exec(
            "sh", "-c", 'if [ ! -e "$1" ]; then exit 44; fi; '
            '[ -f "$1" ] && [ -r "$1" ] || exit 45; head -c "$2" < "$1" | base64',
            "sh", target, str(limit), shell=False,
        )
        if result.exit_code == 44:
            raise FileNotFoundError(target)
        if not result.ok():
            raise ExecNonZeroError(result, command=("read", target))
        data = base64.b64decode(b"".join(result.stdout.split()), validate=True)
        if require_complete and len(data) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the reference implementation's 1 MiB file limit")
        return data

    async def write(self, path: Path, data: io.IOBase, *, user: str | User | None = None) -> None:
        if user is not None:
            raise NotImplementedError("Per-user execution is unsupported")
        target = self.normalize_path(path, for_write=True)
        value = data.read(MAX_FILE_BYTES + 1)
        payload = value.encode() if isinstance(value, str) else bytes(value)
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the reference implementation's 1 MiB file limit")
        temporary = target.parent / f".oas-upload-{uuid.uuid4().hex}"
        await self._checked("mkdir", "-p", str(target.parent))
        await self._checked("sh", "-c", 'umask 077; set -C; : > "$1"', "sh", str(temporary))
        try:
            for offset in range(0, len(payload), _WRITE_CHUNK_BYTES):
                chunk = base64.b64encode(payload[offset:offset + _WRITE_CHUNK_BYTES]).decode()
                await self._checked("sh", "-c", 'printf %s "$1" | base64 -d >> "$2"', "sh", chunk, str(temporary))
            await self._checked(
                "sh", "-c", '[ ! -d "$2" ] || exit 1; exec mv -f "$1" "$2"',
                "sh", str(temporary), str(target),
            )
        finally:
            # A failed cleanup must not replace the upload result or its original error.
            with suppress(Exception):
                await self._checked("rm", "-f", str(temporary))

    async def persist_workspace(self) -> io.IOBase:
        raise NotImplementedError("Workspace persistence is provided by Substrate Actor snapshots")

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        raise NotImplementedError("Resume the existing Actor; portable workspace import is unsupported")
