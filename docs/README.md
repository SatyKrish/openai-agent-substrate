# Documentation

## Start here

- **Connect an existing SDK app.** Prepare a Substrate cluster, then install the
  provider and run the Python session example. [Cluster setup](deployment.md#existing-substrate-cluster) · [SDK integration](usage.md#integrate-into-your-application)
- **Try the integration without an OpenAI API key.** Set up a dedicated Kind
  cluster and check that an Actor's workspace survives suspend and resume.
  [Local Kind guide](local-kubernetes.md)

## Reference

- [Architecture and provider limits](architecture.md) covers execution, workspace persistence, and the trust boundary.
- [Kubernetes deployment components](../deploy/kubernetes/README.md) covers pod identity, Substrate access, storage, and ActorTemplates.
- [Tracing](tracing.md) covers exporting SDK and sandbox spans to Jaeger.
- [Verification record](verification.md) lists tested versions, results, and their scope.

## Examples and development

- [Example applications](../examples/README.md) includes the optional live Starter chat, Document Intelligence, and SRE.
- [Shared harness design](agent-harness.md) describes the runtime used by those applications.
- [Development](development.md) covers local tests, upstream protocol checks, and package builds.
