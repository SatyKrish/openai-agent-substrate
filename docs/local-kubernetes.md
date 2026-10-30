# Local Kubernetes setup and verification

This workflow runs real gVisor-backed Actors on a dedicated Kind cluster. It uses the pinned Agent Substrate source, PostgreSQL, RustFS snapshot storage, and authenticated ingress.

## Requirements

- macOS with Colima, or a Linux host with Docker already running.
- Docker CLI, Git, Go, kubectl, and uv.
- Internet access for the pinned source, Go toolchain/modules, and container images.

On macOS, setup creates a Colima profile named `openai-agent-substrate` with 6 CPUs, 12 GiB RAM, and a 60 GiB virtual disk. The default Docker context and existing Kubernetes configuration remain unchanged. Linux uses the selected Docker daemon; use a dedicated daemon if another cluster already owns the `kind-registry` container on a different port.

## Set up

From the repository root:

```sh
./scripts/local-kubernetes.sh setup
```

The script checks out Substrate commit `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`, creates `kind-openai-agent-substrate`, configures the registry on port 5501, installs Substrate and the sandbox template, and builds `kubectl-ate` under `artifacts/local/bin/`. A repeat setup reuses the existing cluster instead of deleting it.

The cluster enables the PodCertificateRequest and ClusterTrustBundle APIs required by Substrate. gVisor is used on the tested ARM64 Colima VM; microVM/KVM support is not required for this workflow. Egress credential injection is disabled because the harness keeps credentials on the host.

State, kubeconfig, tool binaries, setup logs, and test results stay under the ignored `artifacts/local/` directory. Do not commit or share that directory as a whole; it contains cluster credentials.

## Test

```sh
./scripts/local-kubernetes.sh test
./scripts/local-kubernetes.sh status
```

The test command first runs the real SDK demo, then the opt-in Kubernetes test. It verifies shell tool execution, binary file reads/writes, SDK patching, nonzero exit codes, and session serialization/resume. Each test creates and deletes its own Actor. No OpenAI API key or paid model call is required.

The default unit/integration suite remains key-free and skips Kubernetes tests unless explicitly enabled. To invoke the live test directly:

```sh
export PATH="$PWD/artifacts/local/bin:$PATH"
SUBSTRATE_RUN_KUBERNETES_TESTS=1 \
SUBSTRATE_KUBE_CONTEXT=kind-openai-agent-substrate \
SUBSTRATE_KUBECONFIG="$PWD/artifacts/local/kubeconfig" \
uv run --locked pytest -q tests/kubernetes
```

For a retained workspace:

```sh
export PATH="$PWD/artifacts/local/bin:$PATH"
uv run python -m examples.provider_check --backend substrate \
  --context kind-openai-agent-substrate \
  --kubeconfig artifacts/local/kubeconfig \
  --output-dir artifacts/local/demo --keep-actor
```

Use `python -m examples.provider_check.resume` with the same context, kubeconfig, and `artifacts/local/demo/session.json` to inspect the retained Actor. Add `--delete` to delete that Actor when finished.

## Jaeger tracing

Run `./scripts/local-kubernetes.sh tracing-setup` to deploy Jaeger all-in-one and
configure OTLP for new agent Jobs. Run `agent-run` for live model inference, or
`agent-test` for an explicit scripted evaluation. Start `tracing-ui` in another
terminal to open a loopback port forward for <http://127.0.0.1:16686>.
See [tracing and independent verification](tracing.md) for the complete workflow.

The quick start's golden path is `setup`, `.env`, `starter-setup`, then
`starter-chat`, a terminal chat with the [starter agent](../examples/starter/README.md).
`starter-ui` optionally serves the same conversations on <http://localhost:8002>.

For multi-turn testing, `./scripts/local-kubernetes.sh ui` serves the
[Document Intelligence UI](../examples/document_intelligence/ui/README.md) on
<http://localhost:8000> and shows each turn's files and trace beside the
conversation. Each conversation gets its own Actor and workspace volume, and
accepts `.pdf`, `.md`, `.txt` and `.csv` uploads. `sre-setup` then `sre-ui`
serve the [SRE agent](../examples/sre/README.md) on <http://localhost:8001>
against a demo namespace with two broken workloads. Both UIs run on the
[shared agent harness](agent-harness.md) and use the live model from `.env`.

## Stop and restart

```sh
./scripts/local-kubernetes.sh stop
```

On macOS this stops only the dedicated Colima profile and preserves its disk. Restart that profile with `colima start --profile openai-agent-substrate --activate=false --ssh-config=false`, or rerun setup. The project-specific kubeconfig selects the cluster explicitly.

## Verified recovery scope

The [verification record](verification.md) includes a real restore after replacing both sandbox worker pods and removing the Actor's node-local snapshot reference. The edited input and report were recovered from a completed external snapshot. This is a local functional check, not a production availability or isolation assessment.

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
