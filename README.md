# openai-agent-substrate

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/SatyKrish/openai-agent-substrate/actions/workflows/ci.yml/badge.svg)](https://github.com/SatyKrish/openai-agent-substrate/actions/workflows/ci.yml)

A community reference implementation of **Kubernetes as a sandbox compute provider for OpenAI Agents SDK `SandboxAgent`**, using Agent Substrate to manage sandboxed Actors and workers. The SDK harness runs in your trusted application; shell commands and workspace files run in the Actor.

## Harness separate from compute

This project follows the [harness separate from compute pattern](https://developers.openai.com/api/docs/guides/agents/sandboxes) in the official OpenAI Sandbox Agents guide.

![Harness separate from compute: trusted agent orchestration calls sandbox compute for shell execution and filesystem work.](docs/assets/harness-separate-from-compute.png)

*Diagram from the [official OpenAI documentation](https://developers.openai.com/api/docs/guides/agents/sandboxes).*

`SubstrateSandboxClient` connects the SDK sandbox interface to the Kubernetes execution environment. Agent Substrate manages Actor lifecycle and routes execution requests to each workspace. See the [architecture and supported capabilities](docs/architecture.md).

The Document Intelligence agent uses the configured OpenAI model for normal
execution. `--test` and `agent-test` select deterministic execution evaluations.
See [its agent layout and live run guide](examples/document_intelligence/README.md).

The wheel installs the provider library and reusable testing helpers.
From a checkout, `pip install ./sandbox` installs the provider without the OpenTelemetry
SDK or OTLP exporter; use `pip install './sandbox[tracing]'` for OTLP export. Run the
[quickstart](examples/quickstart/README.md) from a source checkout; the former
`openai-agent-substrate` console command has been removed.

## Quick start

Requires Python 3.11+, macOS or Linux, and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/SatyKrish/openai-agent-substrate.git
cd openai-agent-substrate
uv sync --locked
uv run python -m examples.quickstart --backend local
```

The key-free demo invokes the real SDK runner with a scripted model, writes a report, serializes the session, reconnects, and verifies that edits survived. Results appear in `artifacts/`.

**Explicit `--backend local` mode is a protocol simulator that executes fixed commands on your host. It provides no sandbox isolation.** It requires no API key or cluster, and the CLI restricts it to the scripted model. The CLI defaults to Substrate and requires an explicit Kubernetes context.

## Run on Kubernetes

For a complete local setup and test, run `./scripts/local-kubernetes.sh setup` followed by `./scripts/local-kubernetes.sh test`. See the [local Kubernetes guide](docs/local-kubernetes.md).

Prepare a Substrate cluster and its upstream `sandbox-template` using the [deployment guide](docs/deployment.md), then run:

```sh
uv run python -m examples.quickstart --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --atespace ate-demo-sandbox --template sandbox-template
```

This creates a uniquely named Actor, exercises shell execution and suspend/resume, and deletes the Actor when finished. Add `--keep-actor` to retain the workspace and save session state. For live model execution, set the API key in the host environment and select a model with `--model YOUR_OPENAI_MODEL_ID`.

The provider is pinned to `openai-agents==0.24.0`. Real Actor execution, authenticated routing, suspend/resume, and external snapshot recovery have been verified on local ARM64 Kind with gVisor. A live Document Intelligence run with `gpt-6.1-terra` also passed through a Secret-backed Kubernetes harness and Substrate Actor. On the shared agent harness, nine live conversations started together all passed their checks, the one beyond the cluster's capacity waited for a sandbox worker instead of failing, and the SRE agent's findings matched the evidence it read. Production isolation properties remain unverified. See the [verification record](docs/verification.md).

## OpenAI configuration in a Kubernetes agent pod

Copy `.env.example` to `.env` and set `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and
`OPENAI_MODEL`, with optional organization/project IDs. During setup, `.env` is
imported into a Kubernetes Secret. The dedicated agent harness pod receives it as
environment variables and read-only files; sandbox Actors receive no credentials.

```sh
cp .env.example .env
# Edit .env locally.
./scripts/local-kubernetes.sh setup
./scripts/local-kubernetes.sh agent-setup
./scripts/local-kubernetes.sh agent-test       # scripted; no model API calls
./scripts/local-kubernetes.sh agent-run        # live model from .env
./scripts/local-kubernetes.sh agent-artifacts  # download reports from the PVC
```

See the [Kubernetes agent setup guide](examples/document_intelligence/deploy/README.md) for configuration
semantics, Secret mounting, permissions, and artifact retention. `agent-run`
refreshes the Secret and starts a fresh Job, applying endpoint and model changes.

## Document Intelligence example

The [Document Intelligence example](examples/document_intelligence/README.md) loads
an SDK skill, inspects synthetic annual reports, writes and executes Python in the
sandbox, and generates cited Markdown, CSV, and JSON artifacts. Independent host
checks reconcile the downloaded results with original inputs.

```sh
# Key-free execution test on the host protocol simulator (no isolation)
uv run python -m examples.document_intelligence --test

# After local Kubernetes setup: build the Python Actor image, deploy, and test
./scripts/local-kubernetes.sh document-intelligence
```

Both commands use fixed model decisions with real SDK tool execution. Select a
live OpenAI model on Kubernetes using the example's documented `--model` option.

For multi-turn testing with the live model, `./scripts/local-kubernetes.sh ui`
serves a ChatKit conversation beside an artifact panel on <http://localhost:8000>.
Each conversation is one Actor with its own workspace volume: the first message
creates it, later messages resume it, and deleting the conversation deletes it.
Attach PDF, Markdown, text or CSV files and inspect every turn's files and Jaeger
trace. PDFs are extracted and rendered inside the Actor, and the agent's quotes
are checked against the cited page. See the
[UI guide](examples/document_intelligence/ui/README.md).

## More agents on one harness

The UI runs on a [shared agent harness](docs/agent-harness.md): the agent loop
runs in the trusted harness and tools run in the agent's sandbox, so a new agent
is a `SandboxAgent`, a small spec, a sandbox image and its own UI. The
[SRE agent](examples/sre/README.md) is the second one. It reads the cluster
through read-only harness tools, saves each read as evidence for its sandbox to
analyze, and the host checks every quote in its findings against that evidence.

```sh
./scripts/local-kubernetes.sh sre-setup
./scripts/local-kubernetes.sh sre-ui      # http://localhost:8001
```

## Tracing

Export SDK model/tool spans and sandbox execution/lifecycle spans to local Jaeger:

```sh
./scripts/local-kubernetes.sh tracing-setup
./scripts/local-kubernetes.sh agent-run
./scripts/local-kubernetes.sh tracing-ui
```

The [tracing guide](docs/tracing.md) covers configuration, trace verification,
span content, and the loopback UI. Rebuild the agent Job image with `agent-setup` after
updating source. The exporter stays in the trusted harness.

## Repository layout

```text
pyproject.toml              uv workspace root: dev tools and test settings, no package
sandbox/                    The provider library (openai-agent-substrate), the only published wheel
  src/openai_agent_substrate/  SDK provider, transport, lifecycle, tracing
    testing/                Scripted model and loopback execution helpers
harness/                    Shared agent harness package: the agent loop, one sandbox per conversation
  src/harness/              ChatKit runtime, uploads, evidence, checks, routes, test helpers
examples/                   The agents that run on the harness, and the provider quickstart
tests/                      Provider unit and integration tests, deployment and boundary checks
docs/                       Architecture, deployment, usage, and development guides
  assets/                   Documentation images
scripts/                    Local Kind bootstrap and CLI dispatch
deploy/kubernetes/          Reusable permissions, connection settings, PVC, and sandbox templates
third_party/                Pinned upstream execution server and its license
.github/                    CI, dependency updates, and contribution templates
```

## Documentation

- [Architecture and limits](docs/architecture.md) and the [agent harness](docs/agent-harness.md)
- [Local Kubernetes setup and tests](docs/local-kubernetes.md)
- [Existing-cluster setup](docs/deployment.md)
- [SDK integration and session resume](docs/usage.md)
- [Tracing with Jaeger](docs/tracing.md)
- [Runnable examples](examples/README.md), the [Document Intelligence UI](examples/document_intelligence/ui/README.md) and the [SRE agent](examples/sre/README.md)
- [Shared harness package](harness/README.md)
- [Development](docs/development.md) and the [verification record](docs/verification.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), use the [issue tracker](https://github.com/SatyKrish/openai-agent-substrate/issues) for bugs and proposals, and follow [SECURITY.md](SECURITY.md) for vulnerability reports.

## License

Original project code and documentation are licensed under [Apache-2.0](LICENSE). Third-party code and the OpenAI diagram retain their respective rights and attribution; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
