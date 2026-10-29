# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from types import SimpleNamespace

import pytest
from agents import custom_span, function_span, set_trace_processors, set_tracing_disabled, trace
from agents.tracing import generation_span
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from examples.document_intelligence.main import run
from openai_agent_substrate import tracing


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    exporter = InMemorySpanExporter()
    processor = tracing.OTLPTracingProcessor(exporter)
    set_trace_processors([processor])
    set_tracing_disabled(False)
    try:
        yield processor, exporter
    finally:
        processor.shutdown()
        set_trace_processors([])
        set_tracing_disabled(True)


def test_sdk_parentage_timing_and_sensitive_payloads_are_excluded(recorder):
    processor, exporter = recorder
    canary = "sensitive-prompt-tool-output-key"
    with trace(canary, metadata={"key": canary}) as workflow:
        with function_span("exec_command", input=canary, output=canary) as tool:
            tool.set_error({"message": canary, "data": {"key": canary}})
            with tracing.operation("sandbox.exec") as command:
                command.span_data.data.update(exit_code=7, stdout_bytes=12, stderr_bytes=0, command=canary)
            with generation_span(input=[{"secret": canary}], output=[{"secret": canary}], model_config={"key": canary}):
                pass
        with custom_span(canary, data={"secret": canary}):
            pass
    processor.force_flush()
    spans = exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    root = by_name["agents.workflow"]
    function = by_name["tool.exec_command"]
    command = by_name["sandbox.exec"]
    assert root.parent is None
    assert function.parent.span_id == root.context.span_id
    assert command.parent.span_id == function.context.span_id
    assert function.status.status_code == StatusCode.ERROR
    assert function.status.description is None
    assert command.attributes["sandbox.exit_code"] == 7
    assert len({span.context.trace_id for span in spans}) == 1
    assert f"{root.context.trace_id:032x}" == tracing.otel_trace_id(workflow.trace_id)
    assert all(root.start_time <= span.start_time <= span.end_time <= root.end_time for span in spans)
    assert canary not in json.dumps([span.to_json() for span in spans])


async def test_real_sdk_tools_link_provider_operations(recorder, server, tmp_path):
    processor, exporter = recorder
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


def test_explicit_disable_overrides_endpoint(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "1")
    assert tracing.tracing_enabled() is False


def test_unconfigured_export_is_disabled_and_hosted_processor_is_replaced(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    installed = []
    monkeypatch.setattr(tracing, "set_trace_processors", installed.append)
    assert tracing.configure_tracing() is None
    assert installed == [[]]


def test_otlp_configuration_replaces_hosted_exporter(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4318")
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    installed, exporter = [], InMemorySpanExporter()
    monkeypatch.setattr(tracing, "OTLPSpanExporter", lambda **kwargs: exporter)
    monkeypatch.setattr(tracing, "set_trace_processors", installed.append)
    processor = tracing.configure_tracing()
    assert installed == [[processor]]
    processor.shutdown()


def test_invalid_protocol_is_actionable(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    with pytest.raises(ValueError, match="4318"):
        tracing.configure_tracing()


def test_invalid_span_is_dropped_without_exception_details(recorder, caplog):
    processor, exporter = recorder
    class Poison:
        @property
        def span_data(self):
            raise ValueError("sensitive-secret-in-exception")
    processor.on_span_end(Poison())
    processor.force_flush()
    assert exporter.get_finished_spans() == ()
    assert "Dropped an invalid" in caplog.text
    assert "sensitive-secret" not in caplog.text


async def test_concurrent_traces_keep_separate_parent_trees(recorder):
    processor, exporter = recorder
    async def execute():
        with trace("workflow"):
            with tracing.operation("sandbox.exec"):
                await asyncio.sleep(0)
    await asyncio.gather(execute(), execute())
    processor.force_flush()
    spans = exporter.get_finished_spans()
    roots = {span.context.trace_id: span for span in spans if span.parent is None}
    assert len(roots) == 2
    assert all(span.parent is None or span.parent.span_id == roots[span.context.trace_id].context.span_id for span in spans)


def test_export_failure_does_not_replace_workflow_failure(monkeypatch):
    class FailingExporter(SpanExporter):
        def export(self, spans):
            return SpanExportResult.FAILURE
        def shutdown(self):
            pass
    processor = tracing.OTLPTracingProcessor(FailingExporter())
    set_trace_processors([processor])
    set_tracing_disabled(False)
    try:
        with pytest.raises(ValueError, match="original failure"):
            with trace("workflow"):
                with tracing.operation("sandbox.exec"):
                    raise ValueError("original failure")
        processor.force_flush()
    finally:
        processor.shutdown()
        set_trace_processors([])
        set_tracing_disabled(True)
