"""Resource benchmarks for semantic search.
  python eval/bench_semantic.py load --backend glove --vocab 100000     # fresh-process load time + RSS
  python eval/bench_semantic.py load --backend sbert                    # same for sentence-transformers (needs the package + model download)
  python eval/bench_semantic.py throughput --backend glove              # embedding speed on realistic chunks
  python eval/bench_semantic.py scale --dim 50 --sizes 1000,10000,100000
Each command appends one JSON line to docs/benchmarks/semantic.jsonl. `scale` uses RANDOM unit vectors: it measures
index/search cost versus corpus size only (not relevance)."""
import argparse, json, os, platform, random, shutil, statistics, subprocess, sys, tempfile, time
from pathlib import Path

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
OUT = ROOT / "docs" / "benchmarks" / "semantic.jsonl"
MB = 1048576


def env():
    cpu = platform.processor()
    try: cpu = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception: pass
    return {"platform": platform.platform(), "cpu": cpu, "cores": psutil.cpu_count(), "ram_mb": psutil.virtual_memory().total // MB, "python": platform.python_version()}


def emit(rec):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec, "env": env()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as f: f.write(json.dumps(rec) + "\n")
    print(json.dumps({k: v for k, v in rec.items() if k != "env"}))


def setup_env(a):
    os.environ.setdefault("DOCINTEL_MODELS_DIR", str(ROOT / "eval" / "models"))
    os.environ["DOCINTEL_SEMANTIC"] = a.backend
    if getattr(a, "vocab", None): os.environ["DOCINTEL_GLOVE_VOCAB"] = str(a.vocab)


def sample_chunks(n):
    from synth import invoices as S
    from app.semantic import chunk_text
    rng, out, i = random.Random(5), [], 0
    while len(out) < n:
        spec = S.random_spec(rng, i, rng.randint(3, 6)); i += 1
        text = "\n".join([spec.supplier, f"Invoice Number: {spec.number}", f"Bill To: {spec.customer}", "Description Qty Unit Price Amount"]
                         + [f"{d} {q} {u:.2f} {spec.q(q*u):.2f}" for d, q, u in spec.items] + [f"Subtotal: {spec.subtotal}", f"Total: {spec.total}"] + S.text_page_lines(rng, 12, 12))
        out.extend(chunk_text(text, 250))
    return out[:n]


def cmd_load(a):
    setup_env(a)
    from app import semantic
    chunks = sample_chunks(200)          # generate sample text (imports synth/pymupdf) BEFORE the baseline reading
    proc = psutil.Process(); before = proc.memory_info().rss
    t0 = time.perf_counter(); emb = semantic.get_embedder(); load_s = time.perf_counter() - t0
    emb.embed(chunks); after = proc.memory_info().rss                  # touch typical pages so mmap'd vectors count
    emit({"bench": "load", "backend": a.backend, "model": emb.name, "dim": emb.dim, "load_s": round(load_s, 2),
          "rss_before_mb": round(before / MB), "rss_after_mb": round(after / MB), "rss_delta_mb": round((after - before) / MB)})


def cmd_throughput(a):
    setup_env(a)
    from app import semantic
    emb = semantic.get_embedder(); chunks = sample_chunks(a.n)
    emb.embed(chunks[:50])  # warm-up
    t0 = time.perf_counter(); c0 = time.process_time()
    for i in range(0, len(chunks), 64): emb.embed(chunks[i:i + 64])
    el, cpu = time.perf_counter() - t0, time.process_time() - c0
    emit({"bench": "throughput", "backend": a.backend, "model": emb.name, "chunks": len(chunks), "avg_chunk_chars": round(statistics.mean(map(len, chunks))),
          "chunks_per_s": round(len(chunks) / el, 1), "ms_per_chunk": round(1000 * el / len(chunks), 3), "cpu_s": round(cpu, 2), "wall_s": round(el, 2)})


class _Stub:
    def __init__(self, dim): self.dim, self.name = dim, "bench-random"
    def embed(self, texts):
        v = np.random.default_rng(1).standard_normal((len(texts), self.dim)).astype(np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def cmd_scale(a):
    tmp = Path(tempfile.mkdtemp(prefix="docintel_scale_")); os.environ["DOCINTEL_DATA_DIR"] = str(tmp)
    os.environ["DOCINTEL_SEMANTIC"] = "glove"
    from app import semantic
    from app.db import connect, init_db, now
    init_db(); proc = psutil.Process()
    for n in [int(x) for x in a.sizes.split(",")]:
        with connect() as db:
            db.execute("DELETE FROM chunks"); db.execute("DELETE FROM documents")
            docs = max(1, n // 20)
            db.executemany("INSERT INTO documents(id,filename,stored_name,sha256,size_bytes,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                           [(d, f"d{d}.pdf", f"d{d}", f"h{d}", 1, "completed", now(), now()) for d in range(1, docs + 1)])
        rng = np.random.default_rng(0); t0 = time.perf_counter(); done = 0
        while done < n:
            m = min(5000, n - done)
            v = rng.standard_normal((m, a.dim)).astype(np.float32); v /= np.linalg.norm(v, axis=1, keepdims=True)
            with connect() as db:
                db.executemany("INSERT INTO chunks(document_id,page_no,chunk_idx,text,model,dim,embedding) VALUES (?,?,?,?,?,?,?)",
                               [((done + i) % docs + 1, 1, 0, "synthetic chunk text " * 6, "bench-random", a.dim, v[i].tobytes()) for i in range(m)])
            done += m
        insert_s = time.perf_counter() - t0
        semantic.reset_embedder(); semantic._embedder = _Stub(a.dim); semantic._index = None
        rss0 = proc.memory_info().rss; t0 = time.perf_counter(); semantic.get_index("bench-random", a.dim); build_s = time.perf_counter() - t0
        rss1 = proc.memory_info().rss
        lat = []
        for _ in range(60):
            t = time.perf_counter(); semantic.search("anything at all", limit=10); lat.append((time.perf_counter() - t) * 1000)
        lat = lat[5:]; lat.sort()
        db_mb = (tmp / "docintel.sqlite3").stat().st_size / MB
        emit({"bench": "scale", "dim": a.dim, "chunks": n, "synthetic_random_vectors": True, "insert_s": round(insert_s, 1), "index_build_s": round(build_s, 2),
              "index_rss_mb": round((rss1 - rss0) / MB), "matrix_mb": round(n * a.dim * 4 / MB, 1), "db_file_mb": round(db_mb, 1),
              "search_ms_p50": round(statistics.median(lat), 2), "search_ms_p95": round(lat[int(len(lat) * .95) - 1], 2)})
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["load", "throughput", "scale"])
    ap.add_argument("--backend", default="glove"); ap.add_argument("--vocab", type=int); ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--dim", type=int, default=50); ap.add_argument("--sizes", default="1000,10000,100000")
    a = ap.parse_args(); {"load": cmd_load, "throughput": cmd_throughput, "scale": cmd_scale}[a.cmd](a)
