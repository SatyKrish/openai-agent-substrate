# Documentation

| Guide | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Harness/compute separation, Kubernetes mapping, workspace persistence, provider responsibilities, and limits |
| [Local Kubernetes](local-kubernetes.md) | Set up Kind, deploy Substrate, and run real Actor tests |
| [Deployment](deployment.md) | Connect the provider to a prepared Agent Substrate cluster |
| [Usage](usage.md) | Run on Kubernetes, integrate the SDK, resume sessions, and keep one Actor per conversation |
| [Tracing](tracing.md) | Export SDK and sandbox spans to Jaeger and verify traces |
| [Development](development.md) | Run tests, verify the upstream server, and build distributions |
| [Verification record](verification.md) | Tested versions, results, and unverified infrastructure properties |
| [Kubernetes deployment components](../deploy/kubernetes/README.md) | Reusable permissions, service account, in-cluster kubeconfig and artifact PVC |
| [Document Intelligence deployment](../examples/document_intelligence/deploy/README.md) | .env import, Secret mounting, endpoint/model settings, the sandbox template and agent Jobs |
| [Document Intelligence UI](../examples/document_intelligence/ui/README.md) | ChatKit conversations with uploads, per-turn files and Jaeger traces |
| [Examples](../examples/README.md) | Domain workflows with skills and sandbox code execution |
