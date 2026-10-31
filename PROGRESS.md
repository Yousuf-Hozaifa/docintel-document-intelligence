# PROGRESS

_Last updated: Day 2 (performance, features, semantic search) + Day 4 scope started early (evaluation). Everything marked "verified" was actually executed; see `docs/test-results/`._

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

## Semantic search (done, optional) — details in `docs/SEMANTIC.md`
- Pluggable embedder (`off` default | `glove` | `sbert`), chunk store in SQLite, brute-force cosine, incremental index append, hybrid (RRF) ranking, reindex endpoint, UI mode toggle.
- Measured (GloVe-50, sandbox): +22 MB / 0.05 s load; 44.6k chunks/s; ~25 ms/query at 250k chunks; no measurable pipeline slowdown; hybrid P@1 0.56 vs keyword 0.11 on 9 paraphrase queries
  (tiny, synthetic, optimistic). Decision: optional, not a required dependency.
- **NOT measured:** any transformer model (model hosts unreachable from the sandbox); the `sbert` adapter is unrun. Windows untested.
- Verified: 98 tests (fake embedder; real GloVe in one test), mutation check on the incremental index, Chromium check of both modes and the semantic-off message (0 JS errors).
- Bugs/mistakes caught: benchmark harness first overstated load memory (imported PyMuPDF inside the timed window) and was fixed; I also corrected my earlier "45% slower" claim (see above).

## Evaluation (done for the synthetic dataset) — details and limits in `docs/evaluation/EVALUATION.md`
- Dataset generator with 3 layouts + variants (`eval/make_dataset.py`): dev 86 files/101 invoices; test and test2 174 files/199 invoices each (156 valid + 18 broken).
- Harness (`eval/run_eval.py`): real API + worker; precision/recall/exact-match, line items, validation-rule detection, review-flag rates, calibration, timing, per-type success/failure, negative cases.
- **Held-out `test`, first run on frozen code:** success 156/156, 18/18 negatives handled, header-field exact match 99.5%, line items P=R=99.0%, invoices fully correct 94.0%, error-catch 4/12.
- **Fresh `test2`:** header 99.4%, line items 98.2%/98.1%, fully correct 92.0%; new OCR-confidence rule: error catch 12/16 -> 14/16, clean invoices falsely flagged 0/147 -> 4/147.
- Mistakes found and fixed along the way (all disclosed in the report): dataset bug (sheets without currency), scoring-key mismatch, a false-review metric that counted correct duplicate flags, a CSV delimiter bug, label separators in spreadsheets,
  OCR-noise quantities dropping rows (now kept + flagged). The OCR-confidence rule was designed after inspecting `test` errors, so it is evaluated on the fresh `test2`; its floor (60) was chosen on dev only.
- **Not measured:** real invoices; Windows; LLM extraction (none wired in). 112 tests pass (`docs/test-results/day2-pytest.txt`).
- Frontend tweak (OCR word confidence shown in the inspector) is built but was not re-run in a browser.
- **Performance re-check after the OCR word-confidence change (single runs, sandbox):** scanned 50p 22.4 s (251 MB tree / 206 MB python); digital 1,000p 3.7 s (176 MB);
  **scanned 1,000p 429.6 s, 139.7 pages/min, python peak 217 MB (was 206), tree peak 429 MB (same transient Tesseract spike as before), 0 failures** (earlier: 474.8 s, 126.4 pages/min). OCR word lists for all pages are held until extraction
  (~+11 MB per 1,000 pages; grows linearly). 263/1,000 of those clean synthetic scans were flagged for review (earlier 253): there is no ground truth in this benchmark, so I cannot say how many are real OCR errors versus false flags;
  on the labeled eval the false-review rate for clean invoices was 2.7%, but the 1,000-page file has denser 3–6-row tables with large amounts.

## Remaining work
**Next**: UI pagination for large documents, architecture diagram/ERD, demo script + seeded demo data, `AI_PROMPTS.md`, final clean-setup verification and compliance audit, submission email draft,
Windows benchmark results from you (docs/benchmarks/WINDOWS_RUNBOOK.md).
