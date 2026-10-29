# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import argparse
import os
from pathlib import Path

import pytest

from examples.document_intelligence.main import run

pytestmark = pytest.mark.skipif(
    os.getenv("SUBSTRATE_RUN_KUBERNETES_TESTS") != "1" or not os.getenv("SUBSTRATE_DOCUMENT_TEMPLATE"),
    reason="Requires an explicit local cluster and the deployed Python ActorTemplate",
)


async def test_document_skill_and_code_execution_on_kubernetes(tmp_path):
    args = argparse.Namespace(
        context=os.environ["SUBSTRATE_KUBE_CONTEXT"],
        kubeconfig=Path(os.environ["SUBSTRATE_KUBECONFIG"]),
        atespace="ate-demo-sandbox", template=os.environ["SUBSTRATE_DOCUMENT_TEMPLATE"],
        model="scripted", keep_actor=False, output_dir=tmp_path,
    )
    assert args.context.startswith("kind-"), "This test requires an explicitly selected local Kind cluster"
    assert args.kubeconfig.is_file()
    summary = await run(args)
    assert summary["host_contract_verified"] is True
    assert summary["backend"] == "Agent Substrate on Kubernetes"
    assert summary["tools_used"] == ["apply_patch", "exec_command", "load_skill"]
    assert summary["operating_margins_pct"] == {"ACME": "22.93", "BETA": "30.19"}
