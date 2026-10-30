# Running the large-PDF benchmark on your Windows machine

Run from the repository root in PowerShell, with the backend virtual environment active
(`backend\.venv\Scripts\Activate.ps1`) and `pip install -r backend\requirements-dev.txt` done.
If Tesseract is not on PATH: `$env:TESSERACT_CMD = "C:\Program Files\Tesseract-OCR\tesseract.exe"`.

Safety: every run is a separate process and is killed automatically if its total memory
(Python + all Tesseract children) exceeds `--mem-limit-mb`, or after `--timeout` seconds.
Use `--mem-limit-mb 5000` on an 8 GB machine. Close other heavy applications first.
Test PDFs are generated into `eval\fixtures\` (the 1,000-page scanned file is ~110 MB and takes several minutes to generate).

## 1. Ladder (run in this order; stop and report if any run is not "completed")
```powershell
python eval/bench.py run --kind digital --pages 100
python eval/bench.py run --kind digital --pages 1000
python eval/bench.py run --kind scanned --pages 10  --mem-limit-mb 5000
python eval/bench.py run --kind scanned --pages 50  --mem-limit-mb 5000
python eval/bench.py run --kind scanned --pages 100 --mem-limit-mb 5000
```
Only continue to 250 / 500 / 1000 scanned pages if peak memory is flat between the 50- and 100-page runs.

## 2. Concurrency matrix (50 scanned pages; `--omp 0` = Tesseract's own default threading)
```powershell
foreach ($t in 1,2,4) { foreach ($o in 0,1) {
  python eval/bench.py run --kind scanned --pages 50 --ocr-threads $t --omp $o --mem-limit-mb 5000 --tag matrix } }
```

## 3. API responsiveness while processing
```powershell
python eval/bench.py responsive --kind scanned --pages 50
```

## 4. Report
```powershell
python eval/bench.py report      # writes docs\benchmarks\RESULTS.md
```
Send back `docs\benchmarks\results.jsonl`. Notes: on Windows, `cpu_s` only covers the Python process
(child CPU time is not available there); peak memory uses RSS summed over the process tree.
