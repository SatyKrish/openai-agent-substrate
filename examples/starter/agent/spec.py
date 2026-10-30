# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""What the harness needs beside the SandboxAgent."""

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir

from harness.spec import AgentSpec

from .agent import build_agent
from .check import check


def start(conversation, has_uploads: bool, root: str) -> Manifest:
    """The new conversation's workspace. Stage input files here with File(...) or LocalDir(...)."""
    return Manifest(root=root, entries={"work": Dir(), "output": Dir()})


SPEC = AgentSpec(
    name="starter",
    build_agent=build_agent,
    start=start,
    check=check,
    # Optional: instructions=..., tools=..., uploads=Uploads(...); see harness/src/harness/spec.py.
)
