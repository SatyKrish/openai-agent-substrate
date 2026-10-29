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

Tests cover the actual SDK tool loop, filesystem patching, binary/chunked files, shell output/exit codes, routing headers, state serialization and resume, lost Actors, unsupported manifest options, file limits, and non-replay of failed executions.

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

## Build distributions

```sh
uv build --no-sources
uv run --locked twine check dist/*
```

The wheel contains the SDK adapter, CLI, testing helpers, and license notices. The source distribution also contains the guides, examples, tests, diagram, and upstream execution server.

## Working on the provider

- Keep Actor lifecycle and credentials in the trusted application.
- Implement backend behavior in `src/openai_agent_substrate/`.
- Keep scripted models and loopback emulation under `src/openai_agent_substrate/testing/`.
- Put pure lifecycle tests in `tests/unit/` and HTTP/SDK contract tests in `tests/integration/`.
- Keep unsupported capabilities explicit and preserve saved-session compatibility.
- Update [architecture and limits](architecture.md) when behavior changes.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution and review guidance, and [verification.md](verification.md) for the checks actually performed.
