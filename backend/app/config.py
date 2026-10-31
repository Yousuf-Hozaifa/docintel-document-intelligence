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
    ocr_threads: int              # pages OCR'd concurrently inside one document (memory ~ O(this))
    semantic_backend: str         # off | glove | sbert
    glove_vocab: int              # words kept from the 400k GloVe vocabulary (most frequent first)
    sbert_model: str
    chunk_chars: int
    ocr_field_min_conf: float     # flag fields/line items whose weakest OCR word is below this (0 disables)
    pdf_store_shrink: bool        # flush MuPDF's decoded-image cache after each OCR'd page
    tess_omp_threads: int | None  # OMP_THREAD_LIMIT for each tesseract process (None = tesseract default)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "docintel.sqlite3"

    @property
    def models_dir(self) -> Path:
        return Path(os.environ["DOCINTEL_MODELS_DIR"]) if os.environ.get("DOCINTEL_MODELS_DIR") else self.data_dir / "models"

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
        ocr_threads=max(1, int(e("DOCINTEL_OCR_THREADS", "1"))),
        semantic_backend=e("DOCINTEL_SEMANTIC", "off").lower(),
        glove_vocab=int(e("DOCINTEL_GLOVE_VOCAB", "100000")),
        sbert_model=e("DOCINTEL_SBERT_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        ocr_field_min_conf=float(e("DOCINTEL_OCR_FIELD_MIN_CONF", "60")),
        chunk_chars=int(e("DOCINTEL_CHUNK_CHARS", "250")),
        pdf_store_shrink=e("DOCINTEL_PDF_STORE_SHRINK", "1") != "0",
        tess_omp_threads=(int(e("DOCINTEL_TESS_OMP_THREADS", "1")) or None),  # 0 = tesseract default
    )
