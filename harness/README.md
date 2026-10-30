# Shared agent harness

The trusted side of [harness separate from compute](../docs/agent-harness.md):
it runs an agent's loop with the OpenAI Agents SDK and gives each conversation
its own Agent Substrate sandbox for tool execution. Agent examples supply a
`SandboxAgent` and an `AgentSpec`, and keep their own UI. The harness has no page.

It lives at the repository root beside `deploy/`: reusable code for this
repository's agents, not part of the installed library. It listens on loopback
only, has no sign-in, and keeps conversations in SQLite; the
[design](../docs/agent-harness.md) lists what it needs before it could run in the
cluster or ship as an optional part of the library.

| Module | Role |
| --- | --- |
| `spec.py` | `AgentSpec` (what an agent adds beside its `SandboxAgent`), `Conversation`, `TurnContext`, upload policy |
| `chat.py` | `HarnessChatServer`: the ChatKit server and conversation runtime |
| `uploads.py`, `steps.py` | ChatKit's two-phase uploads; tool calls as workflow steps |
| `store.py`, `traces.py` | ChatKit's SQLite store; the Jaeger trace summary for a turn |
| `http.py` | Routes an agent UI composes: ChatKit, uploads, thread state, recorded files, traces |
| `sandbox.py` | A sandbox profile (image and size) built and registered as an ActorTemplate |
| `settings.py` | `OPENAI_*` settings for UI launchers |

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

Then give the agent a sandbox profile and a UI: a launcher that builds
`HarnessChatServer(SPEC, ...)`, an `app.py` that composes `http.chat_routes`, and
a page. See [Document Intelligence](../examples/document_intelligence/README.md) and the
[SRE agent](../examples/sre/README.md).

## Tests

`uv run --group ui pytest harness/tests` covers two messages sent to
one conversation at once, waiting for and timing out on a full worker pool,
history after a failed turn, and evidence from harness tools. They run against
the local protocol simulator, or the upstream execution server when
`SUBSTRATE_DEMO_BINARY` is set.
