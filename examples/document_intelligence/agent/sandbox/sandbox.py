# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Explicit SDK workspace staging and Kubernetes compute configuration."""

import json
from pathlib import Path

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from openai_agent_substrate import KubectlActorControl, SubstrateSandboxClient

from ..lib.artifacts import digests

WORKSPACE = Path(__file__).resolve().parent / "workspace"


def stage_inputs():
    return {name: (WORKSPACE / "inputs" / name).read_bytes()
            for name in ("acme-2024.md", "beta-2024.md", "kpis-2024.csv", "LICENSE")}


def build_manifest(inputs, analysis_id, root):
    return Manifest(root=root, entries={
        "inputs": Dir(children={**{name: File(content=data) for name, data in inputs.items()},
            "manifest.json": File(content=json.dumps(dict(analysis_id=analysis_id, files=digests(inputs))).encode())}),
        "work": Dir(), "output": Dir(),
    })


def build_client(args, local=None):
    return SubstrateSandboxClient(atespace=args.atespace,
        router_url=local.url if local else None,
        control=local.control if local else KubectlActorControl(context=args.context, kubeconfig=args.kubeconfig, endpoint=getattr(args, "api_endpoint", None)))
