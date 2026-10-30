# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import shlex
import sys
import uuid

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from agents import Runner  # noqa: E402
from chatkit.types import UserMessageItem  # noqa: E402

from agents.items import ModelResponse  # noqa: E402
from agents.models.interface import Model  # noqa: E402
from agents.usage import Usage  # noqa: E402
from openai.types.responses import (  # noqa: E402
    Response, ResponseCompletedEvent, ResponseFunctionToolCall, ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent, ResponseOutputMessage, ResponseOutputText,
)
from starlette.testclient import TestClient  # noqa: E402

from examples.document_intelligence.evals.scripted import DocumentWorkflowModel  # noqa: E402
from examples.document_intelligence.main import QUESTION  # noqa: E402
from examples.document_intelligence.ui.app import create_app  # noqa: E402
from examples.document_intelligence.ui.server import DocumentChatServer, UploadRejected, describe_output, fenced, upload_name  # noqa: E402
from examples.document_intelligence.ui.store import SQLiteStore  # noqa: E402
from examples.document_intelligence.ui.traces import summarize  # noqa: E402
from openai_agent_substrate import SubstrateSandboxClient  # noqa: E402


class FollowUpModel(Model):
    """Reads a turn-1 output from the resumed workspace, then answers."""

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kwargs):
        last = input[-1] if isinstance(input, list) and input else {}
        if isinstance(last, dict) and last.get("type") == "function_call_output":
            text = last["output"] if isinstance(last["output"], str) else json.dumps(last["output"])
            row = next(line for line in text.splitlines() if line.startswith("ACME,"))
            output = [ResponseOutputMessage(id=f"msg_{uuid.uuid4().hex}", role="assistant", type="message", status="completed",
                                            content=[ResponseOutputText(type="output_text", annotations=[], text=f"Still there: {row}")])]
        else:
            output = [ResponseFunctionToolCall(id=f"fc_{uuid.uuid4().hex}", call_id=f"call_{uuid.uuid4().hex}", type="function_call",
                                               name="exec_command", arguments=json.dumps({"cmd": "cat output/kpis.csv", "login": False}))]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, *args, **kwargs):
        raise NotImplementedError
        yield


class StreamingScripted(Model):
    """Streams scripted responses: the workflow for the first question, FollowUpModel afterwards."""

    def __init__(self):
        self.first = DocumentWorkflowModel(sys.executable)
        self.follow_up = FollowUpModel()

    def _pick(self, input):
        users = [item for item in input if isinstance(item, dict) and item.get("role") == "user"]
        return self.first if len(users) <= 1 else self.follow_up

    async def get_response(self, system_instructions, input, *args, **kwargs):
        return await self._pick(input).get_response(system_instructions, input, *args, **kwargs)

    async def stream_response(self, system_instructions, input, *args, **kwargs):
        async for event in self.stream(await self._pick(input).get_response(system_instructions, input, *args, **kwargs)):
            yield event

    @staticmethod
    async def stream(response):
        sequence = 0
        for index, item in enumerate(response.output):
            yield ResponseOutputItemAddedEvent.model_construct(type="response.output_item.added", item=item,
                                                               output_index=index, sequence_number=sequence)
            yield ResponseOutputItemDoneEvent.model_construct(type="response.output_item.done", item=item,
                                                              output_index=index, sequence_number=sequence + 1)
            sequence += 2
        yield ResponseCompletedEvent.model_construct(type="response.completed", sequence_number=sequence, response=Response.model_construct(
            id=f"resp_{uuid.uuid4().hex}", output=list(response.output), usage=None, object="response", model="scripted"))


class ShellSteps(Model):
    """Streams one exec_command per model call, then a final answer."""

    def __init__(self, commands, answer="Done."):
        self.commands = list(commands)
        self.answer = answer
        self.outputs = []

    async def get_response(self, system_instructions, input, *args, **kwargs):
        if self.commands and isinstance(input[-1], dict) and input[-1].get("type") == "function_call_output":
            self.outputs.append(input[-1]["output"])
        if self.commands and len(self.outputs) < len(self.commands):
            command = self.commands[len(self.outputs)]
            output = [ResponseFunctionToolCall(id=f"fc_{uuid.uuid4().hex}", call_id=f"call_{uuid.uuid4().hex}", type="function_call",
                                               name="exec_command", arguments=json.dumps({"cmd": command, "login": False}))]
        else:
            output = [ResponseOutputMessage(id=f"msg_{uuid.uuid4().hex}", role="assistant", type="message", status="completed",
                                            content=[ResponseOutputText(type="output_text", annotations=[], text=self.answer)])]
        return ModelResponse(output=output, usage=Usage(), response_id=None)

    async def stream_response(self, system_instructions, input, *args, **kwargs):
        async for event in StreamingScripted.stream(await self.get_response(system_instructions, input, *args, **kwargs)):
            yield event


def message(text, attachments=()):
    return {"content": [{"type": "input_text", "text": text}], "attachments": list(attachments), "inference_options": {}}


NOTES = b"# Gamma notes\n\nGamma revenue was USD 12.5 billion in fiscal 2025.\nHeadcount grew to 4,100.\n"


def citations(quote, document="gamma-notes.md"):
    payload = json.dumps({"citations": [{"document_id": document, "quote": quote}]})
    return f"printf '%s' '{payload}' > output/result.json && cat output/result.json"


def upload(http, name, data, mime_type="text/markdown"):
    created = post_chatkit(http, {"type": "attachments.create", "params": {"name": name, "size": len(data), "mime_type": mime_type}})
    url = created["upload_descriptor"]["url"]
    assert url.startswith("http://localhost:8000/uploads/")
    response = http.put(url.removeprefix("http://localhost:8000"), content=data)
    assert response.status_code == 200, response.text
    return created["id"]


def post_chatkit(http, payload):
    response = http.post("/chatkit", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code == 200, response.text
    return response.json()


async def events(chat, payload):
    result = await chat.process(json.dumps(payload), {})
    collected = []
    async for chunk in result:
        collected += [json.loads(line[6:]) for line in chunk.decode().splitlines() if line.startswith("data: ")]
    return collected


def task_titles(stream):
    titles = []
    for event in stream:
        item = event.get("item") or {}
        if item.get("type") == "workflow":
            titles += [task.get("title") for task in item["workflow"]["tasks"]]
        update = event.get("update") or {}
        if update.get("task"):
            titles.append(update["task"].get("title"))
    return titles


@pytest.fixture
def chat(server, tmp_path):
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    store = SQLiteStore(tmp_path / "threads.sqlite3")
    yield DocumentChatServer(store, client, model=StreamingScripted(), template="document-intelligence",
                             output_dir=tmp_path / "threads", workspace_root=str(server.root), model_label="scripted")
    store.close()


async def test_two_turns_share_one_actor_and_the_thread_delete_removes_it(chat, server, tmp_path):
    first = await events(chat, {"type": "threads.create", "params": {"input": message(QUESTION)}})
    errors = [event for event in first if event["type"] == "error"]
    assert not errors, errors
    thread_id = next(event["thread"]["id"] for event in first if event["type"] == "thread.created")
    titles = task_titles(first)
    assert any(title.startswith("Created the workspace") for title in titles)
    assert {"load_skill", "apply_patch"} <= {title.split(" · ")[0] for title in titles}
    assert "exec_command · exit 0" in titles
    widgets = [event["item"] for event in first if event["type"] == "thread.item.done" and event["item"]["type"] == "widget"]
    assert "Checked against the original documents" in json.dumps(widgets)
    assert any(event["type"] == "client_effect" and event["data"]["path"] == "output/comparison.md" for event in first)
    assert [kind for kind, _ in server.control.events] == ["create", "resume", "suspend"]

    thread = await chat.store.load_thread(thread_id, {})
    turn = thread.metadata["turns"][0]
    assert turn["status"] == "completed" and turn["lifecycle"] == "created"
    assert turn["check"]["status"] == "passed"
    assert {"output/comparison.md", "output/kpis.csv", "output/result.json", "work/analyze.py"} <= {f["path"] for f in turn["files"]}
    assert (tmp_path / "threads" / thread_id / "turn-1/output/comparison.md").read_text().startswith("# Synthetic")
    assert "OPENAI_API_KEY" not in json.dumps(thread.metadata)

    second = await events(chat, {"type": "threads.add_user_message", "params": {"thread_id": thread_id, "input": message("Is the KPI table still there?")}})
    assert not [event for event in second if event["type"] == "error"]
    assert any(title.startswith("Resumed the workspace") for title in task_titles(second))
    answer = [event["item"] for event in second if event["type"] == "thread.item.done" and event["item"]["type"] == "assistant_message"]
    assert "Still there: ACME,2024,94.2,21.6,22.93" in json.dumps(answer)
    assert [kind for kind, _ in server.control.events].count("create") == 1
    thread = await chat.store.load_thread(thread_id, {})
    second_turn = thread.metadata["turns"][1]
    assert second_turn["lifecycle"] == "resumed"
    assert not any(item["changed"] or item["new"] for item in second_turn["files"])
    assert all(item["new"] for item in turn["files"])
    assert second_turn["check"]["status"] == "passed"

    result = await chat.process(json.dumps({"type": "threads.delete", "params": {"thread_id": thread_id}}), {})
    assert result.json == b"{}"
    assert server.control.events[-1][0] == "delete"
    assert server.control.actors == {}


async def test_http_routes_serve_only_recorded_files_and_refuse_cross_site_requests(chat, server):
    first = await events(chat, {"type": "threads.create", "params": {"input": message(QUESTION)}})
    thread_id = next(event["thread"]["id"] for event in first if event["type"] == "thread.created")
    app = create_app(chat, config={"model": "scripted", "prompts": []}, port=8000, jaeger_url=None)
    with TestClient(app, base_url="http://localhost:8000") as http:
        assert http.get("/").status_code == 200
        state = http.get(f"/api/threads/{thread_id}").json()
        assert state["state"] == "suspended" and state["actor"].startswith("test-space/")
        report = http.get(f"/api/threads/{thread_id}/turns/1/files/output/comparison.md")
        assert report.status_code == 200 and report.headers["content-security-policy"] == "sandbox"
        assert report.headers["content-type"].startswith("text/plain")
        assert http.get(f"/api/threads/{thread_id}/turns/1/files/inputs/acme-2024.md").status_code == 404
        assert http.get(f"/api/threads/{thread_id}/turns/1/files/output/../../threads.sqlite3").status_code == 404
        assert http.get(f"/api/threads/{thread_id}/turns/1/trace").status_code == 404
        refused = http.post("/chatkit", content=b"{}", headers={"Origin": "https://attacker.example"})
        assert refused.status_code == 403
        assert http.post("/chatkit", content=b"{}", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    with TestClient(app, base_url="http://rebound.example") as http:
        assert http.get("/api/config").status_code == 400


def test_trace_summary_groups_staging_and_flags_failed_commands():
    def span(span_id, name, start, duration, parent=None, **tags):
        return {"spanID": span_id, "operationName": name, "startTime": start, "duration": duration,
                "references": [{"refType": "CHILD_OF", "spanID": parent}] if parent else [],
                "tags": [{"key": key.replace("__", "."), "value": value} for key, value in tags.items()]}
    payload = {"data": [{"traceID": "abc", "spans": [
        span("root", "agents.workflow", 0, 10_000_000),
        span("c", "sandbox.create", 0, 50_000, "root"),
        span("s1", "sandbox.exec", 100_000, 1_000_000, "root", sandbox__exit_code=0),
        span("s2", "sandbox.exec", 1_100_000, 1_000_000, "root", sandbox__exit_code=0),
        span("r", "agents.response", 3_000_000, 2_000_000, "root", gen_ai__usage__input_tokens=90, gen_ai__usage__output_tokens=10, gen_ai__usage__total_tokens=100),
        span("t", "tool.exec_command", 5_100_000, 20_000, "root"),
        span("te", "sandbox.exec", 5_100_000, 20_000, "t", error=True, sandbox__exit_code=127),
        span("v", "document_intelligence.verify", 9_000_000, 1_000, "root"),
        span("p", "sandbox.suspend", 9_100_000, 100_000, "root"),
    ]}]}
    summary = summarize(payload)
    labels = [row["label"] for row in summary["rows"]]
    assert labels == ["sandbox.create", "workspace setup · 2 × sandbox.exec", "agents.response", "tool.exec_command",
                      "document_intelligence.verify", "sandbox.suspend"]
    failed = next(row for row in summary["rows"] if row["label"] == "tool.exec_command")
    assert failed["badge"] == "exit 127" and failed["kind"] == "error"
    assert summary["model"]["total_tokens"] == 100 and summary["tools"] == {"calls": 1, "failed": 1, "ms": 20.0}


def test_untrusted_text_is_fenced_and_tool_output_is_trimmed():
    assert fenced("```\nnot markdown") == "````\n```\nnot markdown\n````"
    status, text = describe_output("Chunk ID: x\nProcess exited with code 127\nOutput:\nsh: rg: not found")
    assert status == "exit 127" and text == "sh: rg: not found"


def upload_chat(server, tmp_path, model):
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    store = SQLiteStore(tmp_path / "threads.sqlite3")
    chat = DocumentChatServer(store, client, model=model, template="document-intelligence", output_dir=tmp_path / "threads",
                              workspace_root=str(server.root), model_label="scripted")
    return chat, create_app(chat, config={"model": "scripted", "prompts": []}, port=8000, jaeger_url=None)


async def test_uploads_reach_the_actor_workspace_and_their_quotes_are_checked(server, tmp_path):
    model = ShellSteps(["cat inputs/uploads/gamma-notes.md inputs/uploads/MANIFEST.json",
                        citations("Gamma revenue was USD 12.5 billion in fiscal 2025.")], answer="Gamma revenue: USD 12.5 billion.")
    chat, app = upload_chat(server, tmp_path, model)
    with TestClient(app, base_url="http://localhost:8000") as http:
        attachment = upload(http, "gamma notes.md", NOTES)
        assert not (tmp_path / "threads/.staged" / attachment).with_suffix(".part").exists()
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("What was Gamma's revenue?", [attachment])}})
        assert not [event for event in stream if event["type"] == "error"]
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        assert "Copied 1 upload to inputs/uploads/" in task_titles(stream)
        assert "Gamma revenue was USD 12.5 billion" in model.outputs[0] and '"sha256"' in model.outputs[0]
        state = http.get(f"/api/threads/{thread_id}").json()
        assert state["sources"] == "uploads" and [item["name"] for item in state["uploads"]] == ["gamma-notes.md"]
        turn = state["turns"][0]
        assert turn["check"] == {"status": "citations_passed", "count": 1, "visual": 0, "documents": ["gamma-notes.md"],
                                 "citations": [], "unchecked": [], "scope": "citations"}
        assert "1 quote matches the source documents" in json.dumps(stream)
        # Only the upload was staged: an uploads thread starts without the sample reports.
        assert (server.root / "inputs/uploads/gamma-notes.md").read_bytes() == NOTES
        assert not (server.root / "inputs/acme-2024.md").exists()
        assert not (tmp_path / "threads/.staged" / attachment).exists()
        assert http.get(f"/api/threads/{thread_id}/turns/1/files/inputs/uploads/gamma-notes.md").content == NOTES

        assert http.post("/chatkit", content=json.dumps({"type": "threads.delete", "params": {"thread_id": thread_id}})).status_code == 200
        assert not (tmp_path / "threads" / thread_id).exists()
        assert server.control.actors == {}


async def test_a_quote_missing_from_the_upload_fails_the_check(server, tmp_path):
    model = ShellSteps([citations("Gamma revenue was USD 99 billion in fiscal 2025.")])
    chat, app = upload_chat(server, tmp_path, model)
    with TestClient(app, base_url="http://localhost:8000") as http:
        attachment = upload(http, "gamma-notes.md", NOTES)
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("Revenue?", [attachment])}})
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        check = http.get(f"/api/threads/{thread_id}").json()["turns"][0]["check"]
        assert check["status"] == "failed" and "Not found in gamma-notes.md" in check["detail"]
        assert "Host check failed" in json.dumps(stream)


async def test_uploads_are_validated_before_they_reach_a_workspace(server, tmp_path):
    chat, app = upload_chat(server, tmp_path, ShellSteps([]))
    assert upload_name("../../etc/Q3 report (final).MD") == "Q3-report-final.md"
    assert upload_name("Annual Report 2025.PDF") == "Annual-Report-2025.pdf"
    with pytest.raises(UploadRejected):
        upload_name("macro.docm")
    with TestClient(app, base_url="http://localhost:8000") as http:
        rejected = http.post("/chatkit", content=json.dumps({"type": "attachments.create", "params": {
            "name": "big.pdf", "size": 11 * 1024 * 1024, "mime_type": "application/pdf"}}))
        assert rejected.status_code == 400 and "10 MiB" in rejected.json()["error"]
        assert http.post("/chatkit", content=json.dumps({"type": "attachments.create", "params": {
            "name": "macro.docm", "size": 10, "mime_type": "application/octet-stream"}})).status_code == 400
        fake = post_chatkit(http, {"type": "attachments.create", "params": {"name": "fake.pdf", "size": 9, "mime_type": "application/pdf"}})
        assert http.put("/uploads/" + fake["id"], content=b"not a pdf").status_code == 400
        real = post_chatkit(http, {"type": "attachments.create", "params": {"name": "real.pdf", "size": 9, "mime_type": "application/pdf"}})
        assert http.put("/uploads/" + real["id"], content=b"%PDF-1.4\n").status_code == 200
        attachment = post_chatkit(http, {"type": "attachments.create", "params": {"name": "a.md", "size": 2, "mime_type": "text/markdown"}})
        url = "/uploads/" + attachment["id"]
        assert http.put(url, content=b"\xff\xfe").status_code == 400
        assert http.put(url, content=b"ok", headers={"Origin": "https://attacker.example"}).status_code == 403
        assert http.put(url, files={"file": ("a.md", b"ok", "text/markdown")}).status_code == 200
        assert http.put(url, content=b"again").status_code == 400
        assert http.put("/uploads/../../threads.sqlite3", content=b"x").status_code == 404


def created_thread(stream):
    assert not [event for event in stream if event["type"] == "error"]
    return next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")


async def add_message(chat, thread_id, text, attachments=()):
    stream = await events(chat, {"type": "threads.add_user_message", "params": {
        "thread_id": thread_id, "input": message(text, attachments)}})
    assert not [event for event in stream if event["type"] == "error"]
    return stream


@pytest.mark.parametrize("reverse", [False, True])
async def test_exact_upload_filename_wins_over_another_files_stem(server, tmp_path, reverse):
    chat, app = upload_chat(server, tmp_path, ShellSteps([citations("Revenue was 99.", "report.md")]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            inputs = [("report.md", b"Revenue was 12."), ("report.md.txt", b"Revenue was 99.")]
            attachments = [upload(http, name, data) for name, data in (inputs[::-1] if reverse else inputs)]
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {"input": message("Revenue?", attachments)}}))
            check = http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]
            assert check["status"] == "failed" and "Not found in report.md" in check["detail"]
            chat.model = ShellSteps([citations("Revenue was 12.", "report.md")])
            await add_message(chat, tid, "Cite the first file correctly")
            assert http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]["status"] == "citations_passed"
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


async def test_ambiguous_stems_need_a_filename_or_full_path(server, tmp_path):
    chat, app = upload_chat(server, tmp_path, ShellSteps([citations("Revenue was 12.", "report")]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            attachments = [upload(http, "report.md", b"Revenue was 12."), upload(http, "report.txt", b"Revenue was 99.")]
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {"input": message("Revenue?", attachments)}}))
            check = http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]
            assert check["status"] == "failed" and "ambiguous" in check["detail"]
            chat.model = ShellSteps([citations("Revenue was 12.", "inputs/uploads/report.md")])
            await add_message(chat, tid, "Use an unambiguous source path")
            assert http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]["status"] == "citations_passed"
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


@pytest.mark.parametrize("fail_first", [False, True])
async def test_upload_retry_restores_host_copy_after_server_restart(server, tmp_path, monkeypatch, fail_first):
    quote = "Gamma revenue was USD 12.5 billion in fiscal 2025."
    chat, app = upload_chat(server, tmp_path, ShellSteps([citations(quote)]))
    with TestClient(app, base_url="http://localhost:8000") as http:
        attachment = upload(http, "gamma-notes.md", NOTES)
        with monkeypatch.context() as patcher:
            if fail_first:
                def fail(*args, **kwargs):
                    raise RuntimeError("simulated model failure after staging")
                patcher.setattr(Runner, "run_streamed", fail)
            stream = await events(chat, {"type": "threads.create", "params": {"input": message("Revenue?", [attachment])}})
        tid = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        assert bool([event for event in stream if event["type"] == "error"]) is fail_first
        assert not chat.uploads.path(attachment).exists()
        items = await chat.store.load_thread_items(tid, None, 100, "asc", {})
        item_id = next(item.id for item in items.data if isinstance(item, UserMessageItem))
    chat.store.close()
    # Simulate an untrusted edit: a retry must restore the trusted original bytes.
    (server.root / "inputs/uploads/gamma-notes.md").write_bytes(b"tampered inside the Actor")
    chat, app = upload_chat(server, tmp_path, ShellSteps([citations(quote)]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            retried = await events(chat, {"type": "threads.retry_after_item", "params": {"thread_id": tid, "item_id": item_id}})
            assert not [event for event in retried if event["type"] == "error"]
            state = http.get(f"/api/threads/{tid}").json()
            assert state["turns"][-1]["status"] == "completed"
            assert state["turns"][-1]["check"]["status"] == "citations_passed"
            assert len(state["uploads"]) == 1 and state["uploads"][0]["attachment_id"] == attachment
            assert state["turns"][-1]["uploads"] == ["gamma-notes.md"]
            assert (server.root / "inputs/uploads/gamma-notes.md").read_bytes() == NOTES
            assert not (server.root / "inputs/uploads/gamma-notes-2.md").exists()
            assert http.get(f"/api/threads/{tid}/turns/2/files/inputs/uploads/gamma-notes.md").content == NOTES
            assert [kind for kind, _ in server.control.events].count("create") == 1
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


@pytest.mark.parametrize("valid", [False, True])
async def test_uploads_in_sample_started_threads_are_quote_checked(server, tmp_path, valid):
    chat, app = upload_chat(server, tmp_path, ShellSteps([]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {"input": message("Hello")}}))
            quote = "Gamma revenue was USD 12.5 billion in fiscal 2025." if valid else "A fabricated quote."
            chat.model = ShellSteps([citations(quote)])
            attachment = upload(http, "gamma-notes.md", NOTES)
            await add_message(chat, tid, "Analyze the uploaded file", [attachment])
            state = http.get(f"/api/threads/{tid}").json()
            assert state["sources"] == "samples"  # The initial workspace stays intact.
            check = state["turns"][-1]["check"]
            assert check["status"] == ("citations_passed" if valid else "failed")
            assert check["scope"] == "citations"
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


@pytest.mark.parametrize("valid_sample", [False, True])
async def test_mixed_reports_validate_both_sample_and_uploaded_quotes(server, tmp_path, valid_sample):
    chat, app = upload_chat(server, tmp_path, ShellSteps([]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {"input": message("Hello")}}))
            sample = "Operating income was USD 21.6 billion in fiscal 2024." if valid_sample else "Invented sample quote."
            payload = json.dumps({"citations": [
                {"document_id": "acme-2024", "quote": sample},
                {"document_id": "gamma-notes.md", "quote": "Headcount grew to 4,100."}]})
            chat.model = ShellSteps([f"printf %s {shlex.quote(payload)} > output/result.json"])
            attachment = upload(http, "gamma-notes.md", NOTES)
            await add_message(chat, tid, "Compare the sources", [attachment])
            check = http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]
            assert check["status"] == ("citations_passed" if valid_sample else "failed")
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


async def test_sample_contract_checks_remain_strict_after_an_upload(server, tmp_path):
    chat, app = upload_chat(server, tmp_path, StreamingScripted())
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {"input": message(QUESTION)}}))
            attachment = upload(http, "gamma-notes.md", NOTES)
            chat.model = ShellSteps(["true"])
            await add_message(chat, tid, "Keep the sample outputs unchanged", [attachment])
            assert http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]["status"] == "passed"
            code = "import json; from pathlib import Path; p=Path('output/result.json'); r=json.loads(p.read_text()); r['rows'][0]['operating_margin_pct']='99.99'; p.write_text(json.dumps(r))"
            chat.model = ShellSteps([f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"])
            await add_message(chat, tid, "Recheck the sample calculation")
            assert http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]["status"] == "failed"
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


@pytest.mark.parametrize("damage", ["missing", "changed"])
async def test_retry_rejects_an_unavailable_or_changed_host_copy(server, tmp_path, damage):
    chat, app = upload_chat(server, tmp_path, ShellSteps([]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            attachment = upload(http, "gamma-notes.md", NOTES)
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {
                "input": message("Keep this file", [attachment])}}))
            items = await chat.store.load_thread_items(tid, None, 100, "asc", {})
            item_id = next(item.id for item in items.data if isinstance(item, UserMessageItem))
            host_copy = tmp_path / "threads" / tid / "turn-1/inputs/uploads/gamma-notes.md"
            if damage == "missing":
                host_copy.unlink()
            else:
                host_copy.write_bytes(b"modified on the host")
            stream = await events(chat, {"type": "threads.retry_after_item", "params": {
                "thread_id": tid, "item_id": item_id}})
            errors = [event for event in stream if event["type"] == "error"]
            assert errors and "attach the file again" in json.dumps(errors)
            assert (server.root / "inputs/uploads/gamma-notes.md").read_bytes() == NOTES
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()


async def test_mixed_sources_with_the_same_filename_require_full_paths(server, tmp_path):
    chat, app = upload_chat(server, tmp_path, ShellSteps([]))
    try:
        with TestClient(app, base_url="http://localhost:8000") as http:
            tid = created_thread(await events(chat, {"type": "threads.create", "params": {"input": message("Hello")}}))
            attachment = upload(http, "acme-2024.md", NOTES)
            chat.model = ShellSteps([citations("Headcount grew to 4,100.", "acme-2024.md")])
            await add_message(chat, tid, "Cite the upload", [attachment])
            check = http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]
            assert check["status"] == "failed" and "ambiguous" in check["detail"]
            payload = json.dumps({"citations": [
                {"document_id": "inputs/acme-2024.md", "quote": "Operating income was USD 21.6 billion in fiscal 2024."},
                {"document_id": "inputs/uploads/acme-2024.md", "quote": "Headcount grew to 4,100."}]})
            chat.model = ShellSteps([f"printf %s {shlex.quote(payload)} > output/result.json"])
            await add_message(chat, tid, "Cite each full source path")
            check = http.get(f"/api/threads/{tid}").json()["turns"][-1]["check"]
            assert check["status"] == "citations_passed" and check["count"] == 2
            await chat.store.delete_thread(tid, {})
    finally:
        chat.store.close()
