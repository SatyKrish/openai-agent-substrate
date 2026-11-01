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


def test_harness_holds_no_kubernetes_rights_in_substrates_namespace():
    manifest = resources.build_resources()
    kinds = [item["kind"] for item in manifest["items"]]
    assert "Role" not in kinds and "RoleBinding" not in kinds
    role = next(item for item in manifest["items"] if item["kind"] == "ClusterRole")
    assert role["rules"] == [{"apiGroups": ["certificates.k8s.io"], "resources": ["clustertrustbundles"], "verbs": ["get", "list"]}]
    text = json.dumps(manifest)
    assert not any(grant in text for grant in ("pods/portforward", "serviceaccounts/token", "podcertificaterequests", "ate-client"))


def test_base_matches_deployment_fixture():
    from pathlib import Path
    expected = json.loads((Path(__file__).parent / "fixtures/base.json").read_text())
    assert resources.build_resources() == expected


def test_independent_application_identity_and_resources():
    config = resources.ApplicationResources(namespace="invoice-app", service_account="invoice-agent", resource_prefix="invoice",
                                            artifacts_claim="invoice-reports", atespace="invoices")
    by_kind = {item["kind"]: item for item in resources.build_resources(config)["items"]}
    assert by_kind["Namespace"]["metadata"]["name"] == "invoice-app"
    assert by_kind["ClusterRoleBinding"]["metadata"]["name"] == "invoice-trust-bundles"
    assert by_kind["ClusterRoleBinding"]["subjects"] == [{"kind": "ServiceAccount", "name": "invoice-agent", "namespace": "invoice-app"}]
    assert by_kind["ConfigMap"]["metadata"]["name"] == "invoice-kubeconfig"
    assert by_kind["PersistentVolumeClaim"]["metadata"]["name"] == "invoice-reports"
    assert config.principal == "system:serviceaccount:invoice-app:invoice-agent"
    assert config.spiffe_id == "spiffe://cluster.local/ns/invoice-app/sa/invoice-agent"


def test_substrate_volume_projects_the_pods_own_credentials():
    sources = resources.substrate_volume()["projected"]["sources"]
    token, certificate, router_ca = sources
    assert token["serviceAccountToken"]["audience"] == "api.ate-system.svc"
    assert certificate["podCertificate"]["signerName"] == "podidentity.podcert.ate.dev/identity"
    assert router_ca["clusterTrustBundle"]["signerName"] == "servicedns.podcert.ate.dev/identity"
    assert {token["serviceAccountToken"]["path"], certificate["podCertificate"]["credentialBundlePath"],
            router_ca["clusterTrustBundle"]["path"]} == {"api-token", "credential-bundle.pem", "router-ca.pem"}


def fake_ate(monkeypatch, current):
    calls = []

    def ate(args, *command, **kwargs):
        calls.append((command, json.loads(kwargs["input"]) if "input" in kwargs else None))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(current).encode() if current else b"")

    monkeypatch.setattr(resources, "ate", ate)
    return calls


MEMBER = "user:system:serviceaccount:openai-agent-substrate:openai-agent"


def test_access_is_granted_as_atespace_editor_when_there_is_no_policy(monkeypatch):
    calls = fake_ate(monkeypatch, None)
    assert resources.grant_access(SimpleNamespace(), resources.ApplicationResources()) is True
    assert calls[1] == (("create", "access-policy", "-a", "ate-demo-sandbox", "-f", "-"),
                        {"bindings": [{"role": "editor", "members": [MEMBER]}]})


def test_access_grant_keeps_other_bindings_and_the_policy_version(monkeypatch):
    current = {"metadata": {"name": "default", "uid": "u-1", "version": "7"}, "bindings": [
        {"role": "owner", "members": ["user:alice"]}, {"role": "editor", "members": ["user:bob"]}]}
    calls = fake_ate(monkeypatch, current)
    assert resources.grant_access(SimpleNamespace(), resources.ApplicationResources()) is True
    command, policy = calls[1]
    assert command[0] == "update"
    assert policy["metadata"] == {"name": "default", "uid": "u-1", "version": "7"}
    assert policy["bindings"] == [{"role": "owner", "members": ["user:alice"]}, {"role": "editor", "members": ["user:bob", MEMBER]}]


def test_existing_access_is_left_alone(monkeypatch):
    calls = fake_ate(monkeypatch, {"metadata": {"uid": "u-1", "version": "1"}, "bindings": [{"role": "editor", "members": [MEMBER]}]})
    assert resources.grant_access(SimpleNamespace(), resources.ApplicationResources()) is False
    assert len(calls) == 1


def router(args):
    return {"spec": {"template": {"spec": {"containers": [{"name": "atenet-router", "args": args}]}}}}


@pytest.mark.parametrize(("args", "expected"), [
    (["--port-connect-tls=8444", "--ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client,"
      "spiffe://cluster.local/ns/openai-agent-substrate/sa/openai-agent"], True),
    (["--ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client"], False),
    (["--port-connect-tls=8444"], None),
])
def test_router_allowlist_is_read_from_its_install_flag(monkeypatch, args, expected):
    monkeypatch.setattr(resources, "kubectl", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=json.dumps(router(args)).encode()))
    assert resources.router_allows(SimpleNamespace(), resources.ApplicationResources()) is expected


def test_configure_removes_the_old_grant_and_refuses_a_router_that_rejects_the_harness(monkeypatch, capsys):
    applied, commands = [], []

    def kubectl(args, *command, **kwargs):
        commands.append(command)
        stdout = json.dumps(router(["--ingress-allowed-spiffe-ids=spiffe://cluster.local/ns/ate-system/sa/atenet-client"]))
        return subprocess.CompletedProcess(command, 0, stdout=stdout.encode())

    monkeypatch.setattr(resources, "apply", lambda args, value: applied.append(value))
    monkeypatch.setattr(resources, "kubectl", kubectl)
    fake_ate(monkeypatch, None)
    with pytest.raises(RuntimeError, match="does not admit spiffe://cluster.local/ns/openai-agent-substrate/sa/openai-agent"):
        resources.configure_resources(SimpleNamespace())
    assert applied == [resources.build_resources()]
    assert commands[0] == ("delete", "rolebinding,role", "openai-agent-client", "-n", "ate-system", "--ignore-not-found")
    assert "now an editor of atespace ate-demo-sandbox" in capsys.readouterr().out


def test_resource_builders_return_independent_objects():
    first = resources.build_resources()
    first["items"].clear()
    assert resources.build_resources()["items"]
