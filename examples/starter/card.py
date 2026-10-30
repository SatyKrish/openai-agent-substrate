# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The rows this UI adds to the harness's turn card: what the host check found."""

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
