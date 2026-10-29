# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Host-side contract checks using original trusted fixtures, never sandbox code.

These checks establish source-backed required fields, not semantic correctness of
arbitrary additional prose. They deliberately support only the bundled schema.
"""

import csv
import io
import json
import re
from decimal import Decimal, ROUND_HALF_UP

OUTPUTS = ("comparison.md", "kpis.csv", "result.json")
FIELDS = ["company", "fiscal_year", "revenue_usd_billions",
          "operating_income_usd_billions", "operating_margin_pct", "source_document"]
LIMITATIONS = ["Synthetic demonstration data; not investment advice.",
               "EBITDA is not disclosed and cannot be inferred from operating income."]


def verify_artifacts(artifacts, inputs, analysis_id):
    if set(artifacts) != set(OUTPUTS):
        raise ValueError("Unexpected artifact set")
    expected_rows, expected_citations = [], []
    for company in ("ACME", "BETA"):
        document = f"{company.lower()}-2024"
        section = inputs[f"{document}.md"].decode().split("## Financial performance\n")[1].split("\n## ")[0]
        sentences = section.splitlines()
        revenue = re.fullmatch(rf"{company} revenue was USD (\d+\.\d+) billion in fiscal 2024, .+\.", sentences[0])
        income = re.fullmatch(r"Operating income was USD (\d+\.\d+) billion in fiscal 2024\.", sentences[1])
        if not revenue or not income:
            raise ValueError("Unsupported original input schema")
        margin = Decimal(income[1]) * 100 / Decimal(revenue[1])
        expected_rows.append(dict(zip(FIELDS, [company, "2024", revenue[1], income[1],
                                   str(margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), document])))
        expected_citations += [dict(document_id=document, section="Financial performance", quote=quote)
                               for quote in sentences[:2]]
    original_kpis = list(csv.DictReader(io.StringIO(inputs["kpis-2024.csv"].decode())))
    reconciled = [dict({k: v for k, v in r.items() if k != "operating_margin_pct"},
                       source_section="Financial performance") for r in expected_rows]
    if original_kpis != reconciled:
        raise ValueError("Original KPI table disagrees with documents")
    gap = str((Decimal(expected_rows[1]["revenue_usd_billions"]) - Decimal(expected_rows[0]["revenue_usd_billions"])).quantize(Decimal("0.1")))
    expected = dict(analysis_id=analysis_id, rows=expected_rows, citations=expected_citations,
                    revenue_gap_usd_billions=gap, limitations=LIMITATIONS)
    if json.loads(artifacts["result.json"]) != expected:
        raise ValueError("Result facts, citations, calculations, or limitations disagree with original inputs")
    reader = csv.DictReader(io.StringIO(artifacts["kpis.csv"].decode()))
    if reader.fieldnames != FIELDS or list(reader) != expected_rows:
        raise ValueError("CSV facts disagree with original inputs")
    report = artifacts["comparison.md"].decode()
    required = LIMITATIONS + [f"Revenue gap (BETA - ACME): USD {gap} billion."]
    required += ["| " + " | ".join(row[key] for key in FIELDS) + " |" for row in expected_rows]
    required += [f'{c["document_id"]} / {c["section"]}: {c["quote"]}' for c in expected_citations]
    if any(text not in report for text in required):
        raise ValueError("Markdown lacks a required source fact, citation, or limitation")
    return expected
