# Connect to a Substrate cluster

The reference implementation reuses the upstream `sandbox-template` ActorTemplate and its `/process` server. No changes to the Substrate core are needed.

## Prerequisites

- A Linux Kubernetes cluster with Agent Substrate installed, workers able to run its supported sandbox runtime, and durable snapshot storage configured.
- The `kubectl-ate` plugin and cluster access for Actor create, resume, suspend, and delete.
- The upstream sandbox demo deployed in `ate-demo-sandbox`, with its template's golden snapshot ready.
- A trusted/private route to `atenet-router`.

Use the [upstream installation guide](https://github.com/agent-substrate/substrate/blob/6949841e9dc98edbd2e0042ef95c19b4ce20aef5/README.md) and [sandbox demo setup](https://github.com/agent-substrate/substrate/blob/6949841e9dc98edbd2e0042ef95c19b4ce20aef5/demos/sandbox/README.md) to prepare the cluster. This project does not install or modify an existing cluster automatically.

## Connect

Inspect the intended context and template:

```sh
kubectl config get-contexts
kubectl ate --context YOUR_KUBERNETES_CONTEXT get actor-template sandbox-template --atespace ate-demo-sandbox
```

Keep this router port-forward open in a separate terminal:

```sh
kubectl --context YOUR_KUBERNETES_CONTEXT port-forward \
  -n ate-system svc/atenet-router 8000:80 --address 127.0.0.1
```

Then run from the project directory:

```sh
uv run openai-agent-substrate --backend substrate \
  --context YOUR_KUBERNETES_CONTEXT \
  --router-url http://127.0.0.1:8000 \
  --atespace ate-demo-sandbox --template sandbox-template
```

The control-plane CLI uses the explicit context and its own authentication/connection logic. If you already have a control API tunnel, optionally pass `--api-endpoint HOST:PORT`; the reference implementation does not disable TLS verification or read tokens itself.

An application gateway can replace the kubectl lifecycle implementation by implementing the four async methods in `ActorControl`. Keep tenant authorization and credential handling in that trusted gateway.
