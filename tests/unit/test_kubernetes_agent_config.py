# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import argparse
import base64
import json
import subprocess
import sys

import pytest
from openai import AsyncOpenAI

from deploy.kubernetes.run_agent import configuration
from scripts import kubernetes_agent


def test_secret_import_uses_stdin_and_does_not_log_values(monkeypatch, tmp_path, capsys):
    path = tmp_path / ".env"
    key = "canary-not-a-real-api-key"
    path.write_text(f"OPENAI_API_KEY={key}\nOPENAI_MODEL=example-model\n")
    resource = dict(apiVersion="v1", kind="Secret", metadata=dict(name="test-config"),
                    data=dict(OPENAI_API_KEY=base64.b64encode(key.encode()).decode()))
    calls, applied = [], []

    def kubectl(args, *command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(resource).encode())

    monkeypatch.setattr(kubernetes_agent, "kubectl", kubectl)
    monkeypatch.setattr(kubernetes_agent, "apply", lambda args, value: applied.append(value))
    args = argparse.Namespace(env_file=path, secret_name="test-config")
    kubernetes_agent.import_secret(args)
    assert len(applied) == 2
    assert applied[0]["metadata"]["labels"]["ate.dev/api-access"] == "true"
    assert applied[1]["data"]["OPENAI_API_KEY"] == resource["data"]["OPENAI_API_KEY"]
    assert "--from-env-file=" + str(path) in calls[0][0]
    assert key not in repr(calls)
    assert key not in capsys.readouterr().out


def test_environment_parser_failures_do_not_disclose_input(monkeypatch, tmp_path, capsys):
    path = tmp_path / ".env"
    path.write_text("bad line with canary-value\n")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["kubectl"], stderr=b"bad line with canary-value")

    monkeypatch.setattr(kubernetes_agent, "kubectl", fail)
    with pytest.raises(ValueError, match="Cannot parse .env") as error:
        kubernetes_agent.import_secret(argparse.Namespace(env_file=path, secret_name="test-config"))
    assert "canary-value" not in str(error.value)
    assert "canary-value" not in capsys.readouterr().out


def test_pod_reads_model_from_environment_and_explicit_override(monkeypatch):
    monkeypatch.delenv("SUBSTRATE_API_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "project-model")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("SUBSTRATE_TEMPLATE", "python-template")
    monkeypatch.setattr(sys, "argv", ["run_agent.py"])
    assert configuration().model == "project-model"
    assert configuration().api_endpoint == "api.ate-system.svc:443"
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
    job = kubernetes_agent.make_job("image@sha256:123", "python-template", "scripted", "smoke-config")
    pod = job["spec"]["template"]["spec"]
    assert len(pod["containers"]) == 1
    assert pod["containers"][0]["args"] == ["--test"]
    assert pod["containers"][0]["envFrom"] == [
        {"configMapRef": {"name": "openai-agent-tracing", "optional": True}},
        {"secretRef": {"name": "smoke-config"}},
    ]
    assert pod["volumes"][0]["secret"]["secretName"] == "smoke-config"
    assert pod["containers"][0]["volumeMounts"][0]["readOnly"] is True
    assert job["spec"]["backoffLimit"] == 0
