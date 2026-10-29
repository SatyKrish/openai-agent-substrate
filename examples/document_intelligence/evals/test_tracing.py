# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

from examples.document_intelligence.main import run


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
    assert any(span.name == "sandbox.exec" and span.parent is not None
               and by_id[span.parent.span_id].name == "tool.exec_command" for span in spans)
    assert all(span.parent is None or span.parent.span_id in by_id for span in spans)
