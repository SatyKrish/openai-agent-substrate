# Development

Use Python 3.11 or later on macOS or Linux. Install the project and its development tools with:

```sh
uv sync --locked
```

The default tests and demo use a deterministic model and temporary local workspaces. They require no OpenAI API key or Kubernetes cluster. Loopback sockets must be allowed in your development environment.

## Tests and execution checks

```sh
uv run ruff check src scripts deploy examples tests conftest.py
uv run mypy
uv run pytest -q
uv run python -m examples.quickstart --backend local --output-dir artifacts/local
```

Tests cover native skill loading, domain code execution, exact citations and artifact reconciliation, the actual SDK tool loop, filesystem patching, binary/chunked files, shell output/exit codes, routing headers, state serialization and resume, lost Actors, unsupported manifest options, file limits, and non-replay of failed executions.

For wire compatibility with the actual upstream Go execution server:

```sh
mkdir -p artifacts
go build -o artifacts/upstream-sandbox-server third_party/substrate-sandbox/main.go
SUBSTRATE_DEMO_BINARY="$PWD/artifacts/upstream-sandbox-server" uv run pytest -q
uv run python -m examples.quickstart --backend local \
  --upstream-binary "$PWD/artifacts/upstream-sandbox-server" \
  --output-dir artifacts/upstream
```

This runs the actual upstream HTTP execution code locally. Lifecycle operations remain simulated; it does not prove Kubernetes suspend/resume. The upstream server briefly listens on the selected port on all host interfaces, matching its source. Run this compatibility check only on a trusted development machine.

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
uv build --no-sources
uv run --locked twine check dist/*
```

The wheel contains the SDK adapter, tracing, testing helpers, and license notices. It installs no console command or example application. The source distribution also contains the guides, examples, tests, diagram, and upstream execution server.

## Dependency boundaries and manifest contracts

`tests/deploy/` checks reusable base resources and dependency direction. Library
tests stay under `tests/unit`, `tests/integration` and `tests/kubernetes` and do
not import example applications. Example checks live under their `evals/`
folders, including the Document Intelligence Kubernetes checks.

Base, agent Job, reader and Jaeger builder outputs are compared against saved
pre-refactor manifests. These JSON files are test expectations; production code
does not read them. Review intentional manifest changes alongside the expected
output. The wheel ships only `src/`; the source distribution includes deployment
modules, examples, tests and guides. No separate example pyproject or workspace
is required while its dependencies match the library.

## Working on the provider

- Keep Actor lifecycle and credentials in the trusted application.
- Implement backend behavior in `src/openai_agent_substrate/`.
- Keep domain agent definitions and skills under `examples/DOMAIN/agent/` and their evaluations under sibling `evals/`; keep generic session utilities under `scripts/`.
- Keep provider scripted models and loopback emulation under `src/openai_agent_substrate/testing/`.
- Put pure lifecycle tests in `tests/unit/` and HTTP/SDK contract tests in `tests/integration/`.
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
