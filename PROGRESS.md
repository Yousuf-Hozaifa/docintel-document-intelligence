# PROGRESS

_Updated after receiving the candidate's Windows session log and eval archive. "S" = the assistant's Linux sandbox (1 core, 4 GB); "W" = the candidate's Windows machine (reported by the candidate)._

## Status at a glance
| area | state | evidence |
|---|---|---|
| Pipelines, extraction, validation, cross-document checks, corrections, API, UI, search | done | `docs/COMPLIANCE.md` (final matrix) |
| Tests | S: **131 pass**; W: 123 pass + 1 skip on the earlier 124-test version (the skip was a Linux-only path, now fixed) | `docs/test-results/` |
| Evaluation (synthetic) | done; upper bound | `docs/evaluation/EVALUATION.md` |
| Benchmarks | S measured; W reported (raw data pending) | `docs/benchmarks/` |
| Documentation set | done | `README.md`, `docs/`, `AI_PROMPTS.md` |
| Walkthrough recording | **you** | `docs/DEMO_SCRIPT.md` |
| Submission email | draft ready | `docs/SUBMISSION_EMAIL.md` |

## What the Windows session established
- Your scripts were byte-identical to the shipped ones; digital fixtures were byte-identical across platforms (deterministic generation).
- All 11 demo files produced exactly the statuses predicted by the demo script (01/04/05/06/09 needs review; 02/03/07/08 completed; 10 failed; 11 rejected), with Tesseract 5.4 on Windows vs 5.3.4 in the sandbox.
- Reported benchmarks: scanned 1,000 pages in 321.9 s (186 pages/min), 144 MB peak, throughput flat from 10 to 1,000 pages; digital 1,000 pages 9.15 s. CPU/cores not reported; raw `results.jsonl` not supplied.
- Frontend: Node 26 / npm 11 built and ran fine. GloVe was downloaded, prepared in 7.1 s, and indexed 9/9 documents.

## Fixed because of the Windows session
Missing Tesseract was reported as a "corrupt file" and caused 8 cryptic test failures: now auto-detected on Windows, reported as `ocr_unavailable` (with a hint), shown on `/api/health` and as a UI banner, and `pytest` stops with one clear message. The GloVe test no longer uses a Linux-only path. `RESULTS.md` is generated per machine (the shipped sandbox rows had been mixed with the Windows rows).

## Known gaps and limitations
- Evaluation data are synthetic and share an author with the extractor; no real invoices; no LLM extraction.
- Confidence is heuristic (not calibrated); some extraction errors pass silently (mostly OCR misreads).
- The latest code (131 tests) has not been re-run on Windows. Windows raw benchmark data, CPU/cores, the OCR-threads matrix and responsiveness figures are not in hand.
- Semantic search quality is modest and was never measured with a transformer; the Windows check verified mechanics, not quality.
- Single shared API key; no users/roles/TLS/encryption at rest; parsers not sandboxed (`docs/SECURITY.md`).
- Untested: disk-full, heavy concurrent uploads, files above ~110 MB, multi-core speed-up.

## Next actions
**You:** (1) re-run `python -m pytest tests -q` on Windows (expect 131 passed); (2) send `docs/benchmarks/results.jsonl` and your CPU/cores/RAM; (3) rehearse and record the walkthrough; (4) complete the candidate section of `AI_PROMPTS.md`; (5) push `docintel.bundle` to your remote; (6) edit and send the email (Reply All, keep `apply@careguidebd.com`).
