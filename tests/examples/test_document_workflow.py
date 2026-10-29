# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples.document_intelligence.main import EXAMPLE, digests, run, stage_inputs
from examples.document_intelligence.verify import OUTPUTS, verify_artifacts

HELPER = EXAMPLE / "skills/document-analysis/scripts/documents.py"


async def test_skill_drives_real_sdk_tools_and_artifacts(server, tmp_path):
    args = argparse.Namespace(atespace="test-space", template="document-intelligence", model="scripted",
                              keep_actor=False, output_dir=tmp_path)
    summary = await run(args, server)
    assert summary["host_contract_verified"] is True
    assert summary["model_inference"] is False
    assert summary["tools_used"] == ["apply_patch", "exec_command", "load_skill"]
    assert summary["operating_margins_pct"] == {"ACME": "22.93", "BETA": "30.19"}
    directory = Path(summary["artifact_directory"])
    artifacts = {name: (directory / name).read_bytes() for name in OUTPUTS}
    assert verify_artifacts(artifacts, stage_inputs(), summary["analysis_id"])["revenue_gap_usd_billions"] == "117.8"
    assert (directory / "analysis.py").is_file()
    trace = json.loads((directory / "tool-trace.json").read_text())
    calls = [item["name"] for item in trace if item["type"] in {"function_call", "custom_tool_call"}]
    assert calls == ["load_skill", "exec_command", "exec_command", "exec_command", "apply_patch",
                     "exec_command", "exec_command", "exec_command"]
    assert (server.root / ".agents/document-analysis/SKILL.md").exists()
    assert [event[0] for event in server.control.events][-1] == "delete"


@pytest.fixture
def generated(tmp_path):
    inputs = stage_inputs()
    (tmp_path / "inputs").mkdir()
    for name, data in inputs.items():
        (tmp_path / "inputs" / name).write_bytes(data)
    (tmp_path / "inputs/manifest.json").write_text(json.dumps(dict(analysis_id="test-analysis", files=digests(inputs))))
    subprocess.run([sys.executable, str(HELPER), "extract"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run([sys.executable, str(EXAMPLE / "scripted_analysis.py")], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path, inputs


@pytest.mark.parametrize("corruption", ["citation", "margin", "limitations", "csv", "markdown", "extra"])
def test_host_rejects_corrupted_artifacts(generated, corruption):
    root, inputs = generated
    artifacts = {name: (root / "output" / name).read_bytes() for name in OUTPUTS}
    if corruption in {"citation", "margin", "limitations"}:
        result = json.loads(artifacts["result.json"])
        if corruption == "citation":
            result["citations"][0]["quote"] = "Invented source quotation."
        elif corruption == "margin":
            result["rows"][0]["operating_margin_pct"] = "99.99"
        else:
            result["limitations"] = []
        artifacts["result.json"] = json.dumps(result).encode()
    elif corruption == "csv":
        artifacts["kpis.csv"] = artifacts["kpis.csv"].replace(b"94.2", b"999.0")
    elif corruption == "markdown":
        artifacts["comparison.md"] = artifacts["comparison.md"].replace(b"Operating income was USD", b"Invented income was USD")
    else:
        artifacts["unapproved.txt"] = b"extra"
    with pytest.raises(ValueError):
        verify_artifacts(artifacts, inputs, "test-analysis")


def test_sandbox_verifier_and_staged_input_integrity(generated):
    root, _ = generated
    subprocess.run([sys.executable, str(HELPER), "verify"], cwd=root, check=True, capture_output=True)
    report = root / "inputs/acme-2024.md"
    report.write_bytes(report.read_bytes().replace(b"94.2", b"99.9"))
    result = subprocess.run([sys.executable, str(HELPER), "verify"], cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Input digest mismatch" in result.stderr


def test_reconciles_kpis_even_when_staged_digest_is_valid(generated):
    root, _ = generated
    kpis = root / "inputs/kpis-2024.csv"
    kpis.write_bytes(kpis.read_bytes().replace(b"94.2", b"99.9"))
    manifest = json.loads((root / "inputs/manifest.json").read_text())
    manifest["files"][kpis.name] = dict(bytes=kpis.stat().st_size, sha256=hashlib.sha256(kpis.read_bytes()).hexdigest())
    (root / "inputs/manifest.json").write_text(json.dumps(manifest))
    result = subprocess.run([sys.executable, str(HELPER), "extract"], cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert "KPI CSV disagrees" in result.stderr
