# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import kubernetes_agent as agent

pytestmark = pytest.mark.skipif(
    os.getenv("SUBSTRATE_RUN_AGENT_POD_TESTS") != "1",
    reason="Requires an explicitly selected local Kind cluster and the built agent harness image",
)


def test_env_secret_mount_and_endpoint_model_configuration(tmp_path):
    context = os.environ["SUBSTRATE_KUBE_CONTEXT"]
    assert context.startswith("kind-")
    canary = "canary-not-a-real-openai-key"
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join([
        f"OPENAI_API_KEY={canary}", "OPENAI_BASE_URL=https://example.invalid/v1",
        "OPENAI_MODEL=configured-live-model", "OPENAI_ORG_ID=org-test", "OPENAI_PROJECT_ID=proj_test", "",
    ]))
    args = argparse.Namespace(context=context, kubeconfig=Path(os.environ["SUBSTRATE_KUBECONFIG"]),
        env_file=env_file, secret_name="openai-agent-env-smoke", state_file=agent.ROOT / "artifacts/local/agent-deployment.json",
        template=os.environ["SUBSTRATE_DOCUMENT_TEMPLATE"], model="scripted", wait=False)
    name = None
    try:
        agent.import_secret(args)
        name = agent.run_job(args)
        agent.wait_for_job(args, name)
        logs = agent.kubectl(args, "logs", "job/" + name, "-n", agent.NAMESPACE, capture_output=True).stdout.decode()
        assert canary not in logs
        lines = logs.splitlines()
        mounted = json.loads(lines[0])
        summary = json.loads("\n".join(lines[1:]))
        assert mounted["mounted_environment_matches"] is True
        assert set(mounted["keys"]) == {"OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"}
        assert summary["model"] == "scripted"
        assert summary["model_inference"] is False
        assert summary["execution_mode"] == "scripted-eval"
        assert summary["api_response_ids"] == []
        assert summary["host_contract_verified"] is True
        assert summary["actor_api_key_absent"] is True
        assert summary["tools_used"] == ["apply_patch", "exec_command", "load_skill"]
    finally:
        if name:
            agent.kubectl(args, "delete", "job", name, "-n", agent.NAMESPACE, "--ignore-not-found", capture_output=True)
        agent.kubectl(args, "delete", "secret", args.secret_name, "-n", agent.NAMESPACE, "--ignore-not-found", capture_output=True)


def test_harness_cannot_forward_to_control_plane_pods():
    args = argparse.Namespace(context=os.environ["SUBSTRATE_KUBE_CONTEXT"], kubeconfig=Path(os.environ["SUBSTRATE_KUBECONFIG"]))
    assert args.context.startswith("kind-")
    identity = "system:serviceaccount:openai-agent-substrate:openai-agent"
    pods = json.loads(agent.kubectl(args, "get", "pods", "-n", "ate-system", "-o", "json", capture_output=True).stdout)["items"]
    for app, expected in [("atenet-router", True), ("ate-api-server", False)]:
        name = next(p["metadata"]["name"] for p in pods if p["metadata"].get("labels", {}).get("app") == app)
        prefix = ["kubectl", "--context", args.context, "--kubeconfig", str(args.kubeconfig)]
        result = subprocess.run(prefix + ["auth", "can-i", "create", "pods/" + name, "--subresource=portforward", "-n", "ate-system", "--as", identity], capture_output=True, text=True)
        assert (result.returncode == 0) == expected
