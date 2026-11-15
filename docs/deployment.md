# Deploy on Kubernetes

The provider check uses the upstream `sandbox-template` ActorTemplate and
authenticated `kubectl-ate` routing. The local setup builds a complete Kind
deployment. [Local Kubernetes guide](local-kubernetes.md)

## Existing Substrate cluster

Install Agent Substrate v1.0.0 on a Linux Kubernetes cluster with durable
snapshot storage and a supported sandbox class. Use the `kubectl-ate` binary
from that release and verify its published checksum, or build it from the
pinned source. Kubernetes supplies the `WorkerPool` and `SandboxConfig`
resources. Register `ActorTemplate` definitions through Substrate's API with
`kubectl ate`. Install the sandbox demo for the provider check. [Operator install](https://ate.dev/docs/guides/operator-install/) · [v1.0.0 release assets](https://github.com/agent-substrate/substrate/releases/tag/v1.0.0) · [Sandbox demo](https://github.com/agent-substrate/substrate/tree/v1.0.0/demos/sandbox)

Include the CLI proxy's Pod Identity SPIFFE principal in the control plane's
router allowlist during installation.

```text
spiffe://cluster.local/ns/ate-system/sa/atenet-client
```

The caller also needs the upstream CLI's API permissions and permissions to create, read, and delete PodCertificateRequests in the system namespace. That right can request a certificate for any identity in Substrate's namespace, including its own components, so keep it to operators. The upstream local installation uses shared development identities; operators should configure their own principals and authorization for shared deployments.

### A harness in the cluster

A harness in a pod uses its own ServiceAccount for Substrate API calls and
router ingress. [SDK configuration example](usage.md)

- **API.** Pass a projected ServiceAccount token with audience `api.ate-system.svc` to `kubectl ate --token-file`, with `--endpoint api.ate-system.svc:443`. Grant `system:serviceaccount:NAMESPACE:NAME` the `editor` role on the application's atespace to create, resume, suspend and delete its Actors. Listing workers requires a global role. [Substrate authorization](https://ate.dev/docs/guides/authorization/)
- **Router.** Project a `podCertificate` from `podidentity.podcert.ate.dev/identity` and the CA from the `servicedns.podcert.ate.dev/identity` ClusterTrustBundle. List the pod's SPIFFE ID, `spiffe://cluster.local/ns/NAMESPACE/sa/NAME`, in `--ingress-allowed-spiffe-ids`. The router admits listed identities across Actors. Account for this scope when choosing which clients to allow. [Substrate networking](https://ate.dev/docs/concepts/architecture/#networking-stack-atenet--atunnel)
- **Kubernetes.** Grant read access to ClusterTrustBundles so `kubectl ate` can verify the Substrate API. Label the harness namespace `ate.dev/api-access=true` to allow API traffic under the installed NetworkPolicy.

`python -m deploy.kubernetes.base resources` applies the ServiceAccount and that one ClusterRole, grants the atespace access as the operator, and stops with an error if the router does not admit the harness. The router allowlist is an install flag, so add the harness's SPIFFE ID when you install Substrate, as `scripts/local-kubernetes.sh setup` does:

```text
--ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client,spiffe://cluster.local/ns/openai-agent-substrate/sa/openai-agent
```

Inspect the intended context and template:

```sh
kubectl config get-contexts
kubectl ate --context YOUR_KUBERNETES_CONTEXT get actor-template sandbox-template --atespace ate-demo-sandbox
```

Run the scripted SDK workflow:

```sh
uv run python -m examples.provider_check --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --atespace ate-demo-sandbox --template sandbox-template
```

Pass `--kubeconfig PATH` for a separate kubeconfig. The provider owns an
ephemeral loopback `kubectl ate proxy actor` for each session. The CLI handles
the port-forward and mutual TLS to Substrate's router.

An explicit `--router-url` selects a trusted HTTP gateway you operate, bypassing the owned CLI proxy. Such a gateway must provide the cluster's required upstream authentication and authorization. This option is also used by the local protocol simulator. In the cluster, `router_url` names Substrate's router itself and `router_tls` supplies the pod's certificate; see [a harness in the cluster](#a-harness-in-the-cluster).

An application gateway can implement the four lifecycle methods in `ActorControl`. Supply a trusted `router_url`, or provide an async `start_proxy(atespace, name)` method returning an object with a loopback `url` and async `aclose()` lifecycle.

## Agent sandbox templates

Each agent registers an ActorTemplate for its sandbox image through the
Substrate API. The template selects the worker pool by `workload=sandbox`,
requests `MEMORY` snapshots and mounts a per-Actor `durableDir` at `/workspace`.
The deploy commands push an image, register the content-addressed template
and record it for the agent's launcher.

```sh
uv run python -m examples.document_intelligence.deploy sandbox \
  --context YOUR_KUBERNETES_CONTEXT --image YOUR_REGISTRY/document-intelligence:example
uv run python -m examples.sre.deploy sandbox \
  --context YOUR_KUBERNETES_CONTEXT --image YOUR_REGISTRY/sre:example
uv run python -m examples.starter.deploy sandbox \
  --context YOUR_KUBERNETES_CONTEXT --image YOUR_REGISTRY/starter:example
```

The launchers find the template from that record. Run them from the same
checkout with `uv run --locked --group ui python -m examples.starter` (the
terminal chat) or `-m examples.starter.ui`, passing `--context` and, if needed,
`--kubeconfig`.

Each template limits an Actor to 0.5 CPU and 480 MiB by default
(`deploy/kubernetes/sandbox.py`). The pool's sandbox class and reported worker
capacity determine how many Actors fit. When workers are full, the harness
reports the wait and retries startup up to its configured deadline.

The SRE agent also needs read access to the namespaces it investigates. With an
operator kubeconfig, this creates the `sre-agent-reader` ServiceAccount and a
read-only Role in each existing namespace you name; see the
[SRE agent](../examples/sre/README.md) for what it can read:

```sh
uv run python -m examples.sre.deploy access --context YOUR_KUBERNETES_CONTEXT \
  --kubeconfig PATH --namespace NAME
```
