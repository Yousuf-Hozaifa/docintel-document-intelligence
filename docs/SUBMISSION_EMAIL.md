# Submission email: draft (NOT sent)

This is a draft for you to edit. Nothing here has been sent, and no attachment has been added. Fill every `[PLACEHOLDER]`; keep claims you can stand behind.

## How to send
1. Open the **existing Care Guide recruitment email thread** and use **Reply All** (not a new message).
2. Check the recipient list still includes **apply@careguidebd.com**; do not remove or replace anyone.
3. Keep the original subject line (the draft below assumes the usual `Re:`).
4. Check links open for someone not logged in (or that the reviewers have access): repository and video.
5. Prefer links to attaching large files; attach only what the assessment instructions require.

## Draft
**Subject:** Re: [keep the original subject of the thread]

Dear [Hiring Team / names as they appear in the thread],

Please find my submission for the Senior AI Engineer assessment, "AI-Powered Document Intelligence System".

- Source code (Git): [REPOSITORY LINK]
- Recorded walkthrough (about [N] minutes): [VIDEO LINK]
- Start with `README.md`. Architecture and schema: `docs/ARCHITECTURE.md`, `docs/DATABASE.md`, `docs/openapi.json`. Evaluation: `docs/evaluation/EVALUATION.md`. Benchmarks and test results: `docs/benchmarks/`, `docs/test-results/`. AI prompts and disclosure: `AI_PROMPTS.md`.

What is included: pipelines for digital PDFs, scanned PDFs and images (Tesseract OCR), XLSX/XLS and CSV; structured extraction with source evidence; deterministic validation (arithmetic with rounding tolerance, duplicates, PDF-to-PDF and PDF-to-Excel cross-verification); manual correction with an audit trail; keyword search and an optional semantic search; background jobs with retry; and a React interface. It runs on CPU only, with no paid API and no LLM at run time.

Results, with their limits: the automated test suite has 131 tests (I also ran an earlier version on my Windows machine: 123 passed, 1 skipped). On a labeled **synthetic** dataset, header-field exact match was about 99% on two held-out splits; because the data and the extractor share an author, I treat that as an upper bound for real invoices, and the evaluation report states its methodology and weaknesses (including extraction errors that are not flagged). A 1,000-page scanned PDF processed in about [5.4 minutes - confirm from your own results] on my machine with a peak of about [144 MB - confirm].

AI assistance: I used AI assistants to build this project; the prompts and an accurate disclosure are in `AI_PROMPTS.md`.

Thank you for your time and consideration. I would be glad to walk through any part of the solution.

Kind regards,
[YOUR NAME]
[Contact details exactly as in the thread; do not add references or details you did not provide]
