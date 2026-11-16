# Local Kubernetes setup and verification

Run a scripted SDK agent in a real gVisor-backed Actor on a dedicated Kind
cluster and verify that its workspace survives suspend and resume. These steps
need no `.env` file, OpenAI API key, or model calls.

## Requirements

- macOS with Colima, or a Linux host with Docker already running.
- Docker CLI, Git, Go, kubectl, and uv.
- Internet access for the pinned source, Go toolchain/modules, and container images.

On macOS, setup creates a Colima profile named `openai-agent-substrate` with 4 CPUs, 8 GiB RAM, and a 60 GiB virtual disk. The default Docker context and existing Kubernetes configuration remain unchanged. Linux uses the selected Docker daemon; use a dedicated daemon if another cluster already owns the `kind-registry` container on a different port.

## Set up

Initialize the cluster from the repository root.

```sh
./scripts/local-kubernetes.sh setup
```

Setup installs the pinned Agent Substrate release and sandbox demo in Kind. It
builds the `kubectl ate` plugin and writes a project-local kubeconfig under
`artifacts/local/`.

## Test

```sh
./scripts/local-kubernetes.sh test
```

The scripted provider check creates an Actor, runs a command that changes its
workspace, closes the session to suspend the Actor, then opens it from
serialized session state. The output and
`artifacts/local/results/verification.json` include these fields.

```json
{
  "backend": "Agent Substrate",
  "model": "scripted",
  "workspace_survived_resume": true,
  "initial_manifest_was_not_reapplied": true
}
```

The saved workspace report is `artifacts/local/results/report.txt`. The command
then runs the live Kubernetes test for binary file reads and writes, SDK patching,
nonzero exit codes, and session resume. Expect `1 passed`. Each check creates and
deletes its own Actor. Run `./scripts/local-kubernetes.sh status` to inspect
the node, system pods, and worker pool.

## Stop, restart and tear down

```sh
./scripts/local-kubernetes.sh stop
```

On macOS this stops the dedicated Colima profile and preserves its disk. Rerun
setup to restart it at the configured size. To keep the previous size, use
`colima start --profile openai-agent-substrate --activate=false --ssh-config=false`.
On Linux, manage the selected Docker daemon separately. The project-specific
kubeconfig selects the cluster explicitly.

```sh
./scripts/local-kubernetes.sh teardown
```

This runs the quickstart's `hack/delete-kind-cluster.sh`. It deletes the Kind
cluster and registry, including all Actors, snapshots, and templates, and
removes the project kubeconfig. Start new conversations after a later setup.
The old conversations under `artifacts/examples/` refer to deleted Actors and
can be removed.

## How setup works

The script follows Agent Substrate's own
[quickstart](https://ate.dev/docs/get-started/quickstart/) using the
[v1.0.0 release](https://github.com/agent-substrate/substrate/releases/tag/v1.0.0).
It checks out the commit in `.substrate-revision` under
`artifacts/local/substrate-<revision>/` and builds the tools from that source.
The deployment uses PostgreSQL, RustFS snapshot storage, and authenticated ingress.

![A persistent SDK application and its conversation store outside Kubernetes, with Actor execution and workspace snapshots inside Kind](assets/kind-deployment.svg)

The diagram shows the host placement used by the quickstart. The application
keeps conversation history and saved sandbox session state. Agent Substrate
runs shell and file requests in an Actor and uses RustFS snapshots to preserve
the Actor's `/workspace` across suspend and resume. An SDK application can also
run as a separate Kubernetes Deployment and connect to the same Substrate
services. [In-cluster configuration](deployment.md#a-harness-in-the-cluster)

| Quickstart step | This script |
| --- | --- |
| 1. `hack/create-kind-cluster.sh` | Creates `kind-openai-agent-substrate` with its registry on port 5501 |
| 2. `export ATE_INSTALL_KIND=true` | Exported for every command |
| 3. `ate-setup deploy ate-system` with the bundled `k8s.io` credential provider and the ingress SPIFFE ID | Adds `--sandbox-classes=gvisor` to install only the runtime this project uses, a longer rollout timeout for Colima, and `--no-dev-env` to ignore a personal `.ate-dev-env.sh` in the checkout. The router's allowlist includes the in-cluster harness's ServiceAccount so its Jobs reach Actors with their own pod certificates |
| 4. `ate-setup deploy demo counter` | `ate-setup deploy demo sandbox` instead: its `sandbox-workerpool` (label `workload=sandbox`) runs every agent's Actors, and the provider check uses its `sandbox-template` |
| 5. `go install ./cmd/kubectl-ate` | Builds it into `artifacts/local/bin/`, matching the pinned commit and leaving any other `kubectl-ate` on the machine alone |
| 6. Verify a counter Actor through `kubectl ate proxy` | The [provider and Kubernetes checks](#test) use the authenticated proxy and verify suspend and resume |
| Teardown: `hack/delete-kind-cluster.sh` | [Tear down the local cluster](#stop-restart-and-tear-down) |

A repeat setup reuses a cluster recorded at the same release. Setup detects an
older or unrecorded Substrate install and stops before deploying into it.
Upstream's [rolling-upgrade policy](https://ate.dev/docs/reference/version-skew-policy/)
starts at v1.0.0. To move a pre-release demo cluster to v1.0.0, save any reports
or workspace files you need, [tear down the old cluster](#stop-restart-and-tear-down)
and run setup again.

### Cluster size

The defaults suit one or two people testing. After Substrate's install, setup
scales it down: one API server and one identity server instead of two each, and
one sandbox worker instead of two. A worker runs four sandbox turns at once
(each Actor is 0.5 CPU and 480 MiB), and a fifth waits for room; a conversation
holds a worker only during its turns. Environment variables set the size:

| Variable | Default | What it sets |
| --- | --- | --- |
| `COLIMA_CPUS`, `COLIMA_MEMORY` | `4`, `8` (GiB) | The Colima VM on macOS |
| `SANDBOX_WORKERS` | `1` | Sandbox workers, four concurrent turns each |
| `CONTROL_PLANE_REPLICAS` | `1` | Substrate's API and identity servers |

For concurrency tests, Substrate's own sizing is
`SANDBOX_WORKERS=2 CONTROL_PLANE_REPLICAS=2 COLIMA_CPUS=6 COLIMA_MEMORY=12`.
`scale` applies the replica settings to a running cluster; Colima takes a new
CPU and memory size when it starts, so stop the cluster first, then rerun setup:

```sh
SANDBOX_WORKERS=2 ./scripts/local-kubernetes.sh scale
./scripts/local-kubernetes.sh stop && COLIMA_CPUS=6 COLIMA_MEMORY=12 ./scripts/local-kubernetes.sh setup
```

Use the same variables on every later setup: setup reapplies Substrate's
manifests and then these sizes. `status` shows each node's allocated CPU and
memory.

The cluster enables the PodCertificateRequest and ClusterTrustBundle APIs required by Substrate. The gVisor WorkerPool runs on ARM64 Colima and standard Linux nodes. The bundled credential provider starts with a default-deny namespace policy. The harness keeps its credentials on the host or in its own pod. A Linux host that will run more than about a thousand sandboxes also needs a larger kernel neighbor table, as the quickstart notes.

State, kubeconfig, tool binaries, setup logs, and test results stay under the ignored `artifacts/local/` directory. Do not commit or share that directory as a whole; it contains cluster credentials.

## Run checks directly

The default unit/integration suite remains key-free and skips Kubernetes tests unless explicitly enabled. To invoke the live test directly:

```sh
export PATH="$PWD/artifacts/local/bin:$PATH"
SUBSTRATE_RUN_KUBERNETES_TESTS=1 \
SUBSTRATE_KUBE_CONTEXT=kind-openai-agent-substrate \
SUBSTRATE_KUBECONFIG="$PWD/artifacts/local/kubeconfig" \
uv run --locked pytest -q sandbox/tests/kubernetes/test_live_cluster.py
```

For a retained workspace:

```sh
export PATH="$PWD/artifacts/local/bin:$PATH"
uv run --locked python -m examples.provider_check --backend substrate \
  --context kind-openai-agent-substrate \
  --kubeconfig artifacts/local/kubeconfig \
  --output-dir artifacts/local/demo --keep-actor
```

Use `uv run --locked python -m examples.provider_check.resume` with the same
context, kubeconfig, and `--state-file artifacts/local/demo/session.json` to
inspect the retained Actor. Add `--delete` to remove it when finished.
[Provider check guide](../examples/provider_check/README.md)

## Test worker eviction

Run this separately on the idle, dedicated Kind cluster created by `setup`,
with one sandbox worker. It gracefully deletes that worker's pod, so close
other agent sessions and pause anything that could start an Actor.

```sh
./scripts/local-kubernetes.sh worker-eviction-test
```

The command builds the eviction-aware sandbox image and runs a scripted SDK
agent. The test requires the original worker to remain reachable until the
durable checkpoint completes. The opt-in Kubernetes CI job runs the same
command.

```mermaid
sequenceDiagram
    participant Test as SDK test harness
    participant Original as Original worker Pod
    participant Substrate as Agent Substrate
    participant Replacement as Replacement worker Pod

    Test->>Original: Delete Pod during<br/>an active shell command
    Original-->>Test: Actor reports eviction notice
    Note over Test: Interrupt the turn and<br/>save the pending notice
    Test->>Substrate: Close session and request synchronous suspend
    Substrate->>Original: Checkpoint the Actor
    Test->>Substrate: Check Actor and snapshot status
    Substrate-->>Test: Actor suspended and<br/>durable snapshot complete
    Note over Test,Replacement: Wait for a Ready worker Pod with a different UID
    Test->>Substrate: Resume the same Actor<br/>from saved session state
    Substrate->>Replacement: Restore the Actor
    Test->>Replacement: Verify the restored workspace file
    Test->>Substrate: Delete the test Actor
```

## Verified recovery scope

The [verification record](verification.md) includes a real restore after replacing both sandbox worker pods and removing the Actor's node-local snapshot reference. The edited input and report were recovered from a completed external snapshot. This is a local functional check, not a production availability or isolation assessment.

## Run example agents

Configure `.env` after cluster setup to use a live model with the shared agent
harness. Run these commands with `./scripts/local-kubernetes.sh`.
[Harness design](agent-harness.md)

- **Starter.** Run `starter-setup`, then `starter-chat`. `starter-ui` serves the
  same conversations on <http://localhost:8002>. [Run Starter with a live model](../README.md#run-starter-with-a-live-model)
- **Document Intelligence.** [Deploy the sandbox](#document-intelligence-skill-workflow)
  and run `ui` to serve the app on <http://localhost:8000>. Each conversation gets its own Actor and workspace,
  accepts `.pdf`, `.md`, `.txt` and `.csv` uploads, and displays each turn's
  files and trace. [Document Intelligence UI](../examples/document_intelligence/ui/README.md)
- **SRE.** Run `sre-setup`, then `sre-ui`, to investigate two broken demo
  workloads on <http://localhost:8001>. [SRE agent](../examples/sre/README.md)

## Document Intelligence skill workflow

After setup, run `./scripts/local-kubernetes.sh document-intelligence` to build and
push a Python-capable sandbox image, register its immutable ActorTemplate, run the
skill-driven SDK workflow, and execute its opt-in Kubernetes test. Reports and
tool traces are saved under `artifacts/local/document-intelligence/`. See the
[domain guide](../examples/document_intelligence/README.md) for the workflow,
fixture attribution, live-model options, and contract-checking limits.

## Secret-backed agent harness pod

Create `.env` from `.env.example` before setup to import the OpenAI key, endpoint,
model, and optional organization/project settings into a Kubernetes Secret.
`agent-setup` builds the dedicated harness image and its resources. Use
`agent-test` for an execution test without inference, `agent-run` for live model
execution using `.env`, and `agent-artifacts` to download reports from the PVC.
See [the complete configuration guide](../examples/document_intelligence/deploy/README.md).

## Jaeger tracing

Run `./scripts/local-kubernetes.sh tracing-setup` to deploy Jaeger all-in-one and
configure OTLP for new agent Jobs. Run `agent-run` for live model inference, or
`agent-test` for an explicit scripted evaluation. Start `tracing-ui` in another
terminal to open a loopback port forward for <http://127.0.0.1:16686>.
See [tracing and independent verification](tracing.md) for the complete workflow.
