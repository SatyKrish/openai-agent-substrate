# Output contract

Inputs: `inputs/acme-2024.md`, `inputs/beta-2024.md`, `inputs/kpis-2024.csv`,
and their trusted staging metadata in `inputs/manifest.json`.

`work/extracted.json` has `analysis_id`, `rows`, and `citations`. Each row contains
`company`, `fiscal_year`, `revenue_usd_billions`,
`operating_income_usd_billions`, and `source_document`. Amounts are decimal strings.
Each citation contains `document_id`, `section`, and `quote`; the section must be
`Financial performance` and the quote must be an exact sentence from that section.

Produce exactly these three files in `output/`:

- `kpis.csv`: columns in this order: `company,fiscal_year,revenue_usd_billions,operating_income_usd_billions,operating_margin_pct,source_document`.
  One row each for ACME and BETA, in that order. Add `operating_margin_pct` as a
  two-decimal string to each extracted row; preserve the other values.
- `comparison.md`: a table of those same six fields, in the same order, using
  `| value | value |` rows; the revenue gap in USD billions; all exact citation
  quotes with their document IDs and sections; and both limitations below.
- `result.json`: keys `analysis_id`, `rows` (the same rows as the CSV),
  `revenue_gap_usd_billions` (one-decimal string), `citations` (all extracted
  citations), and `limitations` (the two exact strings below). Write this file
  after generating the report and CSV.

Limitations:

1. `Synthetic demonstration data; not investment advice.`
2. `EBITDA is not disclosed and cannot be inferred from operating income.`
