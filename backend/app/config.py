"""Runtime settings, read from environment at call time (tests override via env)."""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int
    workers: int
    max_attempts: int
    retry_backoff_s: float
    ocr_lang: str
    ocr_dpi: int
    ocr_timeout_s: int
    min_text_chars: int
    max_sheet_rows: int
    tolerance_minor_units: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "docintel.sqlite3"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        data_dir=Path(e("DOCINTEL_DATA_DIR", "./data")),
        max_upload_bytes=int(e("DOCINTEL_MAX_UPLOAD_MB", "200")) * 1024 * 1024,
        workers=int(e("DOCINTEL_WORKERS", "1")),
        max_attempts=int(e("DOCINTEL_MAX_ATTEMPTS", "3")),
        retry_backoff_s=float(e("DOCINTEL_RETRY_BACKOFF_S", "2")),
        ocr_lang=e("DOCINTEL_OCR_LANG", "eng"),
        ocr_dpi=int(e("DOCINTEL_OCR_DPI", "200")),
        ocr_timeout_s=int(e("DOCINTEL_OCR_TIMEOUT_S", "90")),
        min_text_chars=int(e("DOCINTEL_MIN_TEXT_CHARS", "20")),
        max_sheet_rows=int(e("DOCINTEL_MAX_SHEET_ROWS", "100000")),
        tolerance_minor_units=int(e("DOCINTEL_TOLERANCE_MINOR_UNITS", "1")),
    )
