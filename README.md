# registry-outlier-detection
Cross Registry Implant Outlier Detection
- Extract data from registry annual reports (publicly available)
- apply statistical framework for outlier screening
- apply outlier scoring to determine whether the implant is an overall outlier across registries or if specific registries have skewed performance. This scoring will incorporate both revision rate deviation and affected patient volume.

---

## Table extraction pipeline (`regextract`)

Pulls implant-level revision tables out of registry annual-report PDFs and writes tidy CSVs for the R analysis.
It uses fixed rules only: no LLM calls and no paid services. Running it twice on the same PDF gives the same output.

| Registry | Report | Table | Source |
|---|---|---|---|
| AOANJRR | 2025 | KP17: CPR of primary UKA by prosthesis combination | PDF text |
| NJR | 2025 (Knees) | 3.K8: KM cumulative revision by UKA brand (unicondylar section) | PDF text |
| SIRIS | 2025 | 5.15: failure rates of partial knee systems | PDF text |
| EPRD | **2024** | 52: femoro-tibial combinations (unicondylar sections) | PDF text |
| LROI | 2025 | K054 / K055: UKA by component combination (cemented / uncemented) | image → OCR + manual check |

### Setup
```bash
pip install -r requirements.txt
```
Put the report PDFs in `pdfs/`. Git ignores this folder.
LROI only: install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki). On Windows the pipeline finds
`C:\Program Files\Tesseract-OCR\tesseract.exe` automatically. Otherwise, set the `TESSERACT_CMD` environment variable.
Without Tesseract, the pipeline uses the checked copies in `manual/`.

### Workflow
```bash
python -m regextract locate  --procedure UKA      # find table pages -> config/page_map.yaml + outputs/locate_report.csv
#   CHECKPOINT: open the PDFs at the proposed pages and check them
python -m regextract confirm                      # mark proposed pages as confirmed (or edit page_map.yaml by hand)
python -m regextract extract --procedure UKA --xlsx
python -m pytest -q                               # parser unit tests + regression against known values
```
You can limit a run with `--registries NJR SIRIS` or `--year 2025`. `run --auto-confirm` does everything in one step
and skips the checkpoint, so only use it for quick looks.

**How tables are found.** Every page's text is scanned for the configured caption. Table-of-contents pages are
skipped, and each candidate page is scored on whether the expected column headers are there. Continuation pages
are added while the header keeps repeating. `page_map.yaml` stores the pages together with the PDF's SHA-256.
If you replace a PDF, its pages have to be located and confirmed again.

**How tables are parsed.** The pipeline reads word coordinates and places column boundaries from the configured
header phrases. Each record is anchored on its N column, which handles multi-line cells such as NJR brand names and
EPRD manufacturers and at-risk counts. Section rows (e.g. EPRD "Unicondylar knee arthroplasties, fixed bearing,
cemented") are carried forward onto the rows below them. Rows are filtered by procedure, so one EPRD table can
supply both UKA and TKA.

### Outputs (`outputs/<PROCEDURE>/`)
| File | Content |
|---|---|
| `UKA_long.csv` | **Main file for R.** One row per device × time point |
| `UKA_devices.csv` | One row per device: N, age, sex, hospitals, etc. |
| `tables/*_wide.csv` | Each table as published, including raw cell text and parse status, for auditing |
| `validation_report.csv` | Every check that failed (error / warning / info) |
| `extraction_log.csv` | Per page: caption found, headers found, records |
| `UKA_extraction.xlsx` | All of the above in one workbook (`--xlsx`) |

Key columns in `UKA_long.csv`:
- `registry`, `report_year`, `table_id`, `pdf_page`: where the value came from.
- `row_type`: `device`, or a summary row (`total` = all UKA, `other`, or `average` = SIRIS national average).
- `device_label`: the label as printed.
- `femoral`, `tibial`, `manufacturer_*`, `compartment`: the label split into parts. Cross-registry device harmonisation is a separate later step.
- `time_yr`, `estimate`, `lcl`, `ucl`: in percent.
- `n_at_risk`: EPRD only.
- `low_at_risk`: NJR blue italics, meaning 250 or fewer patients at risk.
- `value_status`: `ok`; `blank` (not printed, e.g. <10 at risk); `not_reported` (n.a.); or `ci_not_estimable`.
- `metric_type`, `metric_method`: **these differ between registries** (CPR, 1-KM, failure rate). Keep them in every comparison.
- `source`, `verification`: `pdf_text`; or `manual` + `verified` / `unverified` for image tables.

```r
library(readr); library(dplyr)
uka <- read_csv("outputs/UKA/UKA_long.csv") |>
  filter(row_type == "device", value_status == "ok")
```

### Checks (`validation_report.csv`)
- lcl ≤ estimate ≤ ucl.
- Cumulative estimates never go down over time, and at-risk counts never go up.
- 0–100 range.
- n_revised ≤ N.
- Every cell parsed.
- Minimum row count per table.
- AOANJRR: device rows add up to the TOTAL row.
- LROI: the revision-type counts add up to total revisions.
- `tests/gold/UKA_gold_values.csv`: known values the output must reproduce.

### LROI (image tables): verification step
The LROI online report prints its tables as images, and LROI does not offer the data for download. For each LROI
table the pipeline:
1. saves the image and a grid overlay to `review/*_image.png` and `review/*_grid_check.png`. Use the overlay to check
   that the column edges in the config line up.
2. runs OCR and writes `review/*_ocr.csv`, laid out like the `manual/` copy.
3. if `manual/<REG>_<YEAR>_<table>.csv` exists, uses that copy and writes `review/*_ocr_vs_manual.csv`,
   which lists every cell where OCR and the manual copy disagree.

The 2025 manual copies were transcribed from the images by Claude and are marked `unverified`. To verify one,
compare it with the image, paying most attention to the cells in `*_ocr_vs_manual.csv`, then fill in
`verified_by` and `verified_on` on each row. Until then, the rows carry `verification = unverified` and produce a
warning. For a new report year, copy `review/*_ocr.csv` to `manual/`, correct it, and verify it.

### Adding a registry, report year or procedure
- **New report year:** copy `config/registries/<REG>_<year>.yaml`, then update `pdf_glob` and the table ID and caption.
  Run `locate`. If headers are reported missing, update the header phrases. The code does not need to change.
- **TKA:** add table entries with `procedures: {TKA: ...}`, e.g. AOANJRR KT9–KT11 or NJR 3.K7(a). EPRD Table 52
  already maps its TKA sections. Then run `--procedure TKA`.
- Registry-specific code lives only in `regextract/hooks.py`, which splits labels into femoral/tibial/manufacturer.

### Known issues
- The EPRD PDF in `pdfs/` is the **2024** report (data to 2023). The 2025 report uses Table 60 for the same content.
  Add that PDF and copy `EPRD_2024.yaml` to `EPRD_2025.yaml`, changing the table number to 60.
- Metrics are not directly comparable across registries. See `metric_type` and the population notes in each config.
