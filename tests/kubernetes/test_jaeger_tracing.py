# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os
from pathlib import Path
import uuid

import pytest

from scripts import jaeger
from scripts import kubernetes_agent as agent

pytestmark = pytest.mark.skipif(
    os.getenv("SUBSTRATE_RUN_JAEGER_TESTS") != "1",
    reason="Requires local Jaeger, its query port forward, and the built Kubernetes agent",
)


def test_kubernetes_agent_exports_linked_sdk_and_sandbox_spans(tmp_path):
    context = os.environ["SUBSTRATE_KUBE_CONTEXT"]
    assert context.startswith("kind-")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=jaeger-test-not-a-real-key\nOPENAI_MODEL=unused-test-model\n")
    args = argparse.Namespace(context=context, kubeconfig=Path(os.environ["SUBSTRATE_KUBECONFIG"]),
        secret_name="jaeger-eval-" + uuid.uuid4().hex[:10], env_file=env_file,
        state_file=agent.ROOT / "artifacts/local/agent-deployment.json",
        template=os.environ["SUBSTRATE_DOCUMENT_TEMPLATE"], model="scripted", wait=False)
    name = None
    try:
        agent.import_secret(args)
        name = agent.run_job(args)
        agent.wait_for_job(args, name)
        logs = agent.kubectl(args, "logs", "job/" + name, "-n", agent.NAMESPACE, capture_output=True).stdout.decode()
        assert "jaeger-test-not-a-real-key" not in logs
        summary = json.loads("\n".join(logs.splitlines()[1:]))
        assert summary["execution_mode"] == "scripted-eval"
        assert summary["api_response_ids"] == []
        assert summary["host_contract_verified"] and summary["actor_api_key_absent"]
        payload = jaeger.fetch_trace(os.getenv("SUBSTRATE_JAEGER_QUERY_URL", "http://127.0.0.1:16686"), summary["tracing"]["otel_trace_id"])
        receipt = jaeger.verify_trace(payload, summary)
        assert receipt["parentage_verified"] and receipt["sandbox_tool_link_verified"]
        encoded = json.dumps(payload)
        assert "jaeger-test-not-a-real-key" not in encoded
        assert "ACME revenue was" not in encoded
    finally:
        if name:
            agent.kubectl(args, "delete", "job", name, "-n", agent.NAMESPACE, "--ignore-not-found", capture_output=True)
        agent.kubectl(args, "delete", "secret", args.secret_name, "-n", agent.NAMESPACE, "--ignore-not-found", capture_output=True)
