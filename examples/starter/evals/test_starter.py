# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import sys

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from starlette.testclient import TestClient  # noqa: E402

from deploy.kubernetes.sandbox import build_template  # noqa: E402
from examples.starter.agent.check import check  # noqa: E402
from examples.starter.agent.spec import SPEC  # noqa: E402
from examples.starter.deploy.__main__ import PROFILE  # noqa: E402
from examples.starter.ui.app import create_app  # noqa: E402
from examples.starter.card import check_rows  # noqa: E402
from openai_harness.chat import HarnessChatServer  # noqa: E402
from openai_harness.store import SQLiteStore  # noqa: E402
from openai_harness.testing import Calls, events, message, shell  # noqa: E402
from agent_substrate import SubstrateSandboxClient  # noqa: E402

PRIMES = "import csv; w = csv.writer(open('output/primes.csv', 'w')); w.writerow(['prime']); " \
         "[w.writerow([n]) for n in range(2, 30) if all(n % d for d in range(2, n))]"


def test_the_host_check_accepts_well_formed_outputs_only():
    assert check(None, {}, {"work/run.py": b"print(1)"}) == {"status": "no_output"}
    assert check(None, {}, {"output/a.json": b'{"x": 1}', "output/b.md": b"# Hi"})["status"] == "passed"
    for broken in ({"output/a.json": b"{not json"}, {"output/b.md": b"  \n"}, {"output/c.csv": b"\xff\xfe"}):
        result = check(None, {}, broken)
        assert result["status"] == "failed" and "is not well formed" in result["detail"]


def test_the_sandbox_profile_is_small_and_named_for_the_agent():
    template = build_template(PROFILE, "registry.example/starter@sha256:abc", "ate-demo-sandbox")
    assert template["metadata"]["name"].startswith("starter-")
    assert template["resources"] == {"limits": {"cpuMillicores": 500, "memoryBytes": 480 * 1024 * 1024}}


async def test_a_turn_runs_code_in_the_sandbox_and_the_ui_serves_its_files(server, tmp_path):
    model = Calls([shell(f'{sys.executable} -c "{PRIMES}"')], answer="Saved output/primes.csv.")
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    store = SQLiteStore(tmp_path / "threads.sqlite3")
    chat = HarnessChatServer(SPEC, store, client, model=model, template="starter", output_dir=tmp_path / "threads",
                             workspace_root=str(server.root), model_label="scripted", card=check_rows)
    app = create_app(chat, config={"model": "scripted", "prompts": []}, port=8002, jaeger_url=None)
    try:
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("Primes below 30?")}})
        assert not [event for event in stream if event["type"] == "error"], stream
        assert "Output files are well formed (host check)" in json.dumps(stream)
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        with TestClient(app, base_url="http://localhost:8002") as http:
            assert "Starter agent" in http.get("/").text
            state = http.get(f"/api/threads/{thread_id}").json()
            turn = state["turns"][0]
            assert turn["status"] == "completed" and turn["check"] == {"status": "passed", "files": ["output/primes.csv"]}
            primes = http.get(f"/api/threads/{thread_id}/turns/1/files/output/primes.csv").text
            assert primes.split() == ["prime", "2", "3", "5", "7", "11", "13", "17", "19", "23", "29"]
            assert http.post("/chatkit", content=b"{}", headers={"Origin": "https://attacker.example"}).status_code == 403
        await store.delete_thread(thread_id, {})
        assert server.control.actors == {}
    finally:
        store.close()


async def test_the_terminal_chat_and_the_ui_share_conversations(server, tmp_path):
    from openai_harness.terminal import TerminalChat

    model = Calls([shell(f'{sys.executable} -c "{PRIMES}"')], answer="Saved output/primes.csv.")
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    store = SQLiteStore(tmp_path / "threads.sqlite3")
    chat = HarnessChatServer(SPEC, store, client, model=model, template="starter", output_dir=tmp_path / "threads",
                             workspace_root=str(server.root), model_label="scripted", card=check_rows)
    written = []
    try:
        thread_id = await TerminalChat(chat, title="Starter agent", write=written.append).run("Primes below 30?")
        output = "".join(written)
        assert "agent> Saved output/primes.csv." in output and "✓ Output files are well formed (host check)" in output
        assert f"saved output/primes.csv  →  {tmp_path / 'threads' / thread_id / 'turn-1/output/primes.csv'}" in output
        app = create_app(chat, config={"model": "scripted", "prompts": []}, port=8002, jaeger_url=None)
        with TestClient(app, base_url="http://localhost:8002") as http:
            assert http.get(f"/api/threads/{thread_id}").json()["turns"][0]["check"]["status"] == "passed"
    finally:
        store.close()
