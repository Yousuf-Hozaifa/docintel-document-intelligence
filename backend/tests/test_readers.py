"""Document processing: PDF text, OCR (scanned PDF / PNG / JPG / TIFF), spreadsheets, corrupt files."""
import pymupdf
import pytest
from PIL import Image

from app.errors import CorruptFile, EncryptedFile, UnsupportedFile
from app.readers import detect_type, open_pages
from synth import invoices as S


def read_all(path, name=None):
    kind = detect_type(path, name or path.name)
    total, pages = open_pages(path, kind)
    return kind, total, list(pages)


def test_digital_pdf_uses_text_layer_not_ocr(tmp_path, spec):
    p = S.write_pdf(spec, tmp_path / "a.pdf")
    kind, total, pages = read_all(p)
    assert kind == "pdf" and total == 1 and pages[0].method == "text"
    assert spec.number in pages[0].text and f"{spec.total:.2f}" in pages[0].text


def test_multi_page_pdf_is_read_page_by_page(tmp_path, spec):
    p = S.write_digital_pdf([spec] * 5, tmp_path / "m.pdf")
    _, total, pages = read_all(p)
    assert total == 5 and [x.number for x in pages] == [1, 2, 3, 4, 5]


def test_scanned_pdf_goes_through_ocr(tmp_path, spec):
    p = S.write_scanned_pdf([spec], tmp_path / "s.pdf")
    _, _, pages = read_all(p)
    assert pages[0].method == "ocr" and pages[0].ocr_confidence and pages[0].ocr_confidence > 60
    assert spec.number in pages[0].text


@pytest.mark.parametrize("ext", [".png", ".jpg"])
def test_image_ocr(tmp_path, spec, ext):
    p = S.write_image(spec, tmp_path / f"i{ext}")
    kind, _, pages = read_all(p)
    assert kind in ("png", "jpg") and spec.number in pages[0].text


def test_multiframe_tiff_yields_one_page_per_frame(tmp_path, spec):
    p = S.write_tiff([spec, spec], tmp_path / "t.tiff")
    kind, total, pages = read_all(p)
    assert kind == "tiff" and total == 2 and all(pg.method == "ocr" for pg in pages)


def test_xlsx_reads_every_sheet(tmp_path, spec):
    p = S.write_xlsx([spec, spec, spec], tmp_path / "w.xlsx")
    _, total, pages = read_all(p)
    assert total == 3 and [pg.label for pg in pages] == ["Invoice1", "Invoice2", "Invoice3"]
    assert spec.number in pages[1].text


def test_xls_legacy_format(tmp_path, spec):
    p = S.write_xls([spec, spec], tmp_path / "w.xls")
    kind, total, pages = read_all(p)
    assert kind == "xls" and total == 2 and spec.number in pages[0].text


@pytest.mark.parametrize("delim", [",", ";", "\t"])
def test_csv_delimiters(tmp_path, spec, delim):
    p = S.write_csv(spec, tmp_path / "c.csv", delimiter=delim)
    kind, _, pages = read_all(p)
    assert kind == "csv" and spec.number in pages[0].text


def test_csv_latin1_encoding(tmp_path):
    p = tmp_path / "l.csv"
    p.write_bytes("Invoice Number,INV-9\nSupplier,Café Müller\n".encode("cp1252"))
    _, _, pages = read_all(p)
    assert "Café Müller" in pages[0].text


# ---- failure scenarios
def test_unsupported_extension(tmp_path):
    p = tmp_path / "x.exe"; p.write_bytes(b"MZ....")
    with pytest.raises(UnsupportedFile):
        detect_type(p, "x.exe")


def test_empty_file(tmp_path):
    p = tmp_path / "e.pdf"; p.write_bytes(b"")
    with pytest.raises(CorruptFile):
        detect_type(p, "e.pdf")


def test_extension_content_mismatch(tmp_path):
    p = tmp_path / "fake.pdf"; p.write_bytes(b"just some text, definitely not a pdf")
    with pytest.raises(CorruptFile):
        detect_type(p, "fake.pdf")


def test_zip_that_is_not_xlsx_is_rejected(tmp_path):
    p = tmp_path / "z.csv"; p.write_bytes(b"PK\x03\x04" + b"\0" * 30)
    with pytest.raises(UnsupportedFile):
        detect_type(p, "z.csv")


def test_truncated_pdf_is_corrupt(tmp_path, spec):
    good = S.write_pdf(spec, tmp_path / "g.pdf").read_bytes()
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"%PDF-1.4\n" + good[20:120])
    with pytest.raises(CorruptFile):
        read_all(bad)


def test_corrupt_xlsx(tmp_path):
    p = tmp_path / "bad.xlsx"; p.write_bytes(b"PK\x03\x04" + b"garbage" * 20)
    with pytest.raises(CorruptFile):
        read_all(p)


def test_truncated_png_is_corrupt(tmp_path, spec):
    good = S.write_image(spec, tmp_path / "g.png").read_bytes()
    bad = tmp_path / "bad.png"; bad.write_bytes(good[: len(good) // 3])
    with pytest.raises(CorruptFile):
        read_all(bad)


def test_encrypted_pdf(tmp_path, spec):
    src = S.write_pdf(spec, tmp_path / "p.pdf")
    doc = pymupdf.open(src); out = tmp_path / "enc.pdf"
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")
    with pytest.raises(EncryptedFile):
        read_all(out)


def test_binary_csv_rejected(tmp_path):
    p = tmp_path / "b.csv"; p.write_bytes(b"\x00\x01\x02" * 100)
    with pytest.raises(CorruptFile):
        read_all(p)


def test_blank_image_gives_empty_text_not_crash(tmp_path):
    p = tmp_path / "blank.png"; Image.new("RGB", (800, 600), "white").save(p)
    _, _, pages = read_all(p)
    assert pages[0].text.strip() == ""
