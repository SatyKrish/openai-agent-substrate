# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The SRE agent's read-only cluster identity and the short-lived kubeconfig its harness tools use.

A ServiceAccount in the harness namespace gets a Role in each namespace the agent
may investigate: get, list and watch on pods, their logs, events and workload
objects. No Secrets, no ConfigMaps, no write verbs, nothing cluster-wide.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from deploy.kubernetes.base.resources import NAMESPACE, dns_name

SERVICE_ACCOUNT = "sre-agent-reader"
RULES = [
    {"apiGroups": [""], "resources": ["pods", "pods/log", "events", "services", "endpoints"],
     "verbs": ["get", "list", "watch"]},
    {"apiGroups": ["apps"], "resources": ["deployments", "replicasets", "statefulsets", "daemonsets"],
     "verbs": ["get", "list", "watch"]},
    {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["get", "list", "watch"]},
    {"apiGroups": ["events.k8s.io"], "resources": ["events"], "verbs": ["get", "list", "watch"]},
]


def resources(namespaces) -> dict:
    items = [{"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": SERVICE_ACCOUNT, "namespace": NAMESPACE}}]
    for namespace in sorted(namespaces):
        dns_name(namespace)
        items.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
                      "metadata": {"name": SERVICE_ACCOUNT, "namespace": namespace}, "rules": RULES})
        items.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                      "metadata": {"name": SERVICE_ACCOUNT, "namespace": namespace},
                      "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": SERVICE_ACCOUNT},
                      "subjects": [{"kind": "ServiceAccount", "name": SERVICE_ACCOUNT, "namespace": NAMESPACE}]})
    return {"apiVersion": "v1", "kind": "List", "items": items}


def reader_kubeconfig(cluster: dict, token: str) -> dict:
    """A kubeconfig with only the reader's token: no client certificate, so the admin identity cannot leak in."""
    return {"apiVersion": "v1", "kind": "Config", "current-context": "sre-reader",
            "clusters": [{"name": "cluster", "cluster": cluster}],
            "users": [{"name": SERVICE_ACCOUNT, "user": {"token": token}}],
            "contexts": [{"name": "sre-reader", "context": {"cluster": "cluster", "user": SERVICE_ACCOUNT}}]}


def mint(context: str, kubeconfig: Path | None, output: Path, duration_s: int = 3600) -> float:
    """Request a token for the reader with the operator's credentials and write its kubeconfig; returns its expiry."""
    base = ["kubectl", "--context", context] + (["--kubeconfig", str(kubeconfig)] if kubeconfig else [])
    view = json.loads(subprocess.run([*base, "config", "view", "--raw", "--minify", "--flatten", "-o", "json"],
                                     check=True, capture_output=True, text=True).stdout)
    cluster = view["clusters"][0]["cluster"]
    token = subprocess.run([*base, "create", "token", SERVICE_ACCOUNT, "--namespace", NAMESPACE,
                            "--duration", f"{duration_s}s"], check=True, capture_output=True, text=True).stdout.strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(".part")
    partial.write_text(json.dumps(reader_kubeconfig(cluster, token)))
    os.chmod(partial, 0o600)
    partial.replace(output)
    return time.time() + duration_s
