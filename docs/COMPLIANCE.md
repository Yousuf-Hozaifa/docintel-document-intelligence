# Requirements compliance matrix

Status legend: ✅ implemented and verified · 🟡 partial · ⬜ not started. "Verified" = executed here; see PROGRESS.md.

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| T1 | Accept PDF, JPG, PNG, TIFF, XLSX, XLS, CSV (multi-file) | ✅ | `tests/test_readers.py`, `test_multi_file_upload_with_all_formats` |
| T1 | Digital PDF text · OCR for scans/images | ✅ | per-page routing; OCR conf recorded (95.3 on a synthetic scan) |
| T1 | Multi-page PDFs, multi-sheet workbooks, multi-frame TIFF | ✅ | reader tests |
| T1 | Corrupted / unsupported / encrypted / empty files | ✅ | 8 failure tests; permanent errors not retried |
| T1 | Poor-quality scans | 🟡 | blur/low-res/rotation generators exist; measured in Day 4 eval |
| T1 | ~1,000-page PDFs | 🟡 | measured in sandbox (1 core/4 GB): digital 4.0 s/174 MB; scanned 474.8 s, Python peak 206 MB. Not yet run on target 8 GB Windows machine; dense-text 1,000 pages not measured |
| T2 | Extract 8 fields + line items → JSON | ✅ | rule-based; evidence + heuristic confidence per field |
| T2 | Missing/ambiguous not invented | ✅ | tests: null values, ambiguous date, `¥`, `$` |
| T2 | Optional LLM extraction | ⬜ | deliberately deferred (CPU/no-API constraint) |
| T3 | Missing/invalid fields, numbers, dates, currencies | ✅ | `test_extract_validate.py` |
| T3 | Subtotal/tax/line/total checks with rounding tolerance | ✅ | Decimal, per-currency precision, configurable tolerance |
| T3 | Duplicate invoices | ✅ | symmetric, 2,000 flagged in stress run; `test_review.py` |
| T3 | PDF↔PDF, PDF↔Excel cross-verification | ✅ | header-level comparison incl. 5,000-row register; line items not compared |
| T3 | Manual-review flag | ✅ | `review_required` |
| T3 | Manual correction support | ✅ | audited, re-validated, mark-reviewed |
| T4 | Multi-file upload, status, results, search | ✅ | `tests/test_api.py` |
| T4 | Manual corrections, failed-job listing, reprocess | ✅ | `test_review.py`; API-key auth added |
| T4 | Background processing, retry, duplicate-processing protection | ✅ | DB-backed queue, atomic claim, backoff retry, SHA-256 dedupe |
| T5 | Persist metadata, text, results, line items, issues, history | ✅ | SQLite schema (`app/db.py`) |
| T5 | Keyword search + filters | ✅ | FTS5 + filters |
| T5 | Semantic search | ✅ | optional (off by default), hybrid ranking, measured in `docs/SEMANTIC.md`; quality modest and only validated on a tiny synthetic set; no transformer measured |
| T6 | Upload, progress, lists, inspect, issues, search | ✅ | verified in Chromium; invoice pagination, open-original, delete added |
| T6 | Inline corrections | ✅ | verified in Chromium (edit, add/delete line, mark reviewed, reprocess) |
| T7 | Automated tests | ✅ | 112 pass, mutation-checked; OCR/extraction/validation/API/failure covered |
| T7 | Labeled dataset + measured metrics | ✅ | synthetic only (upper bound); `docs/evaluation/EVALUATION.md`: per-type P/R/exact match, line items, success/failure, timing, calibration, negatives. No real invoices; not run on Windows |

| # | Deliverable | Status |
|---|---|---|
| 1 | Git repository | 🟡 complete history in `docintel.bundle` (clone it); you must push it to your own remote |
| 2 | README + setup | 🟡 complete (config table, API, tests, limits); **Windows commands not yet verified on a Windows machine** |
| 3 | Architecture diagram + schema | ✅ `docs/ARCHITECTURE.md` (+ rendered diagrams), `docs/DATABASE.md` (generated from the real schema), `docs/openapi.json` |
| 4 | Working API + frontend | ✅ verified end-to-end in headless Chromium (upload, progress, inspect, correct, review, search, failure handling) |
| 5 | Four processing pipelines | ✅ |
| 6 | Tests + results | ✅ 124 tests, `docs/test-results/day2-pytest.txt` (Linux sandbox; Windows run pending) |
| 7 | Evaluation report | ✅ generated from measured runs (synthetic data; sandbox environment) |
| 8 | Recorded walkthrough | 🟡 script + demo data + real screenshots ready (`docs/DEMO_SCRIPT.md`); **you must record it** |
| 9 | AI prompts + disclosure | 🟡 `AI_PROMPTS.md` written (verbatim prompts, mistakes list); **the candidate-contribution section must be completed by you** |
