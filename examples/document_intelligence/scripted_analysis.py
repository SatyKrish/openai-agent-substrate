# Copyright 2026 openai-agent-substrate contributors
# SPDX-License-Identifier: Apache-2.0
"""Code fixture emitted through apply_patch by the key-free model, not pre-staged."""

import csv
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

source = json.loads(Path("work/extracted.json").read_text())
rows = source["rows"]
fields = ["company", "fiscal_year", "revenue_usd_billions",
          "operating_income_usd_billions", "operating_margin_pct", "source_document"]
for row in rows:
    margin = Decimal(row["operating_income_usd_billions"]) / Decimal(row["revenue_usd_billions"]) * 100
    row["operating_margin_pct"] = str(margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
gap = str((Decimal(rows[1]["revenue_usd_billions"]) - Decimal(rows[0]["revenue_usd_billions"])).quantize(Decimal("0.1")))
limitations = ["Synthetic demonstration data; not investment advice.",
               "EBITDA is not disclosed and cannot be inferred from operating income."]
output = Path("output")
output.mkdir(exist_ok=True)
with (output / "kpis.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
lines = ["# Synthetic annual-report comparison", "",
         "| " + " | ".join(fields) + " |", "| " + " | ".join(["---"] * len(fields)) + " |"]
lines += ["| " + " | ".join(row[key] for key in fields) + " |" for row in rows]
lines += ["", f"Revenue gap (BETA - ACME): USD {gap} billion.", "", "## Source citations", ""]
lines += [f'{c["document_id"]} / {c["section"]}: {c["quote"]}' for c in source["citations"]]
lines += ["", "## Limitations", "", *limitations, ""]
(output / "comparison.md").write_text("\n".join(lines))
result = dict(source, revenue_gap_usd_billions=gap, limitations=limitations)
(output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print("Generated comparison.md, kpis.csv, and result.json")
