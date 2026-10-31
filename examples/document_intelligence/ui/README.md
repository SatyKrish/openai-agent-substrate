# Document Intelligence UI

A ChatKit conversation with a side panel for the analysis's files, checked
quotes and traces, for testing multi-turn analyses against Agent Substrate. It
runs on the [shared harness](../../../harness/README.md), which runs the agent
loop, owns the sandbox lifecycle and serves the page every agent's UI shares;
this folder configures that page and adds its PDF view.
Each ChatKit thread is one analysis backed by one Actor and its workspace volume:

| ChatKit | Provider and Substrate |
| --- | --- |
| First message in a thread | `client.create()` creates an Actor; the sample reports, or the attached files, go into its workspace |
| Every later message | `client.resume()` resumes the same Actor; it never creates a replacement |
| Attached files | copied into the Actor's workspace volume at `inputs/uploads/`; PDF pages are extracted and rendered in the Actor |
| End of each turn | the session closes, which suspends the Actor (`--mode sync`) |
| Thread metadata | the serialized `SubstrateSessionState`, uploads and turn records |
| SDK session | the agent's conversation history (`SQLiteSession`, one per thread) |
| Deleting the thread | `client.delete()` deletes the Actor and its volume; the server deletes its host copies |

Threads are independent: conversations run at the same time, each in its own
Actor, and none can see another's documents. A message sent while the same
conversation's turn runs waits for it. When every sandbox worker is busy, a turn
shows "Waiting for a sandbox worker" and starts when one has room.

The model is live: the server uses `OPENAI_MODEL` and `OPENAI_API_KEY`, so every
message incurs model API usage.

## Run it

After [local Kubernetes setup](../../../docs/local-kubernetes.md), deploy the
Python sandbox image and template (`document-intelligence`) and, for traces,
Jaeger (`tracing-setup`), then:

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

Attach PDF, Markdown, text or CSV files (`.pdf`, `.md`, `.txt`, `.csv`; up to
five per message, 10 MiB each) with the composer's **+** button. ChatKit uploads in two
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

## PDFs

The sandbox image includes poppler, and a PDF is only ever parsed inside the
Actor. Before the model sees a new PDF, the server runs one command in the
Actor: `pdftotext` extracts each page's text and `pdftoppm` renders each page
as a 100 DPI JPEG, for the first 30 pages, into
`inputs/derived/<name>/page-NNN.txt` and `page-NNN.jpg`. The server reads back a
summary and the page images, validates them (page counts, sizes, JPEG
signatures) and keeps host copies. A damaged or password-protected PDF is
rejected with a message.

The agent reads a page's text first and uses the SDK's built-in `view_image`
tool (part of the `Filesystem` capability) to look at tables, charts and pages
without a text layer. PDF citations name a page:
`{"document_id": "report.pdf", "page": 3, "quote": "..."}`. The host checks each
quote against its copy of that page's extracted text, ignoring whitespace,
ligatures and words hyphenated across lines, so a quote from the wrong page
fails. A quote the agent read from an image (marked `"source": "image"`), or
from a page without a text layer, cannot be checked against text: the card
counts it as a quote to review and the panel shows it beside its page.

## What you see

- **Before the first message:** the panel shows the sample reports every new
  analysis starts with. Suggestions under the chat fill the composer, so you
  can read and edit a question before sending it.
- **Conversation (ChatKit):** each turn starts with a workspace step (created or
  resumed, with the Actor name) and, when files are attached, a step listing
  what was copied into `inputs/uploads/`. Then come the agent's messages and its
  tool calls, each tagged with where it ran (`· sandbox` here; this agent has no
  harness tools). A card closes the turn: the host check result, buttons for the
  files the turn wrote, **Show the quotes** and **View trace**.
- **The turn's result**, under the chat: what the host checked, and buttons to
  open the checked file, the quotes and the trace.
- **Side panel**, opened when a turn writes files and closed for good once you
  collapse it (the panel button then counts new files):
  - **Files:** outputs, inputs and work files with the turn's version picker
    and a download link. A green dot marks a host-checked file, amber one the
    check did not cover. A new upload opens as soon as it is in the workspace:
    a PDF shows its rendered pages, which have a text layer, and later the quotes
    cited from each page.
  - **Quotes:** every quote the host checked, by source; **Show in source**
    opens the host's copy with the quote highlighted, or a PDF at the cited page.
  - **Trace:** the turn from Jaeger as a timeline of the workspace, the harness
    (model calls, the host check) and the sandbox (commands), live while the
    turn runs, with a link to the full trace.
- **Header:** free sandbox slots on the cluster, a menu to copy a link to the
  conversation (`?thread=…`) or delete it after a confirmation that names its
  Actor, and Appearance (System, Light or Dark).

## How it works

- The harness's `HarnessChatServer` runs each turn: it resumes the Actor,
  writes any uploads into it, runs the agent with its SDK session, copies
  `output/` and `work/` to the host
  (`artifacts/examples/document_intelligence/conversations/threads/`), runs the
  agent's host check, and suspends the Actor. See the
  [design](../../../docs/agent-harness.md).
- The agent's side lives in `../agent/`: `spec.py` (starting workspace, upload
  types and the PDF processor, the check), `lib/pdf.py` and `lib/citations.py`,
  whose result lists the quotes it checked for the panel.
- `app.py` holds this UI's settings for the shared page (title, prompts,
  follow-ups, the sample reports to show) and its PDF page route; `card.py` adds
  the check's rows to the turn card and says which files the check covered;
  `static/agent.js` renders a PDF's pages in the panel.
- Threads are kept in SQLite (`conversations/threads.sqlite3`), so threads and
  their Actors survive a server restart. Each turn is one SDK trace with a known
  ID, grouped by the thread. Conversations from before the shared harness stay
  in `artifacts/examples/document_intelligence/ui/` and are not loaded.
- ChatKit's web component loads from `cdn.platform.openai.com`; self-hosted
  mode uses `domainKey: "local-dev"`, which covers localhost, and the `two_phase`
  upload strategy.

## Security

The server is the trusted harness: it holds the API key and Substrate access,
and the Actor receives neither (the first turn checks this).

- It listens on loopback only and rejects other `Host` headers and cross-site
  requests to `/chatkit` and the upload URLs. There is no sign-in: do not expose
  it beyond your machine.
- Uploads are limited by extension, size and count, get a sanitized file name,
  and can be written once. Text uploads must be UTF-8; PDFs must start with
  `%PDF-`. Their contents only reach the Actor's own volume and the host copy
  for that thread.
- PDFs are parsed by poppler in the Actor, never by the server. What comes
  back is treated as untrusted: the server accepts only a well-formed summary
  and JPEG page images, at most 30 pages and 4 MiB per image.
- Text from the sandbox and from uploads (commands, output, file contents) is
  shown as code or plain text, never as HTML. Files are served as `text/plain`,
  page images as `image/jpeg`, both with a sandbox CSP; an uploaded PDF is only
  offered as a download. Only paths the server recorded can be fetched.
- Thread metadata (Actor state) and the SDK session history stay on the
  server; ChatKit never sends them to the browser. The sample reports shown
  before the first message are served read-only from the server's own copies.
- Traces carry timing, tool names, exit codes and token counts only.

## Tests

```sh
uv sync --locked --group ui
uv run pytest -q examples/document_intelligence/evals/test_ui.py examples/document_intelligence/evals/test_pdf.py
```

They drive the ChatKit protocol on the local protocol simulator with scripted
models: one Actor is created and resumed across two turns; the host check runs;
files are copied; uploads go through the two-phase flow into the workspace and
their quotes are checked (and a missing quote fails); filename collisions are
resolved without shadowing another source; retries restore original bytes after
a restart; mixed sample/upload reports check both sets of quotes while sample
calculations remain strictly checked; upload limits are enforced; deleting a
thread deletes its Actor and host copies; an upload opens in the panel before
the model runs; each file's check status and the listed quotes. They also cover
the HTTP guards and the trace summary. The PDF tests cover validation of the
sandbox's output, page-level citation checks (right page, wrong page, quotes to
review) and the page routes; one more test sends a generated PDF through the
whole flow when poppler is installed on the host (CI installs it).
