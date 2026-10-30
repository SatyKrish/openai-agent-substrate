# SRE agent

Investigates unhealthy Kubernetes workloads and explains each cause with
evidence. It reads the cluster through the harness, analyzes in its sandbox, and
cannot change anything. It runs on the [shared harness](../../harness/README.md).

| Where | What runs there |
| --- | --- |
| Harness (trusted) | The agent loop and four read-only tools: `list_resources`, `describe`, `pod_logs`, `namespace_events`. They use a short-lived token for the `sre-agent-reader` ServiceAccount and refuse namespaces outside the allowlist. |
| Sandbox (Actor) | The `kubernetes-triage` skill, the shell and Python over saved evidence. No kubectl, no cluster credentials. |
| Host check | Every quote in `output/findings.json` must appear in the host's copy of the evidence file it cites. |

Each tool call saves its full output twice: on the host, and in the workspace as
`evidence/NNN-label.txt`. The model sees a preview and the path. Editing
`evidence/` inside the sandbox cannot make a quote pass, because the check reads
the host copy.

## Access

`sre-agent-reader` lives in the `openai-agent-substrate` namespace. A Role in each
allowed namespace grants `get`, `list` and `watch` on pods, pod logs, events,
services, endpoints, deployments, replica sets, stateful sets, daemon sets and
jobs. It grants nothing on Secrets or ConfigMaps, no write verbs, no `exec`, and
nothing cluster-wide. The launcher requests a one-hour token with your operator
kubeconfig, writes a kubeconfig that holds only that token (mode 0600, in the
data folder), and renews it before it expires. `kubectl describe` shows what a
pod spec contains, including literal environment values, so keep secrets in
Secrets.

## Run it on Kind

After [local Kubernetes setup](../../docs/local-kubernetes.md):

```sh
./scripts/local-kubernetes.sh sre-setup
./scripts/local-kubernetes.sh sre-ui
```

`sre-setup` builds the sandbox image and registers its template (0.5 CPU,
480 MiB), creates the `sre-demo` namespace, and applies the reader's Role there.
The demo has a healthy `frontend`, a `checkout` that exits with a configuration
error (CrashLoopBackOff), and a `catalog` whose image tag was never pushed
(ErrImagePull). Open <http://localhost:8001> and ask which workloads are
unhealthy. To let the agent read other namespaces, pass `--namespace NAME` to
both commands.

The model is live (`OPENAI_MODEL`, `OPENAI_API_KEY` from `.env`), so every
message incurs model API usage.

## Layout

```text
agent/
  agent.py                  SandboxAgent: shell, filesystem, skills
  instructions.md           Standing behavior
  skills/kubernetes-triage/ Procedure, failure patterns, triage.py
  tools/cluster.py          Read-only harness tools and their allowlists
  lib/findings.py           Host check of findings.json
  spec.py                   AgentSpec for the harness
deploy/                     Sandbox image, reader access, demo workloads
ui/                         Page, routes, card and launcher
evals/                      Tool scope, RBAC, check and end-to-end tests
```
