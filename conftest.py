# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0

import os

import pytest
from agents import set_trace_processors, set_tracing_disabled
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_substrate import tracing

from agent_substrate.testing.server import LocalExecutionServer
from agent_substrate.provider import SubstrateSandboxClient


@pytest.fixture(autouse=True)
def disable_hosted_tracing():
    set_tracing_disabled(True)


@pytest.fixture
def server():
    with LocalExecutionServer(upstream_binary=os.getenv("SUBSTRATE_DEMO_BINARY")) as value:
        yield value


@pytest.fixture
def client(server):
    return SubstrateSandboxClient(router_url=server.url, atespace="test-space", control=server.control)


@pytest.fixture
def trace_recorder(monkeypatch, request):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    exporter = InMemorySpanExporter()
    processor = tracing.OTLPTracingProcessor(exporter, operation_names=getattr(request, "param", ()))
    set_trace_processors([processor])
    set_tracing_disabled(False)
    try:
        yield processor, exporter
    finally:
        processor.shutdown()
        set_trace_processors([])
        set_tracing_disabled(True)
