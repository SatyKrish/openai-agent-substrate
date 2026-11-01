# Kubernetes deployment components

[base/](base/README.md) gives an in-cluster harness its own identity: the
ServiceAccount, its Substrate access grant and credentials volume, an in-cluster
kubeconfig and an artifact PVC. It does not import any example or SDK agent.
Applications own their images, entrypoint and Job.

[sandbox.py](sandbox.py) turns an agent's sandbox profile (its Dockerfile, CPU
and memory) into an image and an immutable ActorTemplate whose name is a hash of
its content. Both agents' `deploy` commands use it. The harness runtime never
imports it.

The [Document Intelligence deployment](../../examples/document_intelligence/deploy/README.md)
shows one consumer of the base resources, including its Secret-backed harness
and optional local Jaeger collector.
