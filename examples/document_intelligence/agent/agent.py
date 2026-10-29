# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Authored agent definition; the OpenAI SDK owns the model/tool loop."""

from pathlib import Path

from agents.sandbox import SandboxAgent

from .tools.capabilities import capabilities

AGENT_ROOT = Path(__file__).resolve().parent


def build_agent(manifest, model):
    return SandboxAgent(
        name="Document Intelligence", model=model,
        instructions=(AGENT_ROOT / "instructions.md").read_text(encoding="utf-8"),
        default_manifest=manifest, capabilities=capabilities(),
    )
