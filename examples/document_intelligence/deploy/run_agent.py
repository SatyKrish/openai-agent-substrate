# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Trusted harness entrypoint. Configuration comes from a mounted Kubernetes Secret."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from agents import flush_traces
from openai_agent_substrate.tracing import configure_tracing

from examples.document_intelligence.main import TRACE_OPERATIONS, run
from examples.document_intelligence.agent.lib.configuration import select_model


def configuration():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Override the live OPENAI_MODEL")
    parser.add_argument("--test", action="store_true", help="Explicitly run a scripted evaluation without API calls")
    args = parser.parse_args()
    try:
        args.model = select_model(args.model, test=args.test)
    except ValueError as error:
        parser.error(str(error))
    args.api_endpoint = os.environ.get("SUBSTRATE_API_ENDPOINT", "api.ate-system.svc:443")
    args.context = "in-cluster"
    args.kubeconfig = Path("/etc/agent/kubeconfig")
    args.atespace = os.environ.get("SUBSTRATE_ATESPACE", "ate-demo-sandbox")
    args.template = os.environ["SUBSTRATE_TEMPLATE"]
    args.output_dir = Path("/artifacts/document-intelligence")
    args.keep_actor = False
    return args


if __name__ == "__main__":
    args = configuration()
    keys = []
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        if name in os.environ:
            if Path("/var/run/agent-config", name).read_text() != os.environ[name]:
                raise RuntimeError(f"Mounted configuration and environment disagree for {name}")
            keys.append(name)
    print(json.dumps(dict(configuration_source="Kubernetes Secret", mounted_environment_matches=True, keys=keys)), flush=True)
    configure_tracing(operation_names=TRACE_OPERATIONS)
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, indent=2))
    finally:
        flush_traces()
