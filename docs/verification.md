# Verification record

## Agent Substrate v1.0.0

The reference deployment pins Agent Substrate v1.0.0 at
`b34fbee742906494b5eb4f1f36987176bf8770a4` and OpenAI Agents SDK `0.24.0`.
The fresh cluster verification on 2026-11-15 used Kubernetes 1.37 on ARM64
Kind with macOS Colima, gVisor, and in-cluster RustFS snapshot storage.
Both the host CLI proxy and in-cluster harness used their configured SPIFFE
identities. The cluster checks used scripted models with an empty OpenAI API
key. [Release pin](../.substrate-revision) · [Kind setup](local-kubernetes.md#set-up)

| Check | Observed result | Reproduce |
| --- | --- | --- |
| Latest local Python suite | `python -m pytest -q` in a locked Python 3.13 environment with the `ui` group installed. 219 passed, 8 skipped. | [Development checks](development.md#tests-and-execution-checks) |
| Local lint, type checks, and packaging | Ruff, focused mypy, and Bash syntax checks passed. The key-free host simulator passed. The provider wheel and source distribution built offline, passed Twine checks, and included the attribution files. | [Development checks](development.md#tests-and-execution-checks) · [Distribution builds](development.md#build-distributions) |
| Pinned upstream HTTP server on the host | 98 passed. The host PDF check skipped because Poppler was unavailable. Actor lifecycle was simulated. | [Upstream protocol check](development.md#upstream-execution-server-on-the-host) |
| Fresh Kind cluster | The provider check and Kubernetes integration test passed through the authenticated CLI proxy. The scripted SDK agent read a workspace file after synchronous suspension and resume. | [Kind checks](local-kubernetes.md#test) |
| Document Intelligence Job on Kind | The scripted Job passed host artifact validation using its Secret-backed harness and direct router mTLS. Kubernetes tests covered the document workflow, Secret configuration, harness permissions, and linked SDK and sandbox spans in Jaeger. Artifact collection copied outputs from the PVC. | [Job guide](../examples/document_intelligence/deploy/README.md) · [Tracing](tracing.md#local-kubernetes-workflow) |

## Actor lifecycle and gateway checks

- The SDK example ran from a clean provider wheel install against the dedicated
  Kind cluster with a scripted model. It suspended the Actor, loaded the saved
  session JSON, and read `report.txt` on the same Actor. [SDK integration](usage.md#integrate-into-your-application)
- Local tests exercised the SDK's default and explicit no-op snapshot options,
  incomplete startup, cancellation, and recovery with a persisted Actor ID.
- The opt-in worker-drain check passed twice on the dedicated ARM64 Kind cluster
  during implementation. A scripted SDK shell command wrote a file before the
  worker Pod began graceful termination. The harness interrupted the turn,
  saved the pending eviction notice, and synchronously suspended the Actor.
  The test confirmed a completed durable checkpoint, loaded the pre-suspend
  session state, and read the file on the same Actor assigned to a replacement
  worker with a different Pod UID. It then deleted the test Actor.
  [Worker-drain check](local-kubernetes.md#test-worker-eviction)
- The gateway suite used separate caller and router TLS identities on loopback.
  It covered exact Actor grants, denial and revocation, trust failures, and
  forwarding of execution and eviction requests. [Gateway checks](../deploy/kubernetes/actor_gateway/README.md#verify-locally)

### External snapshot recovery

A separate pre-release Kind check removed a suspended Actor's node-local
snapshot reference by reverting it to its completed external snapshot, then
replaced both idle worker Pods. A new SDK session resumed the same Actor and
recovered the original report bytes and edited input. This observation used
Substrate commit `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`.

## Prior live model evidence

Pre-release Kind runs with `gpt-6.1-terra` exercised the Document Intelligence
Job, Starter's multi-turn conversation, and SRE's evidence-backed analysis.
The Document Intelligence run used native SDK skill, shell, and patch tools
and passed its host artifact checks. The pinned-release cluster results above
used scripted models. [Example workflows](../examples/README.md)

## CI configuration

Regular CI is configured for the key-free Python 3.11–3.14 matrix, the upstream
HTTP compatibility checks, gateway tests, and a separate provider packaging job.
The manually dispatched Kubernetes job runs the fresh Kind setup, provider and
Document Intelligence checks, Jaeger verification, and worker-drain recovery.
Use the workflow run status for results from GitHub-hosted runners.
[CI workflow](../.github/workflows/ci.yml) · [CI guide](development.md#ci-and-pinned-tools)

## Limits of this verification

- Cluster evidence covers a single-node Kind setup. Production availability,
  multi-tenant authorization, sandbox-escape resistance, and host or node-loss
  durability remain open validation areas.
- Worker-drain recovery depends on the original worker remaining reachable
  long enough to finish the durable checkpoint. The local protocol simulator
  executes on the host without an isolation boundary.
  [Architecture and provider limits](architecture.md) · [Worker-drain check](local-kubernetes.md#test-worker-eviction)
- Router mTLS authenticates an allowlisted identity across Actors. Shared
  deployments need application-level authorization scoped to each Actor.
  [Deployment and authentication](deployment.md#a-harness-in-the-cluster)
- Live model evidence covers the sample workflows and fixtures. General model
  quality, PDF extraction and OCR across real-world corpora, and sustained
  multi-user load remain open validation areas.
