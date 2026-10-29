# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import hashlib
import io
import json
import subprocess
from types import SimpleNamespace

import pytest

from scripts import kubernetes_agent as agent


def test_download_rejects_binary_even_if_remote_checksum_agrees(monkeypatch, tmp_path):
    body = b"tampered executable and checksum from the same origin"
    requests = []

    def fake_open(url, **kwargs):
        requests.append(url)
        return io.BytesIO(hashlib.sha256(body).hexdigest().encode() if url.endswith(".sha256") else body)

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_open)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        agent.download_kubectl(tmp_path / "kubectl", "amd64")
    assert not any(url.endswith(".sha256") for url in requests)


def test_image_digest_error_is_actionable():
    with pytest.raises(RuntimeError, match="requested image repository"):
        agent.resolve_image_digest("registry:5000/harness:tag", ["another/image@sha256:abc"])
    assert agent.resolve_image_digest("registry:5000/harness:tag", ["registry:5000/harness@sha256:abc"]) == "registry:5000/harness@sha256:abc"


def test_reader_meets_restricted_pod_security_contract():
    pod = agent.make_reader_pod("image@sha256:abc", "reader")
    spec = pod["spec"]
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["automountServiceAccountToken"] is False
    assert spec["containers"][0]["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert spec["containers"][0]["securityContext"]["allowPrivilegeEscalation"] is False


@pytest.mark.parametrize("names", [[], ["router-a", "router-b"]])
def test_forwarding_is_restricted_to_discovered_router_pods(monkeypatch, names):
    def kubectl(args, *command, **kwargs):
        if command[:2] == ("get", "service"):
            result = {"spec": {"selector": {"app": "atenet-router"}}}
        else:
            assert "app=atenet-router" in command
            result = {"items": [{"metadata": {"name": name}} for name in names]}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result).encode())

    monkeypatch.setattr(agent, "kubectl", kubectl)
    if not names:
        with pytest.raises(RuntimeError, match="router pods"):
            agent.prepare_resources(SimpleNamespace())
        return
    resources = agent.prepare_resources(SimpleNamespace())
    role = next(item for item in resources["items"] if item["kind"] == "Role")
    forwards = [rule for rule in role["rules"] if "pods/portforward" in rule["resources"]]
    assert forwards and all(rule["resourceNames"] == names for rule in forwards)
    assert not any("pods" in rule["resources"] and "get" in rule["verbs"] and "resourceNames" not in rule for rule in role["rules"])
