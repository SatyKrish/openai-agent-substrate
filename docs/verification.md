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
| Default unit/integration suite | 47 passed; three real-cluster tests are skipped by default |
| Unit/integration suite using the actual upstream Go execution server | 47 passed; three real-cluster tests skipped |
| Opt-in Kubernetes integration test | Passed against a fresh real Actor |
| SDK scripted workflow on Kubernetes | Passed: shell tool execution, report creation, serialized session resume, and preserved edits |
| Kubernetes filesystem behavior | Passed: binary file round trips, SDK patching, and nonzero shell exit codes |
| Repeated setup | Reused the existing cluster; the retained Actor and its files remained usable |
| Document Intelligence on Kubernetes | Passed: native `load_skill`, shell inspection/extraction, `apply_patch`, Python execution, exact citations, and independent host artifact checks |
| Domain artifact rejection tests | Passed: altered citations, calculations, CSV, missing report evidence/limitations, staged digest mismatches, and source-table disagreements are rejected |
| Secret-backed Kubernetes agent pod | Passed: five OpenAI settings match mounted files and environment, the model is selected from the Secret, and real document analysis runs with no harness credential in the Actor |
| Source distribution and wheel | Built successfully; `twine check` passed |

The Kubernetes path uses owned loopback `kubectl ate proxy actor` processes and authenticated Substrate ingress. Tests exercise the real SDK runner and actual sandbox execution; no OpenAI API key or model call was required.

## Document Intelligence

The domain example uses the native SDK `Skills` capability with a lazy local
bundle, `Shell`, and `Filesystem`. Its real-cluster execution image is
`localhost:5501/openai-agent-substrate-document-intelligence@sha256:3a60dcbbd55ce4a00cb5f351a628386a5093eaf014d0d6290b693fa40a8edaaf`,
registered as `document-intelligence-46668a69e650` in the dedicated local cluster.
The input Markdown and CSV fixtures are synthetic and retain their MIT license.

The SDK trace records eight actual tool calls: load the skill, read its contract,
inspect documents, extract and reconcile facts, patch an analysis program, run
that Python program, verify its outputs, and reopen them. The host independently
validated the downloaded report, CSV, and JSON against the original sources.
ACME and BETA operating margins were 22.93% and 30.19%, with a USD 117.8 billion
revenue gap and an explicit missing-EBITDA limitation.

These runs used fixed scripted model decisions, including a code fixture emitted
through the real patch tool. They prove sandbox execution and the required
artifact checks, not live model reasoning or general document understanding.
See [the domain guide](../examples/document_intelligence/README.md).

## Environment configuration in the agent pod

A temporary Secret carried a dummy key, `https://example.invalid/v1`,
`OPENAI_MODEL=scripted`, and test organization/project IDs. The dedicated non-root
harness Job confirmed that all five environment values matched the read-only
Secret files. Model selection came from the Secret, with no command-line model
override. The native skill, shell, patch, and Python workflow completed against a
real Substrate Actor, whose credential preflight passed. No OpenAI API call was
made. The test deleted its Job and Secret; report artifacts were collected from
the retained PVC using a temporary reader pod without credential mounts.

Local unit tests also construct the actual OpenAI client without requests and
verify its endpoint, API key, organization, and project settings from environment
variables. Live endpoint/model execution remains unverified.

## External snapshot recovery

The retained demo Actor was synchronously suspended. Its API status reported `ACTOR_STATE_SUSPENDED`, `SNAPSHOT_FIDELITY_MEMORY`, and `SNAPSHOT_STORAGE_STATE_COMPLETED`.

To test recovery beyond the node-local checkpoint, the test reverted this already-durably-suspended Actor to its external snapshot, confirmed its node-local snapshot reference was removed, and replaced both idle sandbox worker pods. The old and new worker UIDs were disjoint.

A new SDK session then resumed the same Actor through authenticated ingress. The report matched the host's original bytes and the edited input remained intact. The Actor was suspended again with another completed durable snapshot.

Evidence is saved locally under ignored `artifacts/local/`, including `recovery-verification.json`, before/after Actor status, demo reports, and the setup/test logs. The directory also contains kubeconfig credentials and must not be published as a whole.

## Limits of this verification

These checks establish local functional execution and snapshot recovery on a single-node Kind cluster. They do not establish production availability, multi-tenant authorization policy, resistance to sandbox escape, host/node-loss durability, or live OpenAI model behavior. The local installation uses upstream development identities; shared deployments need an operator-defined identity and authorization configuration.

The existing public CI also exercises the key-free paths on Python 3.11–3.14. See [local-kubernetes.md](local-kubernetes.md) to repeat the real-cluster checks.
