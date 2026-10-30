"""Processing pipeline: read pages incrementally -> persist -> segment -> extract -> validate -> persist."""
import json
import re
import time

from .config import get_settings
from .db import add_event, connect, now
from .errors import CorruptFile, DocumentError, NoContent
from .extract import FIELDS, extract_invoice, segment_pages
from .readers import detect_type, open_pages
from .validate import needs_review, validate_invoice


def norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def dup_key(supplier, number) -> str | None:
    return f"{norm(supplier)}|{norm(number)}" if number else None


def _clear_previous(db, doc_id: int) -> None:
    db.execute("DELETE FROM page_fts WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM pages WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM invoices WHERE document_id=?", (doc_id,))  # cascades items/issues/corrections


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
        _clear_previous(db, doc_id)
        db.execute("UPDATE documents SET status='processing', file_type=?, page_count=?, pages_failed=0,"
                   " error_code=NULL, error_message=NULL, updated_at=? WHERE id=?", (kind, total, now(), doc_id))
        if job_id:
            db.execute("UPDATE jobs SET progress_done=0, progress_total=? WHERE id=?", (total, job_id))
        add_event(db, doc_id, "read_started", f"type={kind} pages={total}")

    seg_input: list[tuple[int, str, float]] = []
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
        if pg.text.strip():
            q = (pg.ocr_confidence / 100.0) if pg.ocr_confidence is not None else 1.0
            seg_input.append((pg.number, pg.text, max(min(q, 1.0), 0.05)))

    if failed == total:
        raise CorruptFile("Every page failed to process")
    if not seg_input:
        raise NoContent("No text could be extracted (blank or unreadable document)")

    results = []
    for seg in segment_pages(seg_input):
        ex = extract_invoice(seg)
        ex["page_start"], ex["page_end"] = seg[0][0], seg[-1][0]
        results.append(ex)

    any_review = False
    with connect() as db:
        for seq, ex in enumerate(results, 1):
            F = ex["fields"]
            v = {k: F[k]["value"] for k in FIELDS}
            issues = validate_invoice(F, ex["line_items"])
            key = dup_key(v["supplier"], v["invoice_number"])
            if key:
                other = db.execute("SELECT i.id, i.document_id FROM invoices i WHERE i.dup_key=? AND i.document_id!=?"
                                   " ORDER BY i.id LIMIT 1", (key, doc_id)).fetchone()
                if other:
                    from .validate import Issue
                    issues.append(Issue("VAL-DUP-001", "error", "invoice_number",
                                        f"Same supplier and invoice number already exist in document {other['document_id']}."))
            review = needs_review(issues, F)
            any_review = any_review or review
            cur = db.execute(
                "INSERT INTO invoices(document_id,seq,page_start,page_end,fields_json,invoice_number,invoice_date,"
                "supplier,customer,subtotal,tax,total,currency,dup_key,needs_review,extractor) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, seq, ex["page_start"], ex["page_end"], json.dumps(F), v["invoice_number"], v["invoice_date"],
                 v["supplier"], v["customer"], v["subtotal"], v["tax"], v["total"], v["currency"], key,
                 int(review), ex["extractor"]))
            inv_id = cur.lastrowid
            for i, it in enumerate(ex["line_items"], 1):
                db.execute("INSERT INTO line_items(invoice_id,idx,description,quantity,unit_price,amount,evidence)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (inv_id, i, it["description"], it["quantity"], it["unit_price"], it["amount"], it["evidence"]))
            for iss in issues:
                db.execute("INSERT INTO validation_issues(invoice_id,rule_id,severity,field,message) VALUES (?,?,?,?,?)",
                           (inv_id, iss.rule_id, iss.severity, iss.field, iss.message))
        status = "partial" if failed else "completed"
        db.execute("UPDATE documents SET status=?, review_required=?, pages_failed=?, updated_at=? WHERE id=?",
                   (status, int(any_review), failed, now(), doc_id))
        add_event(db, doc_id, "completed",
                  f"status={status} invoices={len(results)} seconds={time.perf_counter() - t0:.2f}")
    return status
