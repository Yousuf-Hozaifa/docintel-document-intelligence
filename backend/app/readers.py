"""Document readers. Each reader yields Page objects lazily so memory stays bounded
(one rendered page / one sheet in memory at a time, never the whole document)."""
import csv
import io
import os
import zipfile
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pymupdf
import pytesseract
from PIL import Image, ImageOps, ImageSequence
from pytesseract import Output

from .config import get_settings
from .errors import CorruptFile, EncryptedFile, UnsupportedFile

COL_SEP = "  "  # two spaces mark a column gap inside a reconstructed line
ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx", ".xls", ".csv"}
Image.MAX_IMAGE_PIXELS = 100_000_000  # decompression-bomb guard

pymupdf.TOOLS.mupdf_display_errors(False)
if os.environ.get("TESSERACT_CMD"):  # Windows: point at tesseract.exe if it is not on PATH
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]


@dataclass
class Page:
    number: int
    text: str
    method: str                 # text | ocr | sheet | csv | failed
    label: str | None = None    # sheet name
    ocr_confidence: float | None = None
    error: str | None = None
    rows: list | None = None    # spreadsheet cells (strings), used for register detection; not persisted


def _safe_err(e: Exception) -> str:
    """Error text for storage/display: no filesystem paths."""
    import re
    return f"{type(e).__name__}: " + re.sub(r"(/[\w.\-]+){2,}", "<path>", str(e))[:200]


# ---------------------------------------------------------------- type detection
def detect_type(path: Path, filename: str) -> str:
    """Decide the file type from content (magic bytes), cross-checked with the extension."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise UnsupportedFile(f"Unsupported file extension '{ext or '(none)'}'")
    with open(path, "rb") as f:
        head = f.read(16)
    if len(head) == 0:
        raise CorruptFile("File is empty")
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head.startswith(b"PK\x03\x04"):
        if ext != ".xlsx":
            raise UnsupportedFile("ZIP container that is not an .xlsx workbook")
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        if ext != ".xls":
            raise UnsupportedFile("OLE container that is not an .xls workbook")
        return "xls"
    if ext == ".csv":
        return "csv"
    raise CorruptFile(f"File content does not match its '{ext}' extension")


# ---------------------------------------------------------------- OCR
def ocr_image(img: Image.Image) -> tuple[str, float | None]:
    """OCR one PIL image. Returns (text with line/column structure, mean word confidence)."""
    s = get_settings()
    if s.tess_omp_threads:
        os.environ["OMP_THREAD_LIMIT"] = str(s.tess_omp_threads)  # pytesseract passes os.environ to tesseract
    g = ImageOps.autocontrast(ImageOps.grayscale(img))
    if max(g.size) < 1400:  # upscale small / low-resolution scans
        g = g.resize((g.width * 2, g.height * 2), Image.LANCZOS)
    data = pytesseract.image_to_data(g, lang=s.ocr_lang, config="--psm 6",
                                     output_type=Output.DICT, timeout=s.ocr_timeout_s)
    lines: dict[tuple, list] = {}
    for i, word in enumerate(data["text"]):
        word = word.strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((data["left"][i], data["width"][i],
                                          data["height"][i], word, conf))
    out, confs = [], []
    for words in lines.values():
        words.sort()
        parts, prev_right = [], None
        for left, width, height, word, conf in words:
            if prev_right is not None:
                gap = left - prev_right
                parts.append(COL_SEP if gap > 1.5 * height else " ")
            parts.append(word)
            prev_right = left + width
            confs.append(conf)
        out.append("".join(parts))
    mean = round(sum(confs) / len(confs), 1) if confs else None
    return "\n".join(out), mean


# ---------------------------------------------------------------- PDF
def _pdf_text(page: "pymupdf.Page") -> str:
    """Rebuild reading-order lines from word boxes; large horizontal gaps become COL_SEP."""
    words = page.get_text("words", sort=True)  # x0,y0,x1,y1,word,block,line,wno
    lines, cur, cur_y = [], [], None
    for w in sorted(words, key=lambda w: (round(w[1] / 3), w[0])):
        y = (w[1] + w[3]) / 2
        if cur_y is None or abs(y - cur_y) <= 3:
            cur.append(w)
            cur_y = y if cur_y is None else cur_y
        else:
            lines.append(cur)
            cur, cur_y = [w], y
    if cur:
        lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        parts, prev = [], None
        for w in ln:
            if prev is not None:
                gap = w[0] - prev[2]
                h = max(w[3] - w[1], 1)
                parts.append(COL_SEP if gap > 1.5 * h else " ")
            parts.append(w[4])
            prev = w
        out.append("".join(parts))
    return "\n".join(out)


def _read_pdf(path: Path) -> tuple[int, Iterator[Page]]:
    s = get_settings()
    try:
        doc = pymupdf.open(path)
    except Exception as e:  # FileDataError, EmptyFileError, ...
        raise CorruptFile(f"PDF cannot be opened ({type(e).__name__}): the file is damaged or not a valid PDF") from e
    if doc.needs_pass:
        doc.close()
        raise EncryptedFile("PDF is password protected")
    if doc.page_count == 0:
        doc.close()
        raise CorruptFile("PDF has no pages")

    def ocr_task(num: int, img: Image.Image) -> Page:
        try:
            text, conf = ocr_image(img)
            return Page(num, text, "ocr", ocr_confidence=conf)
        except Exception as e:
            return Page(num, "", "failed", error=_safe_err(e))
        finally:
            img.close()

    def prepare(i: int):
        """Main thread only (PyMuPDF is not thread-safe). Returns a Page, or (num, image) needing OCR."""
        try:
            page = doc.load_page(i)
            text = _pdf_text(page)
            if len(text.strip()) >= s.min_text_chars:
                return Page(i + 1, text, "text")
            pix = page.get_pixmap(dpi=s.ocr_dpi, colorspace=pymupdf.csRGB)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            del pix
            if s.pdf_store_shrink:
                pymupdf.TOOLS.store_shrink(100)
            return (i + 1, img)
        except Exception as e:
            return Page(i + 1, "", "failed", error=_safe_err(e))

    def gen() -> Iterator[Page]:
        """Pages are yielded in order. At most `ocr_threads` rendered images exist at once,
        so memory is bounded by ocr_threads x (one rendered page), independent of page count."""
        window = s.ocr_threads
        pool = ThreadPoolExecutor(max_workers=window) if window > 1 else None
        pending: deque = deque()   # Page | Future[Page], in page order
        try:
            for i in range(doc.page_count):
                item = prepare(i)
                if isinstance(item, Page):
                    pending.append(item)
                elif pool:
                    pending.append(pool.submit(ocr_task, *item))
                else:
                    pending.append(ocr_task(*item))
                while len(pending) > 0 and (isinstance(pending[0], Page) or pending[0].done()
                                            or sum(isinstance(p, Future) for p in pending) >= window):
                    head = pending.popleft()
                    yield head if isinstance(head, Page) else head.result()
            while pending:
                head = pending.popleft()
                yield head if isinstance(head, Page) else head.result()
        finally:
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
            doc.close()

    return doc.page_count, gen()


# ---------------------------------------------------------------- images
def _read_image(path: Path) -> tuple[int, Iterator[Page]]:
    try:
        with Image.open(path) as im:
            im.verify()
        im = Image.open(path)
        frames = getattr(im, "n_frames", 1)
    except Exception as e:
        raise CorruptFile(f"Image cannot be decoded ({type(e).__name__}): the file is damaged or truncated") from e

    def gen() -> Iterator[Page]:
        try:
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                try:
                    text, conf = ocr_image(frame.convert("RGB"))
                    yield Page(i + 1, text, "ocr", ocr_confidence=conf)
                except Exception as e:
                    yield Page(i + 1, "", "failed", error=_safe_err(e))
        finally:
            im.close()

    return frames, gen()


# ---------------------------------------------------------------- spreadsheets
def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.10g}"
    if hasattr(v, "isoformat"):
        return v.date().isoformat() if hasattr(v, "date") else v.isoformat()
    return str(v).strip()


def _clean_rows(rows) -> list[list[str]]:
    return [[_cell(c) for c in r] for r in rows]


def _rows_to_text(rows) -> str:
    out = []
    for r in rows:
        cells = [_cell(c) for c in r]
        while cells and cells[-1] == "":
            cells.pop()
        if any(cells):
            out.append(COL_SEP.join(c for c in cells if c != ""))
    return "\n".join(out)


def _read_xlsx(path: Path) -> tuple[int, Iterator[Page]]:
    import openpyxl
    s = get_settings()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as e:
        raise CorruptFile(f"Workbook cannot be opened ({type(e).__name__}): the file is damaged") from e
    except Exception as e:
        raise CorruptFile(f"Workbook cannot be opened ({type(e).__name__}): the file is damaged") from e
    names = wb.sheetnames
    if not names:
        wb.close()
        raise CorruptFile("Workbook has no sheets")

    def gen() -> Iterator[Page]:
        try:
            for i, name in enumerate(names):
                try:
                    ws = wb[name]
                    rows = []
                    for n, r in enumerate(ws.iter_rows(values_only=True)):
                        if n >= s.max_sheet_rows:
                            break
                        rows.append(r)
                    yield Page(i + 1, _rows_to_text(rows), "sheet", label=name, rows=_clean_rows(rows))
                except Exception as e:
                    yield Page(i + 1, "", "failed", label=name, error=_safe_err(e))
        finally:
            wb.close()

    return len(names), gen()


def _read_xls(path: Path) -> tuple[int, Iterator[Page]]:
    import xlrd
    s = get_settings()
    try:
        wb = xlrd.open_workbook(path, on_demand=True)
    except Exception as e:
        raise CorruptFile(f"Workbook cannot be opened ({type(e).__name__}): the file is damaged") from e
    if wb.nsheets == 0:
        raise CorruptFile("Workbook has no sheets")

    def gen() -> Iterator[Page]:
        try:
            for i in range(wb.nsheets):
                try:
                    ws = wb.sheet_by_index(i)
                    rows = [ws.row_values(r) for r in range(min(ws.nrows, s.max_sheet_rows))]
                    yield Page(i + 1, _rows_to_text(rows), "sheet", label=ws.name, rows=_clean_rows(rows))
                    wb.unload_sheet(i)
                except Exception as e:
                    yield Page(i + 1, "", "failed", error=_safe_err(e))
        finally:
            wb.release_resources()

    return wb.nsheets, gen()


def _read_csv(path: Path) -> tuple[int, Iterator[Page]]:
    s = get_settings()
    raw = path.read_bytes()[: 64 * 1024 * 1024]
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise CorruptFile("File contains binary data and is not valid CSV text")
    text = None
    has_bom16 = raw.startswith((b"\xff\xfe", b"\xfe\xff"))
    for enc in (("utf-16",) if has_bom16 else ()) + ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None or not text.strip():
        raise CorruptFile("CSV is empty or undecodable")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for n, r in enumerate(csv.reader(io.StringIO(text), dialect)):
        if n >= s.max_sheet_rows:
            break
        rows.append(r)

    def gen() -> Iterator[Page]:
        yield Page(1, _rows_to_text(rows), "csv", label=path.name, rows=_clean_rows(rows))

    return 1, gen()


READERS = {"pdf": _read_pdf, "png": _read_image, "jpg": _read_image, "tiff": _read_image,
           "xlsx": _read_xlsx, "xls": _read_xls, "csv": _read_csv}


def open_pages(path: Path, kind: str) -> tuple[int, Iterator[Page]]:
    """Return (total_pages, lazy page iterator). Raises DocumentError subclasses."""
    return READERS[kind](path)
