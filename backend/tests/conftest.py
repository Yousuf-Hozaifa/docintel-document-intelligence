import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from synth.invoices import random_spec  # noqa: E402


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
