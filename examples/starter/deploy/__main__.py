# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Build the starter agent's sandbox image and register its ActorTemplate."""

from __future__ import annotations

import argparse
from pathlib import Path

from deploy.kubernetes import sandbox as profiles

ROOT = Path(__file__).resolve().parents[3]
# Half a CPU and 480 MiB per sandbox (the profile's defaults); raise them here if your agent needs more.
PROFILE = profiles.SandboxProfile("starter", Path(__file__).resolve().parent / "sandbox.Dockerfile")
DEPLOYMENT_DIR = ROOT / "artifacts/examples/starter/deployment"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["sandbox"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--atespace", default="ate-demo-sandbox")
    parser.add_argument("--image", default="localhost:5501/openai-agent-substrate-starter:local")
    parser.add_argument("--docker-context")
    parser.add_argument("--output-dir", type=Path, default=DEPLOYMENT_DIR)
    profiles.deploy(PROFILE, parser.parse_args(argv))


if __name__ == "__main__":
    main()
