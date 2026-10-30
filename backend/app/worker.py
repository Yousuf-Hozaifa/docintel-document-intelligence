"""Lightweight DB-backed job queue + bounded worker threads (no Redis/Celery).

Claiming is atomic (BEGIN IMMEDIATE), so a job is never run by two workers. Jobs stuck in
'running' after a crash are re-queued at startup. Permanent errors fail immediately; other
errors retry with exponential backoff up to max_attempts."""
import threading
import time
import traceback

from .config import get_settings
from .db import add_event, connect, now
from .errors import DocumentError
from .pipeline import process_document

_stop = threading.Event()
_threads: list[threading.Thread] = []


def enqueue(db, document_id: int) -> int:
    s = get_settings()
    cur = db.execute("INSERT INTO jobs(document_id,status,max_attempts,created_at) VALUES (?,?,?,?)",
                     (document_id, "queued", s.max_attempts, now()))
    return cur.lastrowid


def recover_stuck() -> int:
    with connect() as db:
        cur = db.execute("UPDATE jobs SET status='queued', run_after=0 WHERE status='running'")
        db.execute("UPDATE documents SET status='queued' WHERE status='processing'")
        return cur.rowcount


def _claim():
    with connect() as db:
        row = db.execute("SELECT id, document_id FROM jobs WHERE status='queued' AND run_after<=? "
                         "ORDER BY id LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return None
        db.execute("UPDATE jobs SET status='running', attempts=attempts+1, started_at=?, error=NULL WHERE id=?",
                   (now(), row["id"]))
        return row["id"], row["document_id"]


def run_one() -> bool:
    """Claim and execute one job. Returns False when nothing was runnable."""
    claimed = _claim()
    if not claimed:
        return False
    job_id, doc_id = claimed
    s = get_settings()
    try:
        status = process_document(doc_id, job_id)
        with connect() as db:
            db.execute("UPDATE jobs SET status='completed', finished_at=? WHERE id=?", (now(), job_id))
            add_event(db, doc_id, "job_completed", f"job={job_id} status={status}")
    except Exception as e:  # noqa: BLE001 - classify below
        permanent = isinstance(e, DocumentError)
        code = getattr(e, "code", "processing_error")
        msg = f"{type(e).__name__}: {e}"
        with connect() as db:
            attempts = db.execute("SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not permanent and attempts["attempts"] < attempts["max_attempts"]:
                delay = s.retry_backoff_s * (2 ** (attempts["attempts"] - 1))
                db.execute("UPDATE jobs SET status='queued', run_after=?, error=? WHERE id=?",
                           (time.time() + delay, msg, job_id))
                db.execute("UPDATE documents SET status='queued', updated_at=? WHERE id=?", (now(), doc_id))
                add_event(db, doc_id, "retry_scheduled", f"job={job_id} attempt={attempts['attempts']} in {delay:.0f}s: {msg}")
            else:
                db.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?", (msg, now(), job_id))
                db.execute("UPDATE documents SET status='failed', error_code=?, error_message=?, updated_at=? WHERE id=?",
                           (code, str(e)[:500], now(), doc_id))
                add_event(db, doc_id, "job_failed", f"job={job_id} {code}: {e}")
        if not permanent:
            traceback.print_exc()
    return True


def run_all(max_jobs: int = 10_000) -> int:
    """Drain the queue synchronously (used by tests / CLI)."""
    n = 0
    while n < max_jobs and run_one():
        n += 1
    return n


def _loop() -> None:
    while not _stop.is_set():
        try:
            if not run_one():
                _stop.wait(0.5)
        except Exception:  # noqa: BLE001 - keep the worker alive
            traceback.print_exc()
            _stop.wait(1.0)


def start() -> None:
    s = get_settings()
    _stop.clear()
    recover_stuck()
    for i in range(s.workers):
        t = threading.Thread(target=_loop, name=f"worker-{i}", daemon=True)
        t.start()
        _threads.append(t)


def stop() -> None:
    _stop.set()
    for t in _threads:
        t.join(timeout=5)
    _threads.clear()
