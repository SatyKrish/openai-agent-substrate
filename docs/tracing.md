# Tracing with the OpenAI Agents SDK and Jaeger

The trusted harness uses the [Agents SDK tracing API](https://openai.github.io/openai-agents-python/tracing/)
and a custom `TracingProcessor` to export spans over OTLP HTTP/protobuf. The
processor replaces the SDK's default hosted exporter; enabling this integration
sends traces to the configured collector. It does not also send them to OpenAI's
Traces dashboard. Model inference still uses the configured OpenAI API endpoint.

## Installation

The OpenTelemetry SDK and OTLP exporter are optional dependencies:

```sh
# From the repository root
pip install './sandbox[tracing]'
```

The provider and its SDK-only instrumentation work without this extra. The
Agents SDK currently brings `opentelemetry-api` transitively through MCP; the
base installation does not require the OpenTelemetry SDK or OTLP exporter. Import
`openai_agent_substrate.tracing` only when the tracing extra is installed. The
repository's development dependencies include the exporter for tests and source
examples; the harness image explicitly installs the tracing extra.

## Local Kubernetes workflow

After [local Kubernetes setup](local-kubernetes.md), configure your `.env`, build
the harness, deploy Jaeger, and run the agent:

```sh
./scripts/local-kubernetes.sh agent-setup
./scripts/local-kubernetes.sh tracing-setup
./scripts/local-kubernetes.sh agent-run
./scripts/local-kubernetes.sh agent-artifacts
```

`agent-run` performs real model inference. For an explicit execution evaluation
without model calls, use `agent-test` instead. Jaeger all-in-one 1.76.0 is pinned
by digest in `examples/document_intelligence/deploy/jaeger.py`. It runs in namespace
`openai-agent-substrate` with an in-memory store and ClusterIP Service. It has
no harness Secret mount or service-account token. Traces disappear when the
Jaeger pod restarts; this deployment is for local development.

The agent UIs on the [shared harness](agent-harness.md) (`ui` for Document
Intelligence, `sre-ui` for the SRE agent) record one trace per conversation turn
and show it beside the chat, live while the turn runs.

In another terminal, expose the query UI on loopback:

```sh
./scripts/local-kubernetes.sh tracing-ui
```

Open <http://127.0.0.1:16686>, choose service `openai-agent-substrate`, and find the
run. Its `verification.json` includes `tracing.sdk_trace_id` and
`tracing.otel_trace_id`. The direct UI URL is
`http://127.0.0.1:16686/trace/OTEL_TRACE_ID`.

Independently query Jaeger and check the completed trace against the run record:

```sh
uv run --locked python -m examples.document_intelligence.deploy.jaeger verify \
  --summary artifacts/local/agent-results/document-intelligence/ANALYSIS_ID/verification.json
```

This checks the workflow root, parent references, timestamps, tool and lifecycle
operations, sandbox executions nested under SDK tools, and live model span
counts. It saves `trace.json` and `verification.json` in `artifacts/local/jaeger/`.

## Configuration

`tracing-setup` creates an optional `openai-agent-tracing` ConfigMap. New harness
Jobs read it before their Secret, so explicit `.env` settings take precedence.
Collector settings and API credentials remain in the trusted agent pod. The
Actor receives no telemetry environment variables or exporter code.

| Setting | Behavior |
| --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector base URL; `/v1/traces` is appended. Local default is `http://jaeger-all-in-one.openai-agent-substrate.svc:4318` |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Optional full trace ingest URL; overrides the generic endpoint |
| `OTEL_SERVICE_NAME` | Jaeger service name; defaults to `openai-agent-substrate` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` / `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` | Must be `http/protobuf`; use port 4318 |
| `OPENAI_AGENTS_DISABLE_TRACING=1` | Disable tracing even when an endpoint is configured |

Without an endpoint, the reference entrypoints disable export. The standard OTel
HTTP exporter also supports collector headers and TLS certificate settings.
Keep authentication settings in `.env`/the Secret. Do not put keys in a URL.

For host execution, set an endpoint reachable from your host before invoking
the CLI; the Kubernetes service DNS name only resolves inside the cluster.
Applications embedding the provider should call `configure_tracing()` once at
startup, wrap the workflow in an SDK `trace(...)`, use
`RunConfig(trace_include_sensitive_data=False)`, and call `flush_traces()` after
the trace closes. The reference CLIs already do this, including on failure.
An application can pass trusted custom span names through
`configure_tracing(operation_names=("invoice.extract",))` or the processor's
`operation_names` constructor argument. Only `sandbox.*` operations are built
in. Unregistered custom names export as `agents.custom`, with their data omitted.
Document Intelligence supplies its own operation names in the example. On the
harness, each agent's turns are traced as `NAME.turn` and its host check as
`NAME.check` (for example `sre.turn`), and tools that run in the harness rather
than the sandbox are registered with `configure_tracing(harness_tools=(...))`
and export as `harness_tool.NAME`.

## Span hierarchy and content

One SDK trace contains the full Document Intelligence workflow, including
Actor creation, startup, the runner, verification, suspension and deletion.
SDK agent/task/turn/model spans retain their timing and parent relationships.
Known native tools appear as `tool.load_skill`, `tool.exec_command`, and
`tool.apply_patch`. Provider spans include `sandbox.create`, `sandbox.resume`,
`sandbox.proxy`, `sandbox.exec`, `sandbox.suspend`, and `sandbox.delete`.
`document_intelligence.verify` records independent host artifact checks.
A nonzero command marks that execution span as an error; SDK file-existence
probes can intentionally return nonzero. The report verifier records whether
the completed workflow satisfies its output contract.

SDK IDs map deterministically to OpenTelemetry IDs. Export happens on a batch
worker with a bounded queue, then flushes when the short-lived harness exits.
Export failures do not replace agent errors; missing traces cause the explicit
Jaeger verification to fail. A run's `tracing.enabled` flag means tracing was
configured, not that the collector acknowledged every span.

Sensitive SDK inputs and outputs are disabled. The processor also constructs an
allowlisted export without calling the SDK payload serializer. It retains span
types, known tool/operation names, numeric token usage, command exit codes,
output byte counts, and generic error status. It omits prompts, documents,
commands, paths, patches, tool outputs, arbitrary metadata/names, and exception
messages. The existing synthetic `tool-trace.json` artifact is separate and
does contain this example's tool inputs and outputs; keep artifact access
appropriate for the data in the workflow.

See the [Jaeger all-in-one guide](https://www.jaegertracing.io/docs/1.76/getting-started/)
and [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/).
