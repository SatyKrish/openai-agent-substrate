# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""ChatKit server for any agent: one thread is one conversation backed by one sandbox session.

Each message takes the conversation's lock and reloads the thread, creates or resumes
the sandbox (waiting while no worker has room), copies uploads into its workspace,
runs the agent loop with the conversation's SDK session history, copies the agent's
output folders to the host, runs the agent's host check, and suspends the sandbox.
Deleting the thread deletes the sandbox, the history and the host copies.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import re
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agents import Runner, SQLiteSession, trace
from agents.run import RunConfig
from agents.sandbox import SandboxRunConfig
from agents.tracing import gen_trace_id
from chatkit.agents import AgentContext, stream_agent_response
from chatkit.errors import CustomStreamError
from chatkit.server import ChatKitServer
from chatkit.store import NotFoundError
from chatkit.types import (
    ClientEffectEvent, CustomTask, ThreadItemAddedEvent, ThreadItemDoneEvent, ThreadItemUpdatedEvent, ThreadMetadata,
    UserMessageItem, WidgetItem, Workflow, WorkflowItem, WorkflowTaskAdded, WorkflowTaskUpdated,
)
from chatkit.widgets import DynamicWidgetRoot

from openai_agent_substrate import ActorCapacityError, SubstrateSandboxOptions
from openai_agent_substrate.instrumentation import operation
from openai_agent_substrate.tracing import otel_trace_id, tracing_enabled

from .sandbox import WORKSPACE_ROOT
from .spec import AgentSpec, Conversation, TurnContext
from .steps import ToolSteps, fenced, user_text
from .uploads import UploadRejected, UploadStore, upload_name

logger = logging.getLogger(__name__)
_MAX_FILES = 40
_FILE_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"


class TurnError(Exception):
    """A turn failure whose message is safe to show."""

    def __init__(self, message: str, *, retry: bool):
        super().__init__(message)
        self.retry = retry


class HarnessChatServer(ChatKitServer[dict]):
    def __init__(self, spec: AgentSpec, store, client, *, model, template: str, output_dir: Path,
                 base_url: str = "http://localhost:8000", workspace_root: str = WORKSPACE_ROOT,
                 model_label: str | None = None, max_turns: int = 25, capacity_wait_s: float = 300,
                 card: Callable[[Conversation, dict, set[str]], list[dict]] | None = None,
                 preferred_output: str | None = None):
        self.spec = spec
        self.output_dir = Path(output_dir)
        self.uploads = UploadStore(store, self.output_dir / ".staged", base_url, spec.uploads)
        super().__init__(store, attachment_store=self.uploads)
        store.on_delete_thread = self.delete_workspace
        self.client = client
        self.model = model
        self.model_label = model_label or str(model)
        self.template = template
        self.workspace_root = workspace_root
        self.max_turns = max_turns
        self.capacity_wait_s = capacity_wait_s
        self.card = card
        self.preferred_output = preferred_output
        self.history_path = self.output_dir / "history.sqlite3"
        self.running: set[str] = set()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def conversation(self, thread: ThreadMetadata) -> Conversation:
        return Conversation(thread, self.output_dir / thread.id)

    def history(self, thread_id: str) -> SQLiteSession:
        """The SDK session that holds this conversation's agent history."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        return SQLiteSession(thread_id, self.history_path)

    async def respond(self, thread: ThreadMetadata, input_user_message: UserMessageItem | None, context: dict):
        if input_user_message is None:
            return
        text = user_text(input_user_message)
        attachments = list(input_user_message.attachments)
        limit = self.spec.uploads.max_count if self.spec.uploads else 0
        if len(attachments) > limit:
            message = f"Attach at most {limit} files per message." if limit else "This agent does not accept uploads."
            raise CustomStreamError(message, allow_retry=False)
        if not text and not attachments:
            raise CustomStreamError("Type a question to send.", allow_retry=False)
        text = text or self.spec.default_question
        async with self._locks[thread.id]:
            # ChatKit loaded this thread before the lock: a turn that finished meanwhile must not be overwritten.
            with contextlib.suppress(NotFoundError):
                fresh = await self.store.load_thread(thread.id, context)
                thread.metadata, thread.title = fresh.metadata, fresh.title
            self.running.add(thread.id)
            try:
                async for event in self._turn(thread, text, attachments, context):
                    yield event
            finally:
                self.running.discard(thread.id)

    async def _turn(self, thread: ThreadMetadata, text: str, attachments: list, context: dict):
        conversation = self.conversation(thread)
        meta = thread.metadata
        if not thread.title:
            thread.title = text if len(text) <= 60 else text[:57].rstrip() + "…"
        turns = meta.setdefault("turns", [])
        sdk_trace_id = gen_trace_id()
        traced = tracing_enabled()
        turn = {
            "number": len(turns) + 1, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
            "sdk_trace_id": sdk_trace_id if traced else None,
            "otel_trace_id": otel_trace_id(sdk_trace_id) if traced else None,
            "lifecycle": "resumed" if meta.get("substrate") else "created",
            "model": self.model_label, "tools": [], "files": [], "uploads": [], "check": None,
        }
        turns.append(turn)
        await self.store.save_thread(thread, context)
        context_ = AgentContext(thread=thread, store=self.store, request_context=context)
        started = time.monotonic()
        turn_operation, _ = self.spec.trace_operations
        try:
            with trace(f"{self.spec.name} turn", trace_id=sdk_trace_id, group_id=thread.id, disabled=not traced):
                with operation(turn_operation):
                    async for event in self._run_turn(conversation, context_, turn, text, attachments):
                        yield event
            turn["status"] = "completed"
        except asyncio.CancelledError:
            turn["status"] = "stopped"
            raise
        except Exception as error:
            turn["status"] = "failed"
            turn["error"] = type(error).__name__
            if context_.workflow_item is not None:
                yield ThreadItemDoneEvent(item=context_.workflow_item)
                context_.workflow_item = None
            if isinstance(error, UploadRejected):
                raise CustomStreamError(str(error), allow_retry=False) from error
            if isinstance(error, TurnError):
                logger.warning("Turn %s of %s failed: %s", turn["number"], thread.id, error)
                raise CustomStreamError(str(error), allow_retry=error.retry) from error
            logger.exception("Turn %s of %s failed", turn["number"], thread.id)
            # Exception text can contain command output; the browser gets the type only.
            raise CustomStreamError(f"The turn failed ({type(error).__name__}); the server log has the cause. "
                                    "You can retry.", allow_retry=True) from error
        finally:
            turn["duration_s"] = round(time.monotonic() - started, 1)
            await self.store.save_thread(thread, context)
        yield self._turn_card(conversation, context, turn)
        changed = [item["path"] for item in turn["files"] if item["changed"] and item["path"].startswith("output/")]
        if changed:
            preferred = self.preferred_output if self.preferred_output in changed else changed[0]
            yield ClientEffectEvent(name="open_file", data={"thread_id": thread.id, "turn": turn["number"], "path": preferred})

    async def _run_turn(self, conversation: Conversation, context_: AgentContext, turn: dict, text: str, attachments: list):
        thread, meta = conversation.thread, conversation.metadata
        created = meta.get("substrate") is None
        step = CustomTask(title="Creating the workspace" if created else "Resuming the workspace", status_indicator="loading")
        workflow = WorkflowItem(id=context_.generate_id("workflow"), created_at=datetime.now(), thread_id=thread.id,
                                workflow=Workflow(type="custom", tasks=[step]))
        context_.workflow_item = workflow
        yield ThreadItemAddedEvent(item=workflow)

        began = time.monotonic()
        if created:
            manifest = self.spec.start(conversation, bool(attachments), self.workspace_root)
            session = await self.client.create(manifest=manifest, options=SubstrateSandboxOptions(template=self.template))
            # Save immediately: a failed first turn must still be deletable.
            meta["substrate"] = self.client.serialize_session_state(session.state)
            meta["actor"] = f"{session.state.atespace}/{session.state.actor_name}"
            await self.store.save_thread(thread, context_.request_context)
        else:
            session = await self.client.resume(self.client.deserialize_session_state(meta["substrate"]))
        actor = meta["actor"]
        async for event in self._start(session, workflow, turn):
            yield event
        async with contextlib.aclosing(session):
            if created:
                boundary = await session.exec('test -z "${OPENAI_API_KEY:-}" && test ! -d /var/run/agent-config')
                if not boundary.ok():
                    raise RuntimeError("The sandbox must not contain the harness API key or Secret mount")
            seconds = time.monotonic() - began
            verb = "Created" if created else "Resumed"
            detail = (f"Actor `{actor}` with its own workspace volume." if created
                      else f"Same Actor `{actor}`; its workspace volume still holds the earlier turns' files.")
            if turn.get("waited_s"):
                detail += f" Waited {turn['waited_s']:.0f} s for a sandbox worker with room."
            step = CustomTask(title=f"{verb} the workspace · {seconds:.1f} s", content=detail, status_indicator="complete")
            workflow.workflow.tasks[0] = step
            yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskUpdated(task=step, task_index=0))

            if attachments:
                noun = "upload" if len(attachments) == 1 else "uploads"
                copying = CustomTask(title=f"Copying {len(attachments)} {noun} into the workspace", status_indicator="loading")
                workflow.workflow.tasks.append(copying)
                index = len(workflow.workflow.tasks) - 1
                yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskAdded(task=copying, task_index=index))
                added = await self._store_uploads(session, conversation, turn, attachments, context_.request_context)
                described = [(item, self._describe_upload(item)) for item in added]
                listing = "\n".join(f"{item['path']}  {item['bytes']} bytes  sha256 {item['sha256'][:12]}…"
                                    + (f"  {summary}" if summary else "") for item, summary in described)
                copied = CustomTask(title=f"Copied {len(added)} {noun} to inputs/uploads/", content=fenced(listing),
                                    status_indicator="complete")
                workflow.workflow.tasks[index] = copied
                yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskUpdated(task=copied, task_index=index))
                text += "\n\n[Uploaded to the workspace this turn: " + ", ".join(
                    f"{item['path']} ({item['bytes']} bytes" + (f"; {summary}" if summary else "") + ")"
                    for item, summary in described) + "]"

            harness_tools = list(self.spec.tools(TurnContext(conversation, turn, session)))
            agent = self.spec.build_agent(session.state.manifest, self.model)
            extra = self.spec.instructions(conversation)
            agent = agent.clone(instructions=agent.instructions + ("\n\n" + extra if extra else ""),
                                tools=[*agent.tools, *harness_tools])
            history = self.history(thread.id)
            kept = len(await history.get_items())
            try:
                result = Runner.run_streamed(
                    agent, text, session=history, max_turns=self.max_turns,
                    run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=not tracing_enabled(),
                                         trace_include_sensitive_data=False))
                steps = ToolSteps(result, context_, turn["tools"], frozenset(tool.name for tool in harness_tools))
                async for event in stream_agent_response(context_, steps):
                    yield event
            except BaseException:
                # The SDK saves items as the run goes; a failed turn must not leave half of itself in the history.
                while len(await history.get_items()) > kept:
                    await history.pop_item()
                raise
            finally:
                history.close()
            files = await self._collect(session, conversation, turn)
            _, check_operation = self.spec.trace_operations
            with operation(check_operation):
                turn["check"] = self.spec.check(conversation, turn, files)
        meta["substrate"] = self.client.serialize_session_state(session.state)

    async def _start(self, session, workflow: WorkflowItem, turn: dict):
        """Start the sandbox, waiting with a visible step while every worker is full."""
        began = time.monotonic()
        delay = 1.0
        refused = False
        while True:
            try:
                await session.start()
                break
            except ActorCapacityError:
                waited = time.monotonic() - began
                if waited >= self.capacity_wait_s:
                    raise TurnError(f"No sandbox worker had room for {self.capacity_wait_s / 60:.0f} minutes: every worker is "
                                    "running another conversation. Retry when one finishes.", retry=True) from None
                if not refused:
                    logger.info("No sandbox worker has room for %s; waiting", session.state.actor_name)
                refused = True
                step = CustomTask(title=f"Waiting for a sandbox worker · {waited:.0f} s", status_indicator="loading",
                                  content="Every sandbox worker is running another conversation. "
                                          "This turn starts as soon as one has room.")
                workflow.workflow.tasks[0] = step
                yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskUpdated(task=step, task_index=0))
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 5.0)
        if refused:
            turn["waited_s"] = round(time.monotonic() - began, 1)

    def _describe_upload(self, entry: dict) -> str:
        processors = self.spec.uploads.processors if self.spec.uploads else {}
        return "; ".join(processor.describe(entry[processor.key], entry)
                         for processor in processors.values() if processor.key in entry)

    async def _store_uploads(self, session, conversation: Conversation, turn: dict, attachments: list, context: dict) -> list[dict]:
        """Stage new uploads once; retries restore the original bytes under the same name."""
        policy = self.uploads.policy()
        uploads = conversation.metadata.setdefault("uploads", [])
        by_id = {item["attachment_id"]: item for item in uploads if item.get("attachment_id")}
        taken = {item["name"] for item in uploads}
        destination = conversation.turn_dir(turn["number"])
        added = []
        for attachment in attachments:
            entry = by_id.get(attachment.id)
            is_new = entry is None
            if is_new:
                data = self.uploads.read_file(attachment.id)
                name = upload_name(attachment.name, policy)
                stem, extension = Path(name).stem, Path(name).suffix
                counter = 2
                while name in taken:
                    name, counter = f"{stem}-{counter}{extension}", counter + 1
                taken.add(name)
                entry = {"attachment_id": attachment.id, "name": name, "path": f"inputs/uploads/{name}",
                         "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "turn": turn["number"]}
            else:
                try:
                    data = conversation.host_copy(entry).read_bytes()
                except FileNotFoundError:
                    raise UploadRejected("The retained upload is unavailable; attach the file again.") from None
                if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise UploadRejected("The retained upload changed; attach the file again.")
            await session.write(Path(entry["path"]), io.BytesIO(data))
            target = destination / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            processor = policy.processors.get(Path(entry["name"]).suffix)
            if processor is not None:
                entry[processor.key] = await processor.run(session, conversation, entry)
            if is_new:
                uploads.append(entry)
                by_id[attachment.id] = entry
            added.append(entry)
            turn["uploads"].append(entry["name"])
            turn["files"].append({"path": entry["path"], "bytes": entry["bytes"], "sha256": entry["sha256"],
                                  "new": is_new, "changed": is_new, "upload": True,
                                  **({processor.key: entry[processor.key]} if processor else {})})
        listing = {"files": []}
        for item in uploads:
            processor = policy.processors.get(Path(item["name"]).suffix)
            info = {processor.key: processor.listing(item[processor.key], item)} if processor and processor.key in item else {}
            listing["files"].append({**{key: item[key] for key in ("name", "bytes", "sha256")}, **info})
        await session.write(Path("inputs/uploads/MANIFEST.json"), io.BytesIO(json.dumps(listing, indent=2).encode()))
        # Persist the attachment-to-host-copy mapping before removing temporary bytes.
        await self.store.save_thread(conversation.thread, context)
        for attachment in attachments:
            await self.uploads.delete_attachment(attachment.id, {})
        return added

    async def _collect(self, session, conversation: Conversation, turn: dict) -> dict[str, bytes]:
        """Copy the agent's output folders to the host while the sandbox is still running."""
        folders = self.spec.collect
        listing = await session.exec("find", *folders, "-maxdepth", "1", "-type", "f", shell=False)
        # Names come from the sandbox: accept only plain file names in the agent's folders.
        pattern = re.compile(f"({'|'.join(map(re.escape, folders))})/{_FILE_NAME}")
        names = sorted({line for line in listing.stdout.decode(errors="replace").splitlines() if pattern.fullmatch(line)})
        previous = {item["path"]: item.get("sha256") for earlier in conversation.metadata["turns"][:-1]
                    for item in earlier["files"]}
        destination = conversation.turn_dir(turn["number"])
        collected = {}
        for name in names[:_MAX_FILES]:
            try:
                with await session.read(Path(name)) as handle:
                    data = handle.read()
            except ValueError:
                turn["files"].append({"path": name, "too_large": True, "changed": True})
                continue
            digest = hashlib.sha256(data).hexdigest()
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            collected[name] = data
            turn["files"].append({"path": name, "bytes": len(data), "sha256": digest,
                                  "new": name not in previous, "changed": previous.get(name) != digest})
        return collected

    def _turn_card(self, conversation: Conversation, context: dict, turn: dict) -> ThreadItemDoneEvent:
        thread = conversation.thread
        changed = {item["path"] for item in turn["files"] if item.get("changed")}
        children = []
        if turn["uploads"]:
            children.append({"type": "Text", "size": "sm", "value": "Uploaded to inputs/uploads/: " + ", ".join(turn["uploads"])})
        if self.card is not None:
            children += self.card(conversation, turn, changed)
        buttons = [
            {"type": "Button", "label": item["path"].removeprefix("output/"), "style": "secondary", "size": "sm",
             "onClickAction": {"type": "open_file", "handler": "client",
                               "payload": {"thread_id": thread.id, "turn": turn["number"], "path": item["path"]}}}
            for item in turn["files"] if item.get("changed") and item["path"].startswith("output/") and not item.get("too_large")
        ][:4]
        if turn.get("otel_trace_id"):
            buttons.append({"type": "Button", "label": "View trace", "style": "secondary", "size": "sm", "iconStart": "chart",
                            "onClickAction": {"type": "open_trace", "handler": "client",
                                              "payload": {"thread_id": thread.id, "turn": turn["number"]}}})
        if buttons:
            children.append({"type": "Row", "gap": 2, "wrap": "wrap", "children": buttons})
        tools = len(turn["tools"])
        in_harness = sum(1 for tool in turn["tools"] if tool.get("where") == "harness")
        caption = (f"Turn {turn['number']} · {turn['duration_s']} s · {turn['lifecycle']} {conversation.metadata['actor']} · "
                   f"{tools} tool call{'s' if tools != 1 else ''}")
        if in_harness:
            caption += f", {in_harness} in the harness"
        if turn.get("waited_s"):
            caption += f" · waited {turn['waited_s']:.0f} s for a worker"
        children.append({"type": "Caption", "size": "sm", "value": caption})
        widget = DynamicWidgetRoot.model_validate({"type": "Card", "size": "full", "children": children})
        return ThreadItemDoneEvent(item=WidgetItem(
            id=self.store.generate_item_id("message", thread, context), thread_id=thread.id,
            created_at=datetime.now(), widget=widget))

    async def delete_workspace(self, thread_id: str) -> None:
        """Delete the Actor (and with it the workspace volume), the history, then the host copies."""
        thread = await self.store.load_thread(thread_id, {})
        state = thread.metadata.get("substrate")
        async with self._locks[thread_id]:
            if state is not None:
                session = await self.client.resume(self.client.deserialize_session_state(state))
                try:
                    await self.client.delete(session)
                except RuntimeError as error:
                    # An Actor that is already gone must not block deleting its thread.
                    if not re.search(r"not found|no longer exists", str(error), re.IGNORECASE):
                        raise
            history = self.history(thread_id)
            try:
                await history.clear_session()
            finally:
                history.close()
            shutil.rmtree(self.output_dir / thread_id, ignore_errors=True)
