# PROGRESS

_Updated after the documentation/hardening session. Every number below was produced by a run recorded in this repository; "sandbox" = Linux, 1 CPU core, 4 GB RAM, **not** the Windows / 8 GB target._

## Status at a glance
| area | state | evidence |
|---|---|---|
| Processing pipelines (PDF text/OCR, images/TIFF, XLSX, XLS, CSV, registers) | done | `backend/tests/test_readers.py`, eval, benchmarks |
| Extraction + deterministic validation + cross-document checks | done (rule-based, synthetic-only evaluation) | `docs/evaluation/EVALUATION.md` |
| REST API (upload, status, results, search, corrections, failed jobs, reprocess, delete, original file) | done | `docs/openapi.json`, `tests/` |
| Frontend (upload, progress, lists, inspector, corrections, review, search, pagination) | done, verified in headless Chromium | `docs/screenshots/`, `scripts/capture_screenshots.py` |
| Tests | **124 pass** in the sandbox; also pass in a fresh virtualenv with only the declared requirements | `docs/test-results/day2-pytest.txt` |
| Large-document benchmarks | done in sandbox; **Windows runs pending (you)** | `docs/benchmarks/` |
| Semantic search | done, optional, modest quality | `docs/SEMANTIC.md` |
| Documentation (README, architecture, schema, security, OpenAPI, demo script, AI disclosure) | done | `README.md`, `docs/`, `AI_PROMPTS.md` |
| Recorded walkthrough | script ready; **you must record it** | `docs/DEMO_SCRIPT.md` |
| Final audit + submission email | not done | next step |

## Key measured results (details and caveats in the linked reports)
- Evaluation, synthetic data (an upper bound): header-field exact match 99.5% (`test`, first run on frozen code) and 99.4% (`test2`, fresh); line items 98-99%; 156/156 valid files processed per split; 18/18 broken files handled; 36/36 deliberately flawed documents flagged.
- Weak spot: only 4/12 (`test`) and 12/16 -> 14/16 (`test2`, without/with the OCR-confidence rule) extraction errors were flagged for review; the rest were silent.
- 1,000 scanned pages: 429.6 s (139.7 pages/min), Python peak 217 MB, 0 failures; 1,000 digital pages: ~3.7 s, 176 MB.
- Semantic search (GloVe, hybrid): right document first for 5/9 paraphrase queries on a small synthetic set; ~10 ms/query at 100k chunks.

## Changes made in the latest session (all tested)
Document deletion; invoice pagination; configurable CORS; path-containment defence for file serving/deletion; original-file endpoint + "Open original" button; security tests (traversal, size limit, poisoned path, headers) mutation-checked; requirements fixed (numpy declared, unused pandas/reportlab removed); OpenAPI export; schema doc generated from the real DB; diagrams rendered and inspected; demo data + real screenshots; clean-environment verification.

## Known gaps and limitations
- All evaluation data are synthetic and share an author with the extractor; no real invoices; no LLM extraction.
- Confidence is heuristic (not calibrated). Silent extraction errors remain, mostly misread characters in OCR'd images.
- Nothing has been run on Windows by the assistant; Windows setup/commands are unverified.
- Single shared API key (or none); no users/roles/TLS/encryption at rest; parsers not sandboxed (`docs/SECURITY.md`).
- SQLite single-process design; PostgreSQL/pgvector and a real queue are the production next step.
- UI cosmetic issues: text values right-aligned in the field table; numeric column headers not right-aligned.
- Not tested: disk-full, heavy concurrent uploads, files above ~110 MB, multi-core speed-up.

## Next actions
**You:** (1) run `python -m pytest tests -q` on Windows and the ladder in `docs/benchmarks/WINDOWS_RUNBOOK.md`, send me the outputs; (2) rehearse and record the walkthrough (`docs/DEMO_SCRIPT.md`); (3) complete the candidate section of `AI_PROMPTS.md`; (4) push `docintel.bundle` to your own remote.
**Assistant (on request):** final compliance audit against your Windows results, update docs with measured Windows numbers, draft the submission email for the Care Guide thread (Reply All, keeping `apply@careguidebd.com`).
