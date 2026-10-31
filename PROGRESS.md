# PROGRESS

_Last updated: Day 2 (performance, features, semantic search). Everything marked "verified" was actually executed; see `docs/test-results/`._

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

## Remaining work
**Next**: UI pagination for large documents, labeled evaluation dataset + harness for extraction (Day 4 scope: per-file-type precision/recall, exact match, failure rate, degraded scans),
Windows benchmark results from you, architecture diagram/ERD, demo script, `AI_PROMPTS.md`, final compliance audit, submission email draft.
