# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from agents.sandbox import Manifest, SandboxAgent  # noqa: E402
from agents.sandbox.capabilities import Shell  # noqa: E402
from agents.sandbox.entries import Dir  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from openai_harness import http  # noqa: E402
from openai_harness.capacity import free_slots, usage  # noqa: E402
from openai_harness.chat import HarnessChatServer, preferred_file, running_elsewhere  # noqa: E402
from openai_harness.spec import AgentSpec  # noqa: E402
from openai_harness.store import SQLiteStore  # noqa: E402
from openai_harness.testing import Calls, events, message, shell  # noqa: E402
from agent_substrate import SubstrateSandboxClient  # noqa: E402

SPEC = AgentSpec(
    name="ui_test",
    build_agent=lambda manifest, model: SandboxAgent(name="Test", model=model, instructions="Answer.",
                                                     default_manifest=manifest, capabilities=[Shell()]),
    start=lambda conversation, has_uploads, root: Manifest(root=root, entries={"output": Dir(), "work": Dir()}),
)
STARTING = {"inputs/notes.md": b"# Notes\n\nRevenue was 4.2.\n"}


def make_chat(server, tmp_path, model, **options):
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    return HarnessChatServer(SPEC, SQLiteStore(tmp_path / "threads.sqlite3"), client, model=model, template="test",
                             output_dir=tmp_path / "threads", workspace_root=str(server.root), **options)


def app_for(chat, capacity=None, **config):
    return http.ui_application(chat, config={"title": "<Test> agent", "model": "scripted", **config}, port=8009,
                               jaeger_url=None, starting_files=STARTING, capacity=capacity)


def test_the_shared_page_is_configured_by_the_agent_and_serves_only_its_starting_files(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls())

    async def capacity():
        return {"workers": 1, "slots": 4, "free": 3}

    with TestClient(app_for(chat, capacity=capacity, follow_ups=[{"label": "Next", "prompt": "Go on."}]),
                    base_url="http://localhost:8009") as client:
        page = client.get("/")
        assert page.status_code == 200 and "<title>&lt;Test&gt; agent</title>" in page.text and "<Test>" not in page.text
        assert client.get("/harness/app.js").status_code == 200 and client.get("/harness/trace.js").status_code == 200
        assert client.get("/favicon.ico").headers["content-type"].startswith("image/svg+xml")
        config = client.get("/api/config").json()
        assert config["name"] == "ui_test" and config["uploads"] is None and config["capacity"] is True
        assert config["starting_files"] == [{"path": "inputs/notes.md", "bytes": len(STARTING["inputs/notes.md"])}]
        assert config["follow_ups"][0]["prompt"] == "Go on." and "extension" not in config
        assert client.get("/api/starting-files/inputs/notes.md").content == STARTING["inputs/notes.md"]
        for path in ("inputs/other.md", "inputs/../threads.sqlite3", "notes.md"):
            assert client.get(f"/api/starting-files/{path}").status_code == 404
        assert client.get("/api/capacity").json() == {"status": "ok", "workers": 1, "slots": 4, "free": 3}
        chat.store.close()


def test_capacity_is_hidden_when_the_cluster_cannot_be_read(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls())

    async def broken():
        raise RuntimeError("kubectl ate get worker failed (exit 1)")

    with TestClient(app_for(chat, capacity=broken), base_url="http://localhost:8009") as client:
        assert client.get("/api/capacity").status_code == 503
    with TestClient(app_for(chat), base_url="http://localhost:8009") as client:
        assert client.get("/api/capacity").status_code == 404 and client.get("/api/config").json()["capacity"] is False
    chat.store.close()


def test_free_slots_count_the_templates_size_on_active_selected_workers():
    table = ("NAME  POOL  STATE  ACTORS  CPU(MILLICORES)  MEMORY  POD  AGE\n"
             "w1  pool  WORKER_STATE_ACTIVE  1/1000  500/2000  480Mi/1984Mi  ns/pod-a  1h\n"
             "w2  pool  WORKER_STATE_ACTIVE  3/1000  1500/2000  1440Mi/1984Mi  ns/pod-b  1h\n")
    assert usage(table) == {"w1": (500, 480 * 1024 ** 2), "w2": (1500, 1440 * 1024 ** 2)}

    def worker(name, state="WORKER_STATE_ACTIVE", labels=None):
        return {"metadata": {"name": name}, "labels": labels or {"workload": "sandbox"},
                "status": {"state": state, "capacity": {"cpuMillicores": "2000", "memoryBytes": str(1984 * 1024 ** 2)}}}

    template = {"resources": {"limits": {"cpuMillicores": 500, "memoryBytes": 480 * 1024 ** 2}},
                "workerSelector": {"matchLabels": {"workload": "sandbox"}}}
    workers = [worker("w1"), worker("w2"), worker("w3", state="WORKER_STATE_DRAINING"), worker("w4", labels={"workload": "other"})]
    assert free_slots(workers, usage(table), template) == {"workers": 2, "slots": 8, "free": 4}


def test_a_turn_running_in_another_process_blocks_a_new_one_until_it_is_stale():
    now = datetime.now(timezone.utc)
    running = {"turns": [{"number": 1, "status": "running", "started_at": now.isoformat()}]}
    assert running_elsewhere(running, set(), "thr_1")
    # Not elsewhere when this process runs it, when it finished, or when it was left running long ago (a crash).
    assert not running_elsewhere(running, {"thr_1"}, "thr_1")
    assert not running_elsewhere({"turns": [{"status": "completed", "started_at": now.isoformat()}]}, set(), "thr_1")
    stale = (now - timedelta(minutes=20)).isoformat()
    assert not running_elsewhere({"turns": [{"status": "running", "started_at": stale}]}, set(), "thr_1")


async def test_a_message_is_refused_while_another_process_runs_the_conversation(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls(answer="Hi."))
    first = await events(chat, {"type": "threads.create", "params": {"input": message("Hello")}})
    thread_id = next(event["thread"]["id"] for event in first if event["type"] == "thread.created")
    thread = await chat.store.load_thread(thread_id, {})
    # Another process (a terminal chat) saved a turn it is still running.
    thread.metadata["turns"].append({"number": 2, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                                     "files": [], "tools": []})
    await chat.store.save_thread(thread, {})
    with TestClient(app_for(chat), base_url="http://localhost:8009") as client:
        assert client.get(f"/api/threads/{thread_id}").json()["state"] == "elsewhere"
    stream = await events(chat, {"type": "threads.add_user_message", "params": {"thread_id": thread_id, "input": message("Again")}})
    errors = [event for event in stream if event["type"] == "error"]
    assert errors and "running in another window or terminal" in errors[0]["message"]
    assert len((await chat.store.load_thread(thread_id, {})).metadata["turns"]) == 2
    # The refused message is not left in the conversation without a reply.
    items = (await chat.store.load_thread_items(thread_id, None, 50, "asc", {})).data
    texts = [part.text for item in items if item.type == "user_message" for part in item.content]
    assert texts == ["Hello"]
    chat.store.close()


async def test_steps_say_where_they_ran_and_every_turn_says_what_was_checked(server, tmp_path):
    chat = make_chat(server, tmp_path, Calls([shell("echo hi > output/a.txt")], answer="Done."))
    stream = await events(chat, {"type": "threads.create", "params": {"input": message("Write a file")}})
    workflows = [event["item"]["workflow"] for event in stream
                 if event["type"] == "thread.item.added" and event["item"]["type"] == "workflow"]
    assert workflows and workflows[0]["expanded"] is True  # open while the agent works
    tasks = [event["update"]["task"] for event in stream if (event.get("update") or {}).get("type") == "workflow.task.updated"]
    done = [task for task in tasks if task["title"].startswith("exec_command")]
    assert done[-1]["title"] == "exec_command · exit 0 · sandbox" and done[-1]["icon"] == "cube"
    card = json.dumps([event for event in stream if event["type"] == "thread.item.done" and event["item"]["type"] == "widget"])
    # The spec has no check: the card still says so.
    assert "Not checked on the host" in card
    thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
    turn = (await chat.store.load_thread(thread_id, {})).metadata["turns"][0]
    assert turn["start_s"] >= 0
    effects = [event for event in stream if event["type"] == "client_effect"]
    assert effects[-1]["data"]["path"] == "output/a.txt"
    chat.store.close()


def test_the_panel_opens_a_checked_output_before_the_agents_report():
    turn = {"files": [{"path": "output/report.md", "changed": True}, {"path": "output/data.json", "changed": True},
                      {"path": "work/x.py", "changed": True}],
            "file_status": {"output/data.json": {"state": "checked"}, "output/report.md": {"state": "unchecked"}}}
    assert preferred_file(turn, "output/report.md") == "output/data.json"
    turn["file_status"] = {}
    assert preferred_file(turn, "output/report.md") == "output/report.md"
    assert preferred_file({"files": [{"path": "work/x.py", "changed": True}]}) is None
