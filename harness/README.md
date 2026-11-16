# Shared agent harness

The example harness runs the OpenAI Agents SDK loop, owns model credentials
and conversation history, and keeps one Agent Substrate Actor per conversation.
The provider handles Actor lifecycle and routes shell and filesystem operations
to the Actor. An application with its own SDK runner can integrate the provider
directly. [SDK integration](../docs/usage.md#integrate-into-your-application) · [Architecture](../docs/architecture.md)

The harness is the `openai-harness` workspace package, imported as
`openai_harness`. Install it from the repository root with
`uv sync --locked --group ui`. The example web launchers bind to loopback and
use local SQLite stores without sign-in.

## A turn

1. **Claim the thread.** The terminal and web launchers use the same ChatKit
   server implementation. They share conversations when they use the same data
   directory. A per-thread file lock beside the SQLite database coordinates
   processes on the same host. The claim covers ChatKit's initial load and
   writes through its final save. A second request in the same process waits
   for the claim. Another process reports the thread as busy before saving a
   message. The OS releases the claim if a process exits.
2. **Create or resume the Actor.** The harness saves the intended Actor identity
   and template before requesting creation, so a retry after a lost response
   can recover the same Actor. It records workspace initialization separately.
   Before running tools in a new workspace, it verifies that the harness's
   OpenAI API key and Secret mount are absent. When Substrate reports no worker
   capacity, the harness shows a waiting step and retries with backoff for up
   to five minutes by default.
3. **Stage uploads and run the agent.** The host keeps copies of uploads and runs
   the configured upload processors inside the Actor. The model runs with its
   own SDK `SQLiteSession`, separate from the ChatKit thread and item store.
   The harness streams SDK tools and progress to ChatKit. A credentialed SDK
   function tool runs in the harness. It can call `await turn.record(label, text)`
   to save a host copy for checks and place a matching file under `evidence/`
   for the Actor to analyze.
4. **Collect and check outputs.** The harness copies configured workspace
   folders to the host and passes the files to the agent's check. The SRE
   agent compares findings against its host copies of cluster tool output.
5. **Suspend and save.** On turn exit the provider requests synchronous
   suspension. Once it succeeds, the harness saves the serialized Actor state
   with the thread. The next turn resumes the same workspace.

The harness records the length of SDK history before running the turn. On
failure it truncates partial SDK history. After a process exits mid-turn, the
next turn uses the persisted length to repair the history and marks a previous
running turn as stopped. Files already written to the Actor can still be
present on resume.

While a turn runs, the eviction watcher polls the Actor for worker drain
notices. A new notice interrupts the turn even if the browser is slow to
consume the stream. The harness persists the pending notice before requesting
the synchronous checkpoint, then saves the handled notice after suspension.
If only the pending notice was saved, the provider retries suspension before
reopening the Actor. [Provider eviction contract](../docs/usage.md#use-one-actor-per-conversation) · [Kind eviction check](../docs/local-kubernetes.md#test-worker-eviction)

Deletion uses the same thread claim. The harness marks deletion as pending,
deletes the Actor and its workspace, clears SDK history, and removes the host
copies. The store removes the ChatKit thread and items after cleanup succeeds.

## Component map

| Code | Responsibility |
| --- | --- |
| [`spec.py`](src/openai_harness/spec.py) | `AgentSpec`, `Conversation`, `TurnContext`, and upload policy |
| [`chat.py`](src/openai_harness/chat.py) | `HarnessChatServer`, SDK loop, history recovery, Actor session ownership, and tool evidence |
| [`store.py`](src/openai_harness/store.py) | SQLite ChatKit store and thread claims |
| [`uploads.py`](src/openai_harness/uploads.py), [`steps.py`](src/openai_harness/steps.py) | Staging uploads and rendering SDK tool calls as ChatKit workflow steps |
| [`http.py`](src/openai_harness/http.py), [`static/`](src/openai_harness/static) | Shared web page, routes, and UI assets |
| [`terminal.py`](src/openai_harness/terminal.py), [`settings.py`](src/openai_harness/settings.py) | Terminal front end and model settings |
| [`capacity.py`](src/openai_harness/capacity.py), [`traces.py`](src/openai_harness/traces.py) | Worker availability and turn trace summaries |
| [`deploy/kubernetes/sandbox.py`](../deploy/kubernetes/sandbox.py) | Build the example sandbox image and register its ActorTemplate at deploy time |

The harness depends on the provider in [`sandbox/`](../sandbox/README.md). The
provider package can be installed by applications that use their own front
end, store, or history. Example launchers and agent-specific checks live in
`examples/`.

## The UI

Each example's `ui/app.py` supplies the page settings for
`http.ui_application`, including the title, composer text, and suggested
prompts. Pass `starting_files` as paths mapped to bytes to expose initial
workspace files read-only. For custom file rendering, supply an agent static
directory with `agent.js` exporting `renderFile(context)` and optional
`agent.css`. Document Intelligence uses this hook to render PDF pages.
[`ui_application`](src/openai_harness/http.py) · [Document Intelligence UI](../examples/document_intelligence/ui/README.md)

The terminal front end runs the ChatKit server in-process. It can link to the
browser's `?thread=...` view of the same conversation. [Terminal implementation](src/openai_harness/terminal.py)

## Adding an agent

Copy Starter and supply a plain SDK `SandboxAgent` plus an `AgentSpec`.
The [Starter spec](../examples/starter/agent/spec.py) shows the required
`name`, `build_agent(manifest, model)`, and
`start(conversation, has_uploads, root)` fields. Optional hooks set per-turn
instructions, trusted tools, upload policy, the default question for
attachment-only messages, and host output checks. The default collection
folders are `output` and `work`.

Register an ActorTemplate for the agent's sandbox image and resource profile,
then wire `HarnessChatServer` into its terminal or web launcher. Use Starter
for the smallest working example, Document Intelligence for upload processors
and citation checks, and SRE for credentialed harness tools and host evidence.
[Starter](../examples/starter/README.md) · [Document Intelligence](../examples/document_intelligence/README.md) · [SRE](../examples/sre/README.md)

## Tests

From the repository root, run the harness suite.

```sh
uv run --locked --group ui pytest -q harness/tests
```

The suite covers thread claims across processes, capacity waits, SDK history
recovery, eviction, parallel tool evidence, and the shared HTTP routes. Tests
use the local protocol simulator or the upstream execution server when
`SUBSTRATE_DEMO_BINARY` is set. Examples can reuse the scripted streaming model
and ChatKit request helpers in [`testing/harness.py`](../testing/harness.py).
[Development checks](../docs/development.md)
