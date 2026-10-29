# Document Intelligence

Compare two synthetic annual reports with a skill-driven `SandboxAgent`. The
agent loads a skill, inspects files, extracts and reconciles source facts, writes
Python analysis code, executes it in a Kubernetes-backed Actor, and checks the
resulting Markdown, CSV, and JSON artifacts.

The workflow follows the domain example in
[SatyKrish/bedrock-document-intelligence-agent](https://github.com/SatyKrish/bedrock-document-intelligence-agent/tree/7eb1dfbba95f87512aa20dc3444111983f0f3b3e).
Only its MIT-licensed synthetic fixtures are reused. This implementation uses
OpenAI Agents SDK and Agent Substrate with Kubernetes as the compute provider.
See [fixture provenance](fixtures/PROVENANCE.json) and [license](fixtures/LICENSE).

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
        source=LocalDir(src=EXAMPLE / "skills"),
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

The [skill](skills/document-analysis/SKILL.md) owns the domain workflow. Its
[helper](skills/document-analysis/scripts/documents.py) checks input bytes and
hashes, extracts exact quotes, and reconciles the KPI table. The agent's generated
code computes operating margins and the revenue gap. A separate
[host verifier](verify.py) checks downloaded artifacts against original host inputs
without executing downloaded code. Checks cover the required structured fields
and report evidence; they do not establish the semantic correctness of arbitrary
additional prose or provide a prompt-injection security guarantee.

## Key-free local execution test

```sh
uv sync --locked
uv run python -m examples.document_intelligence
```

This runs the real SDK tool loop with eight fixed tool decisions, including skill
loading, patching, and Python execution. `scripted_analysis.py` is a code fixture
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
uv run python -m examples.document_intelligence.deploy \
  --context YOUR_KUBERNETES_CONTEXT \
  --image YOUR_REGISTRY/document-intelligence:example

# Use the template name printed by deploy.py.
uv run python -m examples.document_intelligence --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --template document-intelligence-CONFIG_DIGEST
```

Pass `--kubeconfig PATH` to both commands when needed. `deploy.py` also accepts
`--docker-context NAME` and `--atespace NAME`. The existing `ate-demo-sandbox`
atespace and `workload=sandbox` worker pool must exist. The upstream
`sandbox-template` has no Python interpreter and cannot run this example.
The Dockerfile pins both base images, and the registered template uses the pushed
image digest. Build on the same architecture as the sandbox workers. Template names change when their configuration changes.

For live model decisions, set `OPENAI_API_KEY` in your trusted host environment
and add `--model YOUR_OPENAI_MODEL_ID` to the Substrate run. Live mode uses the
same skill, files, tools, and contract checks. It incurs model API usage. Local
scripted tests do not validate live model behavior.

To load endpoint, model, and credentials from `.env` into a Kubernetes-hosted
agent harness instead, use the [Secret-backed agent setup](../../deploy/kubernetes/README.md).

## Artifacts and retained workspaces

Each successful run creates a unique directory under
`artifacts/examples/document_intelligence/` containing:

- `comparison.md`, `kpis.csv`, and `result.json`: verified domain outputs.
- `analysis.py`: downloaded code written by the agent, retained for inspection.
- `tool-trace.json`: actual SDK tool calls and results for this synthetic task.
- `verification.json`: backend, model mode, tool names, input/output hashes, and
  contract verification status.

The local Kubernetes helper instead writes to `artifacts/local/document-intelligence/`.
Expected fixture results: ACME operating margin **22.93%**, BETA **30.19%**,
and revenue gap **USD 117.8 billion**. EBITDA is unavailable.

Actors are deleted by default. Add `--keep-actor` on Substrate to suspend the
workspace and save `session.json`. Inspect it with
`uv run python scripts/resume_session.py --context CONTEXT --state-file PATH`,
including the same kubeconfig and atespace when applicable. Do not publish
credentials or session state from real document workflows.

The input format is bounded fictional Markdown and CSV. This example does not
implement PDF parsing, OCR, arbitrary financial-document extraction, or financial
advice. Extend the skill's extractor and output checks for another document type.
