"""A missing OCR engine is an environment problem: clear error code, no blame on the file, digital files still work."""
import pytest

from app import readers, worker
from synth import invoices as S
from tests.test_api import upload


@pytest.fixture
def no_tesseract(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", "/definitely/not/here/tesseract")
    monkeypatch.setenv("PATH", "")
    readers.configure_tesseract()
    yield
    monkeypatch.undo()
    readers.configure_tesseract()            # restore the real configuration for later tests


def test_health_reports_ocr_availability(client, no_tesseract):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["ocr"]["available"] is False and "TESSERACT_CMD" in h["ocr"]["hint"] and h["ocr"]["version"] is None


def test_health_reports_working_ocr(client):
    h = client.get("/api/health").json()
    assert h["ocr"]["available"] is True and h["ocr"]["version"]


def test_images_and_scans_fail_with_an_actionable_environment_error(client, tmp_path, spec, no_tesseract):
    upload(client, S.write_image(spec, tmp_path / "a.png"), S.write_scanned_pdf([spec], tmp_path / "b.pdf"))
    worker.run_all()
    docs = client.get("/api/documents").json()["documents"]
    assert len(docs) == 2
    for d in docs:
        assert d["status"] == "failed" and d["error_code"] == "ocr_unavailable" and "TESSERACT_CMD" in d["error_message"]
        assert d["job"]["attempts"] == 1                                    # environment errors are not retried blindly


def test_digital_files_still_work_without_ocr(client, tmp_path, spec, no_tesseract):
    upload(client, S.write_pdf(spec, tmp_path / "a.pdf"), S.write_xlsx([spec], tmp_path / "b.xlsx"), S.write_csv(spec, tmp_path / "c.csv"))
    worker.run_all()
    assert [d["status"] for d in client.get("/api/documents").json()["documents"]] == ["completed"] * 3


def test_reprocess_succeeds_once_ocr_is_available(client, tmp_path, spec, monkeypatch):
    import os
    real_path, real_cmd = os.environ["PATH"], os.environ.get("TESSERACT_CMD")
    monkeypatch.setenv("TESSERACT_CMD", "/definitely/not/here/tesseract"); monkeypatch.setenv("PATH", "")
    readers.configure_tesseract()
    upload(client, S.write_image(spec, tmp_path / "a.png")); worker.run_all()
    assert client.get("/api/documents/1").json()["error_code"] == "ocr_unavailable"
    monkeypatch.setenv("PATH", real_path)                                    # the user installs / configures Tesseract
    monkeypatch.delenv("TESSERACT_CMD", raising=False) if real_cmd is None else monkeypatch.setenv("TESSERACT_CMD", real_cmd)
    readers.configure_tesseract()
    assert client.post("/api/jobs/retry-failed").status_code == 202
    worker.run_all()
    d = client.get("/api/documents/1").json()
    assert d["status"] == "completed" and d["invoices"][0]["invoice_number"] == spec.number


def test_windows_default_install_folder_is_detected(monkeypatch, tmp_path):
    fake = tmp_path / "Tesseract-OCR"; fake.mkdir(); exe = fake / "tesseract.exe"; exe.write_text("")
    monkeypatch.delenv("TESSERACT_CMD", raising=False); monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(readers.os, "name", "nt")
    monkeypatch.setattr(readers, "_windows_tesseract_candidates", lambda: [str(exe)])
    readers.configure_tesseract()
    assert readers.pytesseract.pytesseract.tesseract_cmd == str(exe)
    monkeypatch.undo(); readers.configure_tesseract()


def test_explicit_tesseract_cmd_wins_over_autodetection(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", "/custom/tesseract")
    readers.configure_tesseract()
    assert readers.pytesseract.pytesseract.tesseract_cmd == "/custom/tesseract"
    monkeypatch.undo(); readers.configure_tesseract()
