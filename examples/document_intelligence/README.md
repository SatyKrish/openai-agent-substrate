# Document Intelligence

Compare two synthetic annual reports with a skill-driven `SandboxAgent`. The
agent loads a skill, inspects files, extracts and reconciles source facts, writes
Python analysis code, executes it in a Kubernetes-backed Actor, and checks the
resulting Markdown, CSV, and JSON artifacts.

The workflow follows the domain example in
[SatyKrish/bedrock-document-intelligence-agent](https://github.com/SatyKrish/bedrock-document-intelligence-agent/tree/7eb1dfbba95f87512aa20dc3444111983f0f3b3e).
Only its MIT-licensed synthetic fixtures are reused. This implementation uses
OpenAI Agents SDK and Agent Substrate with Kubernetes as the compute provider.
See [fixture provenance](agent/sandbox/PROVENANCE.json) and [license](agent/sandbox/workspace/inputs/LICENSE).

## Agent project structure

This Python project adopts the organization described in
[Eve's project structure guide](https://eve.dev/docs/concepts/project-structure)
and [agent files reference](https://eve.dev/docs/reference/agent-files). A single
`agent/` directory holds authored behavior and capabilities. `evals/` sits beside
it. The OpenAI Agents SDK explicitly loads these files and runs the agent.

```text
examples/document_intelligence/
  agent/
    agent.py                       SandboxAgent definition
    instructions.md                Standing agent behavior
    tools/capabilities.py          Native SDK shell, patch, and skill registration
    skills/document-analysis/      Domain procedure, scripts, output contract
    lib/                           Trusted configuration and artifact verification
    sandbox/
      sandbox.py                   Provider and workspace manifest
      PROVENANCE.json              Synthetic input provenance
      workspace/inputs/            Reports, KPI table, and MIT license
  evals/
    scripted.py                    Test-only fixed model decisions
    fixtures/analysis.py           Code fixture emitted through apply_patch
    test_workflow.py               Execution and output contract checks
    test_configuration.py          Live defaults and explicit test-mode checks
  main.py                          Host CLI and workflow orchestration
  deploy/                          Example images, entrypoint, Job, and CLI
```

The factory reads `instructions.md`, registers capabilities from `tools/`, and
points native SDK skill discovery at `agent/skills/`. Host helpers in `lib/` stay
in the trusted harness. Only selected files from `sandbox/workspace/inputs/` and
the lazily loaded skill enter the Actor. Runtime-created code and output files
stay in its workspace. Evaluation fixtures are imported only for explicit tests.

## Live OpenAI execution

The normal agent path uses a live OpenAI model, Kubernetes compute, and the
configured API endpoint. It requires credentials and never falls back to the
scripted model. For the Kubernetes harness, configure `.env` and run:

```sh
./scripts/local-kubernetes.sh agent-setup
./scripts/local-kubernetes.sh agent-run
```

The [agent setup guide](deploy/README.md) describes API key,
endpoint, model, optional account settings, and Secret mounting. `OPENAI_MODEL`
selects the model; `OPENAI_BASE_URL` selects the endpoint. The live model chooses
tools and writes analysis code. These commands can incur model API usage.

## Tracing

The [Jaeger tracing guide](../../docs/tracing.md) links the real OpenAI model
turns and native tool calls with Actor lifecycle, command execution, and host
artifact verification. Run `./scripts/local-kubernetes.sh tracing-setup` before
`agent-run` to enable OTLP tracing through the harness ConfigMap. Each successful
run records both SDK and OpenTelemetry trace IDs in `verification.json`.

## Interactive UI

`./scripts/local-kubernetes.sh ui` serves a ChatKit conversation with an artifact
panel on <http://localhost:8000>. Each thread is one Actor that the server resumes
for every message and suspends after it, so you can test multi-turn analyses and
inspect each turn's files and Jaeger trace. Attach your own `.md`, `.txt` or `.csv`
documents: they go into the Actor's workspace volume, which lives exactly as long
as the conversation, and the host checks the agent's quotes against them. See the
[UI guide](ui/README.md).

## Skill and tool execution

The trusted host keeps the model connection, provider credentials, input hashes,
and independent artifact checks. The Actor receives the selected input files and
the lazily loaded skill bundle. It has Python and the upstream execution server;
the model API key is never staged in its filesystem or environment. Successful runs
are required to call `load_skill`, `apply_patch`, and `exec_command`.

```python
capabilities = [
    Shell(),
    Filesystem(),
    Skills(lazy_from=LocalDirLazySkillSource(
        source=LocalDir(src=AGENT_ROOT / "skills"),
    )),
]
```

| Tool | Work performed in this example |
| --- | --- |
| `load_skill` | SDK materializes `document-analysis` under `.agents/` and exposes its instructions and resources |
| `exec_command` | Read the skill and output contract; inspect staged reports and SHA-256 manifest; execute the extractor |
| `apply_patch` | Write `work/analyze.py` inside the Actor workspace |
| `exec_command` | Execute that Python code, calculate margins with `Decimal`, and generate report artifacts |
| `exec_command` | Reopen the outputs, verify calculations and exact source quotes, and correct failures if needed |

The [skill](agent/skills/document-analysis/SKILL.md) owns the domain workflow. Its
[helper](agent/skills/document-analysis/scripts/documents.py) checks input bytes and
hashes, extracts exact quotes, and reconciles the KPI table. The agent's generated
code computes operating margins and the revenue gap. A separate
[host verifier](agent/lib/verify.py) checks downloaded artifacts against original host inputs
without executing downloaded code. Checks cover the required structured fields
and report evidence; they do not establish the semantic correctness of arbitrary
additional prose or provide a prompt-injection security guarantee.

## Key-free local execution test

```sh
uv sync --locked
uv run python -m examples.document_intelligence --test
```

This runs the real SDK tool loop with eight fixed tool decisions, including skill
loading, patching, and Python execution. `evals/fixtures/analysis.py` is a code fixture
emitted through `apply_patch`, not an input file pre-staged in the sandbox. There
is no model inference in scripted mode. The local protocol simulator executes on
your host and provides no sandbox isolation; it only accepts scripted mode.

## Deploy and test on local Kubernetes

Set up the dedicated local cluster using the
[local Kubernetes guide](../../docs/local-kubernetes.md), then run:

```sh
./scripts/local-kubernetes.sh document-intelligence
```

This builds and pushes the Python Actor image to the local registry, creates or
reuses a template named from its configuration digest, and runs the same key-free
SDK workflow through authenticated Substrate routing. Commands, generated code,
intermediate files, and artifacts execute inside the real Kubernetes Actor.

To use an existing Substrate cluster and registry instead:

```sh
uv run python -m examples.document_intelligence.deploy sandbox \
  --context YOUR_KUBERNETES_CONTEXT \
  --image YOUR_REGISTRY/document-intelligence:example

# Use the template name printed by the sandbox deployment action.
uv run python -m examples.document_intelligence --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --template document-intelligence-CONFIG_DIGEST --model YOUR_OPENAI_MODEL_ID
```

Pass `--kubeconfig PATH` to both commands when needed. The deployment CLI also accepts
`--docker-context NAME` and `--atespace NAME`. The existing `ate-demo-sandbox`
atespace and `workload=sandbox` worker pool must exist. The upstream
`sandbox-template` has no Python interpreter and cannot run this example.
The Dockerfile pins both base images, and the registered template uses the pushed
image digest. Build on the same architecture as the sandbox workers. Template names change when their configuration changes.

For host execution, set `OPENAI_API_KEY` in your trusted host environment
and choose `OPENAI_MODEL` or pass `--model YOUR_OPENAI_MODEL_ID`. Live mode uses the
same skill, files, tools, and contract checks. It incurs model API usage. Local
scripted tests do not validate live model behavior. Use explicit `--test` to run
a deterministic evaluation. `OPENAI_MODEL=scripted` alone is rejected.

To load endpoint, model, and credentials from `.env` into a Kubernetes-hosted
agent harness instead, use the [Secret-backed agent setup](deploy/README.md).

## Artifacts and retained workspaces

Each successful run creates a unique directory under
`artifacts/examples/document_intelligence/` containing:

- `comparison.md`, `kpis.csv`, and `result.json`: verified domain outputs.
- `analysis.py`: downloaded code written by the agent, retained for inspection.
- `tool-trace.json`: actual SDK tool calls and results for this synthetic task.
- `verification.json`: backend, `execution_mode`, `model_inference`, returned API
  response IDs, tool names, input/output hashes, and contract verification status.
  Scripted evaluations have `execution_mode=scripted-eval`, no API response IDs,
  and `model_inference=false`. A successful live run records `openai-live`.

The local Kubernetes helper instead writes to `artifacts/local/document-intelligence/`.
Expected fixture results: ACME operating margin **22.93%**, BETA **30.19%**,
and revenue gap **USD 117.8 billion**. EBITDA is unavailable.

Actors are deleted by default. Add `--keep-actor` on Substrate to suspend the
workspace and save `session.json`. Inspect it with
`uv run python -m examples.quickstart.resume --context CONTEXT --state-file PATH`,
including the same kubeconfig and atespace when applicable. Do not publish
credentials or session state from real document workflows.

The command-line workflow's input format is bounded fictional Markdown and CSV.
The [UI](ui/README.md) also accepts PDFs, which poppler extracts and renders
inside the Actor for the agent to read and view. Neither implements OCR,
arbitrary financial-document extraction, or financial advice. Extend the skill's
extractor and output checks for another document type.
