# Authenticated Actor gateway

The gateway is an opt-in ingress for application harnesses sharing a Substrate
installation. It authenticates each caller's mTLS certificate and checks its
SPIFFE URI against an exact `atespace/actor` grant before forwarding `/process`
or `/_substrate/eviction`. The gateway opens its own mTLS connection to
Substrate and forwards only the validated Actor target and request body.

## Deploy

Build the image from the repository root and push it to a registry that the
cluster can pull from.

```sh
docker build -t YOUR_REGISTRY/actor-gateway:example deploy/kubernetes/actor_gateway
docker push YOUR_REGISTRY/actor-gateway:example
```

Run the image behind a private `ClusterIP` Service using a dedicated
ServiceAccount, for example `execution-gateway/actor-gateway`. The gateway
accepts direct TLS or TCP pass-through so it can verify each caller's own
certificate. Mount these files read-only and give the container user read
access.

| Path | Material |
| --- | --- |
| `/etc/gateway/serving/cert.pem`, `/etc/gateway/serving/key.pem` | Gateway server certificate and key with the Service DNS name in its certificate SAN |
| `/etc/gateway/callers/ca.pem` | Trusted CA for application client certificates containing their SPIFFE URI SAN |
| `/var/run/substrate/credential-bundle.pem` | Gateway pod's `podCertificate` projection with signer `podidentity.podcert.ate.dev/identity`, key type `ECDSAP256`, and `credentialBundlePath` set to `credential-bundle.pem` |
| `/var/run/substrate/router-ca.pem` | `clusterTrustBundle` projection using signer `servicedns.podcert.ate.dev/identity` and label `podcert.ate.dev/canarying=live` |
| `/etc/gateway/policy/grants.json` | Operator-managed authorization file |

Use these container arguments. The upstream URL must be the private HTTPS
router origin for this Substrate installation.

```yaml
args:
  - --listen=:8443
  - --upstream=https://atenet-router.ate-system.svc
  - --server-cert=/etc/gateway/serving/cert.pem
  - --server-key=/etc/gateway/serving/key.pem
  - --caller-ca=/etc/gateway/callers/ca.pem
  - --upstream-client-bundle=/var/run/substrate/credential-bundle.pem
  - --upstream-ca=/var/run/substrate/router-ca.pem
  - --grants=/etc/gateway/policy/grants.json
```

In the shared installation, set `atenet-router`'s
`--ingress-allowed-spiffe-ids` to the gateway's pod identity, for example
`spiffe://cluster.local/ns/execution-gateway/sa/actor-gateway`. Keep the router
and worker ingress private. Restrict the router's Kubernetes ingress policy to
the gateway pods and any separately trusted operator path. Replace broad
router ingress rules in the installation source because Kubernetes
NetworkPolicies grant access additively. Keep the stock Actor execution port
behind Substrate routing. The local Kind setup continues to use its operator
proxy and direct harness identity.

## Assign and use grants

The trusted application authenticates its users and assigns their workspace
Actors. Provision a dedicated caller identity for each authorization scope and
use its certificate only in the trusted harness. The exact grants file looks
like this.

```json
{
  "spiffe://applications.example/harness/team-a": ["projects/report-a"],
  "spiffe://applications.example/harness/team-b": ["projects/report-b"]
}
```

Choose unique Actor names when granting access. Publish grants from the
application's ownership records, remove them on deletion, and avoid reusing a
name under an existing grant. Update the file by atomic replacement. The
gateway loads it on every request and denies requests if it cannot load the
policy. TLS material is reloaded for new connections.

Pass the gateway as `router_url` and use `RouterTLS` with the application's
client certificate and the gateway serving CA. The application's trusted
lifecycle control remains separately scoped by Substrate API policy. Supply
that control in `control` before constructing the client.

```python
from pathlib import Path

from agent_substrate import RouterTLS, SubstrateSandboxClient, SubstrateSandboxOptions

client = SubstrateSandboxClient(
    atespace="projects",
    control=control,
    router_url="https://actor-gateway.execution-gateway.svc:8443",
    router_tls=RouterTLS(
        credential_bundle=Path("/var/run/actor-gateway-client/credential-bundle.pem"),
        ca_bundle=Path("/var/run/actor-gateway-client/serving-ca.pem"),
    ),
)
options = SubstrateSandboxOptions(template="report", actor_name="report-a")
```

The command path accepts up to 2 MiB of request data and returns up to 4 MiB of
response data. Eviction requests and responses each have a 64-byte cap. An
execution transport failure can leave command outcome unknown. The gateway
passes the request upstream once and rejects redirects. Keep the gateway,
grant writer, TLS issuers, and lifecycle credentials inside trusted
infrastructure. [Substrate authorization](https://ate.dev/docs/guides/authorization/) · [Substrate security scope](https://ate.dev/docs/security/security/#security-scope)

## Verify locally

```sh
cd deploy/kubernetes/actor_gateway
go test ./...
```

The tests use ephemeral certificates and exercise both sides of mutual TLS on
loopback. They cover exact Actor grants and prove the gateway sends its own
identity upstream.
