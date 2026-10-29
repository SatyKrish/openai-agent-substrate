# Deploy on Kubernetes

The reference implementation uses the upstream `sandbox-template` ActorTemplate and authenticated `kubectl-ate` routing. Start with the [local Kubernetes guide](local-kubernetes.md) for a complete Kind deployment.

## Existing Substrate cluster

Prepare a Linux Kubernetes cluster with Agent Substrate, a supported sandbox runtime, durable snapshot storage, and the upstream sandbox demo. Install `kubectl-ate` from the same pinned Substrate source.

The tested source is `6949841e9dc98edbd2e0042ef95c19b4ce20aef5`. Follow its [installation instructions](https://github.com/agent-substrate/substrate/blob/6949841e9dc98edbd2e0042ef95c19b4ce20aef5/README.md) and [sandbox demo setup](https://github.com/agent-substrate/substrate/blob/6949841e9dc98edbd2e0042ef95c19b4ce20aef5/demos/sandbox/README.md).

Allow the CLI proxy's Pod Identity SPIFFE principal at Substrate ingress when installing the control plane:

```text
spiffe://cluster.local/ns/ate-system/sa/atenet-client
```

The caller also needs the upstream CLI's API permissions and permissions to create, read, and delete PodCertificateRequests in the system namespace. The upstream local installation uses shared development identities; operators should configure their own principals and authorization for shared deployments.

Inspect the intended context and template:

```sh
kubectl config get-contexts
kubectl ate --context YOUR_KUBERNETES_CONTEXT get actor-template sandbox-template --atespace ate-demo-sandbox
```

Run the scripted SDK workflow:

```sh
uv run python -m examples.quickstart --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --atespace ate-demo-sandbox --template sandbox-template
```

Pass `--kubeconfig PATH` for a separate kubeconfig. The provider owns an ephemeral loopback `kubectl ate proxy actor` for each session. The CLI authenticates to Substrate's router; a plain HTTP port-forward to the router does not provide that authentication. No separate port-forward is needed for the default path.

An explicit `--router-url` selects a trusted HTTP gateway you operate, bypassing the owned CLI proxy. Such a gateway must provide the cluster's required upstream authentication and authorization. This option is also used by the local protocol simulator.

An application gateway can implement the four lifecycle methods in `ActorControl`. Supply a trusted `router_url`, or provide an async `start_proxy(atespace, name)` method returning an object with a loopback `url` and async `aclose()` lifecycle.
