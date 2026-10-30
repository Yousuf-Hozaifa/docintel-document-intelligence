"""API + background worker: upload, status, results, search, duplicates, failures, retries."""
import random

from app import worker
from app.db import connect
from synth import invoices as S
from synth.invoices import random_spec


def upload(client, *paths):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    r = client.post("/api/documents", files=files)
    assert r.status_code == 202, r.text
    return r.json()["results"]


def test_end_to_end_vertical_slice(client, tmp_path, spec):
    res = upload(client, S.write_pdf(spec, tmp_path / "inv.pdf"))
    doc_id = res[0]["document_id"]
    assert client.get(f"/api/documents/{doc_id}").json()["status"] == "queued"
    assert worker.run_all() == 1
    d = client.get(f"/api/documents/{doc_id}").json()
    assert d["status"] == "completed" and d["file_type"] == "pdf" and d["review_required"] is False
    inv = d["invoices"][0]
    assert inv["invoice_number"] == spec.number and inv["total"] == str(spec.total)
    assert len(inv["line_items"]) == 3 and inv["issues"] == []
    assert [h["event"] for h in d["history"]][0] == "uploaded"
    assert d["job"]["status"] == "completed" and d["job"]["progress_done"] == d["job"]["progress_total"] == 1


def test_multi_file_upload_with_all_formats(client, tmp_path, spec):
    paths = [S.write_pdf(spec, tmp_path / "a.pdf"), S.write_image(spec, tmp_path / "b.png"),
             S.write_xlsx([spec], tmp_path / "c.xlsx"), S.write_csv(spec, tmp_path / "d.csv"),
             S.write_xls([spec], tmp_path / "e.xls"), S.write_tiff([spec], tmp_path / "f.tif"),
             S.write_scanned_pdf([spec], tmp_path / "g.pdf")]
    res = upload(client, *paths)
    assert all(r["status"] == "queued" for r in res)
    worker.run_all()
    docs = client.get("/api/documents").json()["documents"]
    assert len(docs) == 7 and all(d["status"] == "completed" for d in docs), [(d["filename"], d["status"], d["error_message"]) for d in docs]
    for d in docs:
        inv = client.get(f"/api/documents/{d['id']}").json()["invoices"][0]
        assert inv["invoice_number"] == spec.number, d["filename"]


def test_bad_files_rejected_or_failed_without_blocking_good_ones(client, tmp_path, spec):
    good = S.write_pdf(spec, tmp_path / "good.pdf")
    exe = tmp_path / "evil.exe"; exe.write_bytes(b"MZ")
    empty = tmp_path / "empty.pdf"; empty.write_bytes(b"")
    corrupt = tmp_path / "corrupt.pdf"; corrupt.write_bytes(b"%PDF-1.4\n" + good.read_bytes()[20:100])
    res = upload(client, good, exe, empty, corrupt)
    assert [r["status"] for r in res] == ["queued", "rejected", "rejected", "queued"]
    worker.run_all()
    docs = {d["filename"]: d for d in client.get("/api/documents").json()["documents"]}
    assert docs["good.pdf"]["status"] == "completed"
    assert docs["corrupt.pdf"]["status"] == "failed" and docs["corrupt.pdf"]["error_code"] == "corrupt_file"
    assert docs["corrupt.pdf"]["job"]["attempts"] == 1  # permanent errors are not retried


def test_identical_upload_is_not_processed_twice(client, tmp_path, spec):
    p = S.write_pdf(spec, tmp_path / "x.pdf")
    first = upload(client, p)[0]
    second = upload(client, p)[0]
    assert second["status"] == "duplicate" and second["duplicate_of"] == first["document_id"]
    assert len(client.get("/api/documents").json()["documents"]) == 1


def test_same_invoice_as_pdf_and_xlsx_is_cross_verified_not_a_duplicate(client, tmp_path, spec):
    upload(client, S.write_pdf(spec, tmp_path / "one.pdf"))
    upload(client, S.write_xlsx([spec], tmp_path / "two.xlsx"))
    worker.run_all()
    for d in client.get("/api/documents").json()["documents"]:
        full = client.get(f"/api/documents/{d['id']}").json()
        rules = [i["rule_id"] for i in full["invoices"][0]["issues"]]
        assert "VAL-XDOC-002" in rules and "VAL-DUP-001" not in rules
        assert full["review_required"] is False


def test_arithmetic_error_marks_document_for_review(client, tmp_path):
    spec = random_spec(random.Random(3), 5, 2)
    spec.tax_rate = spec.tax_rate  # sanity
    p = S.write_pdf(spec, tmp_path / "w.pdf")
    # tamper with the printed total
    import pymupdf
    doc = pymupdf.open(p); pg = doc[0]
    for r in pg.search_for(f"{spec.total:.2f}"):
        pg.add_redact_annot(r, text="999.99", fontsize=10)
    pg.apply_redactions(); doc.saveIncr()
    upload(client, p); worker.run_all()
    d = client.get("/api/documents").json()["documents"][0]
    full = client.get(f"/api/documents/{d['id']}").json()
    assert d["review_required"] and "VAL-SUB-001" in [i["rule_id"] for i in full["invoices"][0]["issues"]]


def test_transient_error_is_retried_then_succeeds(client, tmp_path, spec, monkeypatch):
    from app import pipeline
    real, calls = pipeline.process_document, {"n": 0}

    def flaky(doc_id, job_id=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("disk hiccup")
        return real(doc_id, job_id)
    monkeypatch.setattr(worker, "process_document", flaky)
    upload(client, S.write_pdf(spec, tmp_path / "r.pdf"))
    assert worker.run_all() == 3
    d = client.get("/api/documents").json()["documents"][0]
    assert d["status"] == "completed" and d["job"]["attempts"] == 3


def test_transient_error_exhausts_attempts_and_fails(client, tmp_path, spec, monkeypatch):
    monkeypatch.setattr(worker, "process_document", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    upload(client, S.write_pdf(spec, tmp_path / "r.pdf"))
    assert worker.run_all() == 3
    d = client.get("/api/documents").json()["documents"][0]
    assert d["status"] == "failed" and d["job"]["attempts"] == 3 and "down" in d["job"]["error"]


def test_job_is_claimed_only_once():
    with connect() as db:
        db.execute("INSERT INTO documents(filename,stored_name,sha256,size_bytes,created_at,updated_at) VALUES ('a','a','h',1,'t','t')")
        worker.enqueue(db, 1)
    assert worker._claim() is not None and worker._claim() is None


def test_stuck_jobs_are_recovered_on_startup():
    with connect() as db:
        db.execute("INSERT INTO documents(filename,stored_name,sha256,size_bytes,status,created_at,updated_at) VALUES ('a','a','h',1,'processing','t','t')")
        jid = worker.enqueue(db, 1)
        db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    assert worker.recover_stuck() == 1 and worker._claim() is not None


def test_keyword_search_and_filters(client, tmp_path):
    a = random_spec(random.Random(1), 11, 2); a.supplier, a.currency = "Zephyr Aerospace", "EUR"
    b = random_spec(random.Random(2), 12, 2); b.supplier, b.currency = "Borealis Foods", "USD"
    upload(client, S.write_pdf(a, tmp_path / "a.pdf"), S.write_pdf(b, tmp_path / "b.pdf")); worker.run_all()
    r = client.get("/api/search", params={"q": "zephyr"}).json()["results"]
    assert len(r) == 1 and r[0]["supplier"] == "Zephyr Aerospace" and "<mark>" in r[0]["snippets"][0]["snippet"]
    assert len(client.get("/api/search", params={"currency": "usd"}).json()["results"]) == 1
    assert client.get("/api/search", params={"supplier": "borealis"}).json()["results"][0]["invoice_number"] == b.number
    assert client.get("/api/search", params={"q": "nonexistentword"}).json()["results"] == []
    assert client.get("/api/search", params={"q": '"; DROP TABLE documents; --'}).status_code == 200


def test_unknown_document_404(client):
    assert client.get("/api/documents/999").status_code == 404


def test_failure_messages_do_not_leak_server_paths(client, tmp_path, spec):
    corrupt = tmp_path / "c.pdf"; corrupt.write_bytes(b"%PDF-1.4\n" + b"x" * 50)
    upload(client, corrupt); worker.run_all()
    d = client.get("/api/documents").json()["documents"][0]
    assert d["status"] == "failed"
    assert "uploads" not in d["error_message"] and "/" not in d["error_message"].replace("PDF", "")
    assert "uploads" not in client.get(f"/api/documents/{d['id']}").json()["job"]["error"]
