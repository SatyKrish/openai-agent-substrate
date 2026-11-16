# openai-agent-substrate

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/SatyKrish/openai-agent-substrate/actions/workflows/ci.yml/badge.svg)](https://github.com/SatyKrish/openai-agent-substrate/actions/workflows/ci.yml)

Run [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/) sandbox tools on Kubernetes with [Agent Substrate v1.0.0](https://github.com/agent-substrate/substrate/releases/tag/v1.0.0). Your application runs the agent and holds its model credentials and conversation history. Substrate runs shell commands and keeps workspace files in an Actor that can suspend and resume.

## Start here

- **Already have an SDK app?** [Connect it to a Substrate cluster](#use-the-provider-in-your-own-application).
- **Want to see the integration work?** [Run the key-free check on local Kind](#quick-start).

## Use the provider in your own application

Start with a Substrate v1.0.0 cluster, the matching `kubectl ate` plugin on
`PATH`, and an ActorTemplate that serves the sandbox execution API. The
integration example uses `sandbox-template` in `ate-demo-sandbox`.
[Existing-cluster setup](docs/deployment.md#existing-substrate-cluster) · [Set up local Kind](#quick-start)

The provider requires Python 3.11 or newer and pins `openai-agents==0.24.0`.
Install it into your application's Python environment from the repository checkout.

```sh
git clone https://github.com/SatyKrish/openai-agent-substrate.git
cd openai-agent-substrate
pip install ./sandbox
```

Create a session with `SubstrateSandboxClient`. Inside the session's
`async with` block, pass it to the runner with your existing `SandboxAgent`.

```python
result = await Runner.run(
    agent,
    message,
    run_config=RunConfig(sandbox=SandboxRunConfig(session=session)),
)
```

Closing the session suspends the Actor. The full example configures the
client, runs the agent, serializes the session state to JSON, and verifies the
same file after resume. [Run the Python integration example](docs/usage.md#integrate-into-your-application)

## Quick start

Try the provider with a scripted model on a real gVisor-backed Actor. The
check needs no OpenAI API key. Use macOS with Colima or Linux with Docker,
plus the Docker CLI, Git, Go, kubectl, uv, and internet access.
[Local requirements](docs/local-kubernetes.md#requirements)

```sh
git clone https://github.com/SatyKrish/openai-agent-substrate.git
cd openai-agent-substrate
./scripts/local-kubernetes.sh setup
./scripts/local-kubernetes.sh test
```

Look for `"workspace_survived_resume": true` in the output and in
`artifacts/local/results/verification.json`. The check creates an Actor,
runs an SDK agent, suspends and resumes the Actor, then deletes it.

The Kind guide covers cluster cleanup and the optional worker-drain test,
which verifies recovery on a replacement worker.
[Local Kind guide](docs/local-kubernetes.md) · [Deployment diagram](docs/local-kubernetes.md#how-setup-works)

## Harness separate from compute

The provider connects the SDK to Actor lifecycle and execution. The trusted
application retains conversation state, calls the model, and runs any
credentialed tools. [Architecture and supported capabilities](docs/architecture.md)

![OpenAI architecture diagram showing a trusted harness connected to sandbox compute](docs/assets/harness-separate-from-compute.png)

*Source: [OpenAI Sandbox Agents guide](https://developers.openai.com/api/docs/guides/agents/sandboxes).*

## Examples

The examples use the same provider and a shared harness. Each has its own
setup and test instructions. [Browse all examples](examples/README.md)

- **Starter** gives each chat a persistent workspace. [Starter guide](examples/starter/README.md)
- **Document Intelligence** checks generated reports against their sources. [Document Intelligence guide](examples/document_intelligence/README.md)
- **SRE** collects read-only cluster evidence in the trusted harness. [SRE guide](examples/sre/README.md)

### Run Starter with a live model

After Kind setup, copy the example environment file and set `OPENAI_API_KEY`
and `OPENAI_MODEL` in `.env`.

```sh
cp .env.example .env
```

Then build the sandbox and start the terminal chat. The Starter guide also
covers the optional browser UI.

```sh
./scripts/local-kubernetes.sh starter-setup
./scripts/local-kubernetes.sh starter-chat
```

## Verification

The provider check passed on Agent Substrate v1.0.0 with gVisor on ARM64 Kind.
The Python example restored the same Actor from a clean wheel install with a
scripted model. The separate worker-drain test interrupted an active SDK
command and restored its workspace on a replacement worker.
[Verification record](docs/verification.md#agent-substrate-v100) · [Scope and limits](docs/verification.md#limits-of-this-verification)

## Contributing

Use the development guide for the provider test suite, package builds, and the
host protocol simulator. For traces from the SDK and sandbox, see the Jaeger
setup. [Contributor guide](CONTRIBUTING.md) · [Development](docs/development.md) · [Tracing](docs/tracing.md) · [Documentation index](docs/README.md)

Report bugs through GitHub Issues and vulnerabilities through the private
reporting channel. [Issue tracker](https://github.com/SatyKrish/openai-agent-substrate/issues) · [Security policy](SECURITY.md)

## License

Original code and documentation are licensed under Apache-2.0. The OpenAI diagram and other
third-party material retain their original rights and notices.
[License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
