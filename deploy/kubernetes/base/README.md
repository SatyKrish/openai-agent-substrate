# Reusable in-cluster provider resources

This layer supplies the service account, permissions, in-cluster kubeconfig and
artifact PVC for an application using the provider. It depends only on Python's
standard library and kubectl. Applications own their entrypoint, images and Job.

```sh
uv run --locked python -m deploy.kubernetes.base resources \
  --context YOUR_CONTEXT --kubeconfig PATH
```

`resources.py` discovers the current router pods and applies Python-built
resources through server-side apply. It rejects an empty router selector or pod
list and grants forwarding only to discovered router names. Reapply after router
rollouts. No placeholder grants or production JSON templates are used.

Choose names for another application:

```sh
uv run --locked python -m deploy.kubernetes.base resources \
  --context YOUR_CONTEXT --kubeconfig PATH \
  --namespace invoice-app --service-account invoice-agent \
  --resource-prefix invoice --artifacts-claim invoice-reports
```

Use a unique resource prefix for each application: it also names the cluster
trust-bundle Role and binding. Optional settings include `--artifact-storage`,
`--control-namespace`, `--router-service` and `--upstream-identity`.
Python consumers pass `ApplicationResources(...)` to `build_resources(...)`.
The default settings preserve the existing local development resource names.

The `secret` action imports `--env-file` into `--secret-name` in the selected
namespace. It passes the manifest over stdin without executing the file or
logging values. Neither action starts an agent or makes a model API request.
