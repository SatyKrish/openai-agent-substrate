# openai-agent-substrate

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/SatyKrish/openai-agent-substrate/actions/workflows/ci.yml/badge.svg)](https://github.com/SatyKrish/openai-agent-substrate/actions/workflows/ci.yml)

A community reference implementation of **Kubernetes as a sandbox compute provider for OpenAI Agents SDK `SandboxAgent`**, using Agent Substrate to manage sandboxed Actors and workers. The SDK harness runs in your trusted application; shell commands and workspace files run in the Actor.

## Harness separate from compute

This project follows the [harness separate from compute pattern](https://developers.openai.com/api/docs/guides/agents/sandboxes) in the official OpenAI Sandbox Agents guide.

![Harness separate from compute: trusted agent orchestration calls sandbox compute for shell execution and filesystem work.](docs/assets/harness-separate-from-compute.png)

*Diagram from the [official OpenAI documentation](https://developers.openai.com/api/docs/guides/agents/sandboxes).*

`SubstrateSandboxClient` connects the SDK sandbox interface to the Kubernetes execution environment. Agent Substrate manages Actor lifecycle and routes execution requests to each workspace. See the [architecture and supported capabilities](docs/architecture.md).

## Quick start

Requires Python 3.11+, macOS or Linux, and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/SatyKrish/openai-agent-substrate.git
cd openai-agent-substrate
uv sync --locked
uv run openai-agent-substrate
```

The key-free demo invokes the real SDK runner with a scripted model, writes a report, serializes the session, reconnects, and verifies that edits survived. Results appear in `artifacts/`.

**The default local mode is a protocol simulator that executes fixed commands on your host. It provides no sandbox isolation.** It requires no API key or cluster, and the CLI restricts it to the scripted model.

## Run on Kubernetes

For a complete local setup and test, run `./scripts/local-kubernetes.sh setup` followed by `./scripts/local-kubernetes.sh test`. See the [local Kubernetes guide](docs/local-kubernetes.md).

Prepare a Substrate cluster and its upstream `sandbox-template` using the [deployment guide](docs/deployment.md), then run:

```sh
uv run openai-agent-substrate --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --atespace ate-demo-sandbox --template sandbox-template
```

This creates a uniquely named Actor, exercises shell execution and suspend/resume, and deletes the Actor when finished. Add `--keep-actor` to retain the workspace and save session state. For live model execution, set the API key in the host environment and select a model with `--model YOUR_OPENAI_MODEL_ID`.

The provider is pinned to `openai-agents==0.24.0`. Real Actor execution, authenticated routing, suspend/resume, and external snapshot recovery have been verified on local ARM64 Kind with gVisor. Live OpenAI model calls and production isolation properties remain unverified. See the [verification record](docs/verification.md).

## Repository layout

```text
src/openai_agent_substrate/  SDK provider, transport, lifecycle, and CLI
  testing/                  Scripted model and loopback execution helpers
examples/                   Runnable SDK and retained-session examples
tests/                      Unit and integration tests
docs/                       Architecture, deployment, usage, and development guides
  assets/                   Documentation images
scripts/                    Repeatable local Kubernetes setup and tests
third_party/                Pinned upstream execution server and its license
.github/                    CI, dependency updates, and contribution templates
```

## Documentation

- [Architecture and limits](docs/architecture.md)
- [Local Kubernetes setup and tests](docs/local-kubernetes.md)
- [Existing-cluster setup](docs/deployment.md)
- [SDK integration and session resume](docs/usage.md)
- [Runnable examples](examples/README.md)
- [Development and verification](docs/development.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), use the [issue tracker](https://github.com/SatyKrish/openai-agent-substrate/issues) for bugs and proposals, and follow [SECURITY.md](SECURITY.md) for vulnerability reports.

## License

Original project code and documentation are licensed under [Apache-2.0](LICENSE). Third-party code and the OpenAI diagram retain their respective rights and attribution; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
