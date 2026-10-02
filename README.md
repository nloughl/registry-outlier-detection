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

**Case-mix tables** (revision by sex / age / implant design) go to a separate file, `UKA_casemix_long.csv`:

| Registry | Report | Table | Notes |
|---|---|---|---|
| AOANJRR | 2025 | KP22: CPR of primary UKA by gender and age (OA) | sex subtotals + TOTAL row kept |
| LROI | 2025 | K052B: cumulative *major* revision of UKA by procedure period (2009-2010 ... 2021-2022) | image → OCR + manual check; one row per period, with `procedure_period`, `period_start` and `period_end` |
| NJR | 2025 (Knees) | 3.K6: KM revision by sex, age, fixation, constraint and bearing | 10 sideways pages; only the `All unicondylar, cemented` / `All unicondylar, uncemented/hybrid` blocks (and their medial/lateral × bearing rows) are kept for UKA |

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
| `UKA_casemix_long.csv` | Case-mix tables: one row per stratum × sex × time point |
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

Key columns in `UKA_casemix_long.csv` (same provenance/metric/value columns as above, plus):
- `row_type`: `stratum`; `subtotal` (AOANJRR sex totals); `group` (NJR design block such as "All unicondylar, cemented"); `total`.
- `procedure_period`, `period_start`, `period_end`: LROI K052B procedure period, e.g. `2009-2010`.
- `sex`, `age_group`: standardised to `<55`, `55-64`, `65-74`, `>=75` across registries. `age_group_raw` keeps the printed label.
- `design_group`, `design_subgroup`, `implant_class`, `fixation`, `compartment`, `bearing`: NJR 3.K6 design strata (e.g. unicondylar / cemented / medial / MBT).
- `row_flags`: `suppressed<4` = NJR suppressed a count below 4.

```r
library(readr); library(dplyr)
uka <- read_csv("outputs/UKA/UKA_long.csv") |>
  filter(row_type == "device", value_status == "ok")
casemix <- read_csv("outputs/UKA/UKA_casemix_long.csv") |>
  filter(value_status == "ok")
```
To rebuild only one kind: `python -m regextract extract --procedure UKA --outputs casemix`.

### Checks (`validation_report.csv`)
- lcl ≤ estimate ≤ ucl.
- Cumulative estimates never go down over time, and at-risk counts never go up.
- 0–100 range.
- n_revised ≤ N.
- Every cell parsed.
- Minimum row count per table.
- AOANJRR: device rows add up to the TOTAL row.
- LROI: the revision-type counts add up to total revisions.
- Case-mix: age rows add up to their sex subtotal (AOANJRR), and medial/lateral rows add up to their design block
  per sex (NJR). NJR's suppressed `<4` counts are allowed for.
- Case-mix gold values: `tests/gold/UKA_casemix_gold_values.csv`.
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
- Config options used by the case-mix tables:
  - `text_rotation: 90` for tables printed sideways.
  - `strata` for male/female blocks printed side by side.
  - `"1 year#2"` to match the 2nd occurrence of a repeated header.
  - `anchor: [a, b]` for several N columns.
  - `group_pattern` for hierarchical row labels.
  - `procedures: {UKA: {groups: [...]}}` to keep only some design blocks.
  - `output: casemix`.
- Registry-specific code lives only in `regextract/hooks.py`, which splits labels into femoral/tibial/manufacturer.

---

## Rare joints: total ankle and total elbow replacement

Run these with `--procedure ANKLE` or `--procedure ELBOW`. Outputs go to `outputs/ANKLE/` and `outputs/ELBOW/`, with the same file layout as UKA:
- `<PROC>_long.csv`: model rows.
- `<PROC>_casemix_long.csv`: overall, diagnosis, period, class and sex/age rows.

The registry mean used in the funnels is picked from these rows in the R repo (`config/rare_joint_means.csv`), not here. Extraction keeps every row as printed.

```bash
python -m regextract locate  --procedure ANKLE     # then check pages and confirm, as for UKA
python -m regextract extract --procedure ANKLE
python -m regextract extract --procedure ELBOW
```

| Registry | Document | Table | Content | Output |
|---|---|---|---|---|
| AOANJRR | Ankle suppl. 2025 (`AOANJRR_2025_ankle.yaml`) | A11 | Total ankle by primary diagnosis | casemix (`diagnosis`) |
| | | A14 | Total ankle by period (pre-2015 / 2015-2024) | casemix (`procedure_period`) |
| | | A15 | Total ankle by prosthesis combination (tibia / talar) | device |
| AOANJRR | Elbow & wrist suppl. 2025 (`AOANJRR_2025_elbow.yaml`) | ET6 / ET7 / ET8 | Total elbow (no radial) by humeral stem, for fracture / OA / RA | device (`diagnosis`) |
| | | ET9 | Total elbow and humeral hemi by class × diagnosis | casemix (`implant_class`, `diagnosis`) |
| NJR | Ankles 2025 (`NJR_2025_ankle.yaml`) | 3.A3 | All cases, then sex × age | casemix (`row_type` total / subtotal / stratum) |
| | | 3.A4 | Ankle brands (printed sideways) | device (`tibial`, `talar`) |
| NJR | Elbows 2025 (`NJR_2025_elbow.yaml`) | 3.E6 | Procedure type within acute trauma / elective (2 pages) | casemix (`diagnosis` = indication, `implant_class`) |
| | | 3.E8 | Elbow brands, grouped: total elbow / radial head / distal humeral hemi (sideways) | device (`section` = `implant_class`, `constraint` = linked) |
| LROI | Annual report 2025 (`LROI_2025.yaml`) | A014B p153 | All primary ankles, overall | casemix (total) |
| | | A015B p153 | Total ankle for OA, by procedure period | casemix (`procedure_period`) |
| | | E016B p224 | All primary elbows, overall | casemix (total) |
| | | E017B p224 | Total elbow and radial head, by type | casemix (`implant_class`) |

LROI publishes no model-level rates for ankle or elbow, so it contributes a registry mean only.

### Decisions (October 2026)
- **Elbow scope is total elbow replacement only.** AOANJRR's supplement is total elbow only (without radial replacement). The funnels compare:
  - AOANJRR ET6–ET9 total elbow
  - NJR 3.E8 total-elbow brands and the 3.E6 total-elbow rows
  - LROI E017B total-elbow row

  Radial head, distal humeral hemi, lateral resurfacing and NJR "unconfirmed" rows are extracted but not plotted. Unconfirmed rows are flagged `unconfirmed_procedure_type`.
- **AOANJRR elbow models:** all three diagnosis tables are extracted, and each row keeps its `diagnosis`. By default the funnel code pools them into one point per humeral stem (n-weighted). It can optionally show each diagnosis against that diagnosis's ET9 rate.
- **AOANJRR ankle mean:** the n-weighted pool of **all A11 diagnosis rows**. This is the same population as the A15 models (all diagnoses and years, n = 5,379). A14 is extracted for period funnels, comparing 2015–2024 with pre-2015.
- **NJR elbow mean:** the n-weighted pool of the two 3.E6 **total elbow replacement** rows, acute trauma (1,224) and elective (3,516). This matches the 3.E8 total-elbow brand rows, which include both indications.
- **NJR ankle mean:** 3.A3 "All cases".
- **LROI ankle mean:** A014B "Primary ankle" (all primary ankles, n = 1,460 from the caption). LROI notes that ankle revisions are under-registered. A015B (total ankle for OA, by period) is the alternative.
- **LROI elbow mean:** E017B "Total elbow arthroplasty", following the total-elbow scope. E016B (all elbow types, n = 1,534) is also extracted.
  - E017B prints no per-type N. The manual copy uses the registered counts from Table E001: 816 total elbows and 599 radial heads (2014–2024). This is approximate, since E017's survival analysis covers 1,405 procedures across both types.
- **Model names:** extraction keeps the names as printed and only strips report marks:
  - `*`: not used in the report year (kept as the flag `not_used_in_report_year_*`)
  - the AOANJRR footnote "1" on "Hintermann Series H3"
  - wrapped hyphens

  Names are harmonised across registries in the R repo (`config/rare_device_families.csv`), e.g. BOX/Box, S.T.A.R/Star, Coonrad/Morrey vs Coonrad Morrey, Latitude.
- **LROI values** are a manual transcription from the images, marked `unverified` as for the other LROI tables. Every estimate equals the midpoint of its CI, which catches 3/5 misreads. `review/*_ocr_vs_manual.csv` lists where OCR disagrees.
- **Time points differ by registry:**
  - AOANJRR: 1/3/5/(7)/10/15; ET9 has 14
  - NJR: 1/3/5/7/10 plus 11 (elbow) or 14 (ankle)
  - LROI: 1/3/5/7/10; E017B has 9 instead of 10

  1, 3 and 5 years are common to all three.

### Parser features added for these tables
- `document:` in a config. A registry can have several PDFs per report year (main report + supplements). The latest config is kept per registry *and* document.
- `set_fields:` sets constant fields on every row of a table, e.g. ET6 `diagnosis: Fracture/Dislocation`.
- `fixed_columns:` fixes the x-position of columns printed without a header (NJR 3.E6 / 3.E8 row labels).
- `span_columns:` handles label cells drawn around several rows. NJR 3.E8 prints "Total elbow replacement brands" once per block, and the drawn cell rectangle assigns it to every row inside.
- `column_bounds:` sets explicit column boundaries where values start left of their header (NJR 3.A3 / 3.A4).
- Manual copies of tables without an N column (LROI overall figures) can carry `n_total`.
- New output columns:
  - device: `talar`, `humeral`, `ulnar`, `diagnosis`, `implant_class`, `constraint`
  - case-mix: `diagnosis`
- Gold values: `tests/gold/RARE_gold_values.csv`.

### Known issues
- The EPRD PDF in `pdfs/` is the **2024** report (data to 2023). The 2025 report uses Table 60 for the same content.
  Add that PDF and copy `EPRD_2024.yaml` to `EPRD_2025.yaml`, changing the table number to 60.
- Metrics are not directly comparable across registries. See `metric_type` and the population notes in each config.
