# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Register the SDK's native tools and lazily materialized domain skill."""

from pathlib import Path

from agents.sandbox.capabilities import Filesystem, LocalDirLazySkillSource, Shell, Skills
from agents.sandbox.entries import LocalDir


def capabilities():
    source = LocalDir(src=Path(__file__).resolve().parents[1] / "skills")
    return [Shell(), Filesystem(), Skills(lazy_from=LocalDirLazySkillSource(source=source))]
