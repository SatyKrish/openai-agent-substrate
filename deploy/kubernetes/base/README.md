# Reusable in-cluster provider resources

This layer gives a harness that runs in a pod its own identity with Substrate:
the ServiceAccount, its access grant, the credentials volume it mounts, an
in-cluster kubeconfig and an artifact PVC. It depends only on Python's standard
library, kubectl and kubectl-ate. Applications own their entrypoint, images and Job.

```sh
uv run --locked python -m deploy.kubernetes.base resources \
  --context YOUR_CONTEXT --kubeconfig PATH
```

`resources` runs as you, the operator, and:

1. Applies the namespace, the ServiceAccount, the artifact PVC, the kubeconfig
   ConfigMap and a ClusterRole that only reads ClusterTrustBundles. `kubectl ate`
   uses those to verify Substrate's API; the harness has no other Kubernetes
   permission.
2. Removes the Role an earlier version granted in `ate-system` (port-forwarding
   to the router, pod certificate requests and `ate-client` tokens).
3. Makes `system:serviceaccount:NAMESPACE:SERVICE_ACCOUNT` an `editor` of
   `--atespace` in its Substrate access policy, keeping the policy's other
   bindings. Editors create, resume, suspend and delete Actors there.
4. Stops with an error if Substrate's router does not admit
   `spiffe://cluster.local/ns/NAMESPACE/sa/SERVICE_ACCOUNT`. Its allowlist is the
   `--ingress-allowed-spiffe-ids` install flag, so only a Substrate install can add it.

`substrate_volume()` is the projected volume a Job mounts at `SUBSTRATE_MOUNT`
(`/var/run/substrate`): `api-token` for Substrate's API (audience
`api.ate-system.svc`), and `credential-bundle.pem` and `router-ca.pem` for mutual
TLS to the router. The kubelet rotates all three, and the provider reads them
again when they change. The [usage guide](../../../docs/usage.md) shows the client
that uses them.

Choose names for another application:

```sh
uv run --locked python -m deploy.kubernetes.base resources \
  --context YOUR_CONTEXT --kubeconfig PATH \
  --namespace invoice-app --service-account invoice-agent \
  --resource-prefix invoice --artifacts-claim invoice-reports --atespace invoices
```

Use a unique resource prefix for each application: it also names the cluster
trust-bundle role and binding. Other options are `--artifact-storage` and
`--control-namespace`. Python consumers pass `ApplicationResources(...)` to
`build_resources(...)`. The default settings keep the existing local
development names.

The `secret` action imports `--env-file` into `--secret-name` in the selected
namespace. It passes the manifest over stdin without executing the file or
logging values. Neither action starts an agent or makes a model API request.
