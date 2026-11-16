# Provider check

This smoke test creates an Actor, runs a scripted SDK agent in it, suspends it,
serializes the session, and confirms that the workspace survived resume. It
writes `verification.json`. Use the copyable provider example to integrate it
into an SDK application, or use Starter as a complete agent reference.
[SDK integration](../../docs/usage.md#integrate-into-your-application) · [Starter](../starter/README.md)

Run from the repository root after `uv sync --locked`. For Kind, complete
`./scripts/local-kubernetes.sh setup` first to build the checkout-local
`kubectl-ate` plugin and create the kubeconfig. [Local setup](../../docs/local-kubernetes.md)

```sh
# Explicit host protocol simulator; scripted execution without isolation.
uv run --locked python -m examples.provider_check --backend local

# Actual Kubernetes Actor; select the cluster explicitly.
export PATH="$PWD/artifacts/local/bin:$PATH"
uv run --locked python -m examples.provider_check \
  --context kind-openai-agent-substrate --kubeconfig artifacts/local/kubeconfig
```

The default backend is Kubernetes and requires `--context`. The scripted model
is the default for this execution check; select `--model MODEL_ID` and configure
`OPENAI_API_KEY` for live inference. Host mode requires explicit `--backend local`
and only accepts scripted execution.

Use `--keep-actor` to suspend the Actor and save its session state, then inspect it:

```sh
export PATH="$PWD/artifacts/local/bin:$PATH"
uv run --locked python -m examples.provider_check.resume \
  --context YOUR_CONTEXT --kubeconfig PATH --state-file artifacts/session.json
```

Pass `--delete` to the resume utility to delete the retained Actor after use.
The library wheel ships neither this example nor the former
`openai-agent-substrate` demo command. Downstream applications import the provider
from `agent_substrate`. The scripted model and local stand-in this check uses
are in the repository's [`testing/`](../../testing/) folder, not in the wheel.
