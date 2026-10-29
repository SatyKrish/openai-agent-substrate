# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Sandbox-side extraction and checks for the bundled synthetic report schema."""

import argparse
import csv
import hashlib
import io
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

DOCUMENTS = ("acme-2024", "beta-2024")
INPUTS = {f"{name}.md" for name in DOCUMENTS} | {"kpis-2024.csv", "LICENSE"}
FIELDS = ["company", "fiscal_year", "revenue_usd_billions",
          "operating_income_usd_billions", "operating_margin_pct", "source_document"]
LIMITATIONS = ["Synthetic demonstration data; not investment advice.",
               "EBITDA is not disclosed and cannot be inferred from operating income."]


def extract(root):
    manifest = json.loads((root / "inputs/manifest.json").read_text())
    if set(manifest["files"]) != INPUTS:
        raise ValueError("Unexpected input selection")
    for name, metadata in manifest["files"].items():
        data = (root / "inputs" / name).read_bytes()
        if len(data) != metadata["bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise ValueError(f"Input digest mismatch: {name}")
    rows, citations = [], []
    for name in DOCUMENTS:
        text = (root / "inputs" / f"{name}.md").read_text()
        section = text.split("## Financial performance\n", 1)[1].split("\n## ", 1)[0]
        revenue = re.search(r"([A-Z]+) revenue was USD ([0-9.]+) billion in fiscal (2024),[^\n]+\.", section)
        income = re.search(r"Operating income was USD ([0-9.]+) billion in fiscal (2024)\.", section)
        if not revenue or not income or revenue[3] != income[2]:
            raise ValueError(f"Unsupported or incomplete report: {name}")
        rows.append(dict(company=revenue[1], fiscal_year=revenue[3],
                         revenue_usd_billions=revenue[2], operating_income_usd_billions=income[1],
                         source_document=name))
        citations.extend(dict(document_id=name, section="Financial performance", quote=m[0])
                         for m in (revenue, income))
    with (root / "inputs/kpis-2024.csv").open(newline="") as handle:
        supplied = list(csv.DictReader(handle))
    expected = [dict(row, source_section="Financial performance") for row in rows]
    if supplied != expected:
        raise ValueError("KPI CSV disagrees with the source reports")
    return dict(analysis_id=manifest["analysis_id"], rows=rows, citations=citations)


def verify(root):
    source = extract(root)
    result = json.loads((root / "output/result.json").read_text())
    rows = []
    for row in source["rows"]:
        margin = (Decimal(row["operating_income_usd_billions"]) / Decimal(row["revenue_usd_billions"]) * 100)
        rows.append(dict(row, operating_margin_pct=str(margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))))
    gap = str((Decimal(rows[1]["revenue_usd_billions"]) - Decimal(rows[0]["revenue_usd_billions"])).quantize(Decimal("0.1")))
    expected = dict(source, rows=rows, revenue_gap_usd_billions=gap, limitations=LIMITATIONS)
    if result != expected:
        raise ValueError("JSON facts, citations, calculations, or limitations failed verification")
    csv_text = (root / "output/kpis.csv").read_text()
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames != FIELDS or list(reader) != rows:
        raise ValueError("CSV does not match the calculated source facts")
    report = (root / "output/comparison.md").read_text()
    required = LIMITATIONS + [f"Revenue gap (BETA - ACME): USD {gap} billion."]
    required += [f'| {" | ".join(row[key] for key in FIELDS)} |' for row in rows]
    required += [f'{c["document_id"]} / {c["section"]}: {c["quote"]}' for c in source["citations"]]
    if any(text not in report for text in required):
        raise ValueError("Markdown is missing a required fact, citation, or limitation")
    if {p.name for p in (root / "output").iterdir()} != {"comparison.md", "kpis.csv", "result.json"}:
        raise ValueError("Unexpected output files")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["extract", "verify"])
    args = parser.parse_args()
    root = Path.cwd()
    if args.action == "extract":
        data = extract(root)
        (root / "work").mkdir(exist_ok=True)
        (root / "work/extracted.json").write_text(json.dumps(data, indent=2) + "\n")
        print(json.dumps(data, indent=2))
    else:
        verify(root)
        print("SANDBOX_DOCUMENT_ANALYSIS_VERIFIED")


if __name__ == "__main__":
    main()
