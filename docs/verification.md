# Verification record

Date: 2026-10-29.

## Versions and environment

- Host: macOS ARM64, Python 3.14.3.
- Local VM: dedicated Colima profile, 6 CPUs and 12 GiB RAM.
- Kubernetes: Kind, v1.37.0, Linux ARM64.
- Sandbox runtime: gVisor (`gvisor-default`), two sandbox worker pods.
- Snapshot storage: in-cluster RustFS through Substrate's object-store plugin.
- OpenAI Agents SDK: `openai-agents==0.24.0`.
- Substrate source: `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`.

## Results

| Check | Result |
| --- | --- |
| Default unit/integration suite | 33 passed; the real-cluster test is skipped by default |
| Unit/integration suite using the actual upstream Go execution server | 33 passed; real-cluster test skipped |
| Opt-in Kubernetes integration test | Passed against a fresh real Actor |
| SDK scripted workflow on Kubernetes | Passed: shell tool execution, report creation, serialized session resume, and preserved edits |
| Kubernetes filesystem behavior | Passed: binary file round trips, SDK patching, and nonzero shell exit codes |
| Repeated setup | Reused the existing cluster; the retained Actor and its files remained usable |
| Source distribution and wheel | Built successfully; `twine check` passed |

The Kubernetes path uses owned loopback `kubectl ate proxy actor` processes and authenticated Substrate ingress. Tests exercise the real SDK runner and actual sandbox execution; no OpenAI API key or model call was required.

## External snapshot recovery

The retained demo Actor was synchronously suspended. Its API status reported `ACTOR_STATE_SUSPENDED`, `SNAPSHOT_FIDELITY_MEMORY`, and `SNAPSHOT_STORAGE_STATE_COMPLETED`.

To test recovery beyond the node-local checkpoint, the test reverted this already-durably-suspended Actor to its external snapshot, confirmed its node-local snapshot reference was removed, and replaced both idle sandbox worker pods. The old and new worker UIDs were disjoint.

A new SDK session then resumed the same Actor through authenticated ingress. The report matched the host's original bytes and the edited input remained intact. The Actor was suspended again with another completed durable snapshot.

Evidence is saved locally under ignored `artifacts/local/`, including `recovery-verification.json`, before/after Actor status, demo reports, and the setup/test logs. The directory also contains kubeconfig credentials and must not be published as a whole.

## Limits of this verification

These checks establish local functional execution and snapshot recovery on a single-node Kind cluster. They do not establish production availability, multi-tenant authorization policy, resistance to sandbox escape, host/node-loss durability, or live OpenAI model behavior. The local installation uses upstream development identities; shared deployments need an operator-defined identity and authorization configuration.

The existing public CI also exercises the key-free paths on Python 3.11–3.14. See [local-kubernetes.md](local-kubernetes.md) to repeat the real-cluster checks.
