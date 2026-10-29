# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Reusable Kubernetes resource builders, router discovery and server-side apply."""

from dataclasses import dataclass, fields
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
NAMESPACE = "openai-agent-substrate"


def dns_name(value):
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Kubernetes resource names must be DNS labels")
    return value


@dataclass(frozen=True)
class ApplicationResources:
    namespace: str = NAMESPACE
    service_account: str = "openai-agent"
    resource_prefix: str = "openai-agent"
    artifacts_claim: str = "openai-agent-artifacts"
    artifact_storage: str = "1Gi"
    control_namespace: str = "ate-system"
    router_service: str = "atenet-router"
    upstream_identity: str = "ate-client"

    def __post_init__(self):
        for field in fields(self):
            if field.name != "artifact_storage":
                dns_name(getattr(self, field.name))

    @property
    def kubeconfig_name(self):
        return self.resource_prefix + "-kubeconfig"


def configuration(args):
    defaults = ApplicationResources()
    return ApplicationResources(**{field.name: getattr(args, field.name, getattr(defaults, field.name))
                                   for field in fields(defaults)})


def add_arguments(parser):
    defaults = ApplicationResources()
    for field in fields(defaults):
        parser.add_argument("--" + field.name.replace("_", "-"), default=getattr(defaults, field.name))


def namespace_resource(namespace=NAMESPACE):
    return dict(apiVersion="v1", kind="Namespace",
                metadata=dict(name=dns_name(namespace), labels={"ate.dev/api-access": "true"}))


def in_cluster_kubeconfig():
    return {"apiVersion": "v1", "kind": "Config",
            "clusters": [{"name": "cluster", "cluster": {
                "server": "https://kubernetes.default.svc",
                "certificate-authority": "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"}}],
            "users": [{"name": "agent", "user": {"tokenFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"}}],
            "contexts": [{"name": "in-cluster", "context": {"cluster": "cluster", "user": "agent"}}],
            "current-context": "in-cluster"}


def build_resources(router_pods, config=None):
    config = config or ApplicationResources()
    names = sorted({dns_name(name) for name in router_pods})
    if not names:
        raise ValueError("Router pod names are required; refusing unrestricted forwarding")
    role_name = config.resource_prefix + "-client"
    trust_name = config.resource_prefix + "-trust-bundles"

    def binding(kind, name, role_kind, namespace=None):
        metadata = {"name": name}
        if namespace:
            metadata["namespace"] = namespace
        return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": kind, "metadata": metadata,
                "subjects": [{"kind": "ServiceAccount", "name": config.service_account, "namespace": config.namespace}],
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": role_kind, "name": name}}

    return {"apiVersion": "v1", "kind": "List", "items": [
        namespace_resource(config.namespace),
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": config.service_account, "namespace": config.namespace}},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
         "metadata": {"name": role_name, "namespace": config.control_namespace},
         "rules": [
             {"apiGroups": [""], "resources": ["services"], "resourceNames": [config.router_service], "verbs": ["get"]},
             {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
             {"apiGroups": [""], "resources": ["pods"], "resourceNames": names.copy(), "verbs": ["get"]},
             {"apiGroups": [""], "resources": ["pods/portforward"], "resourceNames": names.copy(), "verbs": ["get", "create"]},
             {"apiGroups": [""], "resources": ["serviceaccounts/token"], "resourceNames": [config.upstream_identity], "verbs": ["create"]},
             {"apiGroups": ["certificates.k8s.io"], "resources": ["podcertificaterequests"], "verbs": ["create", "get", "delete"]},
         ]},
        binding("RoleBinding", role_name, "Role", config.control_namespace),
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": trust_name},
         "rules": [{"apiGroups": ["certificates.k8s.io"], "resources": ["clustertrustbundles"], "verbs": ["get", "list"]}]},
        binding("ClusterRoleBinding", trust_name, "ClusterRole"),
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": config.kubeconfig_name, "namespace": config.namespace},
         "data": {"kubeconfig": json.dumps(in_cluster_kubeconfig(), separators=(",", ":"))}},
        {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": config.artifacts_claim, "namespace": config.namespace},
         "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": config.artifact_storage}}}},
    ]}


def checked(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def kubectl(args, *command, **kwargs):
    return checked(["kubectl", "--context", args.context, "--kubeconfig", str(args.kubeconfig), *command], **kwargs)


def apply(args, resource):
    # Use stdin, never command-line secret values or a persisted Secret manifest.
    return kubectl(args, "apply", "--server-side", "--field-manager=openai-agent-substrate", "-f", "-", "-o", "name",
                   input=json.dumps(resource).encode(), capture_output=True)


def import_secret(args):
    namespace = configuration(args).namespace
    if not args.env_file.is_file():
        raise ValueError("No .env file found. Copy .env.example to .env and fill in the configuration.")
    # kubectl parses plain KEY=VALUE lines; .env is never executed as a shell script.
    try:
        result = kubectl(args, "create", "secret", "generic", args.secret_name, "-n", namespace,
                         "--from-env-file=" + str(args.env_file), "--dry-run=client", "-o", "json", capture_output=True)
        resource = json.loads(result.stdout)
    except subprocess.CalledProcessError:
        # Parser errors can contain the offending line. Do not expose it.
        raise ValueError("Cannot parse .env; use plain KEY=VALUE lines without export statements") from None
    if not resource.get("data"):
        raise ValueError("The environment file contains no configuration entries")
    # Preserve the label required by Substrate API ingress on every Secret refresh.
    apply(args, namespace_resource(namespace))
    resource["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "openai-agent-substrate"}
    try:
        apply(args, resource)
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not apply the agent configuration Secret; values were not logged") from None
    print(f"Imported configuration into {namespace}/{args.secret_name}; values omitted.")


def prepare_resources(args):
    config = configuration(args)
    service = json.loads(kubectl(args, "get", "service", config.router_service, "-n", config.control_namespace,
                                 "-o", "json", capture_output=True).stdout)
    selector = service["spec"].get("selector", {})
    if not selector:
        raise RuntimeError("The router service has no selector; refusing an unrestricted forwarding grant")
    labels = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    pods = json.loads(kubectl(args, "get", "pods", "-n", config.control_namespace, "-l", labels,
                              "-o", "json", capture_output=True).stdout)
    names = sorted(pod["metadata"]["name"] for pod in pods["items"] if not pod["metadata"].get("deletionTimestamp"))
    if not names:
        raise RuntimeError("No router pods found; refusing an unrestricted forwarding grant")
    return build_resources(names, config)


def configure_resources(args):
    apply(args, prepare_resources(args))
    print("Configured application resources with forwarding restricted to current router pods.")
