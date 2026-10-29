# Development

Use Python 3.11 or later on macOS or Linux. Install the project and its development tools with:

```sh
uv sync --locked
```

The default tests and demo use a deterministic model and temporary local workspaces. They require no OpenAI API key or Kubernetes cluster. Loopback sockets must be allowed in your development environment.

## Tests and execution checks

```sh
uv run pytest -q
uv run openai-agent-substrate --output-dir artifacts/local
```

Tests cover native skill loading, domain code execution, exact citations and artifact reconciliation, the actual SDK tool loop, filesystem patching, binary/chunked files, shell output/exit codes, routing headers, state serialization and resume, lost Actors, unsupported manifest options, file limits, and non-replay of failed executions.

For wire compatibility with the actual upstream Go execution server:

```sh
mkdir -p artifacts
go build -o artifacts/upstream-sandbox-server third_party/substrate-sandbox/main.go
SUBSTRATE_DEMO_BINARY="$PWD/artifacts/upstream-sandbox-server" uv run pytest -q
uv run openai-agent-substrate \
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
workflow, and runs `tests/kubernetes/test_document_intelligence.py`. It uses the
same dedicated local context and kubeconfig as the provider tests.

The optional harness-pod test uses a temporary dummy-key Secret with an invalid
endpoint and `OPENAI_MODEL=scripted`. It verifies mounted files, environment-based
model selection, real Actor execution, and absence of harness credentials in the
Actor. Build the harness first, then explicitly select the local cluster:

```sh
SUBSTRATE_RUN_AGENT_POD_TESTS=1 \
SUBSTRATE_KUBE_CONTEXT=kind-openai-agent-substrate \
SUBSTRATE_KUBECONFIG="$PWD/artifacts/local/kubeconfig" \
SUBSTRATE_DOCUMENT_TEMPLATE=YOUR_PYTHON_TEMPLATE \
uv run pytest -q tests/kubernetes/test_agent_configuration.py
```

It deletes its temporary Job and Secret after the check; artifacts remain on the
harness PVC. No live OpenAI call is required.

## Build distributions

```sh
uv build --no-sources
uv run --locked twine check dist/*
```

The wheel contains the SDK adapter, CLI, testing helpers, and license notices. The source distribution also contains the guides, examples, tests, diagram, and upstream execution server.

## Working on the provider

- Keep Actor lifecycle and credentials in the trusted application.
- Implement backend behavior in `src/openai_agent_substrate/`.
- Keep domain examples and their reusable skills under `examples/DOMAIN/`; keep generic session utilities under `scripts/`.
- Keep provider scripted models and loopback emulation under `src/openai_agent_substrate/testing/`.
- Put pure lifecycle tests in `tests/unit/` and HTTP/SDK contract tests in `tests/integration/`.
- Keep unsupported capabilities explicit and preserve saved-session compatibility.
- Update [architecture and limits](architecture.md) when behavior changes.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution and review guidance, and [verification.md](verification.md) for the checks actually performed.
