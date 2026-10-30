# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("chatkit", reason="install the ui dependency group: uv sync --group ui")

from openai_harness.chat import HarnessChatServer  # noqa: E402
from openai_harness.spec import Conversation  # noqa: E402
from openai_harness.store import SQLiteStore  # noqa: E402
from openai_harness.testing import Calls, events, message, shell  # noqa: E402
from examples.sre.agent.lib import findings  # noqa: E402
from examples.sre.agent.spec import REPORT, make_spec  # noqa: E402
from examples.sre.agent.tools import cluster  # noqa: E402
from examples.sre.deploy import access, demo  # noqa: E402
from examples.sre.ui.card import check_rows  # noqa: E402
from agent_substrate import SubstrateSandboxClient  # noqa: E402

TRIAGE = Path(__file__).resolve().parents[1] / "agent/skills/kubernetes-triage/scripts/triage.py"
PODS = """NAME                        READY   STATUS             RESTARTS      AGE   IP            NODE
checkout-6d8f9c7b5d-x2k4p   0/1     CrashLoopBackOff   5 (40s ago)   4m    10.244.0.31   kind
catalog-7c9d8b6f4-q8w2r     0/1     ImagePullBackOff   0             4m    10.244.0.32   kind
frontend-5b7c8d9e6f-a1b2c   1/1     Running            0             4m    10.244.0.30   kind
"""
LOGS = "checkout 2.3.1 starting\nFATAL: DATABASE_URL is not set; cannot connect to orders database\n"


def test_cluster_tools_only_build_read_commands_inside_their_scope():
    assert cluster.argv_list("pods", "sre-demo") == ["get", "pods", "--namespace", "sre-demo", "--output", "wide"]
    assert cluster.argv_logs("checkout-1", "sre-demo", None, True, 9999)[-3:] == ["--tail", "2000", "--previous"]
    for refused in (lambda: cluster.argv_list("secrets", "sre-demo"), lambda: cluster.argv_list("configmaps", "sre-demo"),
                    lambda: cluster.argv_describe("secret", "db", "sre-demo"),
                    lambda: cluster.argv_logs("--all-namespaces", "sre-demo", None, False, 10),
                    lambda: cluster.argv_describe("pod", "x;rm -rf /", "sre-demo")):
        with pytest.raises(cluster.Refused):
            refused()
    scoped = cluster.ClusterAccess(kubeconfig=Path("reader"), namespaces=frozenset({"sre-demo"}))
    with pytest.raises(cluster.Refused):
        scoped.namespace("kube-system")


def test_the_reader_identity_reads_workloads_only():
    items = access.resources(["sre-demo"])["items"]
    role = next(item for item in items if item["kind"] == "Role")
    granted = {resource for rule in role["rules"] for resource in rule["resources"]}
    assert {"pods", "pods/log", "events", "deployments"} <= granted
    assert not granted & {"secrets", "configmaps", "pods/exec", "pods/portforward"}
    assert {verb for rule in role["rules"] for verb in rule["verbs"]} == {"get", "list", "watch"}
    assert not [item for item in items if item["kind"].startswith("Cluster")]
    user = access.reader_kubeconfig({"server": "https://127.0.0.1:6443"}, "token")["users"][0]["user"]
    assert user == {"token": "token"}  # no client certificate, so the operator's identity cannot be used


def test_demo_workloads_break_in_two_ways():
    items = demo.resources("localhost:5501/openai-agent-substrate-sre@sha256:abc")["items"]
    images = {item["metadata"]["name"]: item["spec"]["template"]["spec"]["containers"][0]["image"]
              for item in items if item["kind"] == "Deployment"}
    assert images["catalog"] == "localhost:5501/sre-demo-catalog:1.4.2"
    checkout = next(item for item in items if item["metadata"]["name"] == "checkout")
    assert "DATABASE_URL is not set" in " ".join(checkout["spec"]["template"]["spec"]["containers"][0]["command"])


def test_triage_script_lists_unhealthy_pods(tmp_path):
    (tmp_path / "001-pods-sre-demo.txt").write_text(PODS)
    output = subprocess.run([sys.executable, str(TRIAGE), str(tmp_path / "001-pods-sre-demo.txt")],
                            capture_output=True, text=True, check=True).stdout
    assert "checkout-6d8f9c7b5d-x2k4p: status CrashLoopBackOff, 5 restarts" in output
    assert "catalog-7c9d8b6f4-q8w2r: status ImagePullBackOff" in output
    assert "frontend" not in output


def conversation_with(tmp_path, text, sandbox_text=None):
    from chatkit.types import ThreadMetadata
    from datetime import datetime

    entry = {"path": "evidence/001-logs-checkout.txt", "label": "logs-checkout", "turn": 1, "bytes": len(text), "sha256": "x"}
    host = tmp_path / "thr" / "turn-1" / entry["path"]
    host.parent.mkdir(parents=True)
    host.write_text(text)
    thread = ThreadMetadata(id="thr", created_at=datetime.now(), metadata={"evidence": [entry]})
    return Conversation(thread, tmp_path / "thr")


def findings_file(*evidence, resource="deployment/checkout"):
    return {"output/findings.json": json.dumps({"findings": [{"resource": resource, "evidence": list(evidence)}]}).encode(),
            "output/findings.md": b"# Findings"}


def test_findings_quotes_are_checked_against_the_host_copy_of_the_evidence(tmp_path):
    conversation = conversation_with(tmp_path, LOGS)
    quote = {"file": "evidence/001-logs-checkout.txt", "quote": "FATAL: DATABASE_URL is not set;   cannot connect"}
    passed = findings.check(conversation, findings_file(quote))
    assert passed == {"status": "evidence_passed", "findings": 1, "quotes": 1, "resources": ["deployment/checkout"],
                      "unchecked": ["findings.md"]}
    invented = findings.check(conversation, findings_file({**quote, "quote": "OOMKilled"}))
    assert invented["status"] == "failed" and "Not found in evidence/001-logs-checkout.txt" in invented["detail"]
    unrecorded = findings.check(conversation, findings_file({**quote, "file": "evidence/999-made-up.txt"}))
    assert unrecorded["status"] == "failed" and "no harness tool wrote" in unrecorded["detail"]
    assert findings.check(conversation, findings_file())["status"] == "failed"  # a finding needs evidence
    assert findings.check(conversation, {"output/findings.md": b"x"})["status"] == "no_findings"


class FakeCluster(cluster.ClusterAccess):
    async def kubectl(self, *args):
        self.calls.append(args)
        return PODS if args[:2] == ("get", "pods") else LOGS


async def test_an_investigation_reads_in_the_harness_and_is_checked(server, tmp_path):
    reader = FakeCluster(kubeconfig=tmp_path / "reader", namespaces=frozenset({"sre-demo"}))
    reader.calls = []
    citation = json.dumps({"findings": [{"resource": "deployment/checkout", "namespace": "sre-demo",
                                         "problem": "CrashLoopBackOff", "cause": "DATABASE_URL is missing",
                                         "fix": "Set DATABASE_URL from a Secret",
                                         "evidence": [{"file": "evidence/003-logs-checkout-6d8f9c7b5d-x2k4p-previous.txt",
                                                       "quote": "FATAL: DATABASE_URL is not set"}]}]})
    model = Calls([("list_resources", {"kind": "pods", "namespace": "sre-demo"}),
                   ("list_resources", {"kind": "secrets", "namespace": "sre-demo"}),
                   ("pod_logs", {"pod": "checkout-6d8f9c7b5d-x2k4p", "namespace": "kube-system"}),
                   ("namespace_events", {"namespace": "sre-demo"}),
                   ("pod_logs", {"pod": "checkout-6d8f9c7b5d-x2k4p", "namespace": "sre-demo", "previous": True}),
                   shell(f"printf '%s' '{citation}' > output/findings.json && echo tampered > evidence/003-logs-checkout-6d8f9c7b5d-x2k4p-previous.txt")],
                  answer="checkout crashes: DATABASE_URL is not set.")
    client = SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)
    store = SQLiteStore(tmp_path / "threads.sqlite3")
    chat = HarnessChatServer(make_spec(reader), store, client, model=model, template="sre", output_dir=tmp_path / "threads",
                             workspace_root=str(server.root), model_label="scripted", card=check_rows, preferred_output=REPORT)
    try:
        stream = await events(chat, {"type": "threads.create", "params": {"input": message("Why is checkout crashing?")}})
        assert not [event for event in stream if event["type"] == "error"], stream
        assert "Refused: list_resources reads" in model.outputs[1]
        assert "Refused: Namespace 'kube-system' is outside" in model.outputs[2]
        assert len(reader.calls) == 3  # refused calls never reach kubectl
        thread_id = next(event["thread"]["id"] for event in stream if event["type"] == "thread.created")
        thread = await store.load_thread(thread_id, {})
        turn = thread.metadata["turns"][0]
        assert [item["path"] for item in thread.metadata["evidence"]] == [
            "evidence/001-pods-sre-demo.txt", "evidence/002-events-sre-demo.txt", "evidence/003-logs-checkout-6d8f9c7b5d-x2k4p-previous.txt"]
        # The sandbox overwrote its copy of the evidence; the check used the host's copy.
        assert turn["check"]["status"] == "evidence_passed" and turn["check"]["quotes"] == 1
        assert "1 finding · 1 quote match the evidence" in json.dumps(stream, ensure_ascii=False)
        assert [tool["where"] for tool in turn["tools"]].count("harness") == 5
    finally:
        store.close()
