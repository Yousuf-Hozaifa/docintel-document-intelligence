import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synth.invoices import random_spec  # noqa: E402


def pytest_sessionstart(session):
    """OCR tests need Tesseract. Fail once, clearly, instead of with many TesseractNotFoundError tracebacks."""
    import os
    from app.readers import ocr_status
    st = ocr_status()
    if not st["available"] and not os.environ.get("DOCINTEL_TESTS_ALLOW_NO_OCR"):
        pytest.exit("Tesseract OCR was not found, so the OCR tests cannot run.\n" + st["hint"] +
                    "\n(PowerShell example:  $env:TESSERACT_CMD = 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe')", returncode=2)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCINTEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DOCINTEL_WORKERS", "0")          # tests drive the queue synchronously
    monkeypatch.setenv("DOCINTEL_RETRY_BACKOFF_S", "0")
    from app.db import init_db
    init_db()


@pytest.fixture
def spec():
    return random_spec(random.Random(7), 1, n_items=3)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)  # no `with`: lifespan (worker threads) intentionally not started
