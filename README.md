# Document Intelligence System (Care Guide assessment)

Upload invoices (PDF, JPG, PNG, TIFF, XLSX, XLS, CSV) → extract text (OCR where needed) → structured fields
→ deterministic validation → searchable, reviewable results. CPU-only, no paid API required.

**Status: Day-1 vertical slice. See `PROGRESS.md` and `docs/COMPLIANCE.md` for what is and is not done.**

## Stack
FastAPI · SQLite (WAL, FTS5) · PyMuPDF · Tesseract · openpyxl/xlrd/csv · React + Vite · DB-backed worker threads.

## Prerequisites (Windows)
1. Python 3.11+ and Node 20+.
2. Tesseract OCR (e.g. the UB Mannheim installer). If `tesseract` is not on PATH:
   `$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"`

## Run (PowerShell)
```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m pytest tests -q                      # expect: 83 passed
python -m uvicorn app.main:app --port 8000     # API + background worker
```
New terminal:
```powershell
cd frontend; npm install; npm run dev          # http://localhost:5173
```
Data (SQLite + uploads) is stored in `backend/data/` (override with `DOCINTEL_DATA_DIR`).

## Configuration (environment variables)
`DOCINTEL_WORKERS` (1) · `DOCINTEL_MAX_UPLOAD_MB` (200) · `DOCINTEL_MAX_ATTEMPTS` (3) · `DOCINTEL_OCR_DPI` (200) ·
`DOCINTEL_TOLERANCE_MINOR_UNITS` (1) · `DOCINTEL_OCR_LANG` (eng) · `TESSERACT_CMD`.

## API
| Method | Path | Purpose |
|---|---|---|
| POST | `/api/documents` | multi-file upload (multipart `files`); per-file result: queued / duplicate / rejected |
| GET | `/api/documents`, `/api/documents/{id}` | list with job progress; full detail (pages, invoices, issues, history) |
| POST | `/api/documents/{id}/reprocess` | re-run extraction (409 if a job is already active); discards results and corrections on that document |
| GET | `/api/jobs?status=failed` | failed-job inspection |
| POST | `/api/jobs/retry-failed` | re-queue every failed document |
| GET / PATCH | `/api/invoices/{id}` | read / correct fields and line items (audited, re-validated) |
| GET | `/api/invoices/{id}/corrections` | audit trail |
| POST | `/api/issues/{id}/resolve` | mark a warning/error reviewed (`{"resolved": false}` re-opens) |
| GET | `/api/search?q=&supplier=&customer=&currency=&status=&review=&date_from=&date_to=` | keyword (FTS5) search + filters |
| GET | `/api/rules` | validation rule catalogue |

Correction example:
```json
PATCH /api/invoices/12
{"fields": {"total": "1,234.50"}, "line_items": [{"idx": 2, "quantity": "7"}, {"description": "Freight", "amount": "40"}], "note": "OCR misread"}
```
Auth: set `DOCINTEL_API_KEY` to require header `X-API-Key` on every endpoint except `/api/health` (unset = open, for local development).
Interactive docs: http://localhost:8000/docs

## Cross-document verification
Invoices match when their normalised invoice numbers are equal and the suppliers do not contradict each other.
Document (PDF/image) vs spreadsheet (XLSX/XLS/CSV): **verification** — values compared (subtotal/tax/total within currency tolerance, currency, date, customer);
match → `VAL-XDOC-002` (info), difference → `VAL-XDOC-001` (error). Document vs document, or spreadsheet vs spreadsheet: **duplicate** (`VAL-DUP-001`, flagged on both files).
A spreadsheet with an invoice-number column and a total column is read as a *register* (one invoice per row); other sheets are read as one invoice each.
Line items are not compared across files.

## Large-document benchmarks
`python eval/bench.py run --kind scanned --pages 100` measures time and peak memory (process tree, incl. Tesseract).
See `docs/benchmarks/WINDOWS_RUNBOOK.md` and the generated `docs/benchmarks/RESULTS.md`.
Tuning knobs: `DOCINTEL_OCR_THREADS` (pages OCR'd concurrently, default 1), `DOCINTEL_TESS_OMP_THREADS`
(Tesseract threads per process, default 1; 0 = Tesseract default), `DOCINTEL_PDF_STORE_SHRINK` (default 1),
`DOCINTEL_OCR_DPI` (default 200).
