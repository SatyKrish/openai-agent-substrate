# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
import argparse
import base64
import json
import subprocess
from types import SimpleNamespace

import pytest

from deploy.kubernetes.base import resources


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

    monkeypatch.setattr(resources, "kubectl", kubectl)
    monkeypatch.setattr(resources, "apply", lambda args, value: applied.append(value))
    args = argparse.Namespace(env_file=path, secret_name="test-config")
    resources.import_secret(args)
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

    monkeypatch.setattr(resources, "kubectl", fail)
    with pytest.raises(ValueError, match="Cannot parse .env") as error:
        resources.import_secret(argparse.Namespace(env_file=path, secret_name="test-config"))
    assert "canary-value" not in str(error.value)
    assert "canary-value" not in capsys.readouterr().out


@pytest.mark.parametrize("names", [[], ["router-a", "router-b"]])
def test_forwarding_is_restricted_to_discovered_router_pods(monkeypatch, names):
    def kubectl(args, *command, **kwargs):
        if command[:2] == ("get", "service"):
            result = {"spec": {"selector": {"app": "atenet-router"}}}
        else:
            assert "app=atenet-router" in command
            result = {"items": [{"metadata": {"name": name}} for name in names]}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result).encode())

    monkeypatch.setattr(resources, "kubectl", kubectl)
    if not names:
        with pytest.raises(RuntimeError, match="router pods"):
            resources.prepare_resources(SimpleNamespace())
        return
    manifest = resources.prepare_resources(SimpleNamespace())
    role = next(item for item in manifest["items"] if item["kind"] == "Role")
    forwards = [rule for rule in role["rules"] if "pods/portforward" in rule["resources"]]
    assert forwards and all(rule["resourceNames"] == names for rule in forwards)
    assert not any("pods" in rule["resources"] and "get" in rule["verbs"] and "resourceNames" not in rule for rule in role["rules"])


def test_base_matches_pre_refactor_deployment():
    from pathlib import Path
    expected = json.loads((Path(__file__).parent / "fixtures/base.json").read_text())
    assert resources.build_resources(["router-b", "router-a", "router-a"]) == expected


def test_independent_application_permissions_and_connection_settings():
    config = resources.ApplicationResources(namespace="invoice-app", service_account="invoice-agent", resource_prefix="invoice", artifacts_claim="invoice-reports", control_namespace="substrate-system")
    manifest = resources.build_resources(["router-a"], config)
    by_kind = {item["kind"]: item for item in manifest["items"]}
    assert by_kind["Namespace"]["metadata"]["name"] == "invoice-app"
    assert by_kind["Role"]["metadata"] == {"name": "invoice-client", "namespace": "substrate-system"}
    assert by_kind["RoleBinding"]["subjects"] == [{"kind": "ServiceAccount", "name": "invoice-agent", "namespace": "invoice-app"}]
    assert by_kind["ClusterRoleBinding"]["metadata"]["name"] == "invoice-trust-bundles"
    assert by_kind["ConfigMap"]["metadata"]["name"] == "invoice-kubeconfig"
    assert by_kind["PersistentVolumeClaim"]["metadata"]["name"] == "invoice-reports"


def test_empty_forwarding_grant_is_never_built():
    with pytest.raises(ValueError, match="Router pod names"):
        resources.build_resources([])


def test_resource_builders_return_independent_objects():
    first = resources.build_resources(["router-a"])
    first["items"].clear()
    assert resources.build_resources(["router-a"])["items"]
