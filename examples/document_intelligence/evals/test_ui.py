# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import sys
import uuid

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

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
from examples.document_intelligence.ui.server import DocumentChatServer, describe_output, fenced  # noqa: E402
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
        response = await self._pick(input).get_response(system_instructions, input, *args, **kwargs)
        sequence = 0
        for index, item in enumerate(response.output):
            yield ResponseOutputItemAddedEvent.model_construct(type="response.output_item.added", item=item,
                                                               output_index=index, sequence_number=sequence)
            yield ResponseOutputItemDoneEvent.model_construct(type="response.output_item.done", item=item,
                                                              output_index=index, sequence_number=sequence + 1)
            sequence += 2
        yield ResponseCompletedEvent.model_construct(type="response.completed", sequence_number=sequence, response=Response.model_construct(
            id=f"resp_{uuid.uuid4().hex}", output=list(response.output), usage=None, object="response", model="scripted"))


def message(text):
    return {"content": [{"type": "input_text", "text": text}], "attachments": [], "inference_options": {}}


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
