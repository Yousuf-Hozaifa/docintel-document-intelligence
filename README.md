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
python -m pytest tests -q                      # expect: 61 passed
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

## API (so far)
`POST /api/documents` (multipart `files`) · `GET /api/documents` · `GET /api/documents/{id}` ·
`GET /api/search?q=&supplier=&currency=&review=` · `GET /api/rules` · `GET /api/health`.
Interactive docs: http://localhost:8000/docs

## Large-document benchmarks
`python eval/bench.py run --kind scanned --pages 100` measures time and peak memory (process tree, incl. Tesseract).
See `docs/benchmarks/WINDOWS_RUNBOOK.md` and the generated `docs/benchmarks/RESULTS.md`.
Tuning knobs: `DOCINTEL_OCR_THREADS` (pages OCR'd concurrently, default 1), `DOCINTEL_TESS_OMP_THREADS`
(Tesseract threads per process, default 1; 0 = Tesseract default), `DOCINTEL_PDF_STORE_SHRINK` (default 1),
`DOCINTEL_OCR_DPI` (default 200).
