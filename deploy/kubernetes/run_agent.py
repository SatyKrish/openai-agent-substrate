# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Trusted harness entrypoint. Configuration comes from a mounted Kubernetes Secret."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from examples.document_intelligence.main import run


def configuration():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Override OPENAI_MODEL; use scripted for a key-free execution test")
    args = parser.parse_args()
    model = args.model or os.environ.get("OPENAI_MODEL")
    if not model:
        parser.error("Set OPENAI_MODEL in .env or provide --model")
    if model != "scripted" and not os.environ.get("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY in .env before running a live model")
    args.model = model
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
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2))
