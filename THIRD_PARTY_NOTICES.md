# Third-party notices

The Apache-2.0 license in the repository root covers original project code and documentation. The following third-party material retains its original rights and notices.

## Agent Substrate execution server

- Where: compiled into the sandbox images that the agents' deploy commands build; not vendored in this repository.
- Source: the `demos/sandbox` package of the Go module `go.ate.dev/substrate` ([agent-substrate/substrate](https://github.com/agent-substrate/substrate/blob/6949841e9dc98edbd2e0042ef95c19b4ce20aef5/demos/sandbox/main.go)) at the commit in `.substrate-revision`, currently `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`. `scripts/build-sandbox-server.sh` downloads it through the Go module proxy, which checks it against the Go checksum database, and builds it unmodified.
- Copyright: 2026 Google LLC.
- License: Apache-2.0. Each image carries upstream's license at `/usr/share/licenses/substrate/LICENSE`.

## Document Intelligence synthetic fixtures

- Files: `examples/document_intelligence/agent/sandbox/workspace/inputs/acme-2024.md`, `beta-2024.md`, and `kpis-2024.csv`.
- Copyright: 2026 SatyKrish and contributors.
- License: [MIT](examples/document_intelligence/agent/sandbox/workspace/inputs/LICENSE).
- Source: [bedrock-document-intelligence-agent](https://github.com/SatyKrish/bedrock-document-intelligence-agent/tree/7eb1dfbba95f87512aa20dc3444111983f0f3b3e/fixtures), commit `7eb1dfbba95f87512aa20dc3444111983f0f3b3e`.
- Reproduced without modification. Checksums and provenance are recorded in `examples/document_intelligence/agent/sandbox/PROVENANCE.json`. This license also applies when the example stages the fixture contents into a sandbox.

## OpenAI architecture diagram

- File: `docs/assets/harness-separate-from-compute.png`.
- Source and attribution: OpenAI, [Sandbox Agents guide](https://developers.openai.com/api/docs/guides/agents/sandboxes), “Harness separate from compute” diagram.
- The image was supplied for inclusion in this project's README and is reproduced without modification.
- The diagram and associated trademarks remain subject to their original rights and terms. They are excluded from the project's Apache-2.0 license grant.

Dependencies installed through `uv.lock` retain their respective licenses; they are not vendored into this repository.
