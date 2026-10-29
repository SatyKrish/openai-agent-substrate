# Kubernetes agent harness and environment configuration

Run the trusted SDK harness in its own Kubernetes Job. A Kubernetes Secret
provides OpenAI configuration through environment variables and a read-only
volume. Agent Substrate runs document processing in a separate sandbox Actor.

## Configure

From the repository root:

```sh
cp .env.example .env
# Edit .env locally and set OPENAI_API_KEY.
```

Supported configuration:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Model API credential; required for live inference |
| `OPENAI_BASE_URL` | OpenAI API base URL, default example `https://api.openai.com/v1` |
| `OPENAI_MODEL` | Live model selected by the pod; scripted evaluation requires explicit `--test` |
| `OPENAI_ORG_ID` | Optional OpenAI organization |
| `OPENAI_PROJECT_ID` | Optional OpenAI project |

Use plain `KEY=VALUE` lines without shell `export`, quotes, or `${VARIABLE}`
interpolation. Kubernetes' env-file parser treats values literally; the setup
helper never sources or executes `.env`. Comment out optional settings you do not
use. Alternate endpoints need the Responses API and the function/custom tools
used by `SandboxAgent`; setting a URL does not establish endpoint compatibility.

The [OpenAI SDK reads API configuration from the environment](https://developers.openai.com/api/docs/quickstart#create-and-export-an-api-key).
This application's pod entrypoint selects the model from `OPENAI_MODEL`.
`.env` is Git-ignored and excluded from image build contexts. `.env.example`
contains placeholders and is included in the source distribution.

## Prepare the local cluster and agent

```sh
./scripts/local-kubernetes.sh setup
./scripts/local-kubernetes.sh agent-setup
```

`setup` imports `.env` when it exists. `agent-setup` imports it again, prepares the
Python sandbox ActorTemplate, builds the trusted harness image, and applies its
ServiceAccount, RBAC, in-cluster kubeconfig, and artifact PVC. Neither setup
command calls the OpenAI API.

The Secret is `openai-agent-substrate/openai-agent-env`. Import uses an in-memory
manifest passed to `kubectl` over stdin. It does not print values, put credentials
on command lines, or save a rendered Secret manifest. The pod's configuration is:

```yaml
envFrom:
  - secretRef:
      name: openai-agent-env
volumeMounts:
  - name: agent-config
    mountPath: /var/run/agent-config
    readOnly: true
volumes:
  - name: agent-config
    secret:
      secretName: openai-agent-env
      defaultMode: 0440
```

The complete [Job manifest](agent-job.json) also configures a non-root user,
read-only root filesystem, bounded resources, and `backoffLimit: 0` to avoid
automatic workflow replays. The [resource manifest](resources.json) grants the
dedicated harness ServiceAccount the operations needed by the pinned development
Substrate CLI, including API token requests, trust bundles, certificate requests,
and control-plane port forwarding. It grants no Secret-read verbs or cluster-admin.
Substrate API/router identities remain the upstream shared development identities
described in the [verification limits](../../docs/verification.md#limits-of-this-verification).

## Test, then run a live model

```sh
# Uses the mounted configuration, requesting explicit --test mode. No API calls.
./scripts/local-kubernetes.sh agent-test

# Uses OPENAI_MODEL and OPENAI_BASE_URL from .env. Makes billable model API calls.
./scripts/local-kubernetes.sh agent-run

# Copy report artifacts from the PVC to artifacts/local/agent-results/.
./scripts/local-kubernetes.sh agent-artifacts
```

Each run refreshes the Secret and starts a fresh Job, so edits to endpoint, model,
and credentials take effect on the next run. Setting `OPENAI_MODEL=scripted` does not enable test mode by itself. The
`agent-test` command supplies explicit `--test`; `agent-run` uses live model
configuration. The Job prints configuration key
names and whether the mounted files match the environment; it omits values.
Its SDK workflow still loads the skill, patches and executes analysis code, and
checks the output contract. The sandbox is checked for absence of the harness
API key and Secret mount before processing documents.

Artifacts persist in PVC `openai-agent-artifacts`, under
`document-intelligence/ANALYSIS_ID/`. Collection uses a temporary reader pod
with no Secret mount or ServiceAccount token, then deletes that reader.
Job logs and records remain available for one day; the PVC is retained.

For an explicit context or a different configuration file, use the helper directly:

```sh
uv run python scripts/kubernetes_agent.py secret \
  --context YOUR_CONTEXT --kubeconfig PATH --env-file PATH_TO_ENV
```

The local image builder targets a homogeneous ARM64 or AMD64 cluster, compiles
`kubectl-ate` from the pinned upstream checkout, verifies the downloaded
`kubectl` checksum, installs locked Python dependencies, and registers the image
by digest. See `scripts/kubernetes_agent.py --help` for build/run/collect options.

This follows the standard Kubernetes mechanisms for
[Secret volumes and environment variables](https://kubernetes.io/docs/tasks/inject-data-application/distribute-credentials-secure/).
