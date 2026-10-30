# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""What the shared harness needs beside the SandboxAgent: the workspace, the cluster tools and the host check."""

from __future__ import annotations

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir

from harness.spec import AgentSpec

from .agent import build_agent
from .lib import findings
from .tools import cluster

# The panel opens this file after a turn that changed it.
REPORT = "output/findings.md"


def start(conversation, has_uploads: bool, root: str) -> Manifest:
    return Manifest(root=root, entries={"evidence": Dir(), "output": Dir(), "work": Dir()})


def make_spec(access: cluster.ClusterAccess) -> AgentSpec:
    scope = ", ".join(sorted(access.namespaces))
    return AgentSpec(
        name="sre",
        build_agent=build_agent,
        start=start,
        instructions=lambda conversation: f"Namespaces you may read: {scope}.",
        tools=lambda turn: cluster.tools(access, turn),
        check=lambda conversation, turn, files: findings.check(conversation, files),
        default_question=f"Find unhealthy workloads in {scope} and explain why.",
    )
