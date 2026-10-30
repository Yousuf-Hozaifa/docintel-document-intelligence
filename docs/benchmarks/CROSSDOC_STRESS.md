# Cross-document verification at scale (single run, not repeated)

Environment: same Linux sandbox as RESULTS.md (1 core, 4 GB). Data: synthetic; in-process pipeline run (no HTTP), `DOCINTEL_WORKERS=0`.
Run after the Day-2 pipeline rewrite (duplicate/cross-checks, issue persistence).

| Step | Wall | Invoices stored | Result | RSS |
|---|---|---|---|---|
| 1. 1,000-page digital PDF, 1 invoice/page | 5.86 s | 1,000 | 0 flagged | 180 MB |
| 2. Byte-identical copy as a second document | 7.12 s | 2,000 | 2,000 `VAL-DUP-001` (both copies flagged), 0 mismatches | 180 MB |
| 3. 5,000-row Excel register (1,000 real + 4,000 unrelated rows) | 7.42 s | 7,000 | 4,000 `VAL-XDOC-002` (verified), 0 mismatches, 4,000 unrelated rows not flagged | 187 MB |

Note: step 1 took 4.01 s in the earlier benchmark (before the rewrite) vs 5.86 s here. The extra work per invoice
(issue persistence, duplicate lookup) costs roughly 45% on digital PDFs. Single runs; run-to-run spread not measured for this suite.
