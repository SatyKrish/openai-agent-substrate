# Security policy

## Reporting a vulnerability

Use the repository's **Security → Report a vulnerability** flow to contact maintainers privately:

https://github.com/SatyKrish/openai-agent-substrate/security/advisories/new

Include the affected version, a minimal reproduction, the relevant trust boundary, and the impact. Omit live credentials and private customer data. If private reporting is unavailable, open an issue asking for a private reporting channel without publishing exploit details.

There is no guaranteed response or remediation timeline. Fixes are developed on `main`; older versions have no separate maintenance policy.

## Execution boundaries

The trusted application owns the SDK harness, credentials, and Actor lifecycle. Substrate and its Kubernetes runtime provide sandbox isolation. The bundled upstream execution server has no application-level authorization and must remain behind a trusted private ingress.

The local simulator runs commands on the host and provides no isolation. The CLI restricts it to the scripted test model. Local test results do not establish Kubernetes isolation or snapshot durability. See the [architecture](docs/architecture.md) and [verification record](docs/verification.md).
