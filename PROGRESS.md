# PROGRESS

_Last updated: end of Day 1. Everything marked "verified" was actually executed; see `docs/test-results/`._

## Current milestone: Day 1 vertical slice — DONE
Upload → read/OCR → extract → validate → persist → inspect in UI.

### Verified
- `pytest`: **61 passed** (sandbox: Linux, 1 CPU core, ~4 GB RAM; log in `docs/test-results/day1-pytest.txt`).
- Live server (uvicorn + background worker thread) processed 4 uploaded files over HTTP: digital PDF, scanned PDF
  (OCR conf 95.3), XLSX → identical total on all three; corrupt PDF → `failed / corrupt_file`, not retried.
- Real browser (Playwright/Chromium, sandbox) drove the UI: multi-file upload, status chips, field inspector,
  validation issue display, keyword search with highlighted snippets. 0 JS console errors.
- Bugs found by tests/runs and fixed: UTF-16 mis-detection of cp1252 CSV; two-column header lines
  (number + date on one line) missed the date; server upload path leaked in failure messages.

### NOT verified
- Anything on your **Windows / 8 GB** machine (no access). Commands in README must be run by you.
- Docker (not used). Semantic search, LLM, 1,000-page runs, evaluation metrics: not started.

## Remaining work (priority order)
**Day 2** (next action: manual corrections + failed-job API)
1. `PATCH /api/invoices/{id}` corrections: audit row in `corrections`, re-run validation, update review flag.
2. `GET /api/jobs?status=failed`, `POST /api/documents/{id}/reprocess`.
3. Make duplicate detection symmetric (today only the *later* file is flagged; the first is not).
4. Cross-document verification: PDF↔PDF and PDF↔Excel (`VAL-XDOC-001`), compare matched invoice numbers.
5. Simple API-key auth + rate/size limits review; OpenAPI export.
**Day 3**: inline-correction UI, reprocess button, cross-doc UI, semantic search (benchmark first), UI polish
(right-align numeric headers, left-align text values).
**Day 4**: labeled synthetic eval set (all types + degraded scans), eval harness, 1,000-page digital & scanned
scale tests (time + peak memory), failure-injection tests.
**Day 5**: clean-setup verification, README/architecture/ERD, demo script, `AI_PROMPTS.md`, compliance audit, email draft.

## Known limitations (current)
- Line items: only 4-column (description, qty, unit price, amount) rows are parsed; wrapped descriptions are skipped.
- Spreadsheets: formula cells without cached values read as empty (openpyxl `data_only`).
- OCR is English-only (`eng`); `psm 6` layout assumption.
- Confidence = heuristic (rule strength × OCR quality), **not calibrated**.
- One worker thread by default; OCR pages are sequential (bounded memory, not yet parallel).
- Multi-invoice splitting keys on a changed invoice number; invoices without numbers are not split.
- No authentication yet.
