# Development

Use Python 3.11 or later on macOS or Linux. The repository is a uv workspace
with two packages: the provider in `sandbox/` (`openai-agent-substrate`, imported
as `agent_substrate`) and the shared agent harness in `harness/` (`openai-harness`,
imported as `openai_harness`). Install the provider and the development tools with:

```sh
uv sync --locked
```

The harness, the agents' terminal chats and UIs, and their tests need the `ui` dependency group,
which installs the harness package with ChatKit, Starlette and uvicorn. CI
installs it. Without it, those tests are skipped:

```sh
uv sync --locked --group ui
```

The default tests and the provider check use a deterministic model and temporary local workspaces. They require no OpenAI API key or Kubernetes cluster. Loopback sockets must be allowed in your development environment.

## Tests and execution checks

```sh
uv run ruff check sandbox harness scripts deploy examples tests conftest.py
uv run mypy
uv run pytest -q
uv run python -m examples.provider_check --backend local --output-dir artifacts/local
```

Tests cover native skill loading, domain code execution, exact citations and artifact reconciliation, the actual SDK tool loop, filesystem patching, binary files and batched multi-command transfers, shell output/exit codes, routing headers, state serialization and resume, lost Actors, unsupported manifest options, file limits, and non-replay of failed executions.

The harness tests (`harness/tests/`) cover two messages sent to one conversation
at once, waiting for and timing out on a full worker pool, history after a
failed turn, evidence from parallel harness tools, and the terminal chat (one
question, an interactive session, and stripped control characters). The starter
agent's tests run a turn end to end, check its host check and sandbox profile,
and confirm its terminal chat and UI share a conversation. The SRE agent's tests
cover its tool allowlists, the reader's Role, the evidence check, and an
investigation end to end with a fake cluster. Agents' tests build on
`openai_harness.testing`.

For wire compatibility with the actual upstream Go execution server:

```sh
sh scripts/build-sandbox-server.sh "$(cat .substrate-revision)" artifacts/upstream-sandbox
mv artifacts/upstream-sandbox/sandbox-server artifacts/upstream-sandbox-server
SUBSTRATE_DEMO_BINARY="$PWD/artifacts/upstream-sandbox-server" uv run pytest -q
uv run python -m examples.provider_check --backend local \
  --upstream-binary "$PWD/artifacts/upstream-sandbox-server" \
  --output-dir artifacts/upstream
```

The script downloads upstream's `demos/sandbox` package at the pinned commit through the Go module proxy, the same source the sandbox images build. This runs the actual upstream HTTP execution code locally. Lifecycle operations remain simulated; it does not prove Kubernetes suspend/resume. The upstream server briefly listens on the selected port on all host interfaces, matching its source. Run this compatibility check only on a trusted development machine.

## Real Kubernetes tests

See [local-kubernetes.md](local-kubernetes.md) for the opt-in cluster test. It is skipped by default and requires an explicit local Kind context and kubeconfig.

The domain example adds an opt-in test after its Python image is deployed:

```sh
./scripts/local-kubernetes.sh document-intelligence
```

This command builds the image, registers the immutable template, runs the domain
workflow, and runs `examples/document_intelligence/evals/kubernetes/test_document_intelligence.py`. It uses the
same dedicated local context and kubeconfig as the provider tests.

The optional harness-pod test uses a temporary dummy-key Secret with an invalid
endpoint and an explicit `--test` override. It verifies mounted files, explicit evaluation
selection, real Actor execution, and absence of harness credentials in the
Actor. Build the harness first, then explicitly select the local cluster:

```sh
SUBSTRATE_RUN_AGENT_POD_TESTS=1 \
SUBSTRATE_KUBE_CONTEXT=kind-openai-agent-substrate \
SUBSTRATE_KUBECONFIG="$PWD/artifacts/local/kubeconfig" \
SUBSTRATE_DOCUMENT_TEMPLATE=YOUR_PYTHON_TEMPLATE \
uv run pytest -q examples/document_intelligence/evals/kubernetes/test_agent_configuration.py
```

It deletes its temporary Job and Secret after the check; artifacts remain on the
harness PVC. No live OpenAI call is required.

## Jaeger integration test

Build the harness and deploy Jaeger with `agent-setup` and `tracing-setup`.
Keep `tracing-ui` running in another terminal, then run:

```sh
SUBSTRATE_RUN_JAEGER_TESTS=1 \
SUBSTRATE_KUBE_CONTEXT=kind-openai-agent-substrate \
SUBSTRATE_KUBECONFIG="$PWD/artifacts/local/kubeconfig" \
SUBSTRATE_DOCUMENT_TEMPLATE=YOUR_PYTHON_TEMPLATE \
uv run --locked pytest -q examples/document_intelligence/evals/kubernetes/test_jaeger_tracing.py
```

This uses a temporary dummy-key Secret and explicit scripted evaluation. It
queries Jaeger to validate SDK tool/provider span linkage without model calls.
The opt-in Kubernetes CI job includes this check. The regular suite tests the
processor in memory without a collector. See [the tracing guide](tracing.md).

## Build distributions

```sh
uv build --no-sources --package openai-agent-substrate --out-dir dist
uv run --locked twine check dist/*
```

The repository is a uv workspace with two packages: the provider library in
`sandbox/` (`openai-agent-substrate`) and the shared agent harness in `harness/`
(`openai-harness`). Only the provider is meant for publishing.
Its wheel and source distribution contain the SDK adapter, tracing, testing
helpers, and license notices; they install no console command, harness or
example application. Guides, examples, tests and the upstream execution server
stay in the repository. `uv sync --locked` installs the provider for development;
`--group ui` adds the harness and its ChatKit dependencies.

The package CI also installs the base wheel in a fresh environment and runs
`sandbox/tests/packaging/core_smoke.py`. It verifies provider import and sandbox execution
without the OpenTelemetry SDK or OTLP exporter; the upstream SDK may still bring
the OpenTelemetry API through MCP. Development dependencies include the exporter
for tracing tests and source examples.

## Dependency boundaries and manifest contracts

`tests/deploy/` checks reusable base resources and dependency direction. Library
tests stay under `sandbox/tests/` (`unit`, `integration`, `kubernetes`) and do
not import example applications. Example checks live under their `evals/`
folders, including the Document Intelligence Kubernetes checks.

Base, agent Job, reader and Jaeger builder outputs are compared against saved
pre-refactor manifests. These JSON files are test expectations; production code
does not read them. Review intentional manifest changes alongside the expected
output. Boundary tests keep the layers apart: the provider imports neither the
harness nor repository code, and the harness imports neither the examples nor
`deploy/`. The examples need no project file of their own; they run from the
workspace root with its environment.

## Working on the provider

- Keep Actor lifecycle and credentials in the trusted application.
- Implement backend behavior in `sandbox/src/agent_substrate/`, and agent-runtime behavior in `harness/src/openai_harness/`.
- Keep domain agent definitions and skills under `examples/DOMAIN/agent/` and their evaluations under sibling `evals/`.
- Keep runnable session utilities under `examples/provider_check/` and shared Kubernetes setup under `deploy/kubernetes/base/`. `scripts/` contains only the shell bootstrap.
- Keep provider scripted models and loopback emulation under `sandbox/src/agent_substrate/testing/`, and agent test helpers in `openai_harness.testing`.
- Put pure lifecycle tests in `sandbox/tests/unit/` and HTTP/SDK contract tests in `sandbox/tests/integration/`; harness tests go in `harness/tests/`, and repository-wide checks (deployment resources, layer boundaries) in `tests/`.
- Keep unsupported capabilities explicit and preserve saved-session compatibility.
- Update [architecture and limits](architecture.md) when behavior changes.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution and review guidance, and [verification.md](verification.md) for the checks actually performed.

## CI and pinned tools

CI installs Go from `.go-version` using a commit-pinned setup action. The normal
Python matrix runs the full suite once; the Go-server pass runs the protocol and
document execution checks rather than repeating unrelated unit tests. Ruff checks
syntax and Pyflakes rules across authored Python; mypy checks the typed transport,
proxy, and lifecycle modules. These focused type checks do not cover every SDK
adapter or example module.

For a real cluster run on GitHub, manually dispatch CI with the `kubernetes`
boolean enabled. That separate, bounded job installs Kind/Substrate, tests the
provider and document workflow, runs the Secret-mounted harness checks, and
collects artifacts. It uses an empty-key configuration and explicit evaluation
mode, so it requires no OpenAI credential. The new dispatch job has been configured
but has not been executed on GitHub in this session.

The executable Substrate revision is centralized in `.substrate-revision`.
Kubectl's version and architecture hashes are committed in
`examples/document_intelligence/deploy/kubectl-checksums.json`; updates must replace those pins along
with validation of the new binaries.
