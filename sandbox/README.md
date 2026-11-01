# openai-agent-substrate

A reference implementation of **Kubernetes as a sandbox compute provider for the
OpenAI Agents SDK `SandboxAgent`**, using [Agent Substrate](https://github.com/agent-substrate/substrate)
to run each sandbox as an Actor. The SDK's agent loop stays in your trusted
application; shell commands and workspace files run in the Actor, which holds
no credentials.

```sh
pip install openai-agent-substrate            # provider only
pip install 'openai-agent-substrate[tracing]' # with the OTLP exporter
```

From a checkout of the repository, install `./sandbox` instead. The package
installs as `openai-agent-substrate` and is imported as `agent_substrate`.

```python
from agents import Runner
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Filesystem, Shell
from agents.sandbox.entries import File
from agent_substrate import (
    KubectlActorControl, SubstrateSandboxClient, SubstrateSandboxOptions,
)

client = SubstrateSandboxClient(
    atespace="ate-demo-sandbox",
    control=KubectlActorControl(context="YOUR_KUBERNETES_CONTEXT"),
)
manifest = Manifest(entries={"task.txt": File(content=b"Write a short report.")})
agent = SandboxAgent(
    name="Substrate worker",
    instructions="Read task.txt and write report.txt in the workspace.",
    capabilities=[Shell(), Filesystem()],
    default_manifest=manifest,
)
session = await client.create(
    manifest=manifest,
    options=SubstrateSandboxOptions(template="sandbox-template"),
)
try:
    async with session:
        result = await Runner.run(
            agent, "Complete the task.",
            run_config=RunConfig(
                sandbox=SandboxRunConfig(session=session),
                tracing_disabled=True,
            ),
        )
finally:
    await client.delete(session)
```

Run it inside an async function, with `OPENAI_API_KEY` set in the host environment.

That client reaches Actors through `kubectl ate proxy` with your kubeconfig. A
harness running in the cluster passes `router_url`, `router_tls=RouterTLS(...)`
and `KubectlActorControl(..., endpoint=..., token_file=...)` instead, so it calls
Substrate's API and router directly as its own ServiceAccount; see
[Usage](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/usage.md).

The package contains the provider (`SubstrateSandboxClient`, Actor lifecycle
through `kubectl ate`, the authenticated Actor proxy, direct router mutual TLS, tracing) and testing
helpers: a scripted model and a local protocol simulator. It is pinned to
`openai-agents==0.24.0`.

The [repository](https://github.com/SatyKrish/openai-agent-substrate) also holds
a shared agent harness, three agents built on it (including a starter to copy), local Kind setup and the
verification record. Start with:

- [Architecture and limits](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/architecture.md)
- [Usage: SDK integration and session resume](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/usage.md)
- [Deployment on an existing Substrate cluster](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/deployment.md)
- [Agent harness](https://github.com/SatyKrish/openai-agent-substrate/blob/main/docs/agent-harness.md)

Licensed under Apache-2.0; see `LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md`.
