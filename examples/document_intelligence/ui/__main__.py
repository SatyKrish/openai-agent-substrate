# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Serve the Document Intelligence UI on loopback against Agent Substrate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openai_harness.settings import live_model, load_env_file

ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT = ROOT / "artifacts/examples/document_intelligence/deployment/deployment.json"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit Kubernetes context")
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--api-endpoint", help="Optional direct Substrate API address")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", help="Python-capable ActorTemplate; defaults to the last sandbox deployment")
    parser.add_argument("--model", help="OpenAI model ID; defaults to OPENAI_MODEL")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="Reads OPENAI_* settings only")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"],
                        help="Loopback only: the server holds the API key and cluster access")
    parser.add_argument("--port", type=int, default=8000)
    # Conversations from before the shared harness stayed in artifacts/examples/document_intelligence/ui.
    parser.add_argument("--data-dir", type=Path, default=ROOT / "artifacts/examples/document_intelligence/conversations")
    parser.add_argument("--jaeger-url", default="http://127.0.0.1:16686", help="Jaeger query URL reachable from this host")
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    if not args.template and DEPLOYMENT.is_file():
        deployment = json.loads(DEPLOYMENT.read_text())
        if deployment.get("context") == args.context:
            args.template = deployment["template"]
    if not args.template:
        parser.error("Pass --template, or deploy the sandbox image first (local-kubernetes.sh document-intelligence)")
    try:
        args.model = live_model(args.model)
    except ValueError as error:
        parser.error(str(error))
    return args


def main(argv=None):
    args = parse_args(argv)
    import logging

    import uvicorn
    from agents import flush_traces

    from openai_harness.chat import HarnessChatServer
    from openai_harness.store import SQLiteStore
    from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient
    from openai_agent_substrate.tracing import configure_tracing, tracing_enabled

    from ..agent.spec import REPORT, SPEC
    from ..main import QUESTION
    from .app import create_app
    from .card import check_rows

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    configure_tracing(operation_names=SPEC.trace_operations)
    # Rendered PDF pages come back as one file, so reads may exceed the 10 MiB upload limit.
    client = SubstrateSandboxClient(atespace=args.atespace, max_file_bytes=32 * 1024 * 1024, control=KubectlActorControl(
        context=args.context, kubeconfig=args.kubeconfig, endpoint=args.api_endpoint))
    store = SQLiteStore(args.data_dir / "threads.sqlite3")
    server = HarnessChatServer(SPEC, store, client, model=args.model, template=args.template,
                               output_dir=args.data_dir / "threads", base_url=f"http://localhost:{args.port}",
                               card=check_rows, preferred_output=REPORT)
    config = {"model": args.model, "context": args.context, "atespace": args.atespace, "template": args.template,
              "tracing": tracing_enabled(), "prompts": [
                  {"label": "Compare ACME and BETA fiscal 2024", "prompt": QUESTION},
                  {"label": "Is EBITDA disclosed?", "prompt": "Is EBITDA disclosed in either report? Cite the exact sources."},
              ]}
    app = create_app(server, config=config, port=args.port, jaeger_url=args.jaeger_url if tracing_enabled() else None)
    print(f"Document Intelligence UI: http://localhost:{args.port}  (model {args.model}, template {args.template})", flush=True)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        flush_traces()
        store.close()


if __name__ == "__main__":
    main()
