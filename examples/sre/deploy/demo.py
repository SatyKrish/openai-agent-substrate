# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""A demo namespace with one healthy and two broken workloads for the SRE agent to investigate.

The workloads reuse the SRE sandbox image, which the local registry already
holds, with their own commands, so the demo needs no other image.
"""

from __future__ import annotations

from deploy.kubernetes.base.resources import dns_name

NAMESPACE = "sre-demo"


def deployment(name: str, image: str, command: list[str] | None, namespace: str) -> dict:
    container = {"name": name, "image": image, "resources": {"requests": {"cpu": "10m", "memory": "16Mi"},
                                                             "limits": {"memory": "64Mi"}}}
    if command:
        container["command"] = command
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": namespace,
                                                                         "labels": {"app": name}},
            "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                     "template": {"metadata": {"labels": {"app": name}}, "spec": {"containers": [container]}}}}


def resources(image: str, namespace: str = NAMESPACE) -> dict:
    """``image`` is the SRE sandbox image's digest reference; ``catalog`` asks for a tag that was never pushed."""
    dns_name(namespace)
    registry = image.split("@", 1)[0].rsplit("/", 1)[0]
    return {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}},
        deployment("frontend", image, ["sh", "-c", "echo 'frontend 1.8.0 serving on :8080'; exec sleep 2147483647"],
                   namespace),
        deployment("checkout", image, ["sh", "-c", "echo 'checkout 2.3.1 starting'; echo 'loading configuration from "
                                       "environment'; echo 'FATAL: DATABASE_URL is not set; cannot connect to orders "
                                       "database' >&2; exit 1"], namespace),
        deployment("catalog", f"{registry}/sre-demo-catalog:1.4.2", None, namespace),
    ]}
