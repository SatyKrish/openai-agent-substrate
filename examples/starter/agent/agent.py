# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The agent: a plain SDK SandboxAgent. Add tools, skills or instructions here."""

from pathlib import Path

from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities import Filesystem, Shell

AGENT_ROOT = Path(__file__).resolve().parent


def build_agent(manifest, model):
    # Shell and Filesystem run in the sandbox. For a skill, add
    # Skills(lazy_from=LocalDirLazySkillSource(...)) as the other examples do.
    return SandboxAgent(
        name="Starter", model=model,
        instructions=(AGENT_ROOT / "instructions.md").read_text(encoding="utf-8"),
        default_manifest=manifest, capabilities=[Shell(), Filesystem()],
    )
