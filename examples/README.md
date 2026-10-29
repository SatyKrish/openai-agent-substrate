# Examples

Run these commands from the repository root after `uv sync --locked`. Prepare the cluster and router connection using the [deployment guide](../docs/deployment.md).

## Live SDK agent

[`run_agent.py`](run_agent.py) stages a task file, runs a live SandboxAgent, copies its report to the host, and deletes the fresh Actor. Set `OPENAI_API_KEY` in the host environment, then choose a model available to your account:

```sh
uv run python examples/run_agent.py \
  --context YOUR_KUBERNETES_CONTEXT \
  --model YOUR_OPENAI_MODEL_ID
```

The API key remains in the trusted application; it is not staged into the Actor. This example makes billable model calls and requires a Substrate-enabled cluster.

## Retained workspace

First run the scripted cluster demo with `--keep-actor` to create `artifacts/session.json`:

```sh
uv run openai-agent-substrate --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT --keep-actor
```

Then inspect the saved workspace without calling a model:

```sh
uv run python examples/resume_session.py \
  --context YOUR_KUBERNETES_CONTEXT \
  --state-file artifacts/session.json
```

The Actor is suspended again on exit. Add `--delete` to permanently remove it after inspection. Use session JSON from your trusted application, and connect through the same atespace and intended cluster.

For a key-free local walkthrough, run `uv run openai-agent-substrate`. Local mode uses a protocol simulator and provides no sandbox isolation.
