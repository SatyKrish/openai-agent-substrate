# Document Intelligence UI

A ChatKit conversation beside an artifact panel, for testing multi-turn
analyses and their traces against Agent Substrate. Each ChatKit thread is one
analysis backed by one Actor:

| ChatKit | Provider and Substrate |
| --- | --- |
| First message in a thread | `client.create()` creates an Actor and stages the reports and skill |
| Every later message | `client.resume()` resumes the same Actor; it never creates a replacement |
| End of each turn | the session closes, which suspends the Actor (`--mode sync`) |
| Thread metadata | the serialized `SubstrateSessionState` and the SDK conversation history |
| Deleting the thread | `client.delete()` deletes the Actor |

The model is live: the server uses `OPENAI_MODEL` and `OPENAI_API_KEY`, so every
message incurs model API usage.

## Run it

After [local Kubernetes setup](../../../docs/local-kubernetes.md), deploy the
Python sandbox image (`agent-setup`) and Jaeger (`tracing-setup`), then:

```sh
./scripts/local-kubernetes.sh ui
```

Open <http://localhost:8000>. The command port-forwards Jaeger's OTLP (4318) and
query (16686) ports to loopback and starts the server with
`uv run --group ui python -m examples.document_intelligence.ui`. It uses the
template from the last sandbox deployment unless you pass `--template`. Extra
arguments go to the server, for example `./scripts/local-kubernetes.sh ui --model YOUR_MODEL`.

## What you see

- **Conversation (ChatKit):** each turn starts with a workspace step (created or
  resumed, with the Actor name), then the agent's messages and its sandbox tool
  calls as collapsible workflow steps showing commands, exit codes and output.
  A card closes the turn: the host check result, buttons for the files the turn
  wrote, and **View trace**.
- **Artifact panel:** a tab for every file in `output/` and `work/`, rendered by
  type (Markdown report, CSV table, JSON, code), with the turn's version picker
  and a download link. Files the host check does not cover are marked as not
  checked.
- **Trace tab:** the turn's trace from Jaeger: duration, model calls, tokens,
  tool calls and failures, where the time went, and a span timeline. It updates
  live while the turn runs and links to the full trace in Jaeger.
- **Appearance:** System (default), Light or Dark. ChatKit only accepts light or
  dark, so the page resolves the system preference and passes it to ChatKit.

## How it works

- `server.py` subclasses ChatKit's `ChatKitServer`. Each turn resumes the Actor,
  runs `Runner.run_streamed`, converts the stream with ChatKit's
  `stream_agent_response`, copies `output/` and `work/` to the host
  (`artifacts/examples/document_intelligence/ui/threads/`), runs the host check
  (`verify_artifacts`), and suspends the Actor. ChatKit does not render tool
  calls, so the server adds them as workflow tasks.
- `store.py` keeps threads in SQLite (`artifacts/examples/document_intelligence/ui/threads.sqlite3`),
  so threads and their Actors survive a server restart.
- `traces.py` reduces a Jaeger trace to the rows and totals the panel shows.
  Each turn is one SDK trace with a known ID, grouped by the analysis ID.
- `static/` is plain HTML, CSS and JavaScript. ChatKit's web component loads from
  `cdn.platform.openai.com`; self-hosted mode uses `domainKey: "local-dev"`, which
  covers localhost.

## Security

The server is the trusted harness: it holds the API key and Substrate access,
and the Actor receives neither (the first turn checks this).

- It listens on loopback only and rejects other `Host` headers and cross-site
  requests to `/chatkit`. There is no sign-in: do not expose it beyond your machine.
- Text from the sandbox (commands, output, file contents) is shown as code or
  plain text, never as HTML. Files are served as `text/plain` with a sandbox CSP,
  and only paths the server recorded can be fetched.
- Thread metadata (Actor state, conversation history) stays on the server;
  ChatKit never sends it to the browser.
- Traces carry timing, tool names, exit codes and token counts only.

## Tests

```sh
uv sync --group ui
uv run pytest -q examples/document_intelligence/evals/test_ui.py
```

They drive the ChatKit protocol through two turns on the local protocol
simulator with a scripted model: one Actor is created, then resumed; the host
check runs; files are copied; deleting the thread deletes the Actor. They also
cover the HTTP guards and the trace summary.
