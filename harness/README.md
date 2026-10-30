# Shared agent harness

The trusted side of [harness separate from compute](../docs/agent-harness.md):
it runs an agent's loop with the OpenAI Agents SDK and gives each conversation
its own Agent Substrate sandbox for tool execution. Agent examples supply a
`SandboxAgent` and an `AgentSpec`, and keep their own UI. The harness has no page.

It is its own package in the repository's uv workspace
(`openai-agent-substrate-harness`, imported as `harness`), depending on the
provider in `../sandbox/`. `uv sync --group ui` installs it. It is not published:
it listens on loopback only, has no sign-in, and keeps conversations in SQLite;
the [design](../docs/agent-harness.md) lists what it needs first.

| Module | Role |
| --- | --- |
| `spec.py` | `AgentSpec` (what an agent adds beside its `SandboxAgent`), `Conversation`, `TurnContext`, upload policy |
| `chat.py` | `HarnessChatServer`: the ChatKit server and conversation runtime |
| `uploads.py`, `steps.py` | ChatKit's two-phase uploads; tool calls as workflow steps |
| `store.py`, `traces.py` | ChatKit's SQLite store; the Jaeger trace summary for a turn |
| `http.py` | Routes an agent UI composes: ChatKit, uploads, thread state, recorded files, traces |
| `terminal.py` | `TerminalChat`: a terminal front end on the same server and store as an agent's UI |
| `settings.py` | `OPENAI_*` settings for launchers |
| `testing.py` | A scripted streaming model and ChatKit request helpers for agents' tests |

An agent's sandbox image and size are deploy-time concerns:
`deploy/kubernetes/sandbox.py` builds and registers them.

## A turn

1. Take the conversation's lock, then reload the thread, so a message sent while
   another turn runs never overwrites that turn's record.
2. Create or resume the sandbox. While Substrate reports that no worker has
   room (`ActorCapacityError`), show "Waiting for a sandbox worker" and retry
   with backoff for up to five minutes.
3. On a new workspace, check that the sandbox holds no API key or Secret mount.
4. Copy uploads in from the host's retained copies and run the agent's upload
   processors.
5. Run the agent with its SDK session (`SQLiteSession`, one per conversation)
   and this turn's harness tools. A failed turn's items are removed from the
   session.
6. Copy the agent's `collect` folders to the host and run its check.
7. Suspend the sandbox.

Harness tools get a `TurnContext`. `turn.record(label, text)` saves a tool's
output as evidence: a host copy, which checks read, and
`evidence/NNN-label.txt` in the workspace, which code in the sandbox reads.

## Adding an agent

Start by copying the [starter agent](../examples/starter/README.md), the
smallest complete agent on the harness. Its spec looks like this:

```python
from harness.spec import AgentSpec

SPEC = AgentSpec(
    name="my_agent",
    build_agent=build_agent,                   # (manifest, model) -> SandboxAgent
    start=lambda conversation, has_uploads, root: Manifest(root=root, entries={"output": Dir()}),
    tools=lambda turn: [...],                  # optional harness tools for this turn
    check=lambda conversation, turn, files: {"status": "..."},
)
```

Then give the agent a sandbox profile and a UI: a Dockerfile registered through
`deploy/kubernetes/sandbox.py`, a launcher that builds `HarnessChatServer(SPEC, ...)`,
an `app.py` that composes `http.chat_routes`, and a page. Its tests can use
`harness.testing`: a scripted streaming model and ChatKit request helpers. See [Document Intelligence](../examples/document_intelligence/README.md) and the
[SRE agent](../examples/sre/README.md).

## Tests

`uv run --locked --group ui pytest -q harness/tests` covers two messages sent to
one conversation at once, waiting for and timing out on a full worker pool,
history after a failed turn, and evidence from parallel harness tools. They run against
the local protocol simulator, or the upstream execution server when
`SUBSTRATE_DEMO_BINARY` is set.
