# Semantic search: measurements and decision

Status: **optional, off by default** (`DOCINTEL_SEMANTIC=off|glove|sbert`). Keyword search never depends on it.
Environment for every number below: Linux sandbox, 1 core, 4 GB RAM (not the Windows/8 GB target). Raw data: `docs/benchmarks/semantic.jsonl`,
`docs/benchmarks/semantic_quality.jsonl`, `docs/benchmarks/results.jsonl`.

## What was measured, and what could not be
- **Measured:** averaged GloVe-50 word vectors (SIF-weighted mean; numpy only; ~66 MB one-time download from GitHub releases).
- **NOT measured:** any transformer (e.g. all-MiniLM-L6-v2). Hugging Face and the fastembed model host were unreachable from my sandbox.
  An adapter (`DOCINTEL_SEMANTIC=sbert`) exists but has never been run; see "Run on your machine".

## Resource cost (GloVe backend, 100k-word vocabulary unless stated)
| Item | Measurement |
|---|---|
| One-time preparation | 14.7 s; 80 MB `vectors.npy` + 3.4 MB `vocab.txt` on disk |
| Load time / memory (fresh process, incl. a typical embed) | 50k vocab: 0.05 s, +15 MB · 100k: 0.05 s, +22 MB · 400k: 0.14 s, +62 MB |
| Embedding speed | 44,641 chunks/s (avg 195 chars) |
| Pipeline overhead, 1,000-page digital PDF (2,000 chunks) | off: 3.63 s, 3.94 s · on: 3.96 s, 3.77 s (embedding itself 0.25–0.28 s); peak RSS 176 → 219 MB |
| Pipeline overhead, 50-page scanned PDF | off 23.47 s · on 23.25 s; peak 251 MB both (model loads after OCR) |
| Search latency, 50-d, **random vectors** (cost only) | 1k: 1.4 ms · 10k: 2.4 ms · 100k: 10.2 ms · 250k: 25.5 ms (p50) |
| Search latency, 384-d (MiniLM-sized), random vectors | 1k: 2.4 ms · 10k: 2.8 ms · 100k: 20.6 ms (p50) |
| In-memory index / DB size | 50-d: 100k chunks +27 MB RAM, DB 40 MB; 250k: +71 MB, 99 MB · 384-d 100k chunks: +155 MB RAM, DB 200 MB |
| Full index rebuild | 100k chunks: 0.37 s (50-d), 1.0 s (384-d). New documents are appended incrementally (tested); deletions/reprocess force one rebuild |

Scaling is roughly linear in chunk count. *Extrapolation, not measured:* ~1M chunks at 384-d would need ~600 MB for the index and on the order of 100–200 ms
per query; that is the point to move to pgvector/ANN. At 100k chunks brute force is comfortably fast.

## Retrieval quality (small, synthetic, optimistic — see caveats)
80 synthetic invoices with 2 line items each from an 11-item catalogue; relevance defined by construction (about 18% of documents are relevant to a query).
9 paraphrase queries (no shared words with the target item) and 3 exact-phrase controls, written by the developer who knew the catalogue.
Keyword = FTS5 matching ANY query word, BM25-ranked (fairer than the API's strict keyword search for sentences).

| System | Paraphrase P@1 | P@10 | MRR | MAP | Exact-phrase P@1 | P@10 |
|---|---|---|---|---|---|---|
| random | 0.19 | 0.18 | 0.39 | 0.22 | 0.19 | 0.20 |
| keyword (OR, BM25) | 0.11 | 0.23 | 0.33 | 0.29 | 1.00 | 1.00 |
| semantic, chunk 700 chars | 0.22 | 0.39 | 0.51 | 0.38 | 1.00 | 0.87 |
| semantic, chunk 250 chars | 0.44 | 0.40 | 0.65 | 0.41 | 1.00 | 0.80 |
| **hybrid (RRF), chunk 250 — shipped default** | **0.56** | 0.37 | **0.70** | **0.43** | 1.00 | 1.00 |
| semantic, chunk 120 chars | 0.11 | 0.18 | 0.38 | 0.24 | 0.00 | 0.40 |

Reading it: on paraphrased queries the right document comes first about half the time with hybrid (5 of 9), roughly 3x random and far above keyword; keyword stays
perfect on exact phrases, which is why hybrid is the default (semantic alone loses 20% P@10 on exact queries). Per-query, semantic beat keyword on 6 of 9 paraphrases and lost on 3
(for example "stand for notebook computer" matches "Laptop stand" literally). A 3-document browser run (real model) put the right document first for 1 of 3 queries, consistent with
this rate. **Caveats:** n=9 queries; chunk size was chosen on the same set (optimistic); synthetic text; one language; averaged static vectors have no sentence context.

## Decision
Keep semantic search **optional and off by default**; hybrid ranking when enabled; keyword search remains the reliable baseline.
Cost is small (tens of MB, sub-second load, ~25 ms/query up to 250k chunks, no measurable pipeline slowdown), but quality is modest and unproven on real documents,
and no transformer has been measured. Do not make it a required dependency until a real-document retrieval set exists.

## Run on your machine
GloVe (no extra Python packages):
```powershell
$env:DOCINTEL_MODELS_DIR = "$PWD\eval\models"; cd backend; python -m app.semantic download-glove; cd ..
$env:DOCINTEL_SEMANTIC = "glove"       # then start the API; POST /api/semantic/reindex backfills existing documents
python eval/bench_semantic.py load --backend glove --vocab 100000
python eval/bench_semantic.py throughput --backend glove
python eval/semantic_quality.py
```
Transformer (UNVERIFIED adapter; `sentence-transformers` installs PyTorch, which is large — measure before adopting):
```powershell
pip install sentence-transformers
python eval/bench_semantic.py load --backend sbert
python eval/bench_semantic.py throughput --backend sbert
python eval/bench_semantic.py scale --dim 384 --sizes 1000,10000,100000
$env:EVAL_BACKEND = "sbert"; python eval/semantic_quality.py
```

## Known limitations
- The index is built per server process; several API processes would each hold a copy.
- Scores are cosine/RRF values for ranking only; there is no meaningful absolute threshold.
- Out-of-vocabulary words (most identifiers, rare terms) are invisible to the embedding; hybrid falls back to keyword for them.
- English only. Embedding failures are logged (`embedding_failed` event) and never fail the document.
