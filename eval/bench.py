"""Large-PDF benchmark: speed, peak memory, reliability, responsiveness. Cross-platform (psutil).

  python eval/bench.py run --kind scanned --pages 100 [--ocr-threads 1] [--omp 1] [--dpi 200]
  python eval/bench.py responsive --kind scanned --pages 50
  python eval/bench.py report            # rewrites docs/benchmarks/RESULTS.md from results.jsonl

kinds: digital (one invoice per page, text layer) | scanned (same invoices as 200-dpi JPEG images, OCR)
       | scanned_text (text-heavy ~52-line pages, image only: OCR stress) | digital_text
Every run executes in a fresh subprocess; peak memory = max over time of RSS(process + all children,
i.e. including each tesseract process). RSS double-counts shared libraries, so it slightly overstates."""
import argparse, json, os, platform, random, shutil, statistics, subprocess, sys, tempfile, time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
FIX = ROOT / "eval" / "fixtures"
OUT = ROOT / "docs" / "benchmarks"
MB = 1024 * 1024


def env_info() -> dict:
    cpu = platform.processor() or ""
    try:
        cpu = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception:
        pass
    import pymupdf
    tess = subprocess.run([os.environ.get("TESSERACT_CMD", "tesseract"), "--version"], capture_output=True, text=True)
    return {"platform": platform.platform(), "cpu": cpu, "cores_logical": psutil.cpu_count(),
            "cores_physical": psutil.cpu_count(logical=False), "ram_total_mb": psutil.virtual_memory().total // MB,
            "python": platform.python_version(), "pymupdf": pymupdf.__version__,
            "tesseract": (tess.stdout or tess.stderr).splitlines()[0]}


# ------------------------------------------------------------------ fixtures
def fixture_path(kind: str, pages: int, dpi: int) -> Path:
    suffix = f"_{dpi}dpi" if kind.startswith("scanned") else ""
    return FIX / f"{kind}_{pages}p{suffix}.pdf"


def make_fixture(kind: str, pages: int, dpi: int) -> tuple[Path, float]:
    from synth import invoices as S
    import pymupdf
    path = fixture_path(kind, pages, dpi)
    if path.exists():
        return path, 0.0
    FIX.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if kind.endswith("_text"):
        S.write_text_pdf(pages, path, scanned=kind.startswith("scanned"), dpi=dpi)
    else:
        rng = random.Random(42)
        doc = pymupdf.open()
        import io
        for n in range(1, pages + 1):
            spec = S.random_spec(rng, n, rng.randint(3, 6))
            if kind == "digital":
                S._draw_invoice(doc.new_page(), spec)
            else:
                buf = io.BytesIO(); S.render_image(spec, dpi=dpi).save(buf, format="JPEG", quality=75)
                p = doc.new_page(); p.insert_image(p.rect, stream=buf.getvalue())
        doc.save(path, garbage=1, deflate=True); doc.close()
    return path, time.perf_counter() - t0


# ------------------------------------------------------------------ child (does the real work)
def child(a) -> None:
    from app.db import connect, init_db, now
    from app.pipeline import process_document
    from app.worker import enqueue
    init_db()
    from app.config import get_settings
    s = get_settings()
    shutil.copy(a.file, s.upload_dir / "bench.pdf")
    with connect() as db:
        db.execute("INSERT INTO documents(filename,stored_name,sha256,size_bytes,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                   ("bench.pdf", "bench.pdf", "x", os.path.getsize(a.file), now(), now()))
        jid = enqueue(db, 1)
    t0 = time.perf_counter()
    err = None
    try:
        status = process_document(1, jid)
    except Exception as e:  # report, do not crash silently
        status, err = "exception", f"{type(e).__name__}: {e}"
    wall = time.perf_counter() - t0
    t = os.times()
    with connect() as db:
        methods = {r[0]: r[1] for r in db.execute("SELECT method, COUNT(*) FROM pages WHERE document_id=1 GROUP BY method")}
        inv = db.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
        review = db.execute("SELECT COUNT(*) FROM invoices WHERE needs_review=1").fetchone()[0]
        conf = db.execute("SELECT AVG(ocr_confidence) FROM pages WHERE method='ocr'").fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        embed_ev = db.execute("SELECT event, detail FROM events WHERE event IN ('embedded','embedding_failed') ORDER BY id DESC LIMIT 1").fetchone()
    peak = None
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024) / MB
    except ImportError:
        peak = getattr(psutil.Process().memory_info(), "peak_wset", 0) / MB
    print("RESULT " + json.dumps({"status": status, "error": err, "wall_s": wall, "cpu_s": t.user + t.system + t.children_user + t.children_system,
                                   "page_methods": methods, "invoices": inv, "invoices_flagged": review,
                                   "mean_ocr_conf": conf, "python_peak_rss_mb": peak,
                                   "chunks": chunks, "embed_event": dict(embed_ev) if embed_ev else None}))


def tree_rss(proc: psutil.Process) -> tuple[int, int, int]:
    """(total RSS bytes of process+children, python-process RSS, number of live tesseract processes)."""
    try:
        kids = proc.children(recursive=True)
        total = proc.memory_info().rss
        own = total
        tess = 0
        for k in kids:
            try:
                total += k.memory_info().rss
                tess += "tesseract" in k.name().lower()
            except psutil.Error:
                pass
        return total, own, tess
    except psutil.Error:
        return 0, 0, 0


def breakdown(proc: psutil.Process) -> list:
    out = []
    try:
        for q in [proc] + proc.children(recursive=True):
            try:
                out.append((q.name(), round(q.memory_info().rss / MB)))
            except psutil.Error:
                pass
    except psutil.Error:
        pass
    return out


def kill_tree(proc: psutil.Process) -> None:
    for k in proc.children(recursive=True):
        try: k.kill()
        except psutil.Error: pass
    try: proc.kill()
    except psutil.Error: pass


def pages_done(db_path: Path) -> int | None:
    import sqlite3
    try:
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        r = c.execute("SELECT progress_done FROM jobs WHERE id=1").fetchone()
        c.close()
        return r[0] if r else 0
    except Exception:
        return None


def run(a) -> dict:
    path, gen_s = make_fixture(a.kind, a.pages, a.dpi)
    data = Path(tempfile.mkdtemp(prefix="docintel_bench_"))
    env = dict(os.environ, DOCINTEL_DATA_DIR=str(data), DOCINTEL_OCR_THREADS=str(a.ocr_threads), DOCINTEL_OCR_DPI=str(a.dpi),
               DOCINTEL_OCR_TIMEOUT_S="120", PYTHONPATH=str(ROOT / "backend"))
    from app.config import get_settings
    omp = a.omp if a.omp is not None else get_settings().tess_omp_threads   # 0/None = tesseract default
    env["DOCINTEL_TESS_OMP_THREADS"] = str(omp or 0)
    cmd = [sys.executable, str(Path(__file__)), "_child", "--file", str(path)]
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ps = psutil.Process(p.pid)
    series, peak, peak_own, peak_tess, outcome = [], 0, 0, 0, "completed"
    peak_info = None
    next_log = 0.0
    avail_min = psutil.virtual_memory().available
    while p.poll() is None:
        total, own, tess = tree_rss(ps)
        el = time.perf_counter() - t0
        if total > peak:
            peak_info = {"at_s": round(el, 1), "pages_done": pages_done(data / "docintel.sqlite3"), "processes_mb": breakdown(ps)}
        peak, peak_own, peak_tess = max(peak, total), max(peak_own, own), max(peak_tess, tess)
        avail_min = min(avail_min, psutil.virtual_memory().available)
        if el >= next_log:
            series.append((round(el, 1), round(total / MB, 1), pages_done(data / "docintel.sqlite3")))
            next_log = el + max(1.0, a.pages / 300)
        if total > a.mem_limit_mb * MB:
            kill_tree(ps); outcome = "aborted_memory_guard"; break
        if el > a.timeout:
            kill_tree(ps); outcome = "aborted_timeout"; break
        time.sleep(0.1)
    out, err = p.communicate()
    wall = time.perf_counter() - t0
    res = {}
    for line in out.splitlines():
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
    if outcome == "completed" and not res:
        outcome = "crashed"
    # memory growth: slope of RSS vs pages processed over the second half of the run (MB per 100 pages)
    pts = [(d, m) for _, m, d in series if d]
    half = pts[len(pts) // 2:]
    slope = None
    if len(half) >= 5 and half[-1][0] > half[0][0]:
        import numpy as np  # optional; falls back below
        slope = float(np.polyfit([x for x, _ in half], [y for _, y in half], 1)[0] * 100)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": a.tag, "kind": a.kind, "pages": a.pages, "dpi": a.dpi,
           "file_mb": round(path.stat().st_size / MB, 1), "ocr_threads": a.ocr_threads, "tess_omp_threads": omp, "pdf_store_shrink": os.environ.get("DOCINTEL_PDF_STORE_SHRINK", "1"), "semantic": os.environ.get("DOCINTEL_SEMANTIC", "off"),
           "chunks": res.get("chunks"), "embed_event": res.get("embed_event"),
           "outcome": outcome, "doc_status": res.get("status"), "error": res.get("error") or (err.strip().splitlines()[-1] if err.strip() and outcome != "completed" else None),
           "wall_s": round(res.get("wall_s", wall), 2), "pages_per_min": round(a.pages / res["wall_s"] * 60, 1) if res.get("wall_s") else None,
           "cpu_s": round(res.get("cpu_s", 0), 1), "peak_tree_rss_mb": round(peak / MB, 0), "peak_python_rss_mb": round(peak_own / MB, 0),
           "max_tesseract_procs": peak_tess, "peak_info": peak_info, "min_system_available_mb": round(avail_min / MB, 0),
           "mem_slope_mb_per_100_pages": None if slope is None else round(slope, 1),
           "page_methods": res.get("page_methods"), "invoices": res.get("invoices"), "invoices_flagged": res.get("invoices_flagged"),
           "mean_ocr_conf": None if res.get("mean_ocr_conf") is None else round(res["mean_ocr_conf"], 1),
           "fixture_gen_s": round(gen_s, 1), "env": env_info(), "mem_limit_mb": a.mem_limit_mb, "series": series[:: max(1, len(series) // 120)]}
    shutil.rmtree(data, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "results.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    brief = {k: rec[k] for k in ("kind", "pages", "ocr_threads", "tess_omp_threads", "outcome", "doc_status", "wall_s", "pages_per_min",
                                  "cpu_s", "peak_tree_rss_mb", "peak_python_rss_mb", "mean_ocr_conf", "invoices", "semantic", "chunks", "embed_event", "error")}
    print(json.dumps(brief))
    return rec


# ------------------------------------------------------------------ responsiveness
def responsive(a) -> None:
    import httpx
    path, _ = make_fixture(a.kind, a.pages, a.dpi)
    data = Path(tempfile.mkdtemp(prefix="docintel_resp_"))
    port = 8765
    env = dict(os.environ, DOCINTEL_DATA_DIR=str(data), DOCINTEL_OCR_THREADS=str(a.ocr_threads), DOCINTEL_OCR_DPI=str(a.dpi),
               DOCINTEL_WORKERS="1", PYTHONPATH=str(ROOT / "backend"))
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                           cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/api/health", timeout=1).status_code == 200: break
            except Exception: time.sleep(0.5)
        with httpx.Client(timeout=30) as c:
            idle = []
            for _ in range(20):
                t = time.perf_counter(); c.get(base + "/api/health"); idle.append(time.perf_counter() - t); time.sleep(0.05)
            t0 = time.perf_counter()
            with open(path, "rb") as f:
                up = c.post(base + "/api/documents", files=[("files", (path.name, f, "application/pdf"))], timeout=300)
            upload_s = time.perf_counter() - t0
            lat = {"/api/health": [], "/api/documents": [], "/api/search": []}; errors = 0; status = "queued"
            tstart = time.perf_counter()
            small = None
            while status in ("queued", "processing") and time.perf_counter() - tstart < a.timeout:
                for ep, params in (("/api/health", None), ("/api/documents", {"limit": 20}), ("/api/search", {"q": "invoice"})):
                    t = time.perf_counter()
                    try:
                        r = c.get(base + ep, params=params); ok = r.status_code == 200
                    except Exception:
                        ok = False
                    lat[ep].append(time.perf_counter() - t); errors += (not ok)
                d = c.get(base + "/api/documents/1").json()
                status = d["status"]
                time.sleep(0.25)
            total = time.perf_counter() - tstart
        def pct(x, q): return round(sorted(x)[min(len(x) - 1, int(len(x) * q))] * 1000, 1) if x else None
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "responsiveness", "src": f"{a.kind}_{a.pages}p", "ocr_threads": a.ocr_threads,
               "final_status": status, "processing_s": round(total, 1), "upload_request_s": round(upload_s, 2), "request_errors": errors,
               "idle_health_ms_p50": pct(idle, .5), "during_ms": {ep: {"n": len(v), "p50": pct(v, .5), "p95": pct(v, .95), "max": pct(v, 1.0)} for ep, v in lat.items()},
               "env": env_info()}
        OUT.mkdir(parents=True, exist_ok=True)
        with open(OUT / "results.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
        print(json.dumps({k: rec[k] for k in rec if k != "env"}))
    finally:
        srv.terminate(); shutil.rmtree(data, ignore_errors=True)


def report() -> None:
    rows = [json.loads(l) for l in open(OUT / "results.jsonl")]
    runs = [r for r in rows if r["kind"] != "responsiveness"]
    resp = [r for r in rows if r["kind"] == "responsiveness"]
    L = ["# Benchmark results (generated by `eval/bench.py report` from `results.jsonl`; do not hand-edit)\n"]
    envs = {json.dumps(r["env"], sort_keys=True) for r in rows}
    for e in envs:
        e = json.loads(e)
        L.append(f"- **Environment:** {e['platform']}; {e['cpu']}; {e['cores_logical']} logical / {e['cores_physical']} physical cores; "
                 f"{e['ram_total_mb']} MB RAM; Python {e['python']}; PyMuPDF {e['pymupdf']}; {e['tesseract']}")
    L += ["", "| kind | pages | dpi | file MB | ocr threads | omp | store shrink | outcome | wall s | pages/min | CPU s | peak RSS MB (tree) | python RSS MB | OCR conf | invoices |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        L.append(f"| {r['kind']} | {r['pages']} | {r['dpi'] if r['kind'].startswith('scanned') else '-'} | {r['file_mb']} | {r['ocr_threads']} | {r['tess_omp_threads'] or 'default'} | {r.get("pdf_store_shrink", "0")} | "
                 f"{r['outcome']}/{r['doc_status']} | {r['wall_s']} | {r['pages_per_min']} | {r['cpu_s']} | {int(r['peak_tree_rss_mb'])} | {int(r['peak_python_rss_mb'])} | "
                 f"{r['mean_ocr_conf']} | {r['invoices']} |")
    if resp:
        L += ["", "## Responsiveness during processing (API latency in ms)", "", "| source | ocr threads | final status | processing s | errors | idle health p50 | health p50/p95/max | documents p50/p95/max | search p50/p95/max |", "|---|---|---|---|---|---|---|---|---|"]
        for r in resp:
            d = r["during_ms"]; f = lambda k: f"{d[k]['p50']}/{d[k]['p95']}/{d[k]['max']}"
            L.append(f"| {r['src']} | {r['ocr_threads']} | {r['final_status']} | {r['processing_s']} | {r['request_errors']} | {r['idle_health_ms_p50']} | {f('/api/health')} | {f('/api/documents')} | {f('/api/search')} |")
    (OUT / "RESULTS.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gen", "run", "responsive", "report", "_child"])
    ap.add_argument("--kind", default="scanned", choices=["digital", "scanned", "scanned_text", "digital_text"])
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--ocr-threads", type=int, default=1)
    ap.add_argument("--omp", type=int, default=None, help="OMP_THREAD_LIMIT per tesseract process")
    ap.add_argument("--mem-limit-mb", type=int, default=2800, help="abort the run if process-tree RSS exceeds this")
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--tag", default="")
    ap.add_argument("--file")
    a = ap.parse_args()
    {"gen": lambda: print(make_fixture(a.kind, a.pages, a.dpi)), "run": lambda: run(a), "responsive": lambda: responsive(a),
     "report": report, "_child": lambda: child(a)}[a.cmd]()
