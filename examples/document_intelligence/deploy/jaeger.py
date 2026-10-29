#!/usr/bin/env python3
# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Deploy local Jaeger all-in-one and independently verify a recorded agent trace."""

import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from deploy.kubernetes.base import resources as base
from . import cli as agent


def verify_trace(payload, summary):
    traces = payload.get("data", [])
    if len(traces) != 1:
        raise ValueError("Expected exactly one Jaeger trace")
    spans = traces[0]["spans"]
    expected_id = summary["tracing"]["otel_trace_id"]
    if not spans or any(span["traceID"] != expected_id for span in spans):
        raise ValueError("Jaeger trace ID does not match the agent run")
    by_id = {span["spanID"]: span for span in spans}
    roots = [span for span in spans if not span["references"]]
    if len(roots) != 1 or roots[0]["operationName"] != "agents.workflow":
        raise ValueError("Expected one SDK workflow root")
    root = roots[0]
    for span in spans:
        if span["duration"] < 0 or span["startTime"] < root["startTime"] or span["startTime"] + span["duration"] > root["startTime"] + root["duration"]:
            raise ValueError("Span timing is outside the workflow")
        visited, current = set(), span
        while current["references"]:
            if current["spanID"] in visited:
                raise ValueError("Cyclic span parentage")
            visited.add(current["spanID"])
            parent = current["references"][0]
            if parent["refType"] != "CHILD_OF" or parent["traceID"] != expected_id or parent["spanID"] not in by_id:
                raise ValueError("Missing or invalid span parent")
            current = by_id[parent["spanID"]]
        if current["spanID"] != root["spanID"]:
            raise ValueError("Disconnected span hierarchy")
    names = {span["operationName"] for span in spans}
    required = {"agents.agent", "tool.load_skill", "tool.exec_command", "tool.apply_patch",
                "sandbox.create", "sandbox.resume", "sandbox.proxy", "sandbox.exec", "sandbox.suspend",
                "sandbox.delete", "document_intelligence.verify", "document_intelligence.run"}
    if not required <= names:
        raise ValueError("Missing expected trace operations: " + ", ".join(sorted(required - names)))
    # At least one actual sandbox execution must be a descendant of an SDK tool.
    tool_execution = False
    for span in spans:
        if span["operationName"] != "sandbox.exec":
            continue
        current = span
        while current["references"]:
            current = by_id[current["references"][0]["spanID"]]
            tool_execution |= current["operationName"].startswith("tool.")
    if not tool_execution:
        raise ValueError("Sandbox execution is not linked to an SDK tool span")
    models = sum(span["operationName"] in {"agents.response", "agents.generation"} for span in spans)
    if summary["model_inference"] and (not summary["api_response_ids"] or models < len(summary["api_response_ids"])):
        raise ValueError("Live run is missing model spans or API response evidence")
    return {"trace_id": expected_id, "span_count": len(spans), "model_span_count": models,
            "operations": sorted(names), "parentage_verified": True, "timing_verified": True,
            "sandbox_tool_link_verified": True, "execution_mode": summary["execution_mode"]}


def fetch_trace(query_url, trace_id, timeout=30):
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        raise ValueError("Expected a 128-bit lowercase hex trace ID")
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(query_url.rstrip("/") + "/api/traces/" + trace_id, timeout=5) as response:
                payload = json.load(response)
            if payload.get("data"):
                return payload
        except (urllib.error.URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError("Trace was not available in Jaeger before the deadline")
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "ui", "verify"])
    parser.add_argument("--context")
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--summary", type=Path, help="Document Intelligence verification.json for this run")
    parser.add_argument("--query-url", default="http://127.0.0.1:16686")
    parser.add_argument("--output-dir", type=Path, default=agent.ROOT / "artifacts/local/jaeger")
    args = parser.parse_args()
    if args.action in {"setup", "ui"} and (not args.context or not args.kubeconfig):
        parser.error("setup/ui require explicit --context and --kubeconfig")
    if args.action == "setup":
        agent.apply(args, base.namespace_resource())
        agent.apply(args, json.loads((agent.MANIFESTS / "jaeger-all-in-one.json").read_text()))
        agent.kubectl(args, "rollout", "status", "deployment/jaeger-all-in-one", "-n", agent.NAMESPACE, "--timeout=180s")
        print("Jaeger is ready. New agent Jobs receive the OTLP configuration.")
    elif args.action == "ui":
        agent.kubectl(args, "port-forward", "service/jaeger-all-in-one", "16686:16686", "-n", agent.NAMESPACE, "--address=127.0.0.1")
    else:
        if not args.summary:
            parser.error("verify requires --summary")
        summary = json.loads(args.summary.read_text())
        payload = fetch_trace(args.query_url, summary["tracing"]["otel_trace_id"])
        receipt = verify_trace(payload, summary)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "trace.json").write_text(json.dumps(payload, indent=2) + "\n")
        (args.output_dir / "verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
