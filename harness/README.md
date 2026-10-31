# Shared agent harness

The trusted side of [harness separate from compute](../docs/agent-harness.md):
it runs an agent's loop with the OpenAI Agents SDK and gives each conversation
its own Agent Substrate sandbox for tool execution. Agent examples supply a
`SandboxAgent` and an `AgentSpec`. Their front ends are the harness's terminal
chat and its shared UI page, which each agent configures from its own `ui/`
folder.

It is its own package in the repository's uv workspace
(`openai-harness`, imported as `openai_harness`), depending on the
provider in `../sandbox/`. `uv sync --group ui` installs it. It is not published:
it listens on loopback only, has no sign-in, and keeps conversations in SQLite;
the [design](../docs/agent-harness.md) lists what it needs first.

| Module | Role |
| --- | --- |
| `spec.py` | `AgentSpec` (what an agent adds beside its `SandboxAgent`), `Conversation`, `TurnContext`, upload policy |
| `chat.py` | `HarnessChatServer`: the ChatKit server and conversation runtime |
| `uploads.py`, `steps.py` | ChatKit's two-phase uploads; tool calls as workflow steps |
| `store.py`, `traces.py` | ChatKit's SQLite store; the Jaeger trace summary for a turn |
| `http.py` | `ui_application`: the shared page and its routes (ChatKit, uploads, thread state, recorded and starting files, traces, capacity) |
| `static/` | The shared page: ChatKit, the side panel (files, checked quotes, the trace timeline), suggestions and the turn's result |
| `capacity.py` | Free sandbox slots for a template, from `kubectl ate get worker` |
| `terminal.py` | `TerminalChat`: a terminal front end on the same server and store as an agent's UI |
| `settings.py` | `OPENAI_*` settings for launchers |
| `testing.py` | A scripted streaming model and ChatKit request helpers for agents' tests |

An agent's sandbox image and size are deploy-time concerns:
`deploy/kubernetes/sandbox.py` builds and registers them.

## A turn

1. Take the conversation's lock, then reload the thread, so a message sent while
   another turn runs never overwrites that turn's record. The lock covers one
   process: a message for a conversation another process (a terminal chat or
   another UI) is running a turn of is refused, and not stored.
2. Create or resume the sandbox. While Substrate reports that no worker has
   room (`ActorCapacityError`), show "Waiting for a sandbox worker" and retry
   with backoff for up to five minutes.
3. On a new workspace, check that the sandbox holds no API key or Secret mount.
4. Copy uploads in from the host's retained copies and run the agent's upload
   processors. The UI opens the first upload as the sandbox extracted it while
   the model reads it.
5. Run the agent with its SDK session (`SQLiteSession`, one per conversation)
   and this turn's harness tools. A failed turn's items are removed from the
   session.
6. Copy the agent's `collect` folders to the host and run its check. The
   agent's `file_status` says which files the check covered, and the turn card
   always says what was checked, even when nothing was.
7. Suspend the sandbox.

Harness tools get a `TurnContext`. `turn.record(label, text)` saves a tool's
output as evidence: a host copy, which checks read, and
`evidence/NNN-label.txt` in the workspace, which code in the sandbox reads.
Each tool step says where it ran: `pod_logs · harness` or
`exec_command · exit 0 · sandbox`.

## The UI

Every agent's UI is the page in `static/`, served by
`http.ui_application(server, config=..., static=...)`. The agent's `ui/app.py`
holds what differs:

| `config` key | What it sets |
| --- | --- |
| `title`, `icon`, `new_label` | The header (`icon` is `document`, `pulse` or `spark`) |
| `greeting`, `placeholder` | ChatKit's start screen and composer |
| `prompts`, `follow_ups` | Suggestions before the first message and after each turn; they fill the composer and are never sent for you |
| `intro`, `starting_note` | The panel's text before any files, and above the starting files |
| `scope` | The header chip; defaults to the atespace and cluster |

`starting_files` (path to bytes) are shown before the first message and served
read-only. A `static/agent.js` module can export `renderFile(context)` to show
its own file types (Document Intelligence renders PDF pages with it), and
`static/agent.css` styles them. A launcher passes `capacity=WorkerCapacity(...)`
to show free sandbox slots in the header.

The page:

- **Panel on demand.** The chat is alone until a turn writes a file, then the
  panel opens on the host-checked output. Collapse it and it stays collapsed for
  that conversation, with a count of new files; below 900 px it is a sheet.
- **Files, Quotes, Trace.** Files are grouped by folder and marked checked or
  not. Quotes lists every quote the host check verified (`checked_quotes` in
  the check's result, with `quotes_from` naming the file they came from); each
  opens its source with the quote highlighted. Trace is the turn as a timeline:
  the workspace's lifecycle, what ran in the harness, and what ran in the sandbox.
- **The turn's result** stays under the chat (ChatKit's own scrolling cannot be
  controlled from the page): what was checked, the file to open, the quotes, the trace.
- **Links.** `?thread=thr_…` opens a conversation, and reloading reopens the
  last one; `TerminalChat(ui_url=...)` prints the link.
- **Delete** asks first, naming the Actor: it deletes the sandbox, its volume,
  the history and the host copies. ChatKit's own delete is turned off.

ChatKit publishes only an unversioned loader
(`cdn.platform.openai.com/deployments/chatkit/chatkit.js`), which points at a
hashed build and is cached for five minutes; there is no versioned build to pin.

## Adding an agent

Start by copying the [starter agent](../examples/starter/README.md), the
smallest complete agent on the harness. Its spec looks like this:

```python
from openai_harness.spec import AgentSpec

SPEC = AgentSpec(
    name="my_agent",
    build_agent=build_agent,                   # (manifest, model) -> SandboxAgent
    start=lambda conversation, has_uploads, root: Manifest(root=root, entries={"output": Dir()}),
    tools=lambda turn: [...],                  # optional harness tools for this turn
    check=lambda conversation, turn, files: {"status": "..."},
)
```

Then give the agent a sandbox profile and a UI: a Dockerfile registered through
`deploy/kubernetes/sandbox.py`, a launcher that builds `HarnessChatServer(SPEC, ...)`
with its `card` and `file_status`, and a `ui/app.py` with its `UI` settings for the
shared page. Its tests can use
`openai_harness.testing`: a scripted streaming model and ChatKit request helpers. See [Document Intelligence](../examples/document_intelligence/README.md) and the
[SRE agent](../examples/sre/README.md).

## Tests

`uv run --locked --group ui pytest -q harness/tests` covers two messages sent to
one conversation at once, waiting for and timing out on a full worker pool,
history after a failed turn, evidence from parallel harness tools, the shared
page's routes, free slots, and a conversation another process is running. They run against
the local protocol simulator, or the upstream execution server when
`SUBSTRATE_DEMO_BINARY` is set.
