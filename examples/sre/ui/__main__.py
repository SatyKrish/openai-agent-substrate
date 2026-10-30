# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Serve the SRE agent's UI on loopback against Agent Substrate."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.settings import live_model, load_env_file
from deploy.kubernetes.sandbox import deployed_template

ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_DIR = ROOT / "artifacts/examples/sre/deployment"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit Kubernetes context")
    parser.add_argument("--kubeconfig", type=Path, help="Operator kubeconfig; used only to mint the reader's token")
    parser.add_argument("--api-endpoint", help="Optional direct Substrate API address")
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", help="SRE ActorTemplate; defaults to the last SRE sandbox deployment")
    parser.add_argument("--model", help="OpenAI model ID; defaults to OPENAI_MODEL")
    parser.add_argument("--namespace", action="append", dest="namespaces",
                        help="A namespace the agent may read; repeat for more (default: sre-demo)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="Reads OPENAI_* settings only")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"],
                        help="Loopback only: the server holds the API key and cluster access")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "artifacts/examples/sre/conversations")
    parser.add_argument("--jaeger-url", default="http://127.0.0.1:16686", help="Jaeger query URL reachable from this host")
    args = parser.parse_args(argv)
    load_env_file(args.env_file)
    args.template = args.template or deployed_template(DEPLOYMENT_DIR, args.context)
    if not args.template:
        parser.error("Pass --template, or deploy the SRE sandbox first (local-kubernetes.sh sre-setup)")
    try:
        args.model = live_model(args.model)
    except ValueError as error:
        parser.error(str(error))
    args.namespaces = frozenset(args.namespaces or ["sre-demo"])
    return args


def main(argv=None):
    args = parse_args(argv)
    import logging

    import uvicorn
    from agents import flush_traces

    from harness.chat import HarnessChatServer
    from harness.store import SQLiteStore
    from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient
    from openai_agent_substrate.tracing import configure_tracing, tracing_enabled

    from ..agent.spec import REPORT, make_spec
    from ..agent.tools.cluster import TOOL_NAMES, ClusterAccess
    from ..deploy import access
    from .app import create_app
    from .card import check_rows

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    reader = args.data_dir / "reader.kubeconfig"
    cluster = ClusterAccess(kubeconfig=reader, namespaces=args.namespaces,
                            refresh=lambda: access.mint(args.context, args.kubeconfig, reader))
    cluster.expires_at = cluster.refresh()
    spec = make_spec(cluster)
    configure_tracing(operation_names=spec.trace_operations, harness_tools=TOOL_NAMES)
    client = SubstrateSandboxClient(atespace=args.atespace, control=KubectlActorControl(
        context=args.context, kubeconfig=args.kubeconfig, endpoint=args.api_endpoint))
    store = SQLiteStore(args.data_dir / "threads.sqlite3")
    server = HarnessChatServer(spec, store, client, model=args.model, template=args.template,
                               output_dir=args.data_dir / "threads", base_url=f"http://localhost:{args.port}",
                               card=check_rows, preferred_output=REPORT)
    scope = ", ".join(sorted(args.namespaces))
    config = {"model": args.model, "context": args.context, "atespace": args.atespace, "template": args.template,
              "tracing": tracing_enabled(), "namespaces": sorted(args.namespaces), "prompts": [
                  {"label": f"What's unhealthy in {scope}?", "prompt": f"Which workloads in {scope} are unhealthy, and why?"},
                  {"label": "Why is checkout crashing?", "prompt": "Why is the checkout deployment in sre-demo crashing? "
                   "Propose a fix."},
              ]}
    app = create_app(server, config=config, port=args.port, jaeger_url=args.jaeger_url if tracing_enabled() else None)
    print(f"SRE agent UI: http://localhost:{args.port}  (model {args.model}, template {args.template}, "
          f"namespaces {scope})", flush=True)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        flush_traces()
        store.close()


if __name__ == "__main__":
    main()
