# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""What the UI and terminal say about a turn's host check: the rows on the turn card and each file's status."""

from __future__ import annotations


def check_rows(conversation, turn: dict, changed: set[str]) -> list[dict]:
    check = turn.get("check") or {}
    if check.get("status") == "passed" and any(path.startswith("output/") for path in changed):
        return [{"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "success",
                 "label": "Output files are well formed (host check)"}]}]
    if check.get("status") == "failed":
        return [{"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "danger",
                 "label": "Host check failed"}]},
                {"type": "Text", "size": "sm", "value": check.get("detail", "")}]
    return []


def file_status(conversation, turn: dict) -> dict[str, dict]:
    """Which of this turn's output files the host checked, for the UI's file list."""
    check = (turn.get("check") or {}).get("status")
    labels = {"passed": ("checked", "well formed (host check)"), "failed": ("failed", "host check failed")}
    state, label = labels.get(check, ("unchecked", "not checked on the host"))
    return {item["path"]: {"state": state, "label": label} for item in turn["files"] if item["path"].startswith("output/")}
