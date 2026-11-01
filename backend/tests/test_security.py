"""Upload hardening, deletion, pagination and CORS configuration."""
import random

import openpyxl

from app import worker
from app.config import get_settings
from synth import invoices as S
from tests.test_api import upload


def test_path_traversal_filenames_are_neutralised(client, tmp_path, spec):
    pdf = S.write_pdf(spec, tmp_path / "a.pdf").read_bytes()
    r = client.post("/api/documents", files=[("files", ("../../../../etc/evil.pdf", pdf, "application/pdf"))]).json()["results"][0]
    assert r["status"] == "queued" and "/" not in r["filename"] and ".." not in r["filename"].replace("evil.pdf", "")
    stored = [p.name for p in get_settings().upload_dir.iterdir()]
    assert len(stored) == 1 and "evil" not in stored[0] and stored[0].endswith(".pdf")       # random uuid name on disk
    worker.run_all()
    assert client.get("/api/documents/1").json()["filename"] == r["filename"]


def test_windows_style_and_odd_filenames(client, tmp_path, spec):
    pdf = S.write_pdf(spec, tmp_path / "a.pdf").read_bytes()
    for name in ("C:\\Users\\x\\inv<>|?.pdf", "inv\x00.pdf"):
        r = client.post("/api/documents", files=[("files", (name, pdf + name.encode()[:3], "application/pdf"))]).json()["results"][0]
        assert r["status"] in ("queued", "rejected") and not any(c in r["filename"] for c in '<>|?\\/\x00')


def test_upload_size_limit_streams_and_leaves_nothing_behind(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCINTEL_MAX_UPLOAD_MB", "1")
    big = tmp_path / "big.csv"; big.write_bytes(b"a,b\n" + b"1,2\n" * 600_000)               # ~2.4 MB
    r = client.post("/api/documents", files=[("files", ("big.csv", big.read_bytes(), "text/csv"))]).json()["results"][0]
    assert r["status"] == "rejected" and "size limit" in r["error"]
    assert list(get_settings().upload_dir.iterdir()) == [] and client.get("/api/documents").json()["documents"] == []


def test_rejected_files_do_not_stay_on_disk(client, tmp_path):
    for name, body in (("x.exe", b"MZ"), ("empty.pdf", b""), ("fake.pdf", b"plain text")):
        client.post("/api/documents", files=[("files", (name, body, "application/octet-stream"))])
    assert list(get_settings().upload_dir.iterdir()) == []


def test_delete_removes_everything_and_refreshes_other_documents(client, tmp_path, spec):
    import shutil
    a = S.write_pdf(spec, tmp_path / "a.pdf"); b = tmp_path / "b.pdf"
    S.write_pdf(spec, b)
    import pymupdf
    d = pymupdf.open(b); d.set_metadata({"title": "copy"}); d.save(str(b) + ".t"); d.close(); shutil.move(str(b) + ".t", b)
    upload(client, a, b); worker.run_all()
    docs = {x["filename"]: x for x in client.get("/api/documents").json()["documents"]}
    assert docs["a.pdf"]["review_required"] and docs["b.pdf"]["review_required"]            # duplicates flag each other
    stored_before = len(list(get_settings().upload_dir.iterdir()))
    assert client.delete(f"/api/documents/{docs['b.pdf']['id']}").json() == {"deleted": docs["b.pdf"]["id"]}
    assert len(list(get_settings().upload_dir.iterdir())) == stored_before - 1               # file removed from disk
    assert client.get(f"/api/documents/{docs['b.pdf']['id']}").status_code == 404
    left = client.get(f"/api/documents/{docs['a.pdf']['id']}").json()
    assert left["review_required"] is False and "VAL-DUP-001" not in [i["rule_id"] for i in left["invoices"][0]["issues"]]
    from app.db import connect
    with connect() as db:
        for t, col in (("pages", "document_id"), ("invoices", "document_id"), ("events", "document_id"), ("jobs", "document_id"), ("page_fts", "document_id")):
            assert db.execute(f"SELECT COUNT(*) FROM {t} WHERE {col}=?", (docs["b.pdf"]["id"],)).fetchone()[0] == 0, t
        assert db.execute("SELECT COUNT(*) FROM line_items WHERE invoice_id NOT IN (SELECT id FROM invoices)").fetchone()[0] == 0
    assert client.delete("/api/documents/9999").status_code == 404
    assert client.get("/api/search", params={"q": spec.supplier.split()[0]}).json()["results"][0]["document_id"] == docs["a.pdf"]["id"]


def test_delete_is_refused_while_a_job_is_active(client, tmp_path, spec):
    upload(client, S.write_pdf(spec, tmp_path / "a.pdf"))          # queued, not yet processed
    assert client.delete("/api/documents/1").status_code == 409
    worker.run_all()
    assert client.delete("/api/documents/1").status_code == 200


def test_delete_requires_the_api_key_when_configured(client, tmp_path, spec, monkeypatch):
    upload(client, S.write_pdf(spec, tmp_path / "a.pdf")); worker.run_all()
    monkeypatch.setenv("DOCINTEL_API_KEY", "k")
    assert client.delete("/api/documents/1").status_code == 401
    assert client.delete("/api/documents/1", headers={"X-API-Key": "k"}).status_code == 200


def test_document_detail_paginates_invoices(client, tmp_path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Invoice No", "Date", "Supplier", "Customer", "Subtotal", "Tax", "Total", "Currency"])
    for i in range(120):
        ws.append([f"R-{i:04d}", "2024-01-02", "Acme", "Globex", 100, 10, 110, "USD"])
    p = tmp_path / "reg.xlsx"; wb.save(p); upload(client, p); worker.run_all()
    d = client.get("/api/documents/1").json()
    assert d["invoice_total"] == 120 and len(d["invoices"]) == 50 and d["invoices"][0]["invoice_number"] == "R-0000"
    d = client.get("/api/documents/1", params={"invoice_offset": 100, "invoice_limit": 50}).json()
    assert len(d["invoices"]) == 20 and d["invoices"][-1]["invoice_number"] == "R-0119"
    assert client.get("/api/documents/1", params={"invoice_limit": 0}).status_code == 422
    assert client.get("/api/documents/1", params={"invoice_limit": 501}).status_code == 422


def test_cors_only_allows_configured_origins(client):
    ok = client.options("/api/documents", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    bad = client.options("/api/documents", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad.headers


def test_original_file_is_served_with_safe_headers(client, tmp_path, spec):
    pdf = S.write_pdf(spec, tmp_path / "a.pdf"); png = S.write_image(spec, tmp_path / "b.png"); xl = S.write_xlsx([spec], tmp_path / "c.xlsx")
    upload(client, pdf, png, xl); worker.run_all()
    r = client.get("/api/documents/1/file")
    assert r.status_code == 200 and r.content == pdf.read_bytes() and r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["content-disposition"].startswith("inline") and "no-store" in r.headers["cache-control"]
    assert client.get("/api/documents/2/file").headers["content-type"] == "image/png"
    x = client.get("/api/documents/3/file")
    assert x.headers["content-disposition"].startswith("attachment") and x.content == xl.read_bytes()      # spreadsheets are never rendered inline
    assert client.get("/api/documents/99/file").status_code == 404


def test_original_file_follows_auth_and_deletion(client, tmp_path, spec, monkeypatch):
    upload(client, S.write_pdf(spec, tmp_path / "a.pdf")); worker.run_all()
    monkeypatch.setenv("DOCINTEL_API_KEY", "k")
    assert client.get("/api/documents/1/file").status_code == 401
    assert client.get("/api/documents/1/file", headers={"X-API-Key": "k"}).status_code == 200
    client.delete("/api/documents/1", headers={"X-API-Key": "k"})
    assert client.get("/api/documents/1/file", headers={"X-API-Key": "k"}).status_code == 404


def test_poisoned_stored_name_cannot_read_or_delete_files_outside_uploads(client, tmp_path, spec):
    import os
    upload(client, S.write_pdf(spec, tmp_path / "a.pdf")); worker.run_all()
    secret = tmp_path / "secret.pdf"; secret.write_bytes(b"%PDF-1.4 TOP SECRET")
    rel = os.path.relpath(secret, get_settings().upload_dir)                       # e.g. ../../secret.pdf, really resolves to the secret
    assert (get_settings().upload_dir / rel).resolve() == secret.resolve()
    from app.db import connect
    with connect() as db:
        db.execute("UPDATE documents SET stored_name=? WHERE id=1", (rel,))
    r = client.get("/api/documents/1/file")
    assert r.status_code == 404 and b"TOP SECRET" not in r.content
    assert client.delete("/api/documents/1").status_code == 200
    assert secret.exists()                                                        # the file outside uploads was NOT deleted
