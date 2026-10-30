# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from datetime import datetime

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from agents import Runner, function_tool  # noqa: E402
from agents.sandbox import Manifest, SandboxAgent  # noqa: E402
from agents.sandbox.capabilities import Shell  # noqa: E402
from agents.sandbox.entries import Dir  # noqa: E402
from chatkit.types import ThreadMetadata  # noqa: E402

from openai_harness.chat import HarnessChatServer  # noqa: E402
from openai_harness.spec import AgentSpec, Conversation, TurnContext  # noqa: E402
from openai_harness.store import SQLiteStore  # noqa: E402
from openai_harness.testing import Calls, events, message, shell, titles  # noqa: E402
from openai_agent_substrate import ActorCapacityError, SubstrateSandboxClient, SubstrateSandboxOptions  # noqa: E402


def build_agent(manifest, model):
    return SandboxAgent(name="Test", model=model, instructions="Answer.", default_manifest=manifest, capabilities=[Shell()])


def start(conversation, has_uploads, root):
    return Manifest(root=root, entries={"output": Dir(), "work": Dir(), "evidence": Dir()})


def logs_tool(turn):
    @function_tool
    async def read_logs(pod: str) -> str:
        """Read a pod's logs."""
        path = await turn.record(f"logs-{pod}", f"FATAL: DATABASE_URL is not set ({pod})\n")
        return f"Saved to {path}"
    return [read_logs]


SPEC = AgentSpec(name="test_agent", build_agent=build_agent, start=start, tools=logs_tool,
                 check=lambda conversation, turn, files: {"status": "checked", "files": sorted(files)})


class FullThenRoom:
    """Substrate control whose pool is full for the first ``full`` resumes."""

    def __init__(self, control, full):
        self.control = control
        self.full = full

    def __getattr__(self, name):
        return getattr(self.control, name)

    async def resume(self, atespace, name):
        if self.full:
            self.full -= 1
            raise ActorCapacityError("Actor lifecycle command failed (exit 1): no worker has room for the actor")
        await self.control.resume(atespace, name)


def make_chat(server, tmp_path, model, *, control=None, **options):
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=control or server.control)
    store = SQLiteStore(tmp_path / "threads.sqlite3")
    return HarnessChatServer(SPEC, store, client, model=model, template="test", output_dir=tmp_path / "threads",
                             workspace_root=str(server.root), model_label="scripted", **options)


async def new_thread(chat, text="First question"):
    stream = await events(chat, {"type": "threads.create", "params": {"input": message(text)}})
    assert not [event for event in stream if event["type"] == "error"], stream
    return next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")


async def user_messages(chat, thread_id):
    history = chat.history(thread_id)
    try:
        return [item["content"] for item in await history.get_items() if item.get("role") == "user"]
    finally:
        history.close()


async def test_two_messages_sent_at_once_both_keep_their_turn_and_history(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls(delay=0.2))
    try:
        thread_id = await new_thread(chat)
        sent = [{"type": "threads.add_user_message", "params": {"thread_id": thread_id, "input": message(text)}}
                for text in ("Second question", "Third question")]
        streams = await asyncio.gather(*(events(chat, payload) for payload in sent))
        assert not [event for stream in streams for event in stream if event["type"] == "error"]
        thread = await chat.store.load_thread(thread_id, {})
        assert [turn["number"] for turn in thread.metadata["turns"]] == [1, 2, 3]
        assert all(turn["status"] == "completed" for turn in thread.metadata["turns"])
        assert not any("waited_s" in turn for turn in thread.metadata["turns"])  # only a full pool counts as waiting
        assert await user_messages(chat, thread_id) == ["First question", "Second question", "Third question"]
    finally:
        chat.store.close()


async def test_a_full_worker_pool_makes_the_turn_wait_then_run(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls(), control=FullThenRoom(server.control, full=2))
    try:
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("Question")}})
        assert not [event for event in stream if event["type"] == "error"]
        assert any(title.startswith("Waiting for a sandbox worker") for title in titles(stream))
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        turn = (await chat.store.load_thread(thread_id, {})).metadata["turns"][0]
        assert turn["status"] == "completed" and turn["waited_s"] >= 2
        assert "s for a worker" in json.dumps(stream)  # in the turn card's caption
    finally:
        chat.store.close()


async def test_a_pool_that_stays_full_fails_with_a_clear_message(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls(), control=FullThenRoom(server.control, full=99), capacity_wait_s=0)
    try:
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("Question")}})
        errors = [event for event in stream if event["type"] == "error"]
        assert errors and "No sandbox worker had room" in errors[0]["message"] and errors[0]["allow_retry"]
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        thread = await chat.store.load_thread(thread_id, {})
        # The Actor exists (created suspended), so retrying resumes it rather than creating another.
        assert thread.metadata["turns"][0]["status"] == "failed" and thread.metadata["actor"].startswith("test-space/")
    finally:
        chat.store.close()


async def test_a_failed_run_leaves_no_partial_turn_in_the_history(server, tmp_path, monkeypatch):
    chat = make_chat(server, tmp_path, Calls())
    try:
        thread_id = await new_thread(chat)
        original = Runner.run_streamed

        def failing(*args, **kwargs):
            result = original(*args, **kwargs)
            stream = result.stream_events

            async def broken():
                async for event in stream():
                    yield event
                raise RuntimeError("simulated failure after the model answered")
            result.stream_events = broken
            return result

        monkeypatch.setattr(Runner, "run_streamed", failing)
        stream = await events(chat, {"type": "threads.add_user_message", "params": {
            "thread_id": thread_id, "input": message("This turn fails")}})
        assert [event for event in stream if event["type"] == "error"]
        assert await user_messages(chat, thread_id) == ["First question"]
    finally:
        chat.store.close()


async def test_harness_tools_record_evidence_the_sandbox_can_read(server, tmp_path):
    model = Calls([("read_logs", {"pod": "checkout"}), shell("cat evidence/001-logs-checkout.txt")])
    chat = make_chat(server, tmp_path, model)
    try:
        thread_id = await new_thread(chat, "Why does checkout crash?")
        assert "FATAL: DATABASE_URL is not set (checkout)" in model.outputs[-1]
        thread = await chat.store.load_thread(thread_id, {})
        turn = thread.metadata["turns"][0]
        assert [tool["where"] for tool in turn["tools"]] == ["harness", "sandbox"]
        assert thread.metadata["evidence"][0]["path"] == "evidence/001-logs-checkout.txt"
        host = tmp_path / "threads" / thread_id / "turn-1/evidence/001-logs-checkout.txt"
        assert host.read_text() == "FATAL: DATABASE_URL is not set (checkout)\n"
        assert any(item.get("evidence") for item in turn["files"])
        assert turn["check"] == {"status": "checked", "files": []}
        await chat.store.delete_thread(thread_id, {})
        assert not (tmp_path / "threads" / thread_id).exists() and server.control.actors == {}
        assert await user_messages(chat, thread_id) == []
    finally:
        chat.store.close()


async def test_parallel_harness_tool_calls_get_distinct_evidence_files(server, tmp_path):

    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    session = await client.create(manifest=start(None, False, str(server.root)), options=SubstrateSandboxOptions(template="test"))
    async with session:
        thread = ThreadMetadata(id="thr", created_at=datetime.now(), metadata={})
        turn = {"number": 1, "files": []}
        context = TurnContext(Conversation(thread, tmp_path / "thr"), turn, session)
        paths = await asyncio.gather(*(context.record("same-label", f"output {index}\n") for index in range(3)))
    await client.delete(session)
    assert paths == ["evidence/001-same-label.txt", "evidence/002-same-label.txt", "evidence/003-same-label.txt"]
    assert [(tmp_path / "thr/turn-1" / path).read_text() for path in paths] == ["output 0\n", "output 1\n", "output 2\n"]
