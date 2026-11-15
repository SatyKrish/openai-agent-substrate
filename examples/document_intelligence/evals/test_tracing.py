# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

import pytest

from examples.document_intelligence.deploy.jaeger import verify_trace
from examples.document_intelligence.main import TRACE_OPERATIONS, run


@pytest.mark.parametrize("trace_recorder", [TRACE_OPERATIONS], indirect=True)
async def test_real_sdk_tools_link_provider_operations(trace_recorder, server, tmp_path):
    processor, exporter = trace_recorder
    args = SimpleNamespace(atespace="test-space", template="document-intelligence", model="scripted",
                           keep_actor=False, output_dir=tmp_path)
    summary = await run(args, server)
    processor.force_flush()
    spans = exporter.get_finished_spans()
    by_id = {span.context.span_id: span for span in spans}
    names = {span.name for span in spans}
    assert {"tool.load_skill", "tool.exec_command", "tool.apply_patch", "sandbox.create", "sandbox.exec",
            "sandbox.resume", "sandbox.suspend", "sandbox.delete", "document_intelligence.verify"} <= names
    assert summary["tracing"]["enabled"] is True
    assert summary["uses_actor_proxy"] is False
    assert "sandbox.proxy" not in names
    assert any(span.name == "sandbox.exec" and span.parent is not None
               and by_id[span.parent.span_id].name == "tool.exec_command" for span in spans)
    assert all(span.parent is None or span.parent.span_id in by_id for span in spans)


@pytest.mark.parametrize("uses_actor_proxy, include_proxy_span, error", [
    pytest.param(False, False, None, id="direct"),
    pytest.param(False, True, "Unexpected proxy span", id="direct-with-proxy-span"),
    pytest.param(True, True, None, id="proxy"),
    pytest.param(True, False, r"Missing expected trace operations: sandbox\.proxy", id="proxy-without-span"),
    pytest.param(None, True, None, id="legacy"),
    pytest.param(None, False, r"Missing expected trace operations: sandbox\.proxy", id="legacy-without-span"),
])
def test_jaeger_verifies_the_actor_route(uses_actor_proxy, include_proxy_span, error):
    trace_id = "a" * 32
    summary = {"tracing": {"otel_trace_id": trace_id}, "model_inference": False, "execution_mode": "scripted"}
    if uses_actor_proxy is not None:
        summary["uses_actor_proxy"] = uses_actor_proxy

    root = "agents.workflow"
    operations = ["agents.agent", "tool.load_skill", "tool.exec_command", "tool.apply_patch",
                  "sandbox.create", "sandbox.resume", "sandbox.exec", "sandbox.suspend", "sandbox.delete",
                  "document_intelligence.verify", "document_intelligence.run"]
    if include_proxy_span:
        operations.append("sandbox.proxy")
    spans = [{"traceID": trace_id, "spanID": root, "operationName": root,
              "references": [], "startTime": 0, "duration": 10}]
    for operation in operations:
        parent = "tool.exec_command" if operation == "sandbox.exec" else root
        spans.append({"traceID": trace_id, "spanID": operation, "operationName": operation,
                      "references": [{"refType": "CHILD_OF", "traceID": trace_id, "spanID": parent}],
                      "startTime": 1, "duration": 1})
    payload = {"data": [{"spans": spans}]}

    if error is None:
        assert verify_trace(payload, summary)["operations"] == sorted([root, *operations])
    else:
        with pytest.raises(ValueError, match=error):
            verify_trace(payload, summary)
