"""Optional semantic search: chunk page text, embed with a local model, brute-force cosine search.

Backends (DOCINTEL_SEMANTIC): off (default) | glove (averaged GloVe-50 word vectors, numpy only)
                              | sbert (sentence-transformers; optional dependency)
Keyword search never depends on this module. Embedding failures never fail document processing.

Scoring note: cosine scores of averaged word vectors are compressed (most are high); use the RANKING,
not an absolute threshold."""
import gzip
import math
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from .config import get_settings
from .db import add_event, connect

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = set("a an the and or of to in on for with by at from is are was were be as it this that these those".split())
GLOVE_URL = "https://github.com/RaRe-Technologies/gensim-data/releases/download/glove-wiki-gigaword-50/glove-wiki-gigaword-50.gz"
GLOVE_FULL_VOCAB = 400_000


class SemanticDisabled(RuntimeError):
    pass


# ------------------------------------------------------------------ embedders
class GloveEmbedder:
    """Sentence vector = SIF-weighted mean of GloVe word vectors (frequent words weigh less). Out-of-vocabulary
    tokens (rare words, most identifiers) are ignored. Static word vectors: synonyms land close together,
    but there is no sentence context, so this is a lightweight baseline rather than a transformer."""
    dim = 50

    def __init__(self, directory: Path, max_vocab: int):
        vocab_file, vec_file = directory / "vocab.txt", directory / "vectors.npy"
        if not (vocab_file.exists() and vec_file.exists()):
            raise SemanticDisabled(f"GloVe files not prepared in {directory}. Run: python -m app.semantic download-glove")
        words = vocab_file.read_text(encoding="utf-8").split("\n")[:max_vocab]
        self.index = {w: i for i, w in enumerate(words)}
        self.vectors = np.load(vec_file, mmap_mode="r")[: len(words)]
        h = math.log(GLOVE_FULL_VOCAB) + 0.577
        ranks = np.arange(len(words), dtype=np.float64)
        p = 1.0 / ((ranks + 1) * h)
        self.weights = (1e-3 / (1e-3 + p)).astype(np.float32)
        self.name = f"glove-50d-v{len(words)}"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for r, t in enumerate(texts):
            ids = [self.index[w] for w in TOKEN_RE.findall(t.lower()) if w not in STOP and w in self.index]
            if ids:
                v = (np.asarray(self.vectors[ids]) * self.weights[ids, None]).sum(axis=0)
                n = np.linalg.norm(v)
                if n > 0:
                    out[r] = v / n
        return out


class SbertEmbedder:
    """sentence-transformers adapter (e.g. all-MiniLM-L6-v2). NOT exercised in the development sandbox
    (model hosts were unreachable): benchmark it with eval/bench_semantic.py before relying on it."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise SemanticDisabled("pip install sentence-transformers to use DOCINTEL_SEMANTIC=sbert") from e
        self.model = SentenceTransformer(model_name, device="cpu")
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.name = f"sbert:{model_name}"
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            return np.asarray(self.model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)


_embedder = None
_embedder_lock = threading.Lock()


def enabled() -> bool:
    return get_settings().semantic_backend not in ("off", "", "none")


def get_embedder():
    """Lazily load the configured embedder (the model costs no memory until first use). None when disabled."""
    global _embedder
    s = get_settings()
    if not enabled():
        return None
    with _embedder_lock:
        if _embedder is None:
            if s.semantic_backend == "glove":
                _embedder = GloveEmbedder(s.models_dir / "glove50", s.glove_vocab)
            elif s.semantic_backend == "sbert":
                _embedder = SbertEmbedder(s.sbert_model)
            else:
                raise SemanticDisabled(f"Unknown DOCINTEL_SEMANTIC backend '{s.semantic_backend}'")
        return _embedder


def reset_embedder() -> None:
    global _embedder, _index
    with _embedder_lock:
        _embedder = None
    _index = None


# ------------------------------------------------------------------ chunking
def chunk_text(text: str, max_chars: int) -> list[str]:
    """Group consecutive lines into chunks of about max_chars (a single longer line becomes its own chunk)."""
    chunks, cur, n = [], [], 0
    for line in (re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()):
        if not line:
            continue
        if cur and n + len(line) > max_chars:
            chunks.append(" ".join(cur)); cur, n = [], 0
        cur.append(line); n += len(line) + 1
    if cur:
        chunks.append(" ".join(cur))
    return chunks


# ------------------------------------------------------------------ storage
PAGE_BATCH = 50


def embed_document(doc_id: int) -> int:
    """(Re)build chunk embeddings for one document from its stored page text. Returns chunks written."""
    emb = get_embedder()
    if emb is None:
        return 0
    s = get_settings()
    with connect() as db:
        db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    last, written = 0, 0
    while True:
        with connect() as db:
            pages = db.execute("SELECT page_no, text FROM pages WHERE document_id=? AND page_no>? AND text!='' "
                               "ORDER BY page_no LIMIT ?", (doc_id, last, PAGE_BATCH)).fetchall()
        if not pages:
            break
        last = pages[-1]["page_no"]
        rows = [(p["page_no"], i, c) for p in pages for i, c in enumerate(chunk_text(p["text"], s.chunk_chars))]
        if not rows:
            continue
        vecs = emb.embed([r[2] for r in rows])            # CPU work happens outside any DB transaction
        with connect() as db:
            db.executemany("INSERT INTO chunks(document_id,page_no,chunk_idx,text,model,dim,embedding) VALUES (?,?,?,?,?,?,?)",
                           [(doc_id, pn, ci, txt, emb.name, emb.dim, v.astype(np.float32).tobytes()) for (pn, ci, txt), v in zip(rows, vecs)])
        written += len(rows)
    return written


def embed_after_processing(doc_id: int) -> None:
    """Pipeline hook: best-effort. A semantic-index failure is recorded but never fails the document."""
    if not enabled():
        return
    t0 = time.perf_counter()
    try:
        n = embed_document(doc_id)
        detail, event = f"chunks={n} seconds={time.perf_counter() - t0:.2f}", "embedded"
    except Exception as e:  # noqa: BLE001
        detail, event = f"{type(e).__name__}: {str(e)[:200]}", "embedding_failed"
    with connect() as db:
        add_event(db, doc_id, event, detail)


# ------------------------------------------------------------------ in-memory index (brute force)
class _Index:
    def __init__(self, key, matrix, meta):
        self.key, self.matrix, self.meta = key, matrix, meta  # meta: list of (chunk_id, document_id, page_no)


_index: _Index | None = None
_index_lock = threading.Lock()


_full_builds = 0   # counts complete rebuilds (observable in tests / benchmarks)


def _read_chunks(db, model: str, dim: int, after_id: int):
    rows = db.execute("SELECT id, document_id, page_no, embedding FROM chunks WHERE model=? AND id>? ORDER BY id", (model, after_id)).fetchall()
    matrix = np.empty((len(rows), dim), dtype=np.float32)
    for i, r in enumerate(rows):
        matrix[i] = np.frombuffer(r["embedding"], dtype=np.float32)
    return matrix, [(r["id"], r["document_id"], r["page_no"]) for r in rows]


def get_index(model: str, dim: int) -> _Index:
    """Matrix of all chunk vectors for the active model. Rebuilt from SQLite only when needed: if chunks were
    purely appended since the last build (ids only grow) just the new rows are read; deletions force a full rebuild."""
    global _index, _full_builds
    with connect() as db:
        count, max_id = db.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM chunks WHERE model=?", (model,)).fetchone()
        key = (model, count, max_id)
        with _index_lock:
            if _index is not None and _index.key == key:
                return _index
            old = _index
            if old is not None and old.key[0] == model and old.key[2] <= max_id and len(old.meta) > 0:
                new_rows = db.execute("SELECT COUNT(*) FROM chunks WHERE model=? AND id>?", (model, old.key[2])).fetchone()[0]
                if old.key[1] + new_rows == count:          # nothing was deleted: append only
                    add, meta = _read_chunks(db, model, dim, old.key[2])
                    _index = _Index(key, np.vstack([old.matrix, add]) if len(meta) else old.matrix, old.meta + meta)
                    return _index
            matrix, meta = _read_chunks(db, model, dim, 0)
            _full_builds += 1
            _index = _Index(key, matrix, meta)
            return _index


def search(query: str, limit: int = 10, candidates: int = 300) -> list[dict]:
    """Rank documents by their best-matching chunk. Returns [{document_id, page_no, chunk_id, score, text}]."""
    emb = get_embedder()
    if emb is None:
        raise SemanticDisabled("Semantic search is disabled (set DOCINTEL_SEMANTIC=glove)")
    q = emb.embed([query])[0]
    if not np.any(q):
        return []
    idx = get_index(emb.name, emb.dim)
    if not len(idx.meta):
        return []
    scores = idx.matrix @ q
    k = min(candidates, len(scores))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    best: dict[int, tuple[float, int]] = {}
    for i in top:
        _, doc_id, _ = idx.meta[i]
        if doc_id not in best:
            best[doc_id] = (float(scores[i]), int(i))
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:limit]
    ids = [idx.meta[i][0] for _, (_, i) in ranked]
    with connect() as db:
        texts = {r["id"]: r["text"] for r in db.execute(f"SELECT id, text FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    return [{"document_id": d, "page_no": idx.meta[i][2], "chunk_id": idx.meta[i][0], "score": round(sc, 4), "text": texts.get(idx.meta[i][0], "")}
            for d, (sc, i) in ranked]


def keyword_docs(query: str, limit: int = 100) -> list[tuple[int, int, str]]:
    """Documents matching ANY query word (FTS5, BM25-ranked): [(document_id, page_no, highlighted snippet)]."""
    toks = [t for t in TOKEN_RE.findall(query.lower()) if t not in STOP]
    if not toks:
        return []
    with connect() as db:
        rows = db.execute("SELECT document_id, page_no, snippet(page_fts,0,'<mark>','</mark>',' … ',14) AS sn FROM page_fts "
                          "WHERE page_fts MATCH ? ORDER BY rank LIMIT ?", (" OR ".join(f'"{t}"' for t in toks), limit * 3)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["document_id"] not in seen:
            seen.add(r["document_id"]); out.append((r["document_id"], r["page_no"], r["sn"]))
    return out[:limit]


def hybrid_search(query: str, limit: int = 10, k: int = 60) -> list[dict]:
    """Reciprocal-rank fusion of semantic and keyword rankings. Falls back to keyword-only for documents
    without embeddings and for queries the embedding model cannot represent (e.g. identifiers)."""
    sem = search(query, limit=100)
    kw = keyword_docs(query, 100)
    score: dict[int, float] = {}
    info: dict[int, dict] = {}
    for rank, h in enumerate(sem):
        score[h["document_id"]] = score.get(h["document_id"], 0) + 1 / (k + rank + 1)
        info[h["document_id"]] = {**h, "sources": ["semantic"]}
    for rank, (d, pn, sn) in enumerate(kw):
        score[d] = score.get(d, 0) + 1 / (k + rank + 1)
        if d in info:
            info[d]["sources"].append("keyword")
        else:
            info[d] = {"document_id": d, "page_no": pn, "chunk_id": None, "text": sn, "sources": ["keyword"]}
    ranked = sorted(score, key=lambda d: -score[d])[:limit]
    return [{**info[d], "score": round(score[d], 5)} for d in ranked]


# ------------------------------------------------------------------ status / reindex
_reindex_state = {"running": False, "done": 0, "total": 0, "error": None}


def status() -> dict:
    s = get_settings()
    out = {"backend": s.semantic_backend, "enabled": enabled(), "reindex": dict(_reindex_state)}
    if enabled():
        with connect() as db:
            out["documents_total"] = db.execute("SELECT COUNT(*) FROM documents WHERE status IN ('completed','partial')").fetchone()[0]
            out["chunks"] = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            out["documents_embedded"] = db.execute("SELECT COUNT(DISTINCT document_id) FROM chunks").fetchone()[0]
        out["model_loaded"] = _embedder is not None
        out["model"] = _embedder.name if _embedder else None
    return out


def start_reindex() -> bool:
    """Embed every finished document that has no chunks yet, in a background thread (one at a time)."""
    if not enabled():
        raise SemanticDisabled("Semantic search is disabled (set DOCINTEL_SEMANTIC=glove)")
    get_embedder()  # fail fast (e.g. missing model files) before starting the thread
    if _reindex_state["running"]:
        return False
    with connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM documents WHERE status IN ('completed','partial') AND id NOT IN "
                                        "(SELECT DISTINCT document_id FROM chunks) ORDER BY id")]
    _reindex_state.update(running=True, done=0, total=len(ids), error=None)

    def run():
        try:
            for d in ids:
                embed_document(d)
                _reindex_state["done"] += 1
        except Exception as e:  # noqa: BLE001
            _reindex_state["error"] = f"{type(e).__name__}: {e}"
        finally:
            _reindex_state["running"] = False

    threading.Thread(target=run, name="semantic-reindex", daemon=True).start()
    return True


# ------------------------------------------------------------------ GloVe preparation (CLI)
def prepare_glove(gz_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    words, rows = [], []
    t0 = time.perf_counter()
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        first = f.readline().split()
        dim = int(first[1]) if len(first) == 2 else 50
        if len(first) != 2:
            f.seek(0)
        for line in f:
            parts = line.rstrip("\n").split(" ")
            if len(parts) != dim + 1 or "\n" in parts[0]:
                continue
            words.append(parts[0]); rows.append(parts[1:])
    mat = np.array(rows, dtype=np.float32)
    np.save(out_dir / "vectors.npy", mat)
    (out_dir / "vocab.txt").write_text("\n".join(words), encoding="utf-8")
    print(f"prepared {len(words)} words x {dim} dims in {time.perf_counter() - t0:.1f}s -> {out_dir}")


def download_glove(out_dir: Path) -> None:
    gz = out_dir.parent / "glove-wiki-gigaword-50.gz"
    gz.parent.mkdir(parents=True, exist_ok=True)
    if not gz.exists():
        print(f"downloading {GLOVE_URL} (~66 MB)")
        urllib.request.urlretrieve(GLOVE_URL, gz)
    prepare_glove(gz, out_dir)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    s = get_settings()
    if cmd == "download-glove":
        download_glove(s.models_dir / "glove50")
    elif cmd == "prepare-glove":
        prepare_glove(Path(sys.argv[2]), s.models_dir / "glove50")
    elif cmd == "status":
        from .db import init_db
        init_db(); print(status())
    else:
        print("usage: python -m app.semantic [status|download-glove|prepare-glove <gz>]")
