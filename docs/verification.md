# Verification record

Date: 2026-10-29.

## Versions and environment

- Host: macOS ARM64, Python 3.14.3.
- Local VM: dedicated Colima profile, 6 CPUs and 12 GiB RAM.
- Kubernetes: Kind, v1.37.0, Linux ARM64.
- Sandbox runtime: gVisor (`gvisor-default`), two sandbox worker pods.
- Snapshot storage: in-cluster RustFS through Substrate's object-store plugin.
- OpenAI Agents SDK: `openai-agents==0.24.0`.
- Substrate source: `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`.

## Results

| Check | Result |
| --- | --- |
| Default unit/integration suite, with the `ui` group | 133 passed; six opt-in or poppler-gated checks skipped |
| Unit/integration suite using the actual upstream Go execution server (before tracing change) | 63 passed; four real-cluster tests skipped |
| Opt-in Kubernetes integration test | Passed against a fresh real Actor |
| SDK scripted workflow on Kubernetes | Passed: shell tool execution, report creation, serialized session resume, and preserved edits |
| Kubernetes filesystem behavior | Passed: binary file round trips, SDK patching, and nonzero shell exit codes |
| Repeated setup | Reused the existing cluster; the retained Actor and its files remained usable |
| Scripted Document Intelligence on Kubernetes | Passed: native `load_skill`, shell inspection/extraction, `apply_patch`, Python execution, exact citations, and independent host artifact checks |
| Domain artifact rejection tests | Passed: altered citations, calculations, CSV, missing report evidence/limitations, staged digest mismatches, and source-table disagreements are rejected |
| Secret-backed Kubernetes agent pod | Passed: five OpenAI settings match mounted files and environment, an explicit evaluation override is required, and real document analysis runs with no harness credential in the Actor |
| Live OpenAI Document Intelligence on Kubernetes | Passed with `gpt-6.1-terra`: seven API responses, native skill/shell/patch tools, model-authored Python, independent artifact validation, and Actor cleanup |
| Full suite with all local Kubernetes and Jaeger checks enabled | 94 passed, no skips; includes the Secret-backed harness and Jaeger query API |
| Source distribution and wheel | Built successfully; `twine check` passed |
| ChatKit UI, multi-turn on Kubernetes | Passed with `gpt-6.1-terra`: three turns on one Actor across a UI server restart, live per-turn traces, and thread deletion removing the Actor |
| Per-Actor workspace volume | Passed: a `durableDir` mounted at `/workspace` kept a file across suspend and resume; the sample workflow passed on the new template |
| Batched file transfer on Kubernetes | Passed: 1 MiB writes went from 10.4 s to 0.82 s, files up to 10 MiB round trip, sample staging went from 13.9 s to 5.9 s, and the real-cluster suite passed on the upstream and Document Intelligence images |
| Uploads and parallel conversations | Passed with `gpt-6.1-terra`: two conversations with different uploaded documents ran concurrently in separate Actors, each saw only its own files, all quotes matched, and follow-ups resumed the right Actor |
| PDF uploads on Kubernetes | Passed with `gpt-6.1-terra`: poppler in the Actor extracted and rendered a four-page PDF, the agent used `view_image` for a chart and a scanned page, page-level quotes were checked against the extracted text, image-read quotes were marked for review, a 9.8 MiB PDF went through, and an 11 MiB file and a fake PDF were refused |
| Sandbox size on Kubernetes | Passed: at 0.5 CPU and 480 MiB per Actor, 8 Actors ran at once on two workers instead of 2; four live conversations started together all passed in 38–42 s |
| Shared agent harness on Kubernetes | Passed with `gpt-6.1-terra`: nine conversations started together all completed and passed their checks, the one beyond capacity waited 41 s for a worker, and two messages sent to one conversation at once both kept their turn |
| SRE agent on Kubernetes | Passed with `gpt-6.1-terra`: read-only cluster reads ran in the harness, analysis ran in the sandbox, and every quote in its findings matched the host's copy of the evidence |

The Kubernetes path uses owned loopback `kubectl ate proxy actor` processes and authenticated Substrate ingress. The scripted infrastructure tests exercise the real SDK runner and actual sandbox execution without model calls. The separate live OpenAI run is recorded below.

## Scripted Document Intelligence

The domain example uses the native SDK `Skills` capability with a lazy local
bundle, `Shell`, and `Filesystem`. Its real-cluster execution image is
`localhost:5501/openai-agent-substrate-document-intelligence@sha256:3a60dcbbd55ce4a00cb5f351a628386a5093eaf014d0d6290b693fa40a8edaaf`,
registered as `document-intelligence-46668a69e650` in the dedicated local cluster.
The input Markdown and CSV fixtures are synthetic and retain their MIT license.

The SDK trace records eight actual tool calls: load the skill, read its contract,
inspect documents, extract and reconcile facts, patch an analysis program, run
that Python program, verify its outputs, and reopen them. The host independently
validated the downloaded report, CSV, and JSON against the original sources.
ACME and BETA operating margins were 22.93% and 30.19%, with a USD 117.8 billion
revenue gap and an explicit missing-EBITDA limitation.

These runs used fixed scripted model decisions, including a code fixture emitted
through the real patch tool. They prove sandbox execution and the required
artifact checks, not live model reasoning or general document understanding.
See [the domain guide](../examples/document_intelligence/README.md).

## Environment configuration in the agent pod

A temporary Secret carried a dummy key, `https://example.invalid/v1`,
a configured model name, and test organization/project IDs. Evaluation was
explicitly selected with `--test`. The dedicated non-root
harness Job confirmed that all five environment values matched the read-only
Secret files. The mounted model configuration was preserved, and `--test` explicitly selected
the scripted evaluation model. The native skill, shell, patch, and Python workflow completed against a
real Substrate Actor, whose credential preflight passed. No OpenAI API call was
made. The test deleted its Job and Secret; report artifacts were collected from
the retained PVC using a temporary reader pod without credential mounts.

Local unit tests also construct the actual OpenAI client without requests and
verify its endpoint, API key, organization, and project settings from environment
variables. A subsequent live endpoint/model run passed as described below.

## Authored agent layout and live defaults

The domain project now has `agent/agent.py`, `agent/instructions.md`, native tool
registration, skills, trusted `lib/` helpers, and a `sandbox/` workspace and image.
Sibling `evals/` contains scripted decisions, code fixtures, and execution checks.
This organization follows [Eve's project conventions](https://eve.dev/docs/concepts/project-structure)
with explicit Python loading through the OpenAI Agents SDK.

Normal domain execution selects a configured live model and requires an API key.
Missing credentials cause an error. Scripted evaluation requires `--test` or an
explicit `--model scripted`; an environment value alone cannot select it. Run
records identify `openai-live` or `scripted-eval` and include returned API response
IDs. Scripted evaluations have no API response IDs. The reorganized harness passed
its real Kubernetes pod evaluation. Those initial checks used no API credentials;
the later live run below uses the updated local `.env`.

## Live OpenAI execution

The updated local `.env` was imported into the `openai-agent-env` Kubernetes
Secret and exposed to the trusted harness through environment variables and
read-only mounted files. The harness confirmed they matched. `OPENAI_MODEL` was
`gpt-6.1-terra`; `OPENAI_BASE_URL` was unset, so the SDK used the actual default
OpenAI endpoint, `https://api.openai.com/v1`.

Job `openai-agent-8692fb0e0eae` completed successfully on the dedicated local
Kind cluster. The run recorded `execution_mode: openai-live`,
`model_inference: true`, and seven returned OpenAI API response IDs. No scripted
evaluation flag was supplied. Its six native tool calls were `load_skill`, three
`exec_command` calls, `apply_patch`, and a final `exec_command` call. The model
loaded the document-analysis skill, inspected and extracted the source facts,
wrote a Python program through the patch tool, and executed it in the Substrate
Actor. The downloaded program differs from the scripted evaluation fixture.

The independent host verifier accepted `comparison.md`, `kpis.csv`, and
`result.json`, including exact source citations, calculations, required schema,
and limitations. Downloaded artifact hashes matched the run record. Operating
margins were ACME 22.93% and BETA 30.19%; the revenue gap was USD 117.8 billion.
The Actor credential preflight confirmed no `OPENAI_API_KEY` or harness Secret
mount was present. Downloaded outputs were checked against the configured
credential, and the temporary Actor was confirmed deleted after completion.

The report, model-authored program, tool trace, API response IDs, and verification
receipt are stored under ignored `artifacts/local/`. Credentials and kubeconfig
files must stay out of published artifacts. This validates one bounded workflow
on synthetic Markdown/CSV fixtures; it does not establish general PDF/OCR or
document-understanding quality.

## SDK tracing to local Jaeger

Jaeger all-in-one 1.76.0 was deployed by digest in the dedicated local Kind
cluster. The updated trusted harness completed a new live Document Intelligence
run with `gpt-6.1-terra`. Jaeger's query API returned one complete trace with
149 spans over 54.302 seconds, including seven model response spans, the skill,
shell and patch tools, sandbox commands, Actor lifecycle, and independent host
verification. The UI timeline was opened and inspected.

The trace verifier checked every parent reference, root and child timing, and
sandbox execution nested beneath SDK tool calls. All three report artifacts
passed source and hash validation; the Actor was confirmed deleted. A private
scan found no configured API credential in the downloaded outputs or Jaeger
payload, and the trace omitted sampled document text, generated code and paths.
The model-authored program differs from the scripted evaluation fixture.

The full suite passed with all local Kubernetes and Jaeger flags enabled: 77
passed, with no skips. The key-free Kubernetes tracing regression also passed
against Jaeger's API.
Unit tests cover payload exclusion, parentage, concurrent trace isolation,
explicit disable, hosted exporter replacement, invalid spans, and export failure.
See [tracing setup and verification](tracing.md) for repeatable commands.

## Review follow-up verification

Directory writes and directory-symlink writes now fail without leaving uploaded
children. Injected upload/cleanup failures confirm cleanup cannot replace the
original upload error or turn a completed write into a failure. Both execution
servers exercise those regressions. A binary-output check documents that command
stdout is UTF-8 JSON text while the file API preserves the original bytes.

The harness connects directly to `api.ate-system.svc:443`, using the namespace
label required by Substrate's existing ingress policy. The pinned router proxy
still requires port forwarding, so setup restricts that permission to discovered
router pod names. Authorization checks allow the router and deny API/database
pod forwarding. The Secret-mounted harness workflow passes with that narrower
Role. The hardened artifact reader also passed server-side Restricted Pod Security
admission and collected its reports successfully.

Ruff and the focused mypy targets pass. The manual Kubernetes CI dispatch is
configured; its GitHub-hosted run remains unverified. Those infrastructure checks
used explicit scripted evaluations. The live run is recorded separately above.

## External snapshot recovery

The retained demo Actor was synchronously suspended. Its API status reported `ACTOR_STATE_SUSPENDED`, `SNAPSHOT_FIDELITY_MEMORY`, and `SNAPSHOT_STORAGE_STATE_COMPLETED`.

To test recovery beyond the node-local checkpoint, the test reverted this already-durably-suspended Actor to its external snapshot, confirmed its node-local snapshot reference was removed, and replaced both idle sandbox worker pods. The old and new worker UIDs were disjoint.

A new SDK session then resumed the same Actor through authenticated ingress. The report matched the host's original bytes and the edited input remained intact. The Actor was suspended again with another completed durable snapshot.

Evidence is saved locally under ignored `artifacts/local/`, including `recovery-verification.json`, before/after Actor status, demo reports, and the setup/test logs. The directory also contains kubeconfig credentials and must not be published as a whole.

## Library, base deployment and example boundaries

The deployment split passed the existing local suite and a Kubernetes test of
its relocated harness entrypoint. Both Actor and harness images built from the
new example-owned Dockerfiles. The library wheel was inspected and imported
outside the checkout: it contains the provider and testing helpers, with no
example application or installed demo command.

Python builders now produce the reusable base resources and the example Job,
reader and Jaeger resources. Their full output matches saved manifests from
before the refactor. Dependency tests enforce the library/base/example direction,
and configurable base names are tested independently of Document Intelligence.
The rebuilt harness and applied base/Jaeger resources passed the full local
suite with every Kubernetes flag enabled: 90 passed, with no skips.

The wheel and source distribution pass `twine check`. The source archive contains
both relocated Dockerfiles and the JSON test expectations. An extracted source
distribution passed 85 tests, with its five cluster checks disabled. This run used
a temporary directory because macOS blocks the SDK's directory-descriptor
traversal into Documents; the SDK file checks were preserved. Ruff and the
focused mypy targets pass. These refactor checks used explicit scripted models;
the earlier live OpenAI and Jaeger evidence remains recorded above.

## Tracing review follow-up

Application-specific operation names are now supplied by the example through the
processor constructor and configuration helper. The library's built-in operation
names cover only sandbox lifecycle and execution. Regression tests verify that
unregistered names and custom payloads stay out of exported traces.

The OpenTelemetry SDK and OTLP exporter moved to the optional `tracing` extra.
A fresh environment installed the base wheel without those distributions and
successfully imported the provider and executed a command through its local
protocol simulator. The Agents SDK currently still brings `opentelemetry-api`
through MCP. Lightweight SDK span helpers are independent of the OTLP module.
The harness image explicitly installs the extra; development dependencies include
the exporter for tests and source examples.

The rebuilt harness passed the full local Kubernetes/Jaeger suite: 94 passed,
with no skips. The local and extracted source-distribution suites passed 89 tests
with five cluster checks skipped. The selected upstream Go-server suite passed
40 tests. Ruff, focused mypy and distribution metadata checks passed. The usage
command, tracing description, package description and source manifest patterns
were corrected. These checks made no new model API calls.

## Document Intelligence UI

The ChatKit UI ran on the host as the trusted harness, against the Kind cluster,
with `gpt-6.1-terra` and the local Jaeger collector.

- **Multi-turn:** turn 1 created an Actor and passed the host contract check in
  55.3 s (7 model calls, 62,184 tokens). Turn 2 resumed the same Actor in 0.9 s
  and wrote a new output that the panel marked as not host-checked. After a UI
  server restart, the thread reloaded from SQLite and turn 3 resumed the same
  Actor and still saw turn 2's file. Jaeger held one trace per turn, with
  `sandbox.create` only in turn 1. Deleting the thread from ChatKit's history
  deleted the Actor (`NotFound`).
- **Workspace volume:** the sandbox template `document-intelligence-272cce09f181`
  mounts a `durableDir` at `/workspace`. A direct provider check showed it as a
  separate mount, wrote a file, suspended and resumed the Actor, and read the
  file back. Creating an Actor with an empty workspace took 5.8 s, against
  about 14 s when the sample reports are staged. The key-free sample workflow
  passed its host contract check on this template.
- **Parallel conversations with uploads:** conversation A uploaded one Markdown
  report and conversation B a Markdown report and a CSV, all fictional. Their
  first turns overlapped for 45 s and their follow-ups for 9.9 s, in two
  separate Actors whose traces started in the same second. Asked to list every
  visible file, each agent found only its own uploads. The host citation check
  passed for every quote (2 in A, 4 in B including the CSV), and the computed
  figures matched the documents. Both Actors were suspended between turns and
  deleted with their threads, along with the host copies.
- **Browser upload:** ChatKit's own two-phase uploader sent a file to the UI
  server, and the turn passed the citation check. This run found that ChatKit
  refuses to start with attachments enabled unless `uploadStrategy` is set, and
  that uploads-only threads must tell the agent not to run the samples-only
  verifier; both are fixed.

## File transfer

The same benchmark ran against one fresh Actor from the Document Intelligence
template, before and after batching writes and chunking reads. Each `/process`
round trip took a median of 400 ms on local Kind, and eight concurrent 0.2 s
commands took 3.4 s: commands to one Actor run one after another, so transfer
time follows the number of commands. A single command carried 16 arguments of
120 KiB (1.4 MiB) in the same 0.41 s as an empty one.

| Operation | Before | After |
| --- | --- | --- |
| Write 64 KiB | 2.2 s | 0.21 s |
| Write 1 MiB | 10.4 s (26 commands) | 0.82 s (2 commands) |
| Write 4 MiB / 10 MiB | over the 1 MiB limit | 2.8 s / 6.7 s |
| Read 1 MiB | 0.45 s | 0.42 s |
| Read 4 MiB / 10 MiB | over the 1 MiB limit | 1.6 s / 4.1 s |
| Stage the sample reports into a new Actor | 13.9 s | 5.9 s |

Every round trip matched byte for byte and no partial files remained. The
provider suite passed 25 tests against both the Python simulator and the upstream
Go server, including multi-command round trips, one command for small and empty
files, the configurable limit, and a failure between write commands leaving no
partial file. The real-cluster suite passed against the upstream
`sandbox-template` image (binary write, read and resume) and the Document
Intelligence image, which shows both provide the `dd`, `wc`, `base64` and
`dirname` the transfer scripts use. These checks made no model API calls.

## UI upload review follow-up

The citation lookup now resolves canonical workspace paths first, exact filenames
second, and unique short document IDs last. Ambiguous filenames or short IDs fail
with a request for the full source path. Regression cases cover filename/stem
collisions in both upload orders and an upload sharing a bundled sample's name.

Uploads retain their ChatKit attachment IDs with the original host copy before
staged bytes are removed. Retrying after a successful or failed turn, including
a server restart, restores the original bytes under the same workspace filename
without creating another Actor or a renamed duplicate. Missing or altered host
copies are rejected with instructions to attach the file again. Uploads saved
before this mapping was introduced need to be attached again when retrying.

Uploads added to sample-started threads receive quote checks against both the
sample fixtures and retained uploads. The UI identifies this as quote validation;
calculated figures and other output files remain unchecked. The full contract
check still applies to sample-only reports, so matching sample quotes cannot
hide a failed calculation or CSV check.

The follow-up added 13 regression cases. On Python 3.11, the full local suite
passed 110 tests, with five opt-in Kubernetes/Jaeger tests skipped. All 20 UI
tests also passed against the upstream Go execution server. Ruff, the configured
mypy targets, JavaScript syntax, wheel/source builds and `twine check` passed.
These follow-up checks used scripted models and made no new OpenAI API calls;
the earlier live Kubernetes and model evidence is recorded above.

After integrating the file-transfer improvements from `main`, the combined local
suite passed 114 tests with five cluster checks skipped. The CI protocol suite
passed 64 tests against the upstream Go execution server. Lint, configured type
checks, JavaScript syntax, distribution builds and metadata checks passed again.

## PDF uploads

A four-page PDF built for this run (a text page with a word hyphenated across
lines, a segment table, a bar chart whose values exist only as bar heights, and
a scanned auditor's statement with no text layer) went through the UI against
the sandbox template on local Kind, with `gpt-6.1-terra`:

- **Processing in the Actor:** one server-run command extracted each page's text
  and rendered each page at 100 DPI, all four pages, three with a text layer.
  Workspace setup for the first turn, including the upload and the PDF
  processing, took 22 commands and 5.6 s.
- **First turn (42 s):** the agent read the extracted text, then called
  `view_image` on two page images (traced as `tool.view_image` spans). The host
  checked its page 1 and page 2 quotes against its own copy of those pages'
  text; the quote read from the scanned page 4 was counted as one to review.
  The answers matched the document: USD 3.2 billion, 14.0%, Cloud at 17.0%, and
  1,240 employees.
- **Follow-up turn (22–23 s, same Actor resumed):** asked about the chart, the
  agent called `view_image` on page 3 and read the four quarters exactly (720,
  770, 810 and 900, summing to the annual USD 3.2 billion).
- **Panel:** the PDF tab showed the rendered pages with "no text layer" and
  "cited" markers, checked and to-review quotes under their pages, and each
  page's extracted text on request. The browser received only JPEG pages and
  text; the PDF itself is served only as a download.
- **Limits:** a 10,241,030-byte (9.8 MiB) PDF went through in its own
  conversation, and copying it into the Actor added about 6 s (37 commands,
  11.6 s of setup). An 11 MiB PDF was refused when the upload was created, and
  an HTML file named `.pdf` was refused at upload.

The run found two defects, both fixed and rerun:

- The Alpine sandbox image had no fonts, so poppler rendered pages that use the
  standard PDF fonts without embedding them with no text at all; the
  extracted text was unaffected, but `view_image` would have seen blank pages.
  The image now installs the URW base 35 fonts (3.1 MB), and the rerun rendered
  every page with its text.
- In a follow-up turn the agent kept `output/result.json` as it was, because the
  instructions said not to rewrite it, so the card reported the earlier quotes
  as passing for an answer they did not cover. The instructions now ask for each
  answer's citations to be added to the list, and the rerun's follow-up added a
  checked page 3 quote. When `result.json` is unchanged, the card and panel now
  say "No new citations this turn" instead.

With these changes the full local suite passed 119 tests (six opt-in or
poppler-gated tests skipped), and the CI protocol suite passed 69 tests against
the upstream Go execution server. The end-to-end PDF test needs poppler on the
host, so it runs in CI, which installs poppler-utils.

## Shared agent harness and SRE agent

Document Intelligence moved onto the [shared harness](agent-harness.md), and the
SRE agent was added on it. Checked on local Kind with `gpt-6.1-terra`.

**Sandbox size.** Each worker reports 2,000 millicores and 1,984 MiB. At 2 CPUs
and 1 GiB per Actor, memory as well as CPU allowed one Actor per worker. A
benchmark with both templates:

| | 2 CPU, 1 GiB | 0.5 CPU, 480 MiB |
| --- | --- | --- |
| Actors running at once | 2 of 3 requested | 8 of 9 requested |
| Fixed Python loop / two at once | 0.32 s / 0.34 s | 0.79 s / 1.51 s |
| Processing the four-page PDF in the Actor | 0.3 s | 0.9 s |
| Peak memory of PDF processing | 22 MiB | 22 MiB |
| Resume / suspend | 2.7 s / 0.2 s | 2.7 s / 0.2 s |

Both sandbox templates now use 0.5 CPU and 480 MiB. Turns mostly wait on the
model, so the slower CPU did not lengthen them noticeably.

**Concurrency on the harness.**

- Nine conversations at once (two PDF, two Markdown and CSV, two Markdown, three
  sample reports): all nine completed and passed their host checks. Eight ran
  immediately; the ninth showed "Waiting for a sandbox worker" for 41 s, started
  when one finished, and passed. Before the harness, turns beyond capacity
  failed within 0.1 s with a generic error.
- Two messages sent to one conversation at the same moment: both were answered,
  the second starting when the first finished, and all three of the
  conversation's turns were recorded with passing checks. Before, the second
  message overwrote the first one's turn record and history.

**SRE agent.** `sre-setup` deployed the SRE sandbox template, a demo namespace
(a crash-looping `checkout`, an unpullable `catalog`, a healthy `frontend`) and
the reader's Role. The reader's token could list pods and read logs in
`sre-demo`, and was refused Secrets, `kube-system`, deletes and `exec`.

- "Which workloads in sre-demo are unhealthy, and why?" took 54.7 s: 10 cluster
  reads in the harness (2.4 s in all, saved as evidence), the triage skill and 6
  commands in the sandbox, and 2 findings whose 9 quotes all matched the host's
  copies. The diagnosis was right: the `catalog` image was not found, and the
  `checkout` command exits 1 unconditionally after printing the database error.
- A follow-up about `frontend` resumed the same Actor and answered from the
  saved evidence in 12.3 s without reading the cluster again.

**Defects found and fixed.**

- The model ran harness tool calls in parallel, and evidence files from one
  batch got the same number (`001-pods…`, `001-deployments…`). Two reads with
  the same label would have overwritten each other. The number is now taken
  before the first await; a rerun numbered its ten files 001 to 010.
- The waiting time on the turn card counted every sandbox start longer than a
  second (3–6 s for a normal start), not only waits for a worker. It now
  records only waits after Substrate refuses for lack of room.

After the fixes the full local suite passed 133 tests (six opt-in or
poppler-gated tests skipped), and the protocol suite, now including the harness
and SRE tests, passed 81 against the upstream Go execution server.

## Limits of this verification

These checks establish local functional execution and snapshot recovery on a single-node Kind cluster. They do not establish production availability, multi-tenant authorization policy, resistance to sandbox escape, host/node-loss durability, or model quality beyond the documented live fixture workflow. The local installation uses upstream development identities; shared deployments need an operator-defined identity and authorization configuration.

The UI checks drove ChatKit's uploader from a script rather than its file picker, used only localhost (ChatKit's `local-dev` domain key), and loaded ChatKit's unversioned script from OpenAI's CDN. Parallel isolation was shown for up to nine conversations on one node, not under sustained load.

The PDF checks used generated PDFs, not a corpus of real-world files. Pages that rely on fonts outside the standard set and not embedded in the PDF (for example most CJK text) may still render without text. Text read from page images is not checked by the host; it is shown for review.

The existing public CI also exercises the key-free paths on Python 3.11–3.14. See [local-kubernetes.md](local-kubernetes.md) to repeat the real-cluster checks.

The SRE agent was checked against one demo namespace with two kinds of failure.
Its reads go through `kubectl` with a token-only kubeconfig; `kubectl describe`
can show literal values from pod specs, which the Role does not restrict.
