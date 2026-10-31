"""Processing pipeline: read pages incrementally -> persist -> segment -> extract -> validate -> persist."""
import json
import re
import time

from .config import get_settings
from .db import add_event, connect, now
from .errors import CorruptFile, DocumentError, NoContent
from .extract import FIELDS, extract_invoice, extract_register, segment_pages
from .readers import detect_type, open_pages
from .semantic import embed_after_processing
from .review import cross_issues, norm, refresh_document_review, revalidate_related, write_issues
from .validate import validate_invoice


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def dup_key(supplier, number) -> str | None:
    return f"{norm(supplier)}|{norm(number)}" if number else None


def _clear_previous(db, doc_id: int) -> set[str]:
    """Remove earlier results (reprocessing). Returns number keys of removed invoices so that other
    documents' duplicate / cross-check flags that pointed at them can be refreshed."""
    keys = {r["num_key"] for r in db.execute("SELECT DISTINCT num_key FROM invoices WHERE document_id=?", (doc_id,)) if r["num_key"]}
    db.execute("DELETE FROM page_fts WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM pages WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM invoices WHERE document_id=?", (doc_id,))  # cascades items/issues/corrections
    revalidate_related(db, keys)
    return keys


def process_document(doc_id: int, job_id: int | None = None) -> str:
    """Run the full pipeline for one document. Returns final status. Raises DocumentError
    for permanent failures, any other exception for (possibly) transient ones."""
    s = get_settings()
    with connect() as db:
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if doc is None:
            raise DocumentError(f"Document {doc_id} not found")
    path = s.upload_dir / doc["stored_name"]
    if not path.exists():
        raise CorruptFile("Stored file is missing")

    t0 = time.perf_counter()
    kind = detect_type(path, doc["filename"])
    total, pages = open_pages(path, kind)
    with connect() as db:
        old_keys = _clear_previous(db, doc_id)
        db.execute("UPDATE documents SET status='processing', file_type=?, page_count=?, pages_failed=0,"
                   " error_code=NULL, error_message=NULL, updated_at=? WHERE id=?", (kind, total, now(), doc_id))
        if job_id:
            db.execute("UPDATE jobs SET progress_done=0, progress_total=? WHERE id=?", (total, job_id))
        add_event(db, doc_id, "read_started", f"type={kind} pages={total}")

    seg_input: list[tuple[int, str, float]] = []
    results = []
    failed = 0
    for pg in pages:  # lazy: one page at a time
        with connect() as db:
            db.execute("INSERT INTO pages(document_id,page_no,label,method,ocr_confidence,text,error)"
                       " VALUES (?,?,?,?,?,?,?)",
                       (doc_id, pg.number, pg.label, pg.method, pg.ocr_confidence, pg.text, pg.error))
            if pg.text:
                db.execute("INSERT INTO page_fts(text, document_id, page_no) VALUES (?,?,?)",
                           (pg.text, doc_id, pg.number))
            if pg.method == "failed":
                failed += 1
                add_event(db, doc_id, "page_failed", f"page={pg.number} {pg.error}")
            if job_id:
                db.execute("UPDATE jobs SET progress_done=progress_done+1 WHERE id=?", (job_id,))
        if pg.rows is not None:
            reg = extract_register(pg.rows)
            pg.rows = None  # free the cell grid
            if reg is not None:
                for r in reg:
                    r["page_start"] = r["page_end"] = pg.number
                results.extend(reg)
                continue
        if pg.text.strip():
            q = (pg.ocr_confidence / 100.0) if pg.ocr_confidence is not None else 1.0
            seg_input.append((pg.number, pg.text, max(min(q, 1.0), 0.05)))

    if failed == total:
        raise CorruptFile("Every page failed to process")
    if not seg_input and not results:
        raise NoContent("No text could be extracted (blank or unreadable document)")

    for seg in segment_pages(seg_input) if seg_input else []:
        ex = extract_invoice(seg)
        ex["page_start"], ex["page_end"] = seg[0][0], seg[-1][0]
        results.append(ex)
    results.sort(key=lambda r: r["page_start"])  # stable: page order, then position within sheet

    new_keys: set[str] = set()
    with connect() as db:
        inv_ids = []
        for seq, ex in enumerate(results, 1):
            F = ex["fields"]
            v = {k: F[k]["value"] for k in FIELDS}
            key = norm(v["invoice_number"]) or None
            if key:
                new_keys.add(key)
            cur = db.execute(
                "INSERT INTO invoices(document_id,seq,page_start,page_end,fields_json,invoice_number,invoice_date,"
                "supplier,customer,subtotal,tax,total,currency,dup_key,num_key,needs_review,extractor) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, seq, ex["page_start"], ex["page_end"], json.dumps(F), v["invoice_number"], v["invoice_date"],
                 v["supplier"], v["customer"], v["subtotal"], v["tax"], v["total"], v["currency"],
                 None, key, 0, ex["extractor"]))
            inv_id = cur.lastrowid
            inv_ids.append(inv_id)
            for i, it in enumerate(ex["line_items"], 1):
                db.execute("INSERT INTO line_items(invoice_id,idx,description,quantity,unit_price,amount,evidence)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (inv_id, i, it["description"], it["quantity"], it["unit_price"], it["amount"], it["evidence"]))
            issues = (validate_invoice(F, ex["line_items"], expect_line_items=not ex["extractor"].endswith("-register"))
                      + cross_issues(db, inv_id, kind, v))
            write_issues(db, inv_id, issues)
        revalidate_related(db, new_keys | old_keys, exclude_document=doc_id)  # other files now see (or lose) this one
        status = "partial" if failed else "completed"
        db.execute("UPDATE documents SET status=?, pages_failed=?, updated_at=? WHERE id=?",
                   (status, failed, now(), doc_id))
        refresh_document_review(db, doc_id)
        add_event(db, doc_id, "completed",
                  f"status={status} invoices={len(results)} seconds={time.perf_counter() - t0:.2f}")
    embed_after_processing(doc_id)   # optional semantic index; failures are logged, never raised
    return status
