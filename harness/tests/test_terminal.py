# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from agents import function_tool  # noqa: E402
from agents.sandbox import Manifest, SandboxAgent  # noqa: E402
from agents.sandbox.capabilities import Shell  # noqa: E402
from agents.sandbox.entries import Dir  # noqa: E402

from openai_harness.chat import HarnessChatServer  # noqa: E402
from openai_harness.spec import AgentSpec  # noqa: E402
from openai_harness.store import SQLiteStore  # noqa: E402
from openai_harness.terminal import TerminalChat, clean  # noqa: E402
from openai_harness.testing import Calls, shell  # noqa: E402
from agent_substrate import SubstrateSandboxClient  # noqa: E402


def tools(turn):
    @function_tool
    async def read_logs(pod: str) -> str:
        """Read a pod's logs."""
        return f"Saved to {await turn.record(f'logs-{pod}', 'FATAL: DATABASE_URL is not set')}"
    return [read_logs]


SPEC = AgentSpec(
    name="terminal_test",
    build_agent=lambda manifest, model: SandboxAgent(name="Test", model=model, instructions="Answer.",
                                                     default_manifest=manifest, capabilities=[Shell()]),
    start=lambda conversation, has_uploads, root: Manifest(root=root, entries={"output": Dir(), "evidence": Dir()}),
    tools=tools,
    check=lambda conversation, turn, files: {"status": "checked"},
)


def card(conversation, turn, changed):
    return [{"type": "Row", "children": [{"type": "Badge", "color": "success", "label": "Checked on the host"}]}]


@pytest.fixture
def chat_server(server, tmp_path):
    def make(model):
        client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
        return HarnessChatServer(SPEC, SQLiteStore(tmp_path / "threads.sqlite3"), client, model=model, template="test",
                                 output_dir=tmp_path / "threads", workspace_root=str(server.root), card=card)
    return make


def terminal(server, inputs=()):
    written = []
    pending = list(inputs)

    async def read():
        return pending.pop(0) if pending else None
    return TerminalChat(server, title="Test agent", jaeger_url=None, read=read, write=written.append), written


async def test_one_question_streams_steps_tools_answer_and_files(chat_server):
    model = Calls([("read_logs", {"pod": "checkout"}), shell("cat evidence/001-logs-checkout.txt > output/notes.md")],
                  answer="checkout is missing DATABASE_URL.\x1b[2J")
    server = chat_server(model)
    chat, written = terminal(server)
    thread_id = await chat.run("Why is checkout down?")
    output = "".join(written)
    assert thread_id and f"conversation {thread_id}" in output
    assert "… Creating the workspace" in output and "· Created the workspace" in output
    assert "· read_logs" in output and "· exec_command · exit 0  cat evidence/001-logs-checkout.txt > output/notes.md" in output
    # The model's escape sequence reaches the terminal as plain text.
    assert "agent> checkout is missing DATABASE_URL.[2J\n" in output and "\x1b" not in output
    assert "✓ Checked on the host" in output and "1 in the harness" in output
    assert f"saved output/notes.md  →  {server.output_dir / thread_id / 'turn-1/output/notes.md'}" in output
    # The conversation is an ordinary thread in the agent's store, so its UI lists it.
    assert (await server.store.load_thread(thread_id, {})).metadata["turns"][0]["status"] == "completed"
    server.store.close()


async def test_an_interactive_session_continues_lists_and_switches_conversations(chat_server):
    server = chat_server(Calls(answer="Hello."))
    chat, written = terminal(server, ["Hi", "Again", "/files", "/list", "/open thr_missing", "/new", "/bogus", "/quit", "never read"])
    first = await chat.run()
    output = "".join(written)
    assert output.count("agent> Hello.") == 2 and output.count("\n  conversation thr_") == 1
    assert "Resumed the workspace" in output
    started = output.split("\n  conversation ", 1)[1].split("\n", 1)[0]
    assert "No files yet." in output and f"{started}  Hi  · 2 turns" in output
    assert "No conversation thr_missing" in output and "New conversation: your next message starts it." in output
    assert "Unknown command." in output and first is None  # /new cleared it before /quit
    server.store.close()


def test_terminal_control_sequences_are_removed_but_lines_are_kept():
    assert clean("a\x1b]0;title\x07b\tc\nd\x9be") == "a]0;titleb\tc\nde"
