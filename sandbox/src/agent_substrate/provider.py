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
from .instrumentation import operation, traced_operation

MAX_FILE_BYTES = 10 * 1024 * 1024
# Each /process round trip costs the same whatever its size, so transfers use as
# few commands as the limits allow. 90 KiB base64-encodes under Linux's 128 KiB
# per-argument limit (and a multiple of 3 keeps parts concatenable); seven parts
# keep a command's arguments under macOS's 1 MiB ARG_MAX for the local simulator.
_WRITE_PART_BYTES = 90 * 1024
_WRITE_PARTS_PER_COMMAND = 7
# A base64 1 MiB read stays under the transport's 4 MiB response limit.
_READ_CHUNK_BYTES = 1024 * 1024
_WRITE_SCRIPT = """
set -e
umask 077
target=$1 partial=$2 first=$3 last=$4
shift 4
if [ "$first" = 1 ]; then
  mkdir -p "$(dirname -- "$target")"
  set -C; : > "$partial"; set +C
fi
for part do printf %s "$part"; done | base64 -d >> "$partial"
if [ "$last" = 1 ]; then
  if [ -d "$target" ]; then rm -f "$partial"; exit 1; fi
  mv -f "$partial" "$target"
fi
"""
_READ_SCRIPT = """
[ -e "$1" ] || exit 44
[ -f "$1" ] && [ -r "$1" ] || exit 45
wc -c < "$1"
dd if="$1" bs="$2" skip="$3" count=1 2>/dev/null | base64
"""


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

    def __init__(self, *, atespace: str, control: ActorControl, router_url: str | None = None, exec_timeout: float = 30,
                 max_file_bytes: int = MAX_FILE_BYTES):
        self.atespace = resource_name(atespace)
        self.control = control
        self.exec_timeout = exec_timeout
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.max_file_bytes = max_file_bytes
        if router_url is None and not callable(getattr(control, "start_proxy", None)):
            raise ValueError("Provide router_url or a control implementation with authenticated start_proxy support")
        self.transport = ProcessTransport(router_url, default_timeout=exec_timeout) if router_url is not None else None

    @traced_operation("sandbox.create")
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

    @traced_operation("sandbox.delete")
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
            with operation("sandbox.resume"):
                await self.client.control.resume(self.state.atespace, self.state.actor_name)
            self.active = True
        if self.client.transport is None and self._proxy is None:
            start_proxy = getattr(self.client.control, "start_proxy")
            with operation("sandbox.proxy"):
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
                with operation("sandbox.suspend"):
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
            with operation("sandbox.exec") as span:
                result = await self._transport.execute(
                    self.actor_ref, [str(arg) for arg in command],
                    cwd=self.state.manifest.root if self._cwd_ready else "/", timeout=timeout,
                )
                if span is not None:
                    span.span_data.data.update(exit_code=result.exit_code,
                        stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr))
                    if result.exit_code:
                        span.set_error({"message": "Command returned nonzero", "data": {}})
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

    def _limit_error(self) -> ValueError:
        return ValueError(f"File exceeds the provider's {self.client.max_file_bytes / 1048576:g} MiB file limit")

    async def read(self, path: Path, *, user: str | User | None = None) -> io.BytesIO:
        return io.BytesIO(await self._read_file(path, self.client.max_file_bytes, user=user, require_complete=True))

    async def _read_bounded(self, path: Path, *, max_bytes: int) -> bytes:
        if max_bytes > self.client.max_file_bytes:
            raise self._limit_error()
        return await self._read_file(path, max_bytes, require_complete=False)

    async def _read_chunk(self, target: str, index: int) -> tuple[int, bytes]:
        """One command returns the file's size and one base64 chunk of it."""
        result = await self.exec("sh", "-c", _READ_SCRIPT, "sh", target, str(_READ_CHUNK_BYTES), str(index), shell=False)
        if result.exit_code == 44:
            raise FileNotFoundError(target)
        if not result.ok():
            raise ExecNonZeroError(result, command=("read", target))
        size, _, encoded = result.stdout.partition(b"\n")
        return int(size.strip()), base64.b64decode(b"".join(encoded.split()), validate=True)

    async def _read_file(self, path: Path, limit: int, *, user: str | User | None = None,
                         require_complete: bool) -> bytes:
        if user is not None:
            raise NotImplementedError("Per-user execution is unsupported")
        target = str(self.normalize_path(path))
        size, data = await self._read_chunk(target, 0)
        if require_complete and size > self.client.max_file_bytes:
            raise self._limit_error()
        wanted = min(size, limit)
        chunks = [data]
        received = len(data)
        while received < wanted:
            current, data = await self._read_chunk(target, len(chunks))
            if current != size or not data:
                raise ValueError(f"{target} changed while it was being read")
            chunks.append(data)
            received += len(data)
        return b"".join(chunks)[:wanted]

    async def write(self, path: Path, data: io.IOBase, *, user: str | User | None = None) -> None:
        if user is not None:
            raise NotImplementedError("Per-user execution is unsupported")
        target = self.normalize_path(path, for_write=True)
        value = data.read(self.client.max_file_bytes + 1)
        payload = value.encode() if isinstance(value, str) else bytes(value)
        if len(payload) > self.client.max_file_bytes:
            raise self._limit_error()
        parts = [base64.b64encode(payload[offset:offset + _WRITE_PART_BYTES]).decode()
                 for offset in range(0, len(payload), _WRITE_PART_BYTES)]
        batches = [parts[index:index + _WRITE_PARTS_PER_COMMAND]
                   for index in range(0, len(parts), _WRITE_PARTS_PER_COMMAND)] or [[]]
        # The first command creates the partial file; the last renames it into place,
        # so the target appears complete or not at all. A small file takes one command.
        partial = target.parent / f".oas-upload-{uuid.uuid4().hex}"
        try:
            for index, batch in enumerate(batches):
                await self._checked("sh", "-c", _WRITE_SCRIPT, "sh", str(target), str(partial),
                                    "1" if index == 0 else "0", "1" if index == len(batches) - 1 else "0", *batch)
        except BaseException:
            # A failed cleanup must not replace the original error.
            with suppress(Exception):
                await self._checked("rm", "-f", str(partial))
            raise

    async def persist_workspace(self) -> io.IOBase:
        raise NotImplementedError("Workspace persistence is provided by Substrate Actor snapshots")

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        raise NotImplementedError("Resume the existing Actor; portable workspace import is unsupported")
