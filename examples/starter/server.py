# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Settings and the harness server, shared by the starter's terminal chat and its UI.

Both use the same data folder, so they see the same conversations.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deploy.kubernetes.sandbox import deployed_template
from harness.settings import live_model, load_env_file

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_DIR = ROOT / "artifacts/examples/starter/deployment"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context", required=True, help="Explicit Kubernetes context")
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--template", help="Defaults to the last starter sandbox deployment")
    parser.add_argument("--model", help="OpenAI model ID; defaults to OPENAI_MODEL")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="Reads OPENAI_* settings only")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "artifacts/examples/starter/conversations")
    parser.add_argument("--jaeger-url", default="http://127.0.0.1:16686", help="Jaeger query URL reachable from this host")


def resolve(parser: argparse.ArgumentParser, args) -> None:
    load_env_file(args.env_file)
    args.template = args.template or deployed_template(DEPLOYMENT_DIR, args.context)
    if not args.template:
        parser.error("Pass --template, or deploy the starter sandbox first (local-kubernetes.sh starter-setup)")
    try:
        args.model = live_model(args.model)
    except ValueError as error:
        parser.error(str(error))


def build(args, *, base_url: str = "http://localhost"):
    """The harness server for the starter agent: Substrate client, ChatKit store and the agent's spec."""
    from harness.chat import HarnessChatServer
    from harness.store import SQLiteStore
    from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient
    from openai_agent_substrate.tracing import configure_tracing, tracing_enabled

    from .agent.spec import SPEC
    from .card import check_rows

    configure_tracing(operation_names=SPEC.trace_operations)
    args.jaeger_url = args.jaeger_url if tracing_enabled() else None
    client = SubstrateSandboxClient(atespace=args.atespace,
                                    control=KubectlActorControl(context=args.context, kubeconfig=args.kubeconfig))
    store = SQLiteStore(args.data_dir / "threads.sqlite3")
    return HarnessChatServer(SPEC, store, client, model=args.model, template=args.template,
                             output_dir=args.data_dir / "threads", base_url=base_url, card=check_rows)
