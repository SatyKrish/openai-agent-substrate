# Using the sandbox provider

## Run the provider check on Kubernetes

Use a cluster with Substrate installed and the upstream sandbox demo's `sandbox-template` prepared. See [deployment guide](deployment.md) for prerequisites and router access.

```sh
uv run python -m examples.provider_check \
  --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --atespace ate-demo-sandbox \
  --template sandbox-template
```

The provider manages an authenticated loopback Actor proxy automatically. Use `--kubeconfig PATH` when the cluster configuration is separate.

This uses the scripted model, so it verifies execution and suspend/resume against a real cluster without making OpenAI API calls. It creates a uniquely named Actor and deletes it on exit by default.

For a live model, set `OPENAI_API_KEY` in the **host** environment and add `--model YOUR_OPENAI_MODEL_ID`. The key is used by the SDK harness and is never put in the manifest or execution request. The provider check exports traces when an OTLP endpoint is configured; see the [tracing guide](tracing.md). Without an endpoint, export is disabled.

To retain the workspace, add `--keep-actor`. Closing the session requests `kubectl ate suspend actor --mode sync`; after the second suspension, the check saves `artifacts/session.json`. This retains the Actor and its storage until you explicitly delete it:

```sh
kubectl ate --context YOUR_KUBERNETES_CONTEXT delete actor ACTOR_NAME --atespace ate-demo-sandbox
```

Read the Actor name from the check's output. Do not delete the shared template or worker pool to clean up one run.

## Integrate into your application

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

The example belongs inside an async function. Set the host API key and a model available to your account when using a live model.

For retained sessions, omit deletion, serialize **after** closing, and reconnect using the same configured client:

```python
payload = client.serialize_session_state(session.state)
# Save payload as JSON in trusted application storage.
resumed = await client.resume(client.deserialize_session_state(payload))
async with resumed:
    # Pass resumed through SandboxRunConfig(session=resumed).
    ...
```

Keep SDK conversation history or `RunState` separately. Actor suspension preserves compute/workspace state, not the external SDK harness. `examples/provider_check/main.py` shows carrying `result.to_input_list()` into the next run.

For a chat application, keep one Actor per conversation: create it on the first message, store the serialized state with the conversation and its history in an SDK session (or `result.to_input_list()`), resume the Actor for each later message, and delete it when the conversation is deleted. Serialize turns within one conversation; separate conversations can run concurrently. The [shared agent harness](agent-harness.md) implements this with ChatKit for the example agents; the [starter agent](../examples/starter/README.md) is the smallest one, and [architecture](architecture.md#workspace-persistence-and-conversations) covers the workspace volume that makes it durable.

For complete runnable examples, see [examples/README.md](../examples/README.md).

The generic retained-session utility is `python -m examples.provider_check.resume`:

```sh
uv run python -m examples.provider_check.resume --context YOUR_KUBERNETES_CONTEXT --state-file artifacts/session.json
```

Pass the same `--kubeconfig` and `--atespace` used for creation when applicable.
Use `--command` to inspect artifacts, or `--delete` to remove the retained Actor.
Domain workflows are documented under [examples/](../examples/README.md).
