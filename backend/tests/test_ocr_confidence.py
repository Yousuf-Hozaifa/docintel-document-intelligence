"""Word-level OCR confidence: per-field attachment, VAL-OCR-001, persistence and manual override."""
from datetime import date

from PIL import Image

from app import readers, worker
from app.extract import extract_invoice
from app.validate import validate_invoice
from tests.test_api import upload

TEXT = """Acme Supplies Ltd
Invoice Number: SV500114/24  Invoice Date: 13 October 2024
Bill To: Globex Corp
Description  Qty  Unit Price  Amount
Widget A  2  1,010.00  2,020.00
Gadget B  3  15.50  46.50
Subtotal: 2,066.50
Tax (10%): 206.65
Total: 2,273.15
Currency: USD"""


def words_for(text, low=None):
    """Fake OCR word list: every word 95 unless its text is in `low` ({word: conf})."""
    low = low or {}
    return [[(w, low.get(w, 95.0)) for w in ln.split()] for ln in text.splitlines()]


def extract(low=None, q=0.94):
    return extract_invoice([(1, TEXT, q, words_for(TEXT, low))])


def test_field_confidence_is_the_weakest_word_behind_the_value():
    r = extract({"SV500114/24": 41.0, "2,020.00": 58.0})
    F = r["fields"]
    assert F["invoice_number"]["ocr_confidence"] == 41.0
    assert F["invoice_date"]["ocr_confidence"] == 95.0           # raw text "13 October 2024" matched word by word
    assert F["total"]["ocr_confidence"] == 95.0
    assert F["invoice_number"]["confidence"] < F["invoice_date"]["confidence"]
    assert r["line_items"][0]["ocr_conf"] == 58.0 and r["line_items"][1]["ocr_conf"] == 95.0


def test_amount_matches_despite_thousands_separators_and_normalisation():
    r = extract({"2,273.15": 30.0})
    assert r["fields"]["total"]["value"] == "2273.15" and r["fields"]["total"]["ocr_confidence"] == 30.0


def test_no_ocr_words_means_no_ocr_confidence():
    r = extract_invoice([(1, TEXT, 1.0)])
    assert all("ocr_confidence" not in f for f in r["fields"].values()) and all("ocr_conf" not in i for i in r["line_items"])


def issues(r, **kw):
    return [(i.rule_id, i.field) for i in validate_invoice(r["fields"], r["line_items"], today=date(2025, 1, 1), **kw)]


def test_low_confidence_values_raise_a_warning(monkeypatch):
    r = extract({"SV500114/24": 41.0, "2,020.00": 58.0})
    assert issues(r) == [("VAL-OCR-001", "invoice_number"), ("VAL-OCR-001", "line_items[1]")]
    sev = {i.rule_id: i.severity for i in validate_invoice(r["fields"], r["line_items"], today=date(2025, 1, 1))}
    assert sev["VAL-OCR-001"] == "warning"


def test_threshold_is_configurable_and_zero_disables(monkeypatch):
    r = extract({"SV500114/24": 41.0})
    monkeypatch.setenv("DOCINTEL_OCR_FIELD_MIN_CONF", "40"); assert issues(r) == []
    monkeypatch.setenv("DOCINTEL_OCR_FIELD_MIN_CONF", "0"); assert issues(r) == []
    monkeypatch.setenv("DOCINTEL_OCR_FIELD_MIN_CONF", "90"); assert ("VAL-OCR-001", "invoice_number") in issues(r)


def test_confident_page_raises_nothing():
    assert issues(extract()) == []


def test_end_to_end_flag_persists_and_manual_correction_clears_it(client, tmp_path, monkeypatch):
    low = {"SV500114/24": 33.0, "2,020.00": 44.0}

    def fake_ocr(img):
        return TEXT, 94.0, words_for(TEXT, low)
    monkeypatch.setattr(readers, "ocr_image", fake_ocr)
    p = tmp_path / "scan.png"; Image.new("RGB", (900, 1200), "white").save(p)
    upload(client, p); worker.run_all()
    d = client.get("/api/documents/1").json()
    inv = d["invoices"][0]
    got = {(i["rule_id"], i["field"]) for i in inv["issues"]}
    assert {("VAL-OCR-001", "invoice_number"), ("VAL-OCR-001", "line_items[1]")} <= got and inv["needs_review"]
    assert inv["fields"]["invoice_number"]["ocr_confidence"] == 33.0
    same = client.patch(f"/api/invoices/{inv['id']}", json={"line_items": [{"idx": 1, "amount": "2020.00"}]}).json()
    assert same["changes_applied"] == 0 and ("VAL-OCR-001", "line_items[1]") in {(i["rule_id"], i["field"]) for i in same["issues"]}   # confirming unchanged = use "Mark reviewed"
    # a human fixes the number: its OCR flag disappears, the line-item flag stays until that row is fixed too
    r = client.patch(f"/api/invoices/{inv['id']}", json={"fields": {"invoice_number": "SI/500114/24"}}).json()
    got = {(i["rule_id"], i["field"]) for i in r["issues"]}
    assert ("VAL-OCR-001", "invoice_number") not in got and ("VAL-OCR-001", "line_items[1]") in got
    r = client.patch(f"/api/invoices/{inv['id']}", json={"line_items": [{"idx": 1, "description": "Widget A (verified)"}]}).json()
    assert [i for i in r["issues"] if i["rule_id"] == "VAL-OCR-001"] == []


def test_ocr_warning_can_be_marked_reviewed_and_survives_revalidation(client, tmp_path, monkeypatch):
    monkeypatch.setattr(readers, "ocr_image", lambda img: (TEXT, 94.0, words_for(TEXT, {"SV500114/24": 33.0})))
    p = tmp_path / "scan.png"; Image.new("RGB", (900, 1200), "white").save(p)
    upload(client, p); worker.run_all()
    inv = client.get("/api/documents/1").json()["invoices"][0]
    issue = [i for i in inv["issues"] if i["rule_id"] == "VAL-OCR-001"][0]
    r = client.post(f"/api/issues/{issue['id']}/resolve", json={"resolved": True}).json()
    assert r["needs_review"] is False
    r = client.patch(f"/api/invoices/{inv['id']}", json={"fields": {"customer": "Other Customer"}}).json()
    assert [i["resolved"] for i in r["issues"] if i["rule_id"] == "VAL-OCR-001"] == [True] and r["needs_review"] is False
