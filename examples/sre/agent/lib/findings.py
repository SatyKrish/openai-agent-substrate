# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Host check of output/findings.json: every quote must appear in the host's copy of the evidence it cites.

The host copies were written by the harness tools before the model saw them, so
editing evidence/ inside the sandbox cannot make a quote pass.
"""

from __future__ import annotations

import json


def _normal(text: str) -> str:
    return " ".join(text.split())


def check(conversation, files: dict[str, bytes]) -> dict:
    outputs = {name.removeprefix("output/"): data for name, data in files.items() if name.startswith("output/")}
    unchecked = sorted(name for name in outputs if name != "findings.json")
    if "findings.json" not in outputs:
        return {"status": "no_findings", "unchecked": unchecked}
    try:
        findings = json.loads(outputs["findings.json"])["findings"]
        if not isinstance(findings, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return {"status": "failed", "detail": "output/findings.json has no findings list.", "unchecked": unchecked}
    evidence = {entry["path"]: entry for entry in conversation.evidence}
    texts: dict[str, str] = {}
    quotes = 0
    resources = []
    for finding in findings:
        if not isinstance(finding, dict):
            return {"status": "failed", "detail": "Each finding must be an object.", "unchecked": unchecked}
        resource = str(finding.get("resource") or "a finding")
        cited = finding.get("evidence")
        if not isinstance(cited, list) or not cited:
            return {"status": "failed", "detail": f"{resource} cites no evidence.", "unchecked": unchecked}
        for item in cited:
            path = item.get("file") if isinstance(item, dict) else None
            quote = item.get("quote") if isinstance(item, dict) else None
            if path not in evidence:
                return {"status": "failed", "unchecked": unchecked,
                        "detail": f"{resource} cites {path!r}, which no harness tool wrote."}
            if not isinstance(quote, str) or not _normal(quote):
                return {"status": "failed", "detail": f"{resource} has an empty quote from {path}.", "unchecked": unchecked}
            if path not in texts:
                texts[path] = _normal(conversation.host_copy(evidence[path]).read_text(encoding="utf-8", errors="replace"))
            if _normal(quote) not in texts[path]:
                shown = quote if len(quote) <= 120 else quote[:120] + "…"
                return {"status": "failed", "detail": f"Not found in {path}: {shown}", "unchecked": unchecked}
            quotes += 1
        resources.append(resource)
    if not findings:
        return {"status": "no_findings", "unchecked": unchecked}
    return {"status": "evidence_passed", "findings": len(findings), "quotes": quotes, "resources": resources,
            "unchecked": unchecked}
