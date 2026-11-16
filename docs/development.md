# Development

Use Python 3.11 or later on macOS or Linux. The [coding agent guide](../AGENTS.md)
maps the code and the contracts to preserve. Install the locked workspace with
the `ui` group for the same dependency coverage as CI.

```sh
uv sync --locked --group ui
```

For provider-only work, `uv sync --locked` omits the `ui` group. Tests that
require the harness dependencies skip. The default suite runs without Kubernetes
or an OpenAI API key and uses loopback sockets for local HTTP integration tests.

## Tests and execution checks

```sh
uv run --locked --group ui ruff check sandbox harness scripts deploy examples testing tests conftest.py
uv run --locked --group ui mypy
uv run --locked --group ui pytest -q
```

Use [provider unit tests](../sandbox/tests/unit/) for lifecycle and proxy
contracts, [provider integration tests](../sandbox/tests/integration/) for HTTP
and SDK tool execution, and [harness tests](../harness/tests/) for conversation
behavior. Each example owns its workflow tests under `evals/`.

### Host protocol simulator

The repository provider check uses a scripted model and a temporary host
workspace in `--backend local` mode. This mode runs commands on your machine
without sandbox isolation. [Provider check modes](../examples/provider_check/README.md)

```sh
uv run --locked python -m examples.provider_check --backend local \
  --output-dir artifacts/local/protocol-simulator
```

### Upstream execution server on the host

Use the upstream Go execution server to check HTTP wire compatibility while
lifecycle operations remain simulated.

```sh
sh scripts/build-sandbox-server.sh "$(cat .substrate-revision)" artifacts/upstream-sandbox
mv artifacts/upstream-sandbox/sandbox-server artifacts/upstream-sandbox-server
SUBSTRATE_DEMO_BINARY="$PWD/artifacts/upstream-sandbox-server" uv run pytest -q
uv run python -m examples.provider_check --backend local \
  --upstream-binary "$PWD/artifacts/upstream-sandbox-server" \
  --output-dir artifacts/upstream
```

The script downloads the v1.0.0 `demos/sandbox` package at the pinned commit
through the Go module proxy and compiles it alongside the project's eviction
notice handler, just as the sandbox images do. The server briefly listens on
the selected port on all host interfaces. Run this compatibility check only on
a trusted development machine.

## Real Kubernetes tests

The key-free Kind check runs SDK commands in real gVisor-backed Actors and
verifies that their files survive suspend and resume. The pytest case is opt-in
and uses an explicit Kind context and kubeconfig. A separate worker-eviction
test verifies recovery on a replacement worker.
[Set up Kind](local-kubernetes.md#set-up) · [Run the provider and Kubernetes checks](local-kubernetes.md#test) · [Test worker eviction](local-kubernetes.md#test-worker-eviction)

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

The build packages the provider library, tracing support, and license notices.
The harness, guides, examples, tests, `testing/` helpers, and upstream execution
server stay in the repository.

The package CI also installs the base wheel in a fresh environment and runs
`sandbox/tests/packaging/core_smoke.py`. It verifies provider import and sandbox execution
without the OpenTelemetry SDK or OTLP exporter, against the checkout's local stand-in; the upstream SDK may still bring
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

Keep unsupported capabilities explicit and preserve compatibility with saved
sessions. Update [architecture and limits](architecture.md) when behavior changes.
Use the repository-only scripted models, server, and harness fixtures from
[`testing/`](../testing/). See the [contribution guide](../CONTRIBUTING.md)
for review guidance and the [verification record](verification.md) for results.

## CI and pinned tools

CI installs Go from `.go-version` using a commit-pinned setup action. The normal
Python matrix runs the full suite once; the Go-server pass runs the protocol and
document execution checks rather than repeating unrelated unit tests. Ruff checks
syntax and Pyflakes rules across authored Python; mypy checks the typed transport,
proxy, and lifecycle modules. These focused type checks do not cover every SDK
adapter or example module.

For a real cluster run on GitHub, manually dispatch CI with the `kubernetes`
boolean enabled. That separate, bounded job installs Kind/Substrate, tests the
provider and document workflow, runs the Secret-mounted harness and worker
eviction checks, and collects artifacts. It uses an empty-key configuration
and explicit evaluation mode, so it requires no OpenAI credential.

The Substrate commit used by CI, local setup, and the sandbox and harness images
is centralized in `.substrate-revision`.
Kubectl's version and architecture hashes are committed in
`examples/document_intelligence/deploy/kubectl-checksums.json`; updates must replace those pins along
with validation of the new binaries.
