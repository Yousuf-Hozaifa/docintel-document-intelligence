# PROGRESS

_Last updated: Day 2 (performance session + deferred feature session). Everything marked "verified" was actually executed; see `docs/test-results/`._

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


## Day 2 — large-document performance (measured; raw data `docs/benchmarks/results.jsonl`, table `RESULTS.md`)
Environment: Linux sandbox, **1 core, 4 GB RAM**, Xeon 2.1 GHz, Tesseract 5.3.4, PyMuPDF 2.0. NOT the target Windows/8 GB machine.
Synthetic clean invoices; one invoice per page. 64 tests pass (`docs/test-results/day2-pytest.txt`).

| Run | Pages | Wall | Pages/min | Peak RAM (process tree) |
|---|---|---|---|---|
| digital | 1,000 | 4.0 s | ~15,000 | 174 MB |
| scanned, defaults (cache flush, Tesseract default threads) | 250 | 213 s | 70.3 | 249 MB |
| scanned, defaults + Tesseract thread cap (current defaults) | 1,000 | 474.8 s | 126.4 | 411 MB (Python 206 MB) |

- Memory is bounded in page count (Python process 204–206 MB from 100 to 1,000 scanned pages). MuPDF's decoded-image cache was
  the main consumer (502 → 249 MB at 100 pages when flushed; ~4% throughput cost).
- `OMP_THREAD_LIMIT=1` gave ~1.7x on this single core (69 → ~119 pages/min; identical OCR confidence). Mechanism unverified; effect on
  multi-core CPUs **unmeasured** — run `docs/benchmarks/WINDOWS_RUNBOOK.md`.
- Page-parallel OCR: +80-100 MB per extra in-flight page, no speedup on 1 core (not measurable here). Run-to-run spread ~±8%.
- Text-heavy pages (~52 lines): 6.0 pages/min (default Tesseract) / 16.3 (capped), tested only up to 25 pages. 1,000 dense pages: **not measured**.
- API stayed responsive during a 50-page scan: 0 errors in ~180 polling rounds; health p95 20 ms, max 182 ms (measured before the thread-cap default).
- The 1,000-page peak (411 MB) is a transient: Python stayed ~206 MB, so ~205 MB was in a single Tesseract process for under one sample interval
  (inferred; the per-process breakdown call missed it). A first 1,000-page attempt was killed at page 253 when the sandbox ended my turn (not a failure of the app).
- **Quality signal:** on the 1,000 OCR'd pages 253 invoices were flagged for review (digital: 0). Diagnosed on a 50-page sample: header totals were
  correct; flags came from OCR cell errors in line items (quantity "7" read as "a" -> row dropped; "79.55" read as "7955"), which validation caught.

### Day 2 features — DONE (verified)
- Corrections API (`PATCH /api/invoices/{id}`): field + line-item edit/add/delete, audit trail, automatic re-validation, "mark reviewed" with
  carry-over across re-validation. Failed-job list, per-document reprocess (409 when a job is active), retry-all-failed. Optional API-key auth.
- Duplicate detection is now symmetric (both files flagged); PDF↔PDF and PDF↔Excel cross-verification (`VAL-XDOC-001/002`); Excel register parsing.
- OCR-dropped quantity no longer drops the line (`quantity=null`, `VAL-LINE-003` with an amount/unit-price hint).
- UI: inline corrections, add/delete line items, mark reviewed, reprocess, retry-all-failed, API-key prompt.
- Verified: **83 tests pass** (`docs/test-results/day2-pytest.txt`); 3 deliberate code mutations each made tests fail (cross-checks off: 8 failures; resolved
  carry-over off: 1; tolerance ignored: 3); real-browser run of the correction / review / retry / API-key flows (0 JS errors);
  cross-verification stress (`docs/benchmarks/CROSSDOC_STRESS.md`).
- **Performance cost:** 1,000-page digital PDF went from 4.0 s to 5.9 s with the new per-invoice checks (single runs). Scanned-PDF benchmarks were
  NOT re-run after this change; OCR dominates their time, so I expect little effect, but that is unmeasured.

### Known limitations of the new features
- Cross-verification compares header values only (not line items). Matching is by invoice number + non-contradicting supplier; a PDF whose supplier
  was mis-read (e.g. positional guess) simply fails to match a register with a different supplier string.
- Register detection is header-alias based (English column names); unusual layouts fall back to single-invoice parsing.
- Reprocessing discards manual corrections on that document. API key is one shared secret (no users/roles).
- The UI shows the first 25 invoices of a document (a 1,000-invoice document is fully stored/searchable but not paginated in the inspector yet).

## Remaining work
**Next**: semantic search (benchmark a small local embedding model first; keyword search stays the default), UI pagination for large documents,
labeled evaluation dataset + harness (Day 4 scope), Windows benchmark results from you, architecture diagram/ERD, demo script, AI disclosure.
