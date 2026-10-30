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
uv run python -m examples.provider_check --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --atespace ate-demo-sandbox --template sandbox-template
```

Pass `--kubeconfig PATH` for a separate kubeconfig. The provider owns an ephemeral loopback `kubectl ate proxy actor` for each session. The CLI authenticates to Substrate's router; a plain HTTP port-forward to the router does not provide that authentication. No separate port-forward is needed for the default path.

An explicit `--router-url` selects a trusted HTTP gateway you operate, bypassing the owned CLI proxy. Such a gateway must provide the cluster's required upstream authentication and authorization. This option is also used by the local protocol simulator.

An application gateway can implement the four lifecycle methods in `ActorControl`. Supply a trusted `router_url`, or provide an async `start_proxy(atespace, name)` method returning an object with a loopback `url` and async `aclose()` lifecycle.

## Agent sandbox templates

Each agent runs in its own ActorTemplate, built from its sandbox image. The
deploy commands build and push the image, register the template (its name is a
hash of its content), and record it for the agent's launcher:

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

Each template limits an Actor to 0.5 CPU and 480 MiB by default (`deploy/kubernetes/sandbox.py`).
A worker runs as many Actors as its reported CPU and memory hold; on local Kind
each worker reports 2,000 millicores and 1,984 MiB, so it runs four. When every
worker is full, the harness waits for room instead of failing the turn.

The SRE agent also needs read access to the namespaces it investigates. With an
operator kubeconfig, this creates the `sre-agent-reader` ServiceAccount and a
read-only Role in each existing namespace you name; see the
[SRE agent](../examples/sre/README.md) for what it can read:

```sh
uv run python -m examples.sre.deploy access --context YOUR_KUBERNETES_CONTEXT \
  --kubeconfig PATH --namespace NAME
```
