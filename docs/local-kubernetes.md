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
uv run openai-agent-substrate --backend substrate \
  --context kind-openai-agent-substrate \
  --kubeconfig artifacts/local/kubeconfig \
  --output-dir artifacts/local/demo --keep-actor
```

Use `examples/resume_session.py` with the same context, kubeconfig, and `artifacts/local/demo/session.json` to inspect the retained Actor. Add `--delete` to delete that Actor when finished.

## Stop and restart

```sh
./scripts/local-kubernetes.sh stop
```

On macOS this stops only the dedicated Colima profile and preserves its disk. Restart that profile with `colima start --profile openai-agent-substrate --activate=false --ssh-config=false`, or rerun setup. The project-specific kubeconfig selects the cluster explicitly.

## Verified recovery scope

The [verification record](verification.md) includes a real restore after replacing both sandbox worker pods and removing the Actor's node-local snapshot reference. The edited input and report were recovered from a completed external snapshot. This is a local functional check, not a production availability or isolation assessment.
