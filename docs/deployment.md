# Deploy on Kubernetes

- **Connect an existing SDK application.** Prepare your Substrate cluster and
  choose how the application will connect. Then install the provider in your
  application's environment. [Cluster setup](#existing-substrate-cluster) · [SDK integration](usage.md#integrate-into-your-application)
- **Try it on your machine.** The key-free Kind quickstart provisions
  Substrate, the CLI plugin, and the demo template. [Local Kind guide](local-kubernetes.md)

## Existing Substrate cluster

### Prepare the cluster and template

1. Run Agent Substrate v1.0.0 on a Linux Kubernetes cluster with durable
   snapshot storage and a supported sandbox class. Kubernetes supplies the
   `WorkerPool` and `SandboxConfig` resources. [Operator install](https://ate.dev/docs/guides/operator-install/)
2. Install the release's `kubectl-ate` binary on `PATH` and verify its
   published checksum. You can also build the plugin from the pinned source.
   [v1.0.0 release assets](https://github.com/agent-substrate/substrate/releases/tag/v1.0.0)
3. Install the upstream sandbox demo to get `sandbox-template` in
   `ate-demo-sandbox` for the provider check and Python example. Register a
   template with the required tools for your application's sandbox image
   through `kubectl ate`.
   [Upstream sandbox demo](https://github.com/agent-substrate/substrate/tree/v1.0.0/demos/sandbox) · [Example application templates](#agent-sandbox-templates)

### An application outside the cluster

The provider uses your kubeconfig and opens an ephemeral `kubectl ate proxy actor`
for each session. The CLI manages the loopback port-forward and mutual TLS to
Substrate's router. [Deployment diagram](local-kubernetes.md#how-setup-works)

Include the CLI proxy's Pod Identity SPIFFE principal in the control plane's
router allowlist during installation.

```text
spiffe://cluster.local/ns/ate-system/sa/atenet-client
```

The caller needs the upstream CLI's API permissions and permission to create,
read, and delete PodCertificateRequests in the system namespace. That right
can request a certificate for any identity in Substrate's namespace, including
its own components, so keep it to operators. The upstream local installation
uses shared development identities. Configure dedicated principals and
authorization for shared deployments.

Confirm the intended context and the demo template.

```sh
kubectl config get-contexts
kubectl ate --context YOUR_KUBERNETES_CONTEXT get actor-template sandbox-template --atespace ate-demo-sandbox
```

Pass `--kubeconfig PATH` to the CLI when you use a separate kubeconfig. Next,
run the scripted check or connect your own SDK application.
[Key-free provider check](usage.md#run-the-provider-check-on-kubernetes) · [SDK integration](usage.md#integrate-into-your-application)

### A harness in the cluster

For a long-running agent, run the SDK application as its own Kubernetes
Deployment and keep its conversation state in application-owned durable
storage. The Document Intelligence batch Job demonstrates the same in-cluster
authentication. [Example Job](../examples/document_intelligence/deploy/README.md)

A harness in a pod uses its own ServiceAccount for Substrate API calls and
router ingress. It uses the projected API token for lifecycle calls and its
projected certificate for direct router connections.
[In-cluster Python configuration](usage.md#connect-an-in-cluster-harness)

- **API.** Pass a projected ServiceAccount token with audience `api.ate-system.svc` to `kubectl ate --token-file`, with `--endpoint api.ate-system.svc:443`. Grant `system:serviceaccount:NAMESPACE:NAME` the `editor` role on the application's atespace to create, resume, suspend and delete its Actors. Listing workers requires a global role. [Substrate authorization](https://ate.dev/docs/guides/authorization/)
- **Router.** Project a `podCertificate` from `podidentity.podcert.ate.dev/identity` and the CA from the `servicedns.podcert.ate.dev/identity` ClusterTrustBundle. List the pod's SPIFFE ID, `spiffe://cluster.local/ns/NAMESPACE/sa/NAME`, in `--ingress-allowed-spiffe-ids`. The router admits listed identities across Actors. Account for this scope when choosing which clients to allow. [Substrate networking](https://ate.dev/docs/concepts/architecture/#networking-stack-atenet--atunnel)
- **Kubernetes.** Grant read access to ClusterTrustBundles so `kubectl ate` can verify the Substrate API. Label the harness namespace `ate.dev/api-access=true` to allow API traffic under the installed NetworkPolicy.

`python -m deploy.kubernetes.base resources` applies the ServiceAccount and that one ClusterRole, grants the atespace access as the operator, and stops with an error if the router does not admit the harness. The router allowlist is an install flag, so add the harness's SPIFFE ID when you install Substrate, as `scripts/local-kubernetes.sh setup` does:

```text
--ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client,spiffe://cluster.local/ns/openai-agent-substrate/sa/openai-agent
```

### An execution gateway

Use `router_url` in your application or `--router-url` in the provider check
to route execution through a trusted HTTP gateway you operate. The gateway
provides the cluster's upstream authentication and authorization. The local
protocol simulator also uses this option. [Provider check options](../examples/provider_check/README.md)

An application gateway can implement the four lifecycle methods in `ActorControl`. Supply a trusted `router_url`, or provide an async `start_proxy(atespace, name)` method returning an object with a loopback `url` and async `aclose()` lifecycle.

For shared execution, the repository includes a gateway that authenticates
application client certificates and applies an explicit per-Actor grant before
forwarding execution and eviction requests to Substrate over mTLS. Run it with
direct router access restricted to trusted infrastructure. [Actor gateway deployment](../deploy/kubernetes/actor_gateway/README.md)

## Agent sandbox templates

Each agent registers an ActorTemplate for its sandbox image through the
Substrate API. The template selects the worker pool by `workload=sandbox`,
requests `MEMORY` snapshots and mounts a per-Actor `durableDir` at `/workspace`.
The deploy commands push an image, register the content-addressed template,
and record it in the checkout for the agent's launcher. Build on the same
architecture as the sandbox workers.

Follow the example guide to deploy Document Intelligence and pass its template
to the command-line workflow. [Document Intelligence sandbox deployment](../examples/document_intelligence/README.md#deploy-and-test-on-local-kubernetes)

To register the Starter or SRE sandbox with your own registry and cluster, run
the corresponding command. With a separate kubeconfig, pass `--kubeconfig PATH`.

```sh
uv run python -m examples.sre.deploy sandbox \
  --context YOUR_KUBERNETES_CONTEXT --image YOUR_REGISTRY/sre:example
uv run python -m examples.starter.deploy sandbox \
  --context YOUR_KUBERNETES_CONTEXT --image YOUR_REGISTRY/starter:example
```

Launch Starter from the same checkout with
`uv run --locked --group ui python -m examples.starter` for terminal chat or
`uv run --locked --group ui python -m examples.starter.ui` for the browser.
Pass `--context` and, if needed, `--kubeconfig`.

Each template limits an Actor to 0.5 CPU and 480 MiB by default
(`deploy/kubernetes/sandbox.py`). The pool's sandbox class and reported worker
capacity determine how many Actors fit. When workers are full, the harness
reports the wait and retries startup up to its configured deadline.

The SRE agent also needs read access to the namespaces it investigates. Run this
with an operator kubeconfig to create the `sre-agent-reader` ServiceAccount and a
read-only Role in each existing namespace you name. [SRE agent permissions](../examples/sre/README.md#access)

```sh
uv run python -m examples.sre.deploy access --context YOUR_KUBERNETES_CONTEXT \
  --kubeconfig PATH --namespace NAME
```
