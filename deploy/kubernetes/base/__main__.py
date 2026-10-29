# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Apply the reusable in-cluster provider resources or import app configuration."""

import argparse
from pathlib import Path

from . import resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["resources", "secret"])
    parser.add_argument("--context", required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=resources.ROOT / ".env")
    parser.add_argument("--secret-name", default="openai-agent-env")
    args = parser.parse_args()
    {"resources": resources.configure_resources, "secret": resources.import_secret}[args.action](args)


if __name__ == "__main__":
    main()
