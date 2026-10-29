# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Application-independent Kubernetes permissions and connection resources."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
MANIFESTS = Path(__file__).parent
NAMESPACE = "openai-agent-substrate"


def checked(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def kubectl(args, *command, **kwargs):
    return checked(["kubectl", "--context", args.context, "--kubeconfig", str(args.kubeconfig), *command], **kwargs)


def apply(args, resource):
    # Use stdin, never command-line secret values or a persisted Secret manifest.
    return kubectl(args, "apply", "--server-side", "--field-manager=openai-agent-substrate", "-f", "-", "-o", "name",
                   input=json.dumps(resource).encode(), capture_output=True)


def namespace_resource():
    resources = json.loads((MANIFESTS / "resources.json").read_text())
    return next(item for item in resources["items"] if item["kind"] == "Namespace")


def import_secret(args):
    if not args.env_file.is_file():
        raise ValueError("No .env file found. Copy .env.example to .env and fill in the configuration.")
    # kubectl parses plain KEY=VALUE lines; .env is never executed as a shell script.
    try:
        result = kubectl(args, "create", "secret", "generic", args.secret_name, "-n", NAMESPACE,
                         "--from-env-file=" + str(args.env_file), "--dry-run=client", "-o", "json", capture_output=True)
        resource = json.loads(result.stdout)
    except subprocess.CalledProcessError:
        # Parser errors can contain the offending line. Do not expose it.
        raise ValueError("Cannot parse .env; use plain KEY=VALUE lines without export statements") from None
    if not resource.get("data"):
        raise ValueError("The environment file contains no configuration entries")
    # Preserve the label required by Substrate API ingress on every Secret refresh.
    apply(args, namespace_resource())
    resource["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "openai-agent-substrate"}
    try:
        apply(args, resource)
    except subprocess.CalledProcessError:
        raise RuntimeError("Could not apply the agent configuration Secret; values were not logged") from None
    print(f"Imported configuration into {NAMESPACE}/{args.secret_name}; values omitted.")


def prepare_resources(args):
    service = json.loads(kubectl(args, "get", "service", "atenet-router", "-n", "ate-system", "-o", "json", capture_output=True).stdout)
    selector = service["spec"].get("selector", {})
    if not selector:
        raise RuntimeError("The router service has no selector; refusing an unrestricted forwarding grant")
    labels = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
    pods = json.loads(kubectl(args, "get", "pods", "-n", "ate-system", "-l", labels, "-o", "json", capture_output=True).stdout)
    names = sorted(pod["metadata"]["name"] for pod in pods["items"] if not pod["metadata"].get("deletionTimestamp"))
    if not names:
        raise RuntimeError("No router pods found; refusing an unrestricted forwarding grant")
    resources = json.loads((MANIFESTS / "resources.json").read_text())
    role = next(item for item in resources["items"] if item["kind"] == "Role")
    for rule in role["rules"]:
        if set(rule["resources"]) & {"pods", "pods/portforward"} and "resourceNames" in rule:
            rule["resourceNames"] = names
    return resources


def configure_resources(args):
    apply(args, prepare_resources(args))
    print("Configured harness resources with forwarding restricted to current router pods.")
