# Verification record

Date: 2026-10-29.

- Python: 3.14.3 on macOS.
- OpenAI Agents SDK: `openai-agents==0.24.0`.
- Substrate source: `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`.
- Full suite against the loopback protocol simulator: **27 passed**.
- Full suite with the actual upstream Go execution server: **27 passed**.
- Scripted demo using the simulator: passed.
- Scripted demo using the upstream execution server: passed.
- Both demo runs verified report contents and preserved edits after closing,
  JSON serialization, and reconnection through a new SDK session.

The tests invoke the real SDK runner and sandbox capabilities, including SDK
workspace patch operations. Substrate lifecycle calls are simulated in local
tests; CLI argument contracts are checked against the pinned upstream source.

No Kubernetes resources were created or modified during development. Actual
Kubernetes Actor scheduling, gVisor/microVM isolation, snapshot durability, and
live OpenAI model calls have not been tested in this environment. The local
verification does not establish those properties.

`uv.lock` captures the resolved dependencies. See the root README for the
commands to repeat the checks and run the same demo on a prepared cluster.
