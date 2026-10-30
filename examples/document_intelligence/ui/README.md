# Document Intelligence UI

A ChatKit conversation beside an artifact panel, for testing multi-turn
analyses and their traces against Agent Substrate. Each ChatKit thread is one
analysis backed by one Actor and its workspace volume:

| ChatKit | Provider and Substrate |
| --- | --- |
| First message in a thread | `client.create()` creates an Actor; the sample reports, or the attached files, go into its workspace |
| Every later message | `client.resume()` resumes the same Actor; it never creates a replacement |
| Attached files | copied into the Actor's workspace volume at `inputs/uploads/` |
| End of each turn | the session closes, which suspends the Actor (`--mode sync`) |
| Thread metadata | the serialized `SubstrateSessionState` and the SDK conversation history |
| Deleting the thread | `client.delete()` deletes the Actor and its volume; the server deletes its host copies |

Threads are independent: two conversations can run at the same time, each in its
own Actor, and neither can see the other's documents.

The model is live: the server uses `OPENAI_MODEL` and `OPENAI_API_KEY`, so every
message incurs model API usage.

## Run it

After [local Kubernetes setup](../../../docs/local-kubernetes.md), deploy the
Python sandbox image and template (`agent-setup`) and Jaeger (`tracing-setup`), then:

```sh
./scripts/local-kubernetes.sh ui
```

Open <http://localhost:8000> (use `localhost`, not `127.0.0.1`: upload URLs and
ChatKit's `local-dev` domain key use that name). The command port-forwards
Jaeger's OTLP (4318) and query (16686) ports to loopback and starts the server
with `uv run --group ui python -m examples.document_intelligence.ui`. It uses
the template from the last sandbox deployment unless you pass `--template`.
Extra arguments go to the server, for example
`./scripts/local-kubernetes.sh ui --model YOUR_MODEL`.

## The workspace volume

The sandbox ActorTemplate declares a Substrate `durableDir` volume mounted at the
agent's workspace root, `/workspace`. It is per-Actor and has exactly the
conversation's lifetime: created with the Actor, kept across every suspend and
resume by Substrate's snapshots, and deleted with the Actor. Kubernetes
ephemeral volumes are not an option here: an ActorTemplate mounts only
`durableDir`, external CSI (a preview feature) or `systemInfo` volumes, and a
worker pod's emptyDir would not follow the Actor when it resumes on another
worker.

## Uploads

Attach Markdown, text or CSV files (`.md`, `.txt`, `.csv`; up to five per
message, 1 MiB each) with the composer's **+** button. ChatKit uploads in two
steps: `attachments.create` returns an upload URL on this server, then the
browser sends the file there. The server stages it on the host until the next
turn, writes it into the Actor at `inputs/uploads/`, lists every upload with its
SHA-256 in `inputs/uploads/MANIFEST.json`, keeps its own copy for checks and the
panel, and deletes the staged copy. Staged files that are never sent expire
after an hour. Retrying a message reuses its attachment IDs and restores the
original host bytes at the same workspace paths, including after a server
restart. The retained copies must stay with the SQLite database. Messages saved
before attachment-ID tracking was added need their files attached again to retry.

A thread that starts with attachments gets an empty workspace; a thread that
starts with a question gets the bundled sample reports. Either kind can receive
more uploads later.

The document-analysis skill's extractor and the host contract check only
understand the sample reports. For uploaded documents the agent analyzes with
the shell and Python, and writes `output/result.json` with a `citations` list.
The host then checks that every quote appears, word for word (ignoring
whitespace), in its own copy of the source file it cites. Use an exact filename
as the `document_id`, or the full workspace path such as
`inputs/uploads/report.md`. A short ID such as `report` is accepted only when it
identifies one source. Exact filenames take priority over short IDs; ambiguous
names require a full path.

In a thread with both sample reports and uploads, a report citing uploads can
quote either set of sources. This check covers quotes only: calculated figures
and other output files remain the agent's own work, as the UI labels explain.
Sample-only reports retain the full sample contract check, including their
calculations and CSV; matching quotes cannot bypass a failed sample check.

## What you see

- **Conversation (ChatKit):** each turn starts with a workspace step (created or
  resumed, with the Actor name) and, when files are attached, a step listing
  what was copied into `inputs/uploads/`. Then come the agent's messages and its
  sandbox tool calls as collapsible workflow steps showing commands, exit codes
  and output. A card closes the turn: the host check result, buttons for the
  files the turn wrote, and **View trace**.
- **Artifact panel:** a tab for every file in `output/`, `inputs/uploads/` and
  `work/`, rendered by type (Markdown report, CSV table, JSON, code), with the
  turn's version picker and a download link. Files the host check does not cover
  are marked as not checked.
- **Trace tab:** the turn's trace from Jaeger: duration, model calls, tokens,
  tool calls and failures, where the time went, and a span timeline. It updates
  live while the turn runs and links to the full trace in Jaeger.
- **Appearance:** System (default), Light or Dark. ChatKit only accepts light or
  dark, so the page resolves the system preference and passes it to ChatKit.

## How it works

- `server.py` subclasses ChatKit's `ChatKitServer`. Each turn resumes the Actor,
  writes any uploads into it, runs `Runner.run_streamed`, converts the stream
  with ChatKit's `stream_agent_response`, copies `output/` and `work/` to the host
  (`artifacts/examples/document_intelligence/ui/threads/`), runs the host check,
  and suspends the Actor. ChatKit does not render tool calls, so the server adds
  them as workflow tasks. Turns in different threads run concurrently; turns in
  one thread run one at a time.
- `store.py` keeps threads in SQLite (`artifacts/examples/document_intelligence/ui/threads.sqlite3`),
  so threads and their Actors survive a server restart.
- `traces.py` reduces a Jaeger trace to the rows and totals the panel shows.
  Each turn is one SDK trace with a known ID, grouped by the analysis ID.
- `static/` is plain HTML, CSS and JavaScript. ChatKit's web component loads from
  `cdn.platform.openai.com`; self-hosted mode uses `domainKey: "local-dev"`, which
  covers localhost, and the `two_phase` upload strategy.

## Security

The server is the trusted harness: it holds the API key and Substrate access,
and the Actor receives neither (the first turn checks this).

- It listens on loopback only and rejects other `Host` headers and cross-site
  requests to `/chatkit` and the upload URLs. There is no sign-in: do not expose
  it beyond your machine.
- Uploads are limited by extension, size and count, must be UTF-8 text, get a
  sanitized file name, and can be written once. Their contents only reach the
  Actor's own volume and the host copy for that thread.
- Text from the sandbox and from uploads (commands, output, file contents) is
  shown as code or plain text, never as HTML. Files are served as `text/plain`
  with a sandbox CSP, and only paths the server recorded can be fetched.
- Thread metadata (Actor state, conversation history) stays on the server;
  ChatKit never sends it to the browser.
- Traces carry timing, tool names, exit codes and token counts only.

## Tests

```sh
uv sync --group ui
uv run pytest -q examples/document_intelligence/evals/test_ui.py
```

They drive the ChatKit protocol on the local protocol simulator with scripted
models: one Actor is created and resumed across two turns; the host check runs;
files are copied; uploads go through the two-phase flow into the workspace and
their quotes are checked (and a missing quote fails); filename collisions are
resolved without shadowing another source; retries restore original bytes after
a restart; mixed sample/upload reports check both sets of quotes while sample
calculations remain strictly checked; upload limits are enforced; deleting a
thread deletes its Actor and host copies. They also cover
the HTTP guards and the trace summary.
