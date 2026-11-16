# Repository guide for coding agents

This repository integrates the OpenAI Agents SDK sandbox with Agent Substrate
on Kubernetes. The trusted application runs the agent and keeps credentials and
conversation state. Agent Substrate runs sandbox tools and persists the Actor's
workspace. The application can run outside the cluster or in its own Deployment.

## Find the code

| Task | Implementation | Tests and reference |
| --- | --- | --- |
| SDK client and session state | [`provider.py`](sandbox/src/agent_substrate/provider.py), [`lifecycle.py`](sandbox/src/agent_substrate/lifecycle.py), [`eviction.py`](sandbox/src/agent_substrate/eviction.py) | [Provider tests](sandbox/tests/) · [SDK usage](docs/usage.md) |
| Shell, files, and router connections | [`transport.py`](sandbox/src/agent_substrate/transport.py), [`proxy.py`](sandbox/src/agent_substrate/proxy.py) | [HTTP integration tests](sandbox/tests/integration/) · [Architecture](docs/architecture.md) |
| Kubernetes identity and templates | [`deploy/kubernetes/`](deploy/kubernetes/), [`scripts/local-kubernetes.sh`](scripts/local-kubernetes.sh) | [Deployment tests](tests/deploy/) · [Cluster setup](docs/deployment.md) · [Local Kind](docs/local-kubernetes.md) |
| Example application runtime | [`harness/`](harness/), [`examples/`](examples/), [`testing/`](testing/) | [Harness tests](harness/tests/) · [Key-free provider check](examples/provider_check/README.md) · [Starter](examples/starter/README.md) |

## Preserve these contracts

- The provider package in `sandbox/` must work independently of `harness/`,
  `deploy/`, `examples/`, and the repository-only `testing/` helpers.
- With an application-owned `SandboxRunConfig(session=...)`, closing the session
  synchronously suspends the Actor. Save the serialized session state in trusted
  application storage and reopen the same Actor on a later turn. SDK-owned
  `client=...` runs use disposable Actors. See [session ownership](docs/usage.md#choose-how-to-retain-a-session).
- For worker eviction, persist the pending notice before suspension. The worker
  must remain reachable long enough to complete a durable checkpoint for this
  recovery path. Keep model credentials out of the Actor workspace.
- Treat [`sandbox/pyproject.toml`](sandbox/pyproject.toml), [`.substrate-revision`](.substrate-revision),
  and [`uv.lock`](uv.lock) as the source for dependency pins.

## Verify changes

```sh
uv sync --locked --group ui
uv run --locked --group ui pytest -q
```

Use the closest test directory for a focused run. The default suite needs
loopback sockets for local fixtures and runs without a cluster or API key.
[Development checks](docs/development.md) cover lint, type checks, protocol
compatibility, and packaging. [Kind checks](docs/local-kubernetes.md#test) are
opt-in. The [worker eviction test](docs/local-kubernetes.md#test-worker-eviction)
deletes a worker Pod on the idle, dedicated Kind cluster.
