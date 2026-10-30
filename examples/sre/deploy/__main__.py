# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""SRE agent deployment: its sandbox template, its read-only cluster access and the demo workloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from deploy.kubernetes.base import resources as base
from deploy.kubernetes import sandbox as profiles

from . import access, demo

ROOT = Path(__file__).resolve().parents[3]
PROFILE = profiles.SandboxProfile("sre", Path(__file__).resolve().parent / "sandbox.Dockerfile")
DEPLOYMENT_DIR = ROOT / "artifacts/examples/sre/deployment"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sandbox", "access", "demo"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--image", default="localhost:5501/openai-agent-substrate-sre:local")
    parser.add_argument("--docker-context")
    parser.add_argument("--namespace", action="append", dest="namespaces",
                        help="A namespace the agent may read; repeat for more (default: sre-demo)")
    parser.add_argument("--output-dir", type=Path, default=DEPLOYMENT_DIR)
    args = parser.parse_args(argv)
    if args.action != "sandbox" and not args.kubeconfig:
        parser.error("this action requires --kubeconfig")
    namespaces = args.namespaces or [demo.NAMESPACE]
    if args.action == "sandbox":
        profiles.deploy(PROFILE, args)
    elif args.action == "access":
        # The namespaces must exist before their Roles; demo creates sre-demo.
        print(base.apply(args, access.resources(namespaces)).stdout.decode(), end="")
    else:
        deployment = json.loads((args.output_dir / "deployment.json").read_text())
        print(base.apply(args, demo.resources(deployment["image"])).stdout.decode(), end="")


if __name__ == "__main__":
    main()
