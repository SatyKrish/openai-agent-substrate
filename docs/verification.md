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
| Default unit/integration suite | 85 passed; five real-cluster checks are opt-in |
| Unit/integration suite using the actual upstream Go execution server (before tracing change) | 63 passed; four real-cluster tests skipped |
| Opt-in Kubernetes integration test | Passed against a fresh real Actor |
| SDK scripted workflow on Kubernetes | Passed: shell tool execution, report creation, serialized session resume, and preserved edits |
| Kubernetes filesystem behavior | Passed: binary file round trips, SDK patching, and nonzero shell exit codes |
| Repeated setup | Reused the existing cluster; the retained Actor and its files remained usable |
| Scripted Document Intelligence on Kubernetes | Passed: native `load_skill`, shell inspection/extraction, `apply_patch`, Python execution, exact citations, and independent host artifact checks |
| Domain artifact rejection tests | Passed: altered citations, calculations, CSV, missing report evidence/limitations, staged digest mismatches, and source-table disagreements are rejected |
| Secret-backed Kubernetes agent pod | Passed: five OpenAI settings match mounted files and environment, an explicit evaluation override is required, and real document analysis runs with no harness credential in the Actor |
| Live OpenAI Document Intelligence on Kubernetes | Passed with `gpt-6.1-terra`: seven API responses, native skill/shell/patch tools, model-authored Python, independent artifact validation, and Actor cleanup |
| Full suite with all local Kubernetes and Jaeger checks enabled | 90 passed, no skips; includes the Secret-backed harness and Jaeger query API |
| Source distribution and wheel | Built successfully; `twine check` passed |

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

## Limits of this verification

These checks establish local functional execution and snapshot recovery on a single-node Kind cluster. They do not establish production availability, multi-tenant authorization policy, resistance to sandbox escape, host/node-loss durability, or model quality beyond the documented live fixture workflow. The local installation uses upstream development identities; shared deployments need an operator-defined identity and authorization configuration.

The existing public CI also exercises the key-free paths on Python 3.11–3.14. See [local-kubernetes.md](local-kubernetes.md) to repeat the real-cluster checks.
