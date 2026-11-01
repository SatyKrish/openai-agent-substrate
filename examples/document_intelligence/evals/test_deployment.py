# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
import hashlib
import io
import sys

import pytest
from openai import AsyncOpenAI

from examples.document_intelligence.deploy import cli as agent
from examples.document_intelligence.deploy.run_agent import configuration


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


def test_pod_reads_model_from_environment_and_explicit_override(monkeypatch):
    monkeypatch.delenv("SUBSTRATE_API_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "project-model")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("SUBSTRATE_TEMPLATE", "python-template")
    monkeypatch.setattr(sys, "argv", ["run_agent.py"])
    assert configuration().model == "project-model"
    args = configuration()
    assert args.api_endpoint == "api.ate-system.svc:443"
    # Its own ServiceAccount, not the shared ate-client owner, for both the API and the router.
    assert str(args.token_file) == "/var/run/substrate/api-token"
    assert args.router_url == "https://atenet-router.ate-system.svc"
    assert str(args.router_tls.credential_bundle) == "/var/run/substrate/credential-bundle.pem"
    assert str(args.router_tls.ca_bundle) == "/var/run/substrate/router-ca.pem"
    monkeypatch.setattr(sys, "argv", ["run_agent.py", "--test"])
    assert configuration().model == "scripted"


async def test_openai_sdk_reads_endpoint_and_account_config_without_request(monkeypatch):
    for name, value in dict(OPENAI_API_KEY="canary-not-a-real-key", OPENAI_BASE_URL="https://example.invalid/v1",
                            OPENAI_ORG_ID="org-test", OPENAI_PROJECT_ID="proj_test").items():
        monkeypatch.setenv(name, value)
    async with AsyncOpenAI() as client:
        assert str(client.base_url) == "https://example.invalid/v1/"
        assert client.api_key == "canary-not-a-real-key"
        assert client.organization == "org-test"
        assert client.project == "proj_test"


def test_job_mounts_config_only_into_trusted_harness():
    job = agent.make_job("image@sha256:123", "python-template", "scripted", "smoke-config")
    pod = job["spec"]["template"]["spec"]
    assert len(pod["containers"]) == 1
    assert pod["containers"][0]["args"] == ["--test"]
    assert pod["containers"][0]["envFrom"] == [
        {"configMapRef": {"name": "openai-agent-tracing", "optional": True}},
        {"secretRef": {"name": "smoke-config"}},
    ]
    assert pod["volumes"][0]["secret"]["secretName"] == "smoke-config"
    assert pod["containers"][0]["volumeMounts"][0]["readOnly"] is True
    mounts = {mount["name"]: mount for mount in pod["containers"][0]["volumeMounts"]}
    assert mounts["substrate"] == {"name": "substrate", "mountPath": "/var/run/substrate", "readOnly": True}
    sources = next(volume for volume in pod["volumes"] if volume["name"] == "substrate")["projected"]["sources"]
    assert sources[0]["serviceAccountToken"]["audience"] == "api.ate-system.svc"
    assert job["spec"]["backoffLimit"] == 0
