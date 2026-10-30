# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""The rows this UI adds to the harness's turn card: what the host check found."""

from __future__ import annotations

from ..agent.lib.verify import OUTPUTS


def check_rows(conversation, turn: dict, changed: set[str]) -> list[dict]:
    check = turn.get("check") or {}
    contract_changed = any(f"output/{name}" in changed for name in OUTPUTS)
    # Quotes from an earlier turn's result.json say nothing about this turn's answer.
    stale = check.get("scope") == "citations" and "output/result.json" not in changed
    rows = []
    if stale and check.get("status") in ("citations_passed", "visual_only"):
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "secondary",
                     "label": "No new citations this turn"}]})
        rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                     "result.json is unchanged: its earlier quotes still match their sources, "
                     "but nothing in this answer was checked."})
    if check.get("status") == "citations_passed" and not stale:
        count = check["count"]
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "success",
                     "label": f"{count} quote{' matches' if count == 1 else 's match'} the source documents"}]})
        rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                     "Each checked quote in result.json appears in the source file it cites, on the cited page "
                     "for PDFs (host check). Figures computed from them are the agent's own work."})
    if check.get("visual") and not stale:
        count = check["visual"]
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "warning",
                     "label": f"{count} quote{'' if count == 1 else 's'} from page images to review"}]})
        rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                     "The agent read these from page images, so the host cannot check them against "
                     "extracted text. Compare them with the page images in the panel."})
    if check.get("status") == "no_citations" and any(path.startswith("output/") for path in changed):
        rows.append({"type": "Row", "children": [{"type": "Badge", "variant": "soft", "color": "warning",
                     "label": "No citations to check"}]})
    if check.get("status") == "passed":
        label = "Checked against the original documents" if contract_changed else "Outputs unchanged · still match the sources"
        rows.append({"type": "Row", "gap": 2, "children": [
            {"type": "Badge", "label": label, "color": "success" if contract_changed else "secondary", "variant": "soft"}]})
        if contract_changed:
            rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                         "comparison.md, kpis.csv and result.json match the source reports and the KPI table. "
                         "The check runs on the host, outside the sandbox."})
    elif check.get("status") == "failed":
        rows.append({"type": "Row", "children": [{"type": "Badge", "label": "Host check failed", "color": "danger", "variant": "soft"}]})
        rows.append({"type": "Text", "size": "sm", "value": check.get("detail", "")})
    unchecked = [name for name in check.get("unchecked", []) if f"output/{name}" in changed]
    if unchecked:
        rows.append({"type": "Row", "children": [{"type": "Badge", "label": "Not checked on the host", "color": "warning", "variant": "soft"}]})
        rows.append({"type": "Text", "size": "sm", "color": "secondary", "value":
                     ", ".join(unchecked) + " is the agent's own work; " + ("the host checked quotes in result.json only."
                     if check.get("scope") == "citations" else "the host check covers the three contract outputs only.")})
    return rows
