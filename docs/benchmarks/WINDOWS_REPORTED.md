# Windows benchmark results (REPORTED by the candidate)

**Provenance - read first.** These figures are transcribed from the candidate's session log (docs/ai-logs/windows-verification-session.md), where a second AI assistant
read the candidate's generated `RESULTS.md` and tabulated the latest seven runs. The raw `results.jsonl` / `RESULTS.md` were **not** supplied to the assistant that wrote this file, so they
could not be re-checked. As a plausibility check, pages / wall time x 60 reproduces every reported throughput to within 0.2%. The runs used the repository's own scripts
(`eval/bench.py` etc. were verified byte-identical to the shipped versions). Replace this file with the regenerated `RESULTS.md` section when the raw data is available.

Machine: Windows 11 (as stated in the log). **CPU model, core count and RAM were not reported.** Tesseract 5.4.0.20240606. Settings: the runbook defaults (1 OCR thread, Tesseract capped to 1 thread, MuPDF cache flush on, 200 dpi) are assumed; the log shows the commands the candidate was *given* (which include `--mem-limit-mb 5000` for the 10/50/100-page scanned runs) but not the exact commands typed, so the settings actually used are not confirmed.

| kind | pages | wall time | pages/min | peak memory (process tree) |
|---|---|---|---|---|
| digital | 100 | 0.95 s | 6,313 | 76 MB |
| digital | 1,000 | 9.15 s | 6,556 | 87 MB |
| scanned | 10 | 3.35 s | 179.4 | 134 MB |
| scanned | 50 | 17.12 s | 175.2 | 134 MB |
| scanned | 100 | 34.32 s | 174.8 | 135 MB |
| scanned | 250 | 81.42 s | 184.2 | 137 MB |
| scanned | 500 | 161.70 s | 185.5 | 140 MB |
| scanned | 1,000 | 321.86 s (5 min 22 s) | 186.4 | 144 MB |

Mean OCR confidence for the 1,000-page scanned run: 94.9% (from the second assistant's summary card). All eight runs completed.

## What the numbers say (and do not say)
- The 1,000-page scanned PDF completed on the candidate's machine in about 5.4 minutes with a 144 MB process-tree peak: no out-of-memory and no failure was reported.
- Throughput is flat (175-186 pages/min) from 10 to 1,000 pages, and memory grew by about 10 MB over a 100x increase in pages: consistent with the bounded, page-streaming design.
- Compared with the development sandbox (1 core): scanned 1,000 pages was 1.33x faster here (321.9 s vs 429.6 s); the digital 1,000-page run was about 2.5x *slower* (9.15 s vs ~3.7 s). I have not investigated why (candidates: SQLite commit cost on Windows, antivirus scanning, disk); it is unmeasured.
- Memory is not directly comparable with the sandbox (different OS accounting and Python build).
- API responsiveness: the log states that the candidate's benchmark output included a responsiveness table with completed processing and **zero request errors**, but the figures were not reproduced in the log, so none are quoted here.
- **Not reported / unknown:** CPU and core count; the concurrency matrix (OCR threads x Tesseract threads) on this machine; text-heavy page speed; whether Windows Defender was active.
