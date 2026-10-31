"""SQLite storage (stdlib sqlite3, WAL mode). One short-lived connection per unit of work."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  file_type TEXT,
  size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  error_code TEXT,
  error_message TEXT,
  page_count INTEGER,
  pages_failed INTEGER NOT NULL DEFAULT 0,
  review_required INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS ix_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL,
  run_after REAL NOT NULL DEFAULT 0,
  progress_done INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER,
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_claim ON jobs(status, run_after);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  label TEXT,
  method TEXT,
  ocr_confidence REAL,
  text TEXT,
  error TEXT,
  UNIQUE(document_id, page_no)
);
CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
  text, document_id UNINDEXED, page_no UNINDEXED, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  page_start INTEGER NOT NULL,
  page_end INTEGER NOT NULL,
  fields_json TEXT NOT NULL,
  invoice_number TEXT, invoice_date TEXT, supplier TEXT, customer TEXT,
  subtotal TEXT, tax TEXT, total TEXT, currency TEXT,
  dup_key TEXT,
  num_key TEXT,
  needs_review INTEGER NOT NULL DEFAULT 0,
  extractor TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_invoices_doc ON invoices(document_id);
CREATE INDEX IF NOT EXISTS ix_invoices_dup ON invoices(dup_key);

CREATE TABLE IF NOT EXISTS line_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  description TEXT, quantity TEXT, unit_price TEXT, amount TEXT,
  evidence TEXT
);

CREATE TABLE IF NOT EXISTS validation_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  rule_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  field TEXT,
  message TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  field TEXT NOT NULL, old_value TEXT, new_value TEXT, note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  chunk_idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS ix_chunks_model ON chunks(model);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ts TEXT NOT NULL, event TEXT NOT NULL, detail TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open() -> sqlite3.Connection:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(s.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def connect():
    """Transactional connection: commits on success, rolls back on error."""
    conn = _open()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    s = get_settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    conn = _open()
    try:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(invoices)")}
        if "num_key" not in cols:  # migrate a Day-1 database in place
            conn.execute("ALTER TABLE invoices ADD COLUMN num_key TEXT")
            for r in conn.execute("SELECT id, invoice_number FROM invoices").fetchall():
                conn.execute("UPDATE invoices SET num_key=? WHERE id=?",
                             ("".join(ch for ch in (r["invoice_number"] or "").lower() if ch.isalnum()) or None, r["id"]))
        conn.execute("CREATE INDEX IF NOT EXISTS ix_invoices_numkey ON invoices(num_key)")
    finally:
        conn.close()


def add_event(db, document_id: int, event: str, detail: str | None = None) -> None:
    db.execute("INSERT INTO events(document_id, ts, event, detail) VALUES (?,?,?,?)",
               (document_id, now(), event, detail))
