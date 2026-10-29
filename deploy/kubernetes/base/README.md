# Reusable in-cluster provider resources

Apply permissions, service account, in-cluster kubeconfig and an artifact PVC:

```sh
uv run --locked python -m deploy.kubernetes.base resources \
  --context YOUR_CONTEXT --kubeconfig PATH
```

`resources.py` discovers the current router pods and applies resources through
server-side apply. It refuses an empty router selector or pod list, and grants
forwarding only to the discovered names. Run it again after router rollouts.
Secrets can be imported with the `secret` action and `--env-file`/`--secret-name`.
No application entrypoint, image build, or model request runs here.
