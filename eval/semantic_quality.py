"""Retrieval-quality evaluation: keyword (FTS5, OR + BM25) vs semantic vs random, on a labeled synthetic corpus.

LIMITATIONS (also printed in the report): synthetic invoices; every invoice has exactly 2 line items drawn from a
fixed 11-item catalogue; relevance is defined by construction (a document is relevant to a query iff it contains the
target item); the paraphrase queries were written by the developer who knew the catalogue (small n, optimistic).
Use it to compare configurations, not as a claim about real-world retrieval quality."""
import json, os, random, re, shutil, statistics, sys, tempfile, time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
OUT = ROOT / "docs" / "benchmarks"

PARAPHRASE = {  # query -> target catalogue item (no shared words with the item name by design)
    "expert advice services": "Consulting hour",
    "ink for office printing machine": "Printer toner",
    "computer networking wires": "Cable bundle",
    "protective hand wear": "Safety gloves",
    "data center cabinet": "Server rack",
    "box for freight": "Shipping crate",
    "maintenance contract": "Support plan",
    "stand for notebook computer": "Laptop stand",
    "instrument testing and adjustment": "Calibration service",
}
EXACT = {"printer toner": "Printer toner", "safety gloves": "Safety gloves", "server rack": "Server rack"}  # lexical controls
N_DOCS, SEED = 80, 11


def build_corpus(tmp: Path):
    from synth import invoices as S
    from app.db import connect, init_db, now
    from app.worker import enqueue, run_all
    init_db()
    from app.config import get_settings
    s = get_settings(); rng = random.Random(SEED); truth = {}
    for n in range(1, N_DOCS + 1):
        spec = S.random_spec(rng, n, 2)
        names = rng.sample(S.ITEMS, 2)
        spec.items = [(nm, rng.randint(1, 5), Decimal(rng.randint(500, 9900)) / 100) for nm in names]
        p = S.write_pdf(spec, s.upload_dir / f"d{n}.pdf")
        with connect() as db:
            db.execute("INSERT INTO documents(filename,stored_name,sha256,size_bytes,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                       (f"d{n}.pdf", f"d{n}.pdf", f"h{n}", 1, now(), now()))
            did = db.execute("SELECT max(id) FROM documents").fetchone()[0]; enqueue(db, did)
        truth[did] = set(names)
    run_all()
    return truth


def rank_metrics(ranking: list[int], relevant: set[int], k=10):
    hits = [1 if d in relevant else 0 for d in ranking]
    p_at_k = sum(hits[:k]) / k
    mrr = next((1 / (i + 1) for i, h in enumerate(hits) if h), 0.0)
    ap, seen = 0.0, 0
    for i, h in enumerate(hits):
        if h:
            seen += 1; ap += seen / (i + 1)
    ap = ap / len(relevant) if relevant else 0.0
    return p_at_k, mrr, ap, hits[0] if hits else 0


def keyword_ranking(q: str, all_ids: list[int]) -> list[int]:
    from app.db import connect
    from app.semantic import STOP, TOKEN_RE
    toks = [t for t in TOKEN_RE.findall(q.lower()) if t not in STOP]
    if not toks:
        return list(all_ids)
    expr = " OR ".join(f'"{t}"' for t in toks)
    with connect() as db:
        rows = db.execute("SELECT document_id, MIN(rank) r FROM page_fts WHERE page_fts MATCH ? GROUP BY document_id ORDER BY r", (expr,)).fetchall()
    ranked = [r["document_id"] for r in rows]
    return ranked + [d for d in all_ids if d not in set(ranked)]   # unmatched documents ranked last (arbitrary order)


def semantic_ranking(q: str, all_ids: list[int]) -> list[int]:
    from app import semantic
    hits = semantic.search(q, limit=len(all_ids), candidates=10**6)
    ranked = [h["document_id"] for h in hits]
    return ranked + [d for d in all_ids if d not in set(ranked)]


def hybrid_ranking(q: str, all_ids: list[int], k: int = 60) -> list[int]:
    """Reciprocal-rank fusion of keyword and semantic rankings (documents unmatched by keyword get no keyword vote)."""
    from app.db import connect
    from app.semantic import STOP, TOKEN_RE
    kw = [d for d in keyword_ranking(q, all_ids)]
    toks = [t for t in TOKEN_RE.findall(q.lower()) if t not in STOP]
    with connect() as db:
        matched = {r[0] for r in db.execute("SELECT DISTINCT document_id FROM page_fts WHERE page_fts MATCH ?", (" OR ".join(f'"{t}"' for t in toks),))} if toks else set()
    kw = [d for d in kw if d in matched]
    sem = semantic_ranking(q, all_ids)
    score = {}
    for rank, d in enumerate(kw):
        score[d] = score.get(d, 0) + 1 / (k + rank + 1)
    for rank, d in enumerate(sem):
        score[d] = score.get(d, 0) + 1 / (k + rank + 1)
    return sorted(all_ids, key=lambda d: -score.get(d, 0))


def evaluate(truth, ranker, queries):
    ids = sorted(truth)
    rows = []
    for q, item in queries.items():
        rel = {d for d, items in truth.items() if item in items}
        rows.append(rank_metrics(ranker(q, ids), rel))
    return {"P@1": round(statistics.mean(r[3] for r in rows), 3), "P@10": round(statistics.mean(r[0] for r in rows), 3),
            "MRR": round(statistics.mean(r[1] for r in rows), 3), "MAP": round(statistics.mean(r[2] for r in rows), 3)}


def per_query_ap(truth, ranker, queries):
    ids = sorted(truth)
    return {q: round(rank_metrics(ranker(q, ids), {d for d, it in truth.items() if item in it})[2], 3) for q, item in queries.items()}


def random_baseline(truth, queries, trials=300):
    rng = random.Random(0); ids = sorted(truth); acc = []
    for _ in range(trials):
        acc.append(evaluate(truth, lambda q, ids_: rng.sample(ids_, len(ids_)), queries))
    return {m: round(statistics.mean(a[m] for a in acc), 3) for m in ("P@1", "P@10", "MRR", "MAP")}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="docintel_semq_"))
    os.environ["DOCINTEL_DATA_DIR"] = str(tmp)
    os.environ.setdefault("DOCINTEL_MODELS_DIR", str(ROOT / "eval" / "models"))
    os.environ["DOCINTEL_SEMANTIC"] = "off"      # build the corpus without embedding; embed per configuration below
    os.environ["DOCINTEL_WORKERS"] = "0"
    truth = build_corpus(tmp)
    prevalence = statistics.mean(sum(item in v for v in truth.values()) / len(truth) for item in PARAPHRASE.values())
    result = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "n_docs": len(truth), "n_paraphrase_queries": len(PARAPHRASE),
              "n_exact_queries": len(EXACT), "mean_relevant_fraction": round(prevalence, 3), "systems": {}}
    from app import semantic
    from app.db import connect
    os.environ["DOCINTEL_SEMANTIC"] = os.environ.get("EVAL_BACKEND", "glove")
    for name, qs in (("paraphrase", PARAPHRASE), ("exact", EXACT)):
        result["systems"].setdefault("random", {})[name] = random_baseline(truth, qs)
        result["systems"].setdefault("keyword(OR,bm25)", {})[name] = evaluate(truth, keyword_ranking, qs)
    for chunk in (700, 250, 120):
        os.environ["DOCINTEL_CHUNK_CHARS"] = str(chunk)
        semantic.reset_embedder()
        t0 = time.perf_counter()
        with connect() as db:
            db.execute("DELETE FROM chunks")
        for d in truth:
            semantic.embed_document(d)
        build = time.perf_counter() - t0
        with connect() as db:
            n_chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        label = f"semantic({os.environ['DOCINTEL_SEMANTIC']}, chunk={chunk})"
        for name, qs in (("paraphrase", PARAPHRASE), ("exact", EXACT)):
            result["systems"].setdefault(label, {})[name] = evaluate(truth, semantic_ranking, qs)
        result["systems"].setdefault(f"hybrid-RRF(keyword+semantic, chunk={chunk})", {}).update(
            {name: evaluate(truth, hybrid_ranking, qs) for name, qs in (("paraphrase", PARAPHRASE), ("exact", EXACT))})
        if chunk == 250:
            result["per_query_AP"] = {"keyword": per_query_ap(truth, keyword_ranking, PARAPHRASE), "semantic_chunk250": per_query_ap(truth, semantic_ranking, PARAPHRASE)}
        result["systems"][label]["chunks"] = n_chunks; result["systems"][label]["embed_seconds"] = round(build, 2)
    shutil.rmtree(tmp, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "semantic_quality.jsonl", "a") as f:
        f.write(json.dumps(result) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
