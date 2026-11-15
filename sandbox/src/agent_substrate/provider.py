# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

"""Pinned SDK adapter: real SandboxSession wrapping an Actor-backed BaseSandboxSession."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import uuid
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from agents.sandbox import Manifest
from agents.sandbox.entries import BaseEntry, Dir, File, LocalDir, LocalFile
from agents.sandbox.errors import ExecNonZeroError, ExecTransportError
from agents.sandbox.session import SandboxSession, SandboxSessionState
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
from agents.sandbox.session.sandbox_client import BaseSandboxClient, BaseSandboxClientOptions
from agents.sandbox.snapshot import LocalSnapshotSpec, NoopSnapshot, NoopSnapshotSpec, SnapshotBase, SnapshotSpec
from agents.sandbox.snapshot_defaults import default_local_snapshot_base_dir
from agents.sandbox.types import ExecResult, User

from .lifecycle import (
    ActorAlreadyExistsError,
    ActorControl,
    ActorCreateUncertainError,
    ActorNotFoundError,
    ActorOperationUncertainError,
    ActorResumeInProgressError,
    resource_name,
)
from .proxy import ActorProxy
from .transport import ProcessTransport, ProcessTransportError, RouterTLS
from .instrumentation import operation, traced_operation

MAX_FILE_BYTES = 10 * 1024 * 1024
_logger = logging.getLogger(__name__)
_T = TypeVar("_T")
_UNCERTAIN_OUTCOMES = (ActorOperationUncertainError, TimeoutError)
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
    workspace_initialized: bool = True
    pending_eviction_id: int = Field(default=0, ge=0, lt=1 << 64)
    handled_eviction_id: int = Field(default=0, ge=0, lt=1 << 64)

    @model_validator(mode="before")
    @classmethod
    def _restore_workspace_initialization(cls, value: object) -> object:
        if isinstance(value, dict) and "workspace_initialized" not in value:
            return {**value, "workspace_initialized": value.get("workspace_root_ready", True)}
        return value

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


async def _finish_actor_operation(task: asyncio.Task[_T]) -> _T:
    """Let an in-flight lifecycle operation settle before releasing its caller."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(task)
        if not task.cancelled():
            task.exception()
        raise


class SubstrateSandboxClient(BaseSandboxClient[SubstrateSandboxOptions]):
    """One client is bound to a trusted router, control implementation, and atespace.

    Create owns a fresh Actor. Closing suspends it; delete is explicit. Resume never
    silently creates a replacement if the Actor is lost. Portable SDK tar snapshots
    are intentionally unsupported; Substrate owns memory/filesystem persistence.

    In the cluster, pass the router's ``router_url`` and ``router_tls`` so commands go
    straight to the router under the pod's own certificate. Without ``router_url``, each
    session starts ``kubectl ate proxy``, the path for a harness outside the cluster.
    """

    backend_id = "agent-substrate-poc"

    def __init__(self, *, atespace: str, control: ActorControl, router_url: str | None = None,
                 router_tls: RouterTLS | None = None, exec_timeout: float = 30, max_file_bytes: int = MAX_FILE_BYTES):
        self.atespace = resource_name(atespace)
        self.control = control
        self.exec_timeout = exec_timeout
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")
        self.max_file_bytes = max_file_bytes
        if router_url is None and not callable(getattr(control, "start_proxy", None)):
            raise ValueError("Provide router_url or a control implementation with authenticated start_proxy support")
        if router_tls is not None and router_url is None:
            raise ValueError("router_tls needs the router_url it authenticates to")
        self.transport = (ProcessTransport(router_url, default_timeout=exec_timeout, tls=router_tls)
                          if router_url is not None else None)

    async def create(self, *, options: SubstrateSandboxOptions, manifest: Manifest | None = None,
                     snapshot: SnapshotSpec | SnapshotBase | None = None) -> SandboxSession:
        state = self.prepare_session_state(manifest=manifest, options=options, snapshot=snapshot)
        creation = asyncio.create_task(self._allocate_new_session(state, options))
        try:
            return await _finish_actor_operation(creation)
        except asyncio.CancelledError:
            # No session was returned to the caller. Clean up this known successful allocation.
            if not creation.cancelled() and creation.exception() is None:
                try:
                    await _finish_actor_operation(asyncio.create_task(self.delete(creation.result())))
                except Exception as error:
                    _logger.warning("Could not clean up Actor %s/%s after canceled create (%s)",
                                    state.atespace, state.actor_name, type(error).__name__)
            raise

    async def _allocate_new_session(
        self, state: SubstrateSessionState, options: SubstrateSandboxOptions
    ) -> SandboxSession:
        try:
            return await self.create_from_state(state, options)
        except _UNCERTAIN_OUTCOMES as error:
            uncertainty = ActorCreateUncertainError(state.atespace, state.actor_name)
            if options.actor_name is None:
                # Only a freshly generated identity can be safely deleted without proof of creation.
                try:
                    await self.control.delete(state.atespace, state.actor_name)
                except Exception as cleanup_error:
                    # A not-found result can race a create that has yet to commit.
                    _logger.warning("%s after compensating delete returned %s", uncertainty, type(cleanup_error).__name__)
                else:
                    raise
            else:
                _logger.warning("%s with an explicitly chosen name", uncertainty)
            raise uncertainty from error

    def prepare_session_state(self, *, options: SubstrateSandboxOptions, manifest: Manifest | None = None,
                              snapshot: SnapshotSpec | SnapshotBase | None = None) -> SubstrateSessionState:
        """Validate inputs and choose the Actor identity before allocating remote resources."""
        # The pinned SDK supplies its standard local archive directory on client-owned runs.
        # Treat that location as a no-op sentinel since Substrate owns snapshots.
        sdk_default = isinstance(snapshot, LocalSnapshotSpec) and snapshot.base_path == default_local_snapshot_base_dir()
        if snapshot is not None and not isinstance(snapshot, (NoopSnapshot, NoopSnapshotSpec)) and not sdk_default:
            raise NotImplementedError("Use Substrate Actor suspension/resumption, not portable SDK snapshots")
        manifest = manifest if manifest is not None else Manifest()
        self._validate_manifest_for_create(manifest)
        validate_manifest(manifest)
        return SubstrateSessionState(
            actor_name=options.actor_name or f"oas-{uuid.uuid4().hex}",
            atespace=self.atespace, manifest=manifest, snapshot=NoopSnapshot(id=uuid.uuid4().hex),
            workspace_initialized=False,
        )

    @traced_operation("sandbox.create")
    async def create_from_state(self, state: SubstrateSessionState, options: SubstrateSandboxOptions, *,
                                recover: bool = False) -> SandboxSession:
        """Allocate an Actor from a prepared state. Recover only caller-owned creation intents."""
        self._validate_state_for_session(state)
        if state.workspace_initialized or (options.actor_name is not None and options.actor_name != state.actor_name):
            raise ValueError("Actor creation requires matching, uninitialized session state")
        if recover and options.actor_name is not None:
            raise ValueError("Automatic create recovery requires a provider-generated Actor name")
        try:
            await self.control.create(state.atespace, state.actor_name, options.template)
        except ActorAlreadyExistsError:
            if not recover:
                raise
        return self._wrap_session(_SubstrateSession(state.model_copy(deep=True), self))

    def _validate_state_for_session(self, state: SandboxSessionState) -> SubstrateSessionState:
        if not isinstance(state, SubstrateSessionState) or state.atespace != self.atespace:
            raise ValueError("Substrate state must belong to this client's configured atespace")
        state.assert_path_grants_rebound()
        validate_manifest(state.manifest)
        if not isinstance(state.snapshot, NoopSnapshot):
            raise NotImplementedError("Portable SDK snapshots are unsupported")
        return state

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        state = self._validate_state_for_session(state)
        return self._wrap_session(_SubstrateSession(state.model_copy(deep=True), self))

    def deserialize_session_state(self, payload: dict[str, object]) -> SubstrateSessionState:
        state = self._deserialize_session_state_payload(payload, SubstrateSessionState)
        if not isinstance(state, SubstrateSessionState) or state.atespace != self.atespace:
            raise ValueError("Session state belongs to a different atespace")
        return state

    def _owned_session(self, session: SandboxSession) -> _SubstrateSession:
        inner = session._inner
        if not isinstance(inner, _SubstrateSession) or inner.client is not self:
            raise ValueError("Requires a session owned by this client")
        return inner

    async def _delete_actor(self, state: SubstrateSessionState) -> None:
        with suppress(ActorNotFoundError):
            await self.control.delete(state.atespace, state.actor_name)

    @traced_operation("sandbox.delete")
    async def delete(self, session: SandboxSession) -> SandboxSession:
        inner = self._owned_session(session)
        if not inner.deleted:
            # Deletion must remain possible after failed startup/suspension.
            # It also bypasses suspension when the workspace is disposable.
            await inner.close_proxy()
            await _finish_actor_operation(asyncio.create_task(self._delete_actor(inner.state)))
            inner.deleted = True
            inner.active = False
            await session.aclose()
        return session

    async def poll_eviction(self, session: SandboxSession) -> int | None:
        """Report a worker drain signal, zero before activation, or None for older Actor images."""
        inner = self._owned_session(session)
        if not inner.active or inner._transport is None:
            return 0
        return await inner._transport.poll_eviction(inner.actor_ref)

    def note_eviction(self, session: SandboxSession, notice: int) -> bool:
        """Record an unhandled drain signal for synchronous suspension."""
        state = self._owned_session(session).state
        if not notice or notice == state.handled_eviction_id:
            return False
        state.pending_eviction_id = notice
        return True


class _SubstrateSession(BaseSandboxSession):
    state: SubstrateSessionState

    def __init__(self, state: SubstrateSessionState, client: SubstrateSandboxClient):
        self.state = state
        self.client = client
        self.active = False
        self._resume_outcome_uncertain = False
        self.deleted = False
        self._cwd_ready = False
        self._proxy: ActorProxy | None = None
        self._transport = client.transport
        self._set_start_state_preserved(state.workspace_initialized)

    @property
    def actor_ref(self) -> str:
        return f"{self.state.atespace}/{self.state.actor_name}"

    def _mark_eviction_suspended(self) -> None:
        if notice := self.state.pending_eviction_id:
            self.state.handled_eviction_id = notice
            self.state.pending_eviction_id = 0

    async def _suspend_actor(self) -> None:
        try:
            await self.client.control.suspend(self.state.atespace, self.state.actor_name)
        except _UNCERTAIN_OUTCOMES:
            if not self.state.pending_eviction_id:
                raise
            # Substrate re-reads the Actor under its lease on each request and
            # returns success if the first attempt already suspended it.
            await asyncio.sleep(0.25)
            await self.client.control.suspend(self.state.atespace, self.state.actor_name)

    async def _finish_pending_eviction(self) -> None:
        if self.state.pending_eviction_id:
            with operation("sandbox.suspend"):
                await self._suspend_actor()
            self._mark_eviction_suspended()

    async def start(self) -> None:
        try:
            await super().start()
        except asyncio.CancelledError:
            # The SDK's start cleanup handles Exception, and cancellation is a BaseException.
            await self._after_start_failed()
            raise

    async def _resume_backend(self) -> None:
        await self._finish_pending_eviction()
        with operation("sandbox.resume"):
            try:
                await self.client.control.resume(self.state.atespace, self.state.actor_name)
            except _UNCERTAIN_OUTCOMES:
                self._resume_outcome_uncertain = True
                raise
        self._resume_outcome_uncertain = False
        self.active = True

    async def _ensure_backend_started(self) -> None:
        if self.deleted:
            raise RuntimeError("This Actor has been deleted")
        validate_manifest(self.state.manifest)
        if not self.active:
            await _finish_actor_operation(asyncio.create_task(self._resume_backend()))
        if self.client.transport is None and self._proxy is None:
            start_proxy = getattr(self.client.control, "start_proxy")
            with operation("sandbox.proxy"):
                proxy: ActorProxy = await start_proxy(self.state.atespace, self.state.actor_name)
            self._proxy = proxy
            self._transport = ProcessTransport(proxy.url, default_timeout=self.client.exec_timeout)

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

    async def _after_start(self) -> None:
        self.state.workspace_initialized = True
        self._set_start_state_preserved(True)

    async def _suspend_backend(self) -> None:
        try:
            await self.close_proxy()
        finally:
            if (self.active or self._resume_outcome_uncertain) and not self.deleted:
                with operation("sandbox.suspend"):
                    try:
                        await self._suspend_actor()
                    except ActorResumeInProgressError:
                        if not self._resume_outcome_uncertain:
                            raise
                        # Resume is idempotent and can finish a partially applied restore.
                        await self.client.control.resume(self.state.atespace, self.state.actor_name)
                        await self._suspend_actor()
                self.active = False
                self._resume_outcome_uncertain = False
                self._mark_eviction_suspended()

    async def _shutdown_backend(self) -> None:
        await _finish_actor_operation(asyncio.create_task(self._suspend_backend()))

    async def _after_start_failed(self) -> None:
        try:
            await self._shutdown_backend()
        except Exception as error:
            _logger.warning("Actor startup cleanup failed (%s)", type(error).__name__)

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
        limit = self.client.max_file_bytes
        payload = bytearray()
        while True:
            value = data.read(limit + 1 - len(payload))
            if value is None:
                raise BlockingIOError("Sandbox file writes require a blocking stream")
            if not value:
                break
            payload.extend(value.encode() if isinstance(value, str) else value)
            if len(payload) > limit:
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
