# Documentation

| Guide | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Harness/compute separation, Kubernetes mapping, workspace persistence, provider responsibilities, and limits |
| [Agent harness](agent-harness.md) | The shared harness that runs any agent's loop, what an agent supplies, and the SRE agent |
| [Local Kubernetes](local-kubernetes.md) | Set up Kind, deploy Substrate, and run real Actor tests |
| [Deployment](deployment.md) | Connect the provider to a prepared Agent Substrate cluster |
| [Usage](usage.md) | Run on Kubernetes, integrate the SDK, resume sessions, and keep one Actor per conversation |
| [Tracing](tracing.md) | Export SDK and sandbox spans to Jaeger and verify traces |
| [Development](development.md) | Run tests, verify the upstream server, and build distributions |
| [Verification record](verification.md) | Tested versions, results, and unverified infrastructure properties |
| [Kubernetes deployment components](../deploy/kubernetes/README.md) | Reusable permissions, service account, in-cluster kubeconfig and artifact PVC |
| [Document Intelligence deployment](../examples/document_intelligence/deploy/README.md) | .env import, Secret mounting, endpoint/model settings, the sandbox template and agent Jobs |
| [Document Intelligence UI](../examples/document_intelligence/ui/README.md) | ChatKit conversations with uploads, per-turn files and Jaeger traces |
| [SRE agent](../examples/sre/README.md) | Read-only Kubernetes investigation with evidence-checked findings |
| [Examples](../examples/README.md) | Domain workflows with skills and sandbox code execution |
