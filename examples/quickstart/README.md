# Provider quickstart

Run from the repository root after `uv sync --locked`:

```sh
# Explicit host protocol simulator; scripted execution without isolation.
uv run --locked python -m examples.quickstart --backend local

# Actual Kubernetes Actor; select the cluster explicitly.
uv run --locked python -m examples.quickstart \
  --context kind-openai-agent-substrate --kubeconfig artifacts/local/kubeconfig
```

The default backend is Kubernetes and requires `--context`. The scripted model
is the default for this execution check; select `--model MODEL_ID` and configure
`OPENAI_API_KEY` for live inference. Host mode requires explicit `--backend local`
and only accepts scripted execution. See [local setup](../../docs/local-kubernetes.md).

Use `--keep-actor` to suspend the Actor and save its session state, then inspect it:

```sh
uv run --locked python -m examples.quickstart.resume \
  --context YOUR_CONTEXT --kubeconfig PATH --state-file artifacts/session.json
```

Pass `--delete` to the resume utility to delete the retained Actor after use.
The library wheel ships neither this example nor the former
`openai-agent-substrate` demo command. Downstream applications import the provider
from `openai_agent_substrate` and may reuse its `testing/` helpers.
