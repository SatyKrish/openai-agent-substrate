# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from deploy.kubernetes.base.resources import ApplicationResources
from examples.document_intelligence.deploy.job import make_job, make_reader_pod
from examples.document_intelligence.deploy.jaeger import build_jaeger

FIXTURES = Path(__file__).parent / "fixtures/deployment"


def test_job_matches_pre_refactor_deployment():
    actual = make_job("registry.example/harness@sha256:abc", "python-template", "scripted", name="document-test")
    assert actual == json.loads((FIXTURES / "job.json").read_text())


def test_jaeger_matches_pre_refactor_deployment():
    assert build_jaeger() == json.loads((FIXTURES / "jaeger.json").read_text())


def test_reader_matches_pre_refactor_deployment():
    assert make_reader_pod("registry.example/harness@sha256:abc", "reader-test") == json.loads((FIXTURES / "reader.json").read_text())


def test_example_job_uses_explicit_base_configuration():
    config = ApplicationResources(namespace="invoice-app", service_account="invoice-agent", resource_prefix="invoice", artifacts_claim="invoice-reports")
    actual = make_job("image", "template", "live-model", "invoice-config", name="invoice-run", resources=config)
    pod = actual["spec"]["template"]["spec"]
    assert actual["metadata"]["namespace"] == "invoice-app"
    assert pod["serviceAccountName"] == "invoice-agent"
    assert pod["containers"][0]["args"] == ["--model", "live-model"]
    volumes = {item["name"]: item for item in pod["volumes"]}
    assert volumes["kubeconfig"]["configMap"]["name"] == "invoice-kubeconfig"
    assert volumes["artifacts"]["persistentVolumeClaim"]["claimName"] == "invoice-reports"
    assert volumes["agent-config"]["secret"]["secretName"] == "invoice-config"


def test_live_default_and_manifests_do_not_share_mutable_state():
    first = make_job("image", "template")
    second = make_job("image", "template")
    assert "args" not in first["spec"]["template"]["spec"]["containers"][0]
    assert first["metadata"]["name"] != second["metadata"]["name"]
    first["spec"]["template"]["spec"]["containers"][0]["env"].clear()
    assert second["spec"]["template"]["spec"]["containers"][0]["env"]
