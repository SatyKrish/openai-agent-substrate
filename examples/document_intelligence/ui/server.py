# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""ChatKit server: one thread is one analysis, backed by one Substrate Actor.

Each message resumes the thread's Actor, copies any uploaded documents into its
workspace volume, runs the SandboxAgent, copies the workspace outputs to the
host, checks them against the original inputs, and suspends the Actor again.
Deleting the thread deletes the Actor, its volume and the host copies.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
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
from chatkit.store import AttachmentStore
from chatkit.types import (
    Attachment, AttachmentCreateParams, AttachmentUploadDescriptor, ClientEffectEvent, CustomTask, FileAttachment,
    ThreadItemAddedEvent, ThreadItemDoneEvent, ThreadItemUpdatedEvent, ThreadMetadata, UserMessageItem, WidgetItem,
    Workflow, WorkflowItem, WorkflowTaskAdded, WorkflowTaskUpdated,
)
from chatkit.widgets import DynamicWidgetRoot

from openai_agent_substrate import SubstrateSandboxClient, SubstrateSandboxOptions
from openai_agent_substrate.instrumentation import operation
from openai_agent_substrate.tracing import otel_trace_id, tracing_enabled

from ..agent.agent import build_agent
from ..agent.lib.verify import OUTPUTS, verify_artifacts
from ..agent.sandbox.sandbox import ACTOR_WORKSPACE, build_manifest, stage_inputs

TRACE_OPERATIONS = ("document_intelligence.turn", "document_intelligence.verify")
FOLLOW_UP_INSTRUCTIONS = """
This is a multi-turn conversation in one persistent workspace. Files from earlier
turns stay in inputs/, work/ and output/. For a follow-up question, start from the
existing files and the cited sources, write new code only when the question needs
it, and save any new result in output/ under a descriptive name. Do not rewrite
comparison.md, kpis.csv or result.json unless the user asks for it.

The user may upload documents. They arrive in inputs/uploads/, listed with their
SHA-256 in inputs/uploads/MANIFEST.json. The document-analysis skill's extractor
supports only the bundled sample reports: analyze uploaded documents with the shell
and Python instead. When you report facts from uploaded documents, also write
output/result.json with a "citations" list of {"document_id": "<file name>",
"quote": "<text copied exactly from that file>"}; the host checks every quote
against its retained source bytes. Use a full inputs/ path when file names or
short document IDs are ambiguous. Mixed reports may cite both the sample reports
and uploads; their calculated figures are not covered by the citation check.
""".strip()
UPLOADS_ONLY_INSTRUCTIONS = """
This workspace has no sample reports: its sources are the uploaded documents in
inputs/uploads/. Do not run the document-analysis skill's documents.py extract or
verify commands; they accept only the bundled sample reports. Check your own
figures and citations against the uploaded files instead.
""".strip()
UPLOAD_TYPES = {".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv"}
MAX_UPLOAD_BYTES = 1024 * 1024
MAX_UPLOADS_PER_MESSAGE = 5
_COLLECTED = re.compile(r"(output|work)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ATTACHMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_EXIT_CODE = re.compile(r"Process exited with code (-?\d+)")
_MAX_FILES = 40
_TASK_TEXT = 1600
_STAGED_TTL_S = 3600


class UploadRejected(ValueError):
    """An upload the server refuses; the message is safe to show."""


def upload_name(name: str) -> str:
    """A plain file name for inputs/uploads/, keeping one of the accepted extensions."""
    base = Path(name.replace("\\", "/")).name
    stem, dot, extension = base.rpartition(".")
    extension = "." + extension.lower() if dot else ""
    if extension not in UPLOAD_TYPES:
        raise UploadRejected("Upload Markdown, text or CSV files (.md, .txt, .csv).")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:80] or "document"
    return stem + extension


class UploadStore(AttachmentStore[dict]):
    """Two-phase ChatKit uploads, staged on the host until a turn copies them into the Actor."""

    def __init__(self, store, directory: Path, base_url: str):
        self.store = store
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")

    def path(self, attachment_id: str) -> Path:
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            raise UploadRejected("Unknown upload")
        return self.directory / attachment_id

    def _prune(self) -> None:
        # Files that were uploaded but never sent do not linger.
        cutoff = time.time() - _STAGED_TTL_S
        for staged in self.directory.iterdir():
            if staged.stat().st_mtime < cutoff:
                staged.unlink(missing_ok=True)

    async def create_attachment(self, input: AttachmentCreateParams, context: dict) -> Attachment:
        name = upload_name(input.name)
        if input.size > MAX_UPLOAD_BYTES:
            raise UploadRejected("Uploads are limited to 1 MiB per file.")
        self._prune()
        attachment_id = self.generate_attachment_id(input.mime_type, context)
        descriptor = AttachmentUploadDescriptor(url=f"{self.base_url}/uploads/{attachment_id}", method="PUT")
        return FileAttachment(id=attachment_id, name=name, mime_type=UPLOAD_TYPES[Path(name).suffix],
                              upload_descriptor=descriptor)

    async def write_file(self, attachment_id: str, data: bytes, context: dict) -> Attachment:
        target = self.path(attachment_id)
        attachment = await self.store.load_attachment(attachment_id, context)
        if attachment.upload_descriptor is None:
            raise UploadRejected("This upload has already been received.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise UploadRejected("Uploads are limited to 1 MiB per file.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadRejected("Uploads must be UTF-8 text.") from None
        partial = target.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(target)
        attachment = attachment.model_copy(update={"upload_descriptor": None})
        await self.store.save_attachment(attachment, context)
        return attachment

    def read_file(self, attachment_id: str) -> bytes:
        try:
            return self.path(attachment_id).read_bytes()
        except FileNotFoundError:
            raise UploadRejected("An attachment was not uploaded; remove it and attach the file again.") from None

    async def delete_attachment(self, attachment_id: str, context: dict) -> None:
        self.path(attachment_id).unlink(missing_ok=True)


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
                 base_url: str = "http://localhost:8000", workspace_root: str = ACTOR_WORKSPACE,
                 model_label: str | None = None, max_turns: int = 25):
        self.output_dir = Path(output_dir)
        self.uploads = UploadStore(store, self.output_dir / ".staged", base_url)
        super().__init__(store, attachment_store=self.uploads)
        store.on_delete_thread = self.delete_workspace
        self.client = client
        self.model = model
        self.model_label = model_label or str(model)
        self.template = template
        self.workspace_root = workspace_root
        self.max_turns = max_turns
        self.running: set[str] = set()
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._inputs = stage_inputs()

    async def respond(self, thread: ThreadMetadata, input_user_message: UserMessageItem | None, context: dict):
        if input_user_message is None:
            return
        text = user_text(input_user_message)
        attachments = list(input_user_message.attachments)
        if len(attachments) > MAX_UPLOADS_PER_MESSAGE:
            raise CustomStreamError(f"Attach at most {MAX_UPLOADS_PER_MESSAGE} files per message.", allow_retry=False)
        if not text and not attachments:
            raise CustomStreamError("Send a question about the reports.", allow_retry=False)
        text = text or "Summarize the uploaded documents, citing exact quotes."
        async with self._locks[thread.id]:
            self.running.add(thread.id)
            try:
                async for event in self._turn(thread, text, attachments, context):
                    yield event
            finally:
                self.running.discard(thread.id)

    async def _turn(self, thread: ThreadMetadata, text: str, attachments: list, context: dict):
        meta = thread.metadata
        analysis_id = meta.setdefault("analysis_id", str(uuid.uuid4()))
        # A thread that starts with uploads gets an empty workspace; otherwise the sample reports.
        meta.setdefault("sources", "uploads" if attachments and not meta.get("substrate") else "samples")
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
        try:
            with trace("Document Intelligence turn", trace_id=sdk_trace_id, group_id=analysis_id, disabled=not traced):
                with operation("document_intelligence.turn"):
                    async for event in self._run_turn(thread, context_, turn, text, attachments, analysis_id):
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

    async def _run_turn(self, thread, context_: AgentContext, turn: dict, text: str, attachments: list, analysis_id: str):
        meta = thread.metadata
        created = meta.get("substrate") is None
        samples = meta["sources"] == "samples"
        step = CustomTask(title="Creating the workspace" if created else "Resuming the workspace", status_indicator="loading")
        workflow = WorkflowItem(id=context_.generate_id("workflow"), created_at=datetime.now(), thread_id=thread.id,
                                workflow=Workflow(type="custom", tasks=[step]))
        context_.workflow_item = workflow
        yield ThreadItemAddedEvent(item=workflow)

        began = time.monotonic()
        if created:
            manifest = build_manifest(self._inputs if samples else {}, analysis_id, self.workspace_root)
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
            if created:
                detail = f"Actor `{actor}` with its own workspace volume" + (", holding the sample reports." if samples else ".")
            else:
                detail = f"Same Actor `{actor}`; its workspace volume still holds the earlier turns' files."
            step = CustomTask(title=f"{verb} the workspace · {seconds:.1f} s", content=detail, status_indicator="complete")
            workflow.workflow.tasks[0] = step
            yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskUpdated(task=step, task_index=0))

            if attachments:
                noun = "upload" if len(attachments) == 1 else "uploads"
                copying = CustomTask(title=f"Copying {len(attachments)} {noun} into the workspace", status_indicator="loading")
                workflow.workflow.tasks.append(copying)
                index = len(workflow.workflow.tasks) - 1
                yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskAdded(task=copying, task_index=index))
                added = await self._store_uploads(session, thread, turn, attachments, context_.request_context)
                listing = "\n".join(f"{item['path']}  {item['bytes']} bytes  sha256 {item['sha256'][:12]}…" for item in added)
                copied = CustomTask(title=f"Copied {len(added)} {noun} to inputs/uploads/", content=fenced(listing),
                                    status_indicator="complete")
                workflow.workflow.tasks[index] = copied
                yield ThreadItemUpdatedEvent(item_id=workflow.id, update=WorkflowTaskUpdated(task=copied, task_index=index))
                text += "\n\n[Uploaded to the workspace this turn: " + ", ".join(
                    f"{item['path']} ({item['bytes']} bytes)" for item in added) + "]"

            agent = build_agent(session.state.manifest, self.model)
            instructions = agent.instructions + "\n\n" + FOLLOW_UP_INSTRUCTIONS
            if not samples:
                instructions += "\n\n" + UPLOADS_ONLY_INSTRUCTIONS
            agent = agent.clone(instructions=instructions)
            history = meta.get("history", [])
            result = Runner.run_streamed(
                agent, [*history, {"role": "user", "content": text}], max_turns=self.max_turns,
                run_config=RunConfig(sandbox=SandboxRunConfig(session=session), tracing_disabled=not tracing_enabled(),
                                     trace_include_sensitive_data=False))
            async for event in stream_agent_response(context_, _ToolSteps(result, context_, turn["tools"])):
                yield event
            meta["history"] = result.to_input_list()
            files = await self._collect(session, thread, turn)
            turn["check"] = self._check(thread, files, analysis_id)
        meta["substrate"] = self.client.serialize_session_state(session.state)

    def _host_upload_path(self, thread: ThreadMetadata, upload: dict) -> Path:
        return self.output_dir / thread.id / f"turn-{upload['turn']}" / upload["path"]

    async def _store_uploads(self, session, thread: ThreadMetadata, turn: dict, attachments: list, context: dict) -> list[dict]:
        """Stage new uploads once; retries restore the original bytes under the same name."""
        uploads = thread.metadata.setdefault("uploads", [])
        by_id = {item["attachment_id"]: item for item in uploads if item.get("attachment_id")}
        taken = {item["name"] for item in uploads}
        destination = self.output_dir / thread.id / f"turn-{turn['number']}"
        added = []
        for attachment in attachments:
            entry = by_id.get(attachment.id)
            is_new = entry is None
            if is_new:
                data = self.uploads.read_file(attachment.id)
                name = upload_name(attachment.name)
                stem, extension = Path(name).stem, Path(name).suffix
                counter = 2
                while name in taken:
                    name, counter = f"{stem}-{counter}{extension}", counter + 1
                taken.add(name)
                entry = {"attachment_id": attachment.id, "name": name, "path": f"inputs/uploads/{name}",
                         "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "turn": turn["number"]}
            else:
                try:
                    data = self._host_upload_path(thread, entry).read_bytes()
                except FileNotFoundError:
                    raise UploadRejected("The retained upload is unavailable; attach the file again.") from None
                if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise UploadRejected("The retained upload changed; attach the file again.")
            await session.write(Path(entry["path"]), io.BytesIO(data))
            target = destination / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if is_new:
                uploads.append(entry)
                by_id[attachment.id] = entry
            added.append(entry)
            turn["uploads"].append(entry["name"])
            turn["files"].append({"path": entry["path"], "bytes": entry["bytes"], "sha256": entry["sha256"],
                                  "new": is_new, "changed": is_new, "upload": True})
        listing = {"files": [{key: item[key] for key in ("name", "bytes", "sha256")} for item in uploads]}
        await session.write(Path("inputs/uploads/MANIFEST.json"), io.BytesIO(json.dumps(listing, indent=2).encode()))
        # Persist the attachment-to-host-copy mapping before removing temporary bytes.
        await self.store.save_thread(thread, context)
        for attachment in attachments:
            await self.uploads.delete_attachment(attachment.id, {})
        return added

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

    def _check(self, thread: ThreadMetadata, files: dict[str, bytes], analysis_id: str) -> dict:
        outputs = {name.removeprefix("output/"): data for name, data in files.items() if name.startswith("output/")}
        samples = thread.metadata["sources"] == "samples"
        with operation("document_intelligence.verify"):
            sample_check = self._check_samples(outputs, analysis_id) if samples else None
            if sample_check and (sample_check["status"] == "passed" or not thread.metadata.get("uploads")):
                return sample_check
            sources = self._citation_sources(thread, include_samples=samples)
            checked = self._check_citations(thread, outputs, sources=sources)
            checked["scope"] = "citations"
            if checked["status"] == "citations_passed" and sample_check:
                uses_uploads = any(sources[self._resolve_source(document, sources)]["uploaded"]
                                   for document in checked["documents"])
                if not uses_uploads:
                    # Matching sample quotes cannot hide a broken sample calculation or CSV.
                    return sample_check
            return checked

    def _check_samples(self, outputs: dict[str, bytes], analysis_id: str) -> dict:
        extra = sorted(name for name in outputs if name not in OUTPUTS)
        if not set(OUTPUTS) <= outputs.keys():
            return {"status": "not_written", "unchecked": extra}
        try:
            verify_artifacts({name: outputs[name] for name in OUTPUTS}, self._inputs, analysis_id)
        except ValueError as error:
            return {"status": "failed", "detail": str(error), "unchecked": extra}
        return {"status": "passed", "unchecked": extra}

    def _citation_sources(self, thread: ThreadMetadata, *, include_samples=False) -> dict:
        sources = {}
        if include_samples:
            for name, data in self._inputs.items():
                if Path(name).suffix in UPLOAD_TYPES:
                    sources[f"inputs/{name}"] = {"name": name, "text": " ".join(data.decode("utf-8").split()), "uploaded": False}
        for upload in thread.metadata.get("uploads", []):
            text = self._host_upload_path(thread, upload).read_text(encoding="utf-8")
            sources[upload["path"]] = {"name": upload["name"], "text": " ".join(text.split()), "uploaded": True}
        return sources

    @staticmethod
    def _resolve_source(document, sources: dict) -> str | None:
        """Paths and exact filenames take priority; stems must resolve uniquely."""
        if not isinstance(document, str):
            return None
        if document in sources:
            return document
        matches = [key for key, source in sources.items() if source["name"] == document]
        if not matches:
            matches = [key for key, source in sources.items() if Path(source["name"]).stem == document]
        if len(matches) > 1:
            raise ValueError(f"Document ID {document!r} is ambiguous; cite the full inputs/ path.")
        return matches[0] if matches else None

    def _check_citations(self, thread: ThreadMetadata, outputs: dict[str, bytes], *, sources=None) -> dict:
        """Check quotes against trusted host sources, never against sandbox-edited inputs."""
        unchecked = sorted(name for name in outputs if name != "result.json")
        if "result.json" not in outputs:
            return {"status": "no_citations", "unchecked": unchecked}
        if sources is None:
            sources = self._citation_sources(thread)
        try:
            citations = json.loads(outputs["result.json"])["citations"]
            if not isinstance(citations, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return {"status": "failed", "detail": "output/result.json has no citations list.", "unchecked": unchecked}
        documents = set()
        for citation in citations:
            document = citation.get("document_id") if isinstance(citation, dict) else None
            quote = citation.get("quote") if isinstance(citation, dict) else None
            try:
                source_id = self._resolve_source(document, sources)
            except ValueError as error:
                return {"status": "failed", "detail": str(error), "unchecked": unchecked}
            if source_id is None:
                return {"status": "failed", "detail": f"A citation names {document!r}, which is not a source file.",
                        "unchecked": unchecked}
            if not isinstance(quote, str) or not quote.strip() or " ".join(quote.split()) not in sources[source_id]["text"]:
                shown = clipped(str(quote), 120)
                return {"status": "failed", "detail": f"Not found in {document}: {shown}", "unchecked": unchecked}
            documents.add(document)
        if not documents:
            return {"status": "no_citations", "unchecked": unchecked}
        return {"status": "citations_passed", "count": len(citations), "documents": sorted(documents), "unchecked": unchecked}

    def _turn_card(self, thread: ThreadMetadata, context: dict, turn: dict) -> ThreadItemDoneEvent:
        check = turn.get("check") or {}
        changed = {item["path"] for item in turn["files"] if item.get("changed")}
        contract_changed = any(f"output/{name}" in changed for name in OUTPUTS)
        children = []
        if turn["uploads"]:
            children.append({"type": "Text", "size": "sm", "value":
                             "Uploaded to inputs/uploads/: " + ", ".join(turn["uploads"])})
        if check.get("status") == "citations_passed":
            count = check["count"]
            children.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "success",
                             "label": f"{count} quote{' matches' if count == 1 else 's match'} the source documents"}]})
            children.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                             "Every quote in result.json appears in the source file it cites (host check). "
                             "Figures computed from them are the agent's own work."})
        elif check.get("status") == "no_citations" and any(path.startswith("output/") for path in changed):
            children.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "warning",
                             "label": "No citations to check"}]})
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
                             ", ".join(unchecked) + " is the agent's own work; " + ("the host checked quotes in result.json only."
                             if check.get("scope") == "citations" else "the host check covers the three contract outputs only.")})
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
        """Delete the Actor (and with it the workspace volume), then the host copies."""
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
            shutil.rmtree(self.output_dir / thread.id, ignore_errors=True)
