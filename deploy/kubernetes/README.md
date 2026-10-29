# Kubernetes deployment components

[base/](base/README.md) contains reusable provider permissions, the application
service account, in-cluster kubeconfig and artifact PVC. It does not import any
example or SDK agent. Applications own their images, entrypoint and Job.

The [Document Intelligence deployment](../../examples/document_intelligence/deploy/README.md)
shows one consumer of the base resources, including its Secret-backed harness
and optional local Jaeger collector.
