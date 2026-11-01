# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Explicit SDK workspace staging and Kubernetes compute configuration."""

import json
from pathlib import Path

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File

from agent_substrate import KubectlActorControl, SubstrateSandboxClient

from ..lib.artifacts import digests

WORKSPACE = Path(__file__).resolve().parent / "workspace"
# The Actor's workspace root. The ActorTemplate mounts its durableDir volume here,
# so staged inputs, uploads and outputs live and die with the Actor.
ACTOR_WORKSPACE = "/workspace"


def stage_inputs():
    return {name: (WORKSPACE / "inputs" / name).read_bytes()
            for name in ("acme-2024.md", "beta-2024.md", "kpis-2024.csv", "LICENSE")}


def build_manifest(inputs, analysis_id, root):
    """Stage the sample reports; pass no inputs for a workspace that receives uploads instead."""
    if not inputs:
        return Manifest(root=root, entries={"inputs": Dir(children={"uploads": Dir()}), "work": Dir(), "output": Dir()})
    return Manifest(root=root, entries={
        "inputs": Dir(children={**{name: File(content=data) for name, data in inputs.items()},
            "manifest.json": File(content=json.dumps(dict(analysis_id=analysis_id, files=digests(inputs))).encode())}),
        "work": Dir(), "output": Dir(),
    })


def build_client(args, local=None):
    if local:
        return SubstrateSandboxClient(atespace=args.atespace, router_url=local.url, control=local.control)
    # In the cluster, args carries the harness's own token and router TLS; outside it, neither, and
    # each session reaches its Actor through `kubectl ate proxy` as the operator.
    return SubstrateSandboxClient(atespace=args.atespace,
        router_url=getattr(args, "router_url", None), router_tls=getattr(args, "router_tls", None),
        control=KubectlActorControl(context=args.context, kubeconfig=args.kubeconfig,
            endpoint=getattr(args, "api_endpoint", None), token_file=getattr(args, "token_file", None)))
