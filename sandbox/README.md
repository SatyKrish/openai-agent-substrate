# openai-agent-substrate

A reference implementation of **Kubernetes as a sandbox compute provider for the
OpenAI Agents SDK `SandboxAgent`**, using [Agent Substrate](https://github.com/agent-substrate/substrate)
to run each sandbox as an Actor. The SDK's agent loop stays in your trusted
application; shell commands and workspace files run in the Actor, which holds
no credentials.

This provider targets [Agent Substrate v1.0.0](https://github.com/agent-substrate/substrate/releases/tag/v1.0.0)
with the upstream sandbox demo's HTTP execution server. It requires Python 3.11
or newer and pins `openai-agents==0.24.0`.

Install from the repository root.

```sh
pip install ./sandbox
```

For the optional OTLP exporter, use `pip install './sandbox[tracing]'`.
The package installs as `openai-agent-substrate` and is imported as `agent_substrate`.

Create a session with `SubstrateSandboxClient` and pass
`SandboxRunConfig(session=session)` to your SDK runner. Closing the session
suspends the Actor synchronously. Save its JSON state after closing, restore
the session with `client.resume`, and call `client.delete` when your
application no longer needs the workspace.
[Complete Python example](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/usage.md#integrate-into-your-application)

The client reaches Actors through `kubectl ate proxy` with your kubeconfig. A
harness running in the cluster passes `router_url`, `router_tls=RouterTLS(...)`
and `KubectlActorControl(..., endpoint=..., token_file=...)` instead, so it calls
Substrate's API and router directly as its own ServiceAccount.
[In-cluster harness configuration](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/usage.md#connect-an-in-cluster-harness)

The package contains the provider (`SubstrateSandboxClient`, Actor lifecycle
through `kubectl ate`, the authenticated Actor proxy, direct router mutual TLS, tracing).
Its test doubles, a scripted model and a local protocol simulator, stay in the
repository.

The [repository](https://github.com/SatyKrish/openai-agent-substrate) also holds
a shared agent harness, three agents built on it (including a starter to copy), local Kind setup and the
verification record. Start with:

- [Architecture and limits](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/architecture.md)
- [Usage: SDK integration and session resume](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/usage.md)
- [Deployment on an existing Substrate cluster](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/deployment.md)
- [Agent harness](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/agent-harness.md)

Licensed under Apache-2.0; see `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md`.
