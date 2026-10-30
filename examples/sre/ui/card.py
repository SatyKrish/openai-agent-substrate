# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The rows this UI adds to the harness's turn card: what the evidence check found."""

from __future__ import annotations


def check_rows(conversation, turn: dict, changed: set[str]) -> list[dict]:
    check = turn.get("check") or {}
    evidence = sum(1 for item in turn["files"] if item.get("evidence"))
    rows = []
    if evidence:
        rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                     f"{evidence} cluster read{'s' if evidence != 1 else ''} saved to evidence/ by the harness "
                     "(read-only, outside the sandbox)."})
    fresh = "output/findings.json" in changed
    if check.get("status") == "evidence_passed" and fresh:
        count, quotes = check["findings"], check["quotes"]
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "success", "label":
                     f"{count} finding{'s' if count != 1 else ''} · {quotes} quote{'s' if quotes != 1 else ''} match the evidence"}]})
        rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                     "Every quote in findings.json appears in the host's copy of the evidence it cites. "
                     "Causes and fixes are the agent's own reasoning."})
    elif check.get("status") == "evidence_passed":
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "secondary",
                     "label": "No new findings this turn"}]})
    elif check.get("status") == "failed":
        rows.append({"type": "Row", "children": [{"type": "Badge", "label": "Evidence check failed", "color": "danger",
                     "variant": "soft"}]})
        rows.append({"type": "Text", "size": "sm", "value": check.get("detail", "")})
    elif check.get("status") == "no_findings" and any(path.startswith("output/") for path in changed):
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "warning",
                     "label": "No findings.json to check"}]})
    return rows
