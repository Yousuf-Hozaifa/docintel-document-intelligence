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
| T5 | Semantic search | ⬜ | to be benchmarked first (Day 3) |
| T6 | Upload, progress, lists, inspect, issues, search | ✅ | verified in Chromium |
| T6 | Inline corrections | ✅ | verified in Chromium (edit, add/delete line, mark reviewed, reprocess) |
| T7 | Automated tests | 🟡 | 83 pass, mutation-checked; OCR/extraction/validation/API/failure covered |
| T7 | Labeled dataset + measured metrics | ⬜ | Day 4 |

| # | Deliverable | Status |
|---|---|---|
| 1 | Git repository | 🟡 local repo only; you must push |
| 2 | README + setup | 🟡 Day-1 version, Windows commands unverified |
| 3 | Architecture diagram + schema | ⬜ (schema exists in code) |
| 4 | Working API + frontend | 🟡 slice works |
| 5 | Four processing pipelines | ✅ |
| 6 | Tests + results | 🟡 `docs/test-results/day1-pytest.txt` |
| 7 | Evaluation report | ⬜ |
| 8 | Recorded walkthrough | ⬜ (you record; I'll write the script) |
| 9 | AI prompts + disclosure | ⬜ |
