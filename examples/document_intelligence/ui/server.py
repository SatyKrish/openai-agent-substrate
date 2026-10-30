# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""ChatKit server: one thread is one analysis, backed by one Substrate Actor.

Each message resumes the thread's Actor, runs the SandboxAgent, copies the
workspace outputs to the host, checks them against the original inputs, and
suspends the Actor again. Deleting the thread deletes the Actor.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from agents import Runner, trace
from agents.run import RunConfig
from agents.sandbox import SandboxRunConfig
from agents.tracing import gen_trace_id
from chatkit.agents import AgentContext, stream_agent_response
from chatkit.errors import CustomStreamError
from chatkit.server import ChatKitServer
from chatkit.types import (
    ClientEffectEvent, CustomTask, ThreadItemAddedEvent, ThreadItemDoneEvent, ThreadItemUpdatedEvent,
    ThreadMetadata, UserMessageItem, WidgetItem, Workflow, WorkflowItem, WorkflowTaskUpdated,
)
from chatkit.widgets import DynamicWidgetRoot

from openai_agent_substrate import SubstrateSandboxClient, SubstrateSandboxOptions
from openai_agent_substrate.instrumentation import operation
from openai_agent_substrate.tracing import otel_trace_id, tracing_enabled

from ..agent.agent import build_agent
from ..agent.lib.verify import OUTPUTS, verify_artifacts
from ..agent.sandbox.sandbox import build_manifest, stage_inputs

TRACE_OPERATIONS = ("document_intelligence.turn", "document_intelligence.verify")
FOLLOW_UP_INSTRUCTIONS = """
This is a multi-turn conversation in one persistent workspace. Files from earlier
turns stay in inputs/, work/ and output/. For a follow-up question, start from the
existing files and the cited sources, write new code only when the question needs
it, and save any new result in output/ under a descriptive name. Do not rewrite
comparison.md, kpis.csv or result.json unless the user asks for it.
""".strip()
_COLLECTED = re.compile(r"(output|work)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_EXIT_CODE = re.compile(r"Process exited with code (-?\d+)")
_MAX_FILES = 40
_TASK_TEXT = 1600


def fenced(text: str, language: str = "") -> str:
    """Fence untrusted text as code, so ChatKit never renders it as Markdown."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language}\n{text}\n{fence}"


def clipped(text: str, limit: int = _TASK_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n… {len(text) - limit} more characters"


def user_text(item: UserMessageItem) -> str:
    return "\n".join(part.text for part in item.content if getattr(part, "type", None) == "input_text").strip()


def _field(raw, name):
    return raw.get(name) if isinstance(raw, dict) else getattr(raw, name, None)


def describe_call(raw) -> tuple[str, str, str]:
    """Tool name, call id and a short, human-readable view of the call arguments."""
    name = _field(raw, "name") or _field(raw, "type") or "tool"
    call_id = _field(raw, "call_id") or _field(raw, "id") or ""
    arguments = _field(raw, "arguments")
    if arguments is None:
        arguments = _field(raw, "input") or ""
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            arguments = parsed.get("cmd") or parsed.get("skill_name") or json.dumps(parsed)
    elif isinstance(arguments, dict):
        arguments = arguments.get("cmd") or arguments.get("skill_name") or json.dumps(arguments)
    return str(name), str(call_id), str(arguments)


def describe_output(output) -> tuple[str, str]:
    """A status suffix (exit code) and the readable part of a tool's output."""
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    match = _EXIT_CODE.search(text)
    status = f"exit {match[1]}" if match else ""
    if "\nOutput:\n" in text:
        text = text.split("\nOutput:\n", 1)[1]
    return status, text.strip()


class _ToolSteps:
    """Adds sandbox tool calls to ChatKit's workflow while ChatKit converts the model stream.

    ChatKit's converter renders assistant messages and reasoning but not tool calls,
    and it only reads ``stream_events()`` from the run result.
    """

    def __init__(self, result, context: AgentContext, record: list):
        self.result = result
        self.context = context
        self.record = record

    async def stream_events(self):
        pending: dict[str, tuple[WorkflowItem, int, str, str]] = {}
        async for event in self.result.stream_events():
            if getattr(event, "type", None) == "run_item_stream_event":
                if event.name == "tool_called":
                    name, call_id, arguments = describe_call(event.item.raw_item)
                    task = CustomTask(title=name, content=fenced(clipped(arguments)), status_indicator="loading")
                    await self.context.add_workflow_task(task)
                    workflow = self.context.workflow_item
                    pending[call_id] = (workflow, workflow.workflow.tasks.index(task), name, arguments)
                    self.record.append({"tool": name, "status": "running"})
                elif event.name == "tool_output":
                    call_id = _field(event.item.raw_item, "call_id") or ""
                    if call_id in pending:
                        workflow, index, name, arguments = pending.pop(call_id)
                        status, text = describe_output(event.item.output)
                        title = f"{name} · {status}" if status else name
                        content = fenced(clipped(arguments))
                        if text:
                            content += "\n\n" + fenced(clipped(text))
                        task = CustomTask(title=title, content=content, status_indicator="complete")
                        for entry in reversed(self.record):
                            if entry["tool"] == name and entry["status"] == "running":
                                entry["status"] = status or "done"
                                break
                        if self.context.workflow_item is workflow:
                            await self.context.update_workflow_task(task, index)
            yield event


class DocumentChatServer(ChatKitServer[dict]):
    def __init__(self, store, client: SubstrateSandboxClient, *, model, template: str, output_dir: Path,
                 workspace_root: str = "/workspace", model_label: str | None = None, max_turns: int = 25):
        super().__init__(store)
        store.on_delete_thread = self.delete_workspace
        self.client = client
        self.model = model
        self.model_label = model_label or str(model)
        self.template = template
        self.output_dir = Path(output_dir)
        self.workspace_root = workspace_root
        self.max_turns = max_turns
        self.running: set[str] = set()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._inputs = stage_inputs()

    async def respond(self, thread: ThreadMetadata, input_user_message: UserMessageItem | None, context: dict):
        if input_user_message is None:
            return
        text = user_text(input_user_message)
        if not text:
            raise CustomStreamError("Send a question about the reports.", allow_retry=False)
        async with self._locks[thread.id]:
            self.running.add(thread.id)
            try:
                async for event in self._turn(thread, text, context):
                    yield event
            finally:
                self.running.discard(thread.id)

    async def _turn(self, thread: ThreadMetadata, text: str, context: dict):
        meta = thread.metadata
        analysis_id = meta.setdefault("analysis_id", str(uuid.uuid4()))
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
            "model": self.model_label, "tools": [], "files": [], "check": None,
        }
        turns.append(turn)
        await self.store.save_thread(thread, context)
        context_ = AgentContext(thread=thread, store=self.store, request_context=context)
        started = time.monotonic()
        try:
            with trace("Document Intelligence turn", trace_id=sdk_trace_id, group_id=analysis_id, disabled=not traced):
                with operation("document_intelligence.turn"):
                    async for event in self._run_turn(thread, context_, turn, text, analysis_id):
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
            # Exception text can contain command output; the browser gets the type only.
            raise CustomStreamError(
                f"The turn failed ({type(error).__name__}). The workspace was suspended; you can retry.",
                allow_retry=True) from error
        finally:
            turn["duration_s"] = round(time.monotonic() - started, 1)
            await self.store.save_thread(thread, context)
        yield self._turn_card(thread, context, turn)
        changed = [item["path"] for item in turn["files"] if item["changed"] and item["path"].startswith("output/")]
        if changed:
            preferred = "output/comparison.md" if "output/comparison.md" in changed else changed[0]
            yield ClientEffectEvent(name="open_file", data={"thread_id": thread.id, "turn": turn["number"], "path": preferred})

    async def _run_turn(self, thread, context_: AgentContext, turn: dict, text: str, analysis_id: str):
        meta = thread.metadata
        created = meta.get("substrate") is None
        step = CustomTask(title="Creating the workspace" if created else "Resuming the workspace", status_indicator="loading")
        context_.workflow_item = WorkflowItem(
            id=context_.generate_id("workflow"), created_at=datetime.now(), thread_id=thread.id,
            workflow=Workflow(type="custom", tasks=[step]))
        yield ThreadItemAddedEvent(item=context_.workflow_item)

        began = time.monotonic()
        if created:
            manifest = build_manifest(self._inputs, analysis_id, self.workspace_root)
            session = await self.client.create(manifest=manifest, options=SubstrateSandboxOptions(template=self.template))
            # Save immediately: a failed first turn must still be deletable.
            meta["substrate"] = self.client.serialize_session_state(session.state)
            meta["actor"] = f"{session.state.atespace}/{session.state.actor_name}"
            await self.store.save_thread(thread, context_.request_context)
        else:
            session = await self.client.resume(self.client.deserialize_session_state(meta["substrate"]))
        actor = meta["actor"]
        async with session:
            if created:
                boundary = await session.exec('test -z "${OPENAI_API_KEY:-}" && test ! -d /var/run/agent-config')
                if not boundary.ok():
                    raise RuntimeError("The sandbox must not contain the harness API key or Secret mount")
            seconds = time.monotonic() - began
            verb = "Created" if created else "Resumed"
            detail = (f"Actor `{actor}`, staged the reports and the skill." if created
                      else f"Same Actor `{actor}`; files from earlier turns are still there.")
            step = CustomTask(title=f"{verb} the workspace · {seconds:.1f} s", content=detail, status_indicator="complete")
            context_.workflow_item.workflow.tasks[0] = step
            yield ThreadItemUpdatedEvent(item_id=context_.workflow_item.id,
                                         update=WorkflowTaskUpdated(task=step, task_index=0))

            agent = build_agent(session.state.manifest, self.model)
            agent = agent.clone(instructions=agent.instructions + "\n\n" + FOLLOW_UP_INSTRUCTIONS)
            history = meta.get("history", [])
            result = Runner.run_streamed(
                agent, [*history, {"role": "user", "content": text}], max_turns=self.max_turns,
                run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=not tracing_enabled(),
                                     trace_include_sensitive_data=False))
            async for event in stream_agent_response(context_, _ToolSteps(result, context_, turn["tools"])):
                yield event
            meta["history"] = result.to_input_list()
            files = await self._collect(session, thread, turn)
            turn["check"] = self._check(files, analysis_id)
        meta["substrate"] = self.client.serialize_session_state(session.state)

    async def _collect(self, session, thread: ThreadMetadata, turn: dict) -> dict[str, bytes]:
        """Copy output/ and work/ files to the host while the Actor is still running."""
        listing = await session.exec("find", "output", "work", "-maxdepth", "1", "-type", "f", shell=False)
        # Names come from the sandbox: accept only plain file names in the two folders.
        names = sorted({line for line in listing.stdout.decode(errors="replace").splitlines() if _COLLECTED.fullmatch(line)})
        previous = {item["path"]: item.get("sha256") for earlier in thread.metadata["turns"][:-1] for item in earlier["files"]}
        destination = self.output_dir / thread.id / f"turn-{turn['number']}"
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

    def _check(self, files: dict[str, bytes], analysis_id: str) -> dict:
        outputs = {name.removeprefix("output/"): data for name, data in files.items() if name.startswith("output/")}
        extra = sorted(name for name in outputs if name not in OUTPUTS)
        if not set(OUTPUTS) <= outputs.keys():
            return {"status": "not_written", "unchecked": extra}
        with operation("document_intelligence.verify"):
            try:
                verify_artifacts({name: outputs[name] for name in OUTPUTS}, self._inputs, analysis_id)
            except ValueError as error:
                return {"status": "failed", "detail": str(error), "unchecked": extra}
        return {"status": "passed", "unchecked": extra}

    def _turn_card(self, thread: ThreadMetadata, context: dict, turn: dict) -> ThreadItemDoneEvent:
        check = turn.get("check") or {}
        changed = {item["path"] for item in turn["files"] if item.get("changed")}
        contract_changed = any(f"output/{name}" in changed for name in OUTPUTS)
        children = []
        if check.get("status") == "passed":
            label = "Checked against the original documents" if contract_changed else "Outputs unchanged · still match the sources"
            children.append({"type": "Row", "gap": 2, "children": [
                {"type": "Badge", "label": label, "color": "success" if contract_changed else "secondary", "variant": "soft"}]})
            if contract_changed:
                children.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                                 "comparison.md, kpis.csv and result.json match the source reports and the KPI table. "
                                 "The check runs on the host, outside the sandbox."})
        elif check.get("status") == "failed":
            children.append({"type": "Row", "children": [{"type": "Badge", "label": "Host check failed", "color": "danger", "variant": "soft"}]})
            children.append({"type": "Text", "size": "sm", "value": check.get("detail", "")})
        unchecked = [name for name in check.get("unchecked", []) if f"output/{name}" in changed]
        if unchecked:
            children.append({"type": "Row", "children": [{"type": "Badge", "label": "Not checked on the host", "color": "warning", "variant": "soft"}]})
            children.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                             ", ".join(unchecked) + " is the agent's own work; the host check covers the three contract outputs only."})
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
        children.append({"type": "Caption", "size": "sm", "value":
                         f"Turn {turn['number']} · {turn['duration_s']} s · {turn['lifecycle']} {thread.metadata['actor']} · "
                         f"{tools} tool call{'s' if tools != 1 else ''}"})
        widget = DynamicWidgetRoot.model_validate({"type": "Card", "size": "full", "children": children})
        return ThreadItemDoneEvent(item=WidgetItem(
            id=self.store.generate_item_id("message", thread, context), thread_id=thread.id,
            created_at=datetime.now(), widget=widget))

    async def delete_workspace(self, thread_id: str) -> None:
        thread = await self.store.load_thread(thread_id, {})
        state = thread.metadata.get("substrate")
        if state is None:
            return
        async with self._locks[thread_id]:
            session = await self.client.resume(self.client.deserialize_session_state(state))
            try:
                await self.client.delete(session)
            except RuntimeError as error:
                # An Actor that is already gone must not block deleting its thread.
                if not re.search(r"not found|no longer exists", str(error), re.IGNORECASE):
                    raise
