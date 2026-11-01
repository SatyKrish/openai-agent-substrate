# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Reusable Kubernetes resource builders, the harness's Substrate access grant and server-side apply."""

from dataclasses import dataclass, fields
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
NAMESPACE = "openai-agent-substrate"
# The in-cluster harness reads its Substrate credentials here; see substrate_volume().
SUBSTRATE_MOUNT = "/var/run/substrate"
API_AUDIENCE = "api.ate-system.svc"
ROUTER = "atenet-router"


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
    atespace: str = "ate-demo-sandbox"
    control_namespace: str = "ate-system"

    def __post_init__(self):
        for field in fields(self):
            if field.name != "artifact_storage":
                dns_name(getattr(self, field.name))

    @property
    def kubeconfig_name(self):
        return self.resource_prefix + "-kubeconfig"

    @property
    def principal(self):
        """The ID Substrate's API derives from this ServiceAccount's tokens."""
        return f"system:serviceaccount:{self.namespace}:{self.service_account}"

    @property
    def spiffe_id(self):
        """The ID in this ServiceAccount's pod certificates, which Substrate's router must allow."""
        return f"spiffe://cluster.local/ns/{self.namespace}/sa/{self.service_account}"


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


def substrate_volume(name="substrate"):
    """The pod's own Substrate credentials, mounted at SUBSTRATE_MOUNT; the kubelet rotates all three.

    ``api-token`` authenticates to Substrate's API as the ServiceAccount. ``credential-bundle.pem``
    (the pod's key and certificate) and ``router-ca.pem`` authenticate both ends of mutual TLS to
    the router.
    """
    return {"name": name, "projected": {"sources": [
        {"serviceAccountToken": {"audience": API_AUDIENCE, "expirationSeconds": 3600, "path": "api-token"}},
        {"podCertificate": {"signerName": "podidentity.podcert.ate.dev/identity", "keyType": "ECDSAP256",
                            "credentialBundlePath": "credential-bundle.pem"}},
        {"clusterTrustBundle": {"signerName": "servicedns.podcert.ate.dev/identity", "path": "router-ca.pem",
                                "labelSelector": {"matchLabels": {"podcert.ate.dev/canarying": "live"}}}},
    ]}}


def build_resources(config=None):
    config = config or ApplicationResources()
    trust_name = config.resource_prefix + "-trust-bundles"
    subject = {"kind": "ServiceAccount", "name": config.service_account, "namespace": config.namespace}
    return {"apiVersion": "v1", "kind": "List", "items": [
        namespace_resource(config.namespace),
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": config.service_account, "namespace": config.namespace}},
        # kubectl-ate verifies Substrate's API against the servicedns ClusterTrustBundle, read with this.
        # It is the harness's only Kubernetes permission: its Substrate rights come from the access policy.
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": trust_name},
         "rules": [{"apiGroups": ["certificates.k8s.io"], "resources": ["clustertrustbundles"], "verbs": ["get", "list"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": trust_name},
         "subjects": [subject], "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": trust_name}},
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": config.kubeconfig_name, "namespace": config.namespace},
         "data": {"kubeconfig": json.dumps(in_cluster_kubeconfig(), separators=(",", ":"))}},
        {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": config.artifacts_claim, "namespace": config.namespace},
         "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": config.artifact_storage}}}},
    ]}


def checked(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def kubectl(args, *command, **kwargs):
    return checked(["kubectl", "--context", args.context, "--kubeconfig", str(args.kubeconfig), *command], **kwargs)


def ate(args, *command, **kwargs):
    # The operator's own identity: changing an atespace's access policy needs its owner.
    try:
        return checked(["kubectl", "ate", "--context", args.context, "--kubeconfig", str(args.kubeconfig), *command],
                       capture_output=True, **kwargs)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.decode(errors="replace").strip()[-1000:]
        raise RuntimeError(f"kubectl ate {' '.join(command[:2])} failed: {detail}") from None


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


def grant_access(args, config):
    """Make the harness an editor of its atespace and nothing more, keeping the policy's other bindings.

    Editors create, resume, suspend and delete Actors there. Returns whether the policy changed.
    """
    member = "user:" + config.principal
    stdout = ate(args, "get", "access-policy", "-a", config.atespace, "-o", "json").stdout
    # An atespace without a policy prints nothing: no policy grants nothing.
    policy = json.loads(stdout) if stdout.strip() else None
    verb = "create" if policy is None else "update"
    policy = policy or {"bindings": []}
    bindings = policy.setdefault("bindings", [])
    if any(binding.get("role") in {"editor", "owner"} and member in binding.get("members", []) for binding in bindings):
        return False
    editor = next((binding for binding in bindings if binding.get("role") == "editor"), None)
    if editor is None:
        bindings.append({"role": "editor", "members": [member]})
    else:
        editor.setdefault("members", []).append(member)
    # update sends back the uid and version read above, so it fails if the policy changed in between.
    ate(args, verb, "access-policy", "-a", config.atespace, "-f", "-", input=json.dumps(policy).encode())
    return True


def router_allows(args, config):
    """Whether Substrate's router admits this ServiceAccount's pod certificates; None if it cannot tell.

    The allowlist is the router's --ingress-allowed-spiffe-ids install flag, so only an install can change it.
    """
    router = json.loads(kubectl(args, "get", "deployment", ROUTER, "-n", config.control_namespace, "-o", "json",
                                capture_output=True).stdout)
    for container in router["spec"]["template"]["spec"]["containers"]:
        for arg in container.get("args", []):
            if arg.startswith("--ingress-allowed-spiffe-ids="):
                return config.spiffe_id in arg.split("=", 1)[1].split(",")
    return None


def configure_resources(args):
    config = configuration(args)
    apply(args, build_resources(config))
    # Earlier versions let the harness port-forward to the router, request pod certificates in
    # Substrate's namespace and mint tokens for the shared ate-client owner. Remove that grant.
    kubectl(args, "delete", "rolebinding,role", config.resource_prefix + "-client", "-n", config.control_namespace,
            "--ignore-not-found", capture_output=True)
    changed = grant_access(args, config)
    print(f"Configured application resources. The harness acts as {config.principal}, "
          f"{'now' if changed else 'already'} an editor of atespace {config.atespace} and nothing else.")
    allowed = router_allows(args, config)
    if allowed is False:
        raise RuntimeError(f"Substrate's router does not admit {config.spiffe_id}. Add it to the router's "
                           "--ingress-allowed-spiffe-ids when installing Substrate; scripts/local-kubernetes.sh setup does.")
    if allowed is None:
        print(f"Could not read the router's ingress allowlist; make sure it admits {config.spiffe_id}.")
