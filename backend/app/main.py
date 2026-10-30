"""FastAPI application: upload, status, results, search."""
import hashlib
import json
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import worker
from .config import get_settings
from .db import add_event, connect, init_db, now
from .errors import DocumentError
from .readers import ALLOWED_EXT, detect_type
from .validate import RULES


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Care Guide Document Intelligence API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _safe_name(name: str) -> str:
    base = re.sub(r"[^\w.\- ]", "_", (name or "upload").replace("\\", "/").split("/")[-1])
    return base[:150] or "upload"


def _doc_row(r) -> dict:
    d = dict(r)
    d.pop("stored_name", None)
    d["review_required"] = bool(d["review_required"])
    return d


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/rules")
def rules():
    return RULES


@app.post("/api/documents", status_code=202)
async def upload(files: list[UploadFile] = File(...)):
    """Accept many files. Each is validated independently; one bad file never blocks the rest."""
    s = get_settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in files:
        name = _safe_name(f.filename)
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXT:
            results.append({"filename": name, "status": "rejected", "error": f"Unsupported extension '{ext}'"})
            continue
        stored = f"{uuid.uuid4().hex}{ext}"
        path = s.upload_dir / stored
        h, size = hashlib.sha256(), 0
        too_big = False
        with open(path, "wb") as out:  # stream to disk in chunks; never hold the file in memory
            while chunk := await f.read(1024 * 1024):
                size += len(chunk)
                if size > s.max_upload_bytes:
                    too_big = True
                    break
                h.update(chunk)
                out.write(chunk)
        if too_big or size == 0:
            path.unlink(missing_ok=True)
            results.append({"filename": name, "status": "rejected",
                            "error": "File exceeds the upload size limit" if too_big else "File is empty"})
            continue
        try:
            detect_type(path, name)
        except DocumentError as e:
            path.unlink(missing_ok=True)
            results.append({"filename": name, "status": "rejected", "error": str(e)})
            continue
        sha = h.hexdigest()
        with connect() as db:
            existing = db.execute("SELECT id, status FROM documents WHERE sha256=? AND status!='failed' "
                                  "ORDER BY id LIMIT 1", (sha,)).fetchone()
            if existing:  # duplicate-processing protection: identical bytes are not processed twice
                path.unlink(missing_ok=True)
                results.append({"filename": name, "status": "duplicate", "document_id": existing["id"],
                                "duplicate_of": existing["id"]})
                continue
            ts = now()
            cur = db.execute("INSERT INTO documents(filename,stored_name,sha256,size_bytes,status,created_at,updated_at)"
                             " VALUES (?,?,?,?,?,?,?)", (name, stored, sha, size, "queued", ts, ts))
            doc_id = cur.lastrowid
            job_id = worker.enqueue(db, doc_id)
            add_event(db, doc_id, "uploaded", f"size={size} sha256={sha[:12]}")
        results.append({"filename": name, "status": "queued", "document_id": doc_id, "job_id": job_id})
    return {"results": results}


@app.get("/api/documents")
def list_documents(status: str | None = None, review: bool | None = None,
                   limit: int = Query(50, le=200), offset: int = 0):
    q, args = "SELECT * FROM documents WHERE 1=1", []
    if status:
        q += " AND status=?"; args.append(status)
    if review is not None:
        q += " AND review_required=?"; args.append(int(review))
    q += " ORDER BY id DESC LIMIT ? OFFSET ?"
    with connect() as db:
        rows = db.execute(q, (*args, limit, offset)).fetchall()
        out = []
        for r in rows:
            d = _doc_row(r)
            j = db.execute("SELECT id, status, attempts, progress_done, progress_total, error FROM jobs "
                           "WHERE document_id=? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
            d["job"] = dict(j) if j else None
            d["invoice_count"] = db.execute("SELECT COUNT(*) FROM invoices WHERE document_id=?", (r["id"],)).fetchone()[0]
            out.append(d)
    return {"documents": out}


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    with connect() as db:
        r = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Document not found")
        d = _doc_row(r)
        j = db.execute("SELECT * FROM jobs WHERE document_id=? ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()
        d["job"] = dict(j) if j else None
        d["pages"] = [dict(p) for p in db.execute(
            "SELECT page_no,label,method,ocr_confidence,error,substr(text,1,2000) AS text_preview "
            "FROM pages WHERE document_id=? ORDER BY page_no LIMIT 200", (doc_id,))]
        invs = []
        for i in db.execute("SELECT * FROM invoices WHERE document_id=? ORDER BY seq", (doc_id,)).fetchall():
            inv = dict(i)
            inv["fields"] = json.loads(inv.pop("fields_json"))
            inv["needs_review"] = bool(inv["needs_review"])
            inv["line_items"] = [dict(x) for x in db.execute(
                "SELECT idx,description,quantity,unit_price,amount,evidence FROM line_items WHERE invoice_id=? ORDER BY idx", (i["id"],))]
            inv["issues"] = [dict(x) for x in db.execute(
                "SELECT id,rule_id,severity,field,message,resolved FROM validation_issues WHERE invoice_id=? ORDER BY id", (i["id"],))]
            invs.append(inv)
        d["invoices"] = invs
        d["history"] = [dict(e) for e in db.execute(
            "SELECT ts,event,detail FROM events WHERE document_id=? ORDER BY id", (doc_id,))]
    return d


def _fts_query(q: str) -> str | None:
    toks = re.findall(r"\w+", q, flags=re.UNICODE)
    return " ".join(f'"{t}"' + ("*" if i == len(toks) - 1 else "") for i, t in enumerate(toks)) or None


@app.get("/api/search")
def search(q: str | None = None, supplier: str | None = None, customer: str | None = None,
           currency: str | None = None, status: str | None = None, review: bool | None = None,
           date_from: str | None = None, date_to: str | None = None, limit: int = Query(25, le=100)):
    """Keyword (SQLite FTS5) search over extracted text, combined with structured filters."""
    sql = ("SELECT d.id AS document_id, d.filename, d.status, d.review_required, i.id AS invoice_id, i.invoice_number,"
           " i.supplier, i.customer, i.invoice_date, i.total, i.currency FROM documents d "
           "LEFT JOIN invoices i ON i.document_id=d.id WHERE 1=1")
    args: list = []
    fts = _fts_query(q) if q else None
    if q and not fts:
        return {"results": []}
    if fts:
        sql += " AND d.id IN (SELECT document_id FROM page_fts WHERE page_fts MATCH ?)"; args.append(fts)
    for col, val in (("i.supplier", supplier), ("i.customer", customer)):
        if val:
            sql += f" AND {col} LIKE ?"; args.append(f"%{val}%")
    if currency:
        sql += " AND i.currency=?"; args.append(currency.upper())
    if status:
        sql += " AND d.status=?"; args.append(status)
    if review is not None:
        sql += " AND d.review_required=?"; args.append(int(review))
    if date_from:
        sql += " AND i.invoice_date>=?"; args.append(date_from)
    if date_to:
        sql += " AND i.invoice_date<=?"; args.append(date_to)
    sql += " ORDER BY d.id DESC LIMIT ?"; args.append(limit)
    with connect() as db:
        rows = [dict(r) for r in db.execute(sql, args)]
        if fts:
            for r in rows:
                sn = db.execute("SELECT page_no, snippet(page_fts,0,'<mark>','</mark>',' … ',14) AS snippet "
                                "FROM page_fts WHERE page_fts MATCH ? AND document_id=? ORDER BY rank LIMIT 3",
                                (fts, r["document_id"])).fetchall()
                r["snippets"] = [dict(x) for x in sn]
    return {"results": rows}
