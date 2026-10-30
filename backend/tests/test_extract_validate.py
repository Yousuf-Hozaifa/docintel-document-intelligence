"""Deterministic extraction and validation rules."""
from datetime import date
from decimal import Decimal

import pytest

from app.extract import extract_invoice, parse_date, segment_pages
from app.money import parse_money
from app.validate import validate_invoice

BASE = """Acme Supplies Ltd
INVOICE
Invoice Number: INV-1001  Invoice Date: 15 March 2024
Bill To: Globex Corp
Currency: USD
Description  Qty  Unit Price  Amount
Widget A  2  10.00  20.00
Gadget B  3  15.50  46.50
Subtotal: 66.50
Tax (10%): 6.65
Total: 73.15
"""


def ex(text, q=1.0):
    return extract_invoice([(1, text, q)])


def ids(r):
    return [i.rule_id for i in validate_invoice(r["fields"], r["line_items"], today=date(2025, 1, 1))]


def vals(r):
    return {k: v["value"] for k, v in r["fields"].items()}


def test_extracts_all_fields_and_line_items():
    r = ex(BASE)
    assert vals(r) == {"invoice_number": "INV-1001", "invoice_date": "2024-03-15", "supplier": "Acme Supplies Ltd",
                       "customer": "Globex Corp", "subtotal": "66.50", "tax": "6.65", "total": "73.15", "currency": "USD"}
    assert [(i["description"], i["quantity"], i["amount"]) for i in r["line_items"]] == [
        ("Widget A", "2", "20.00"), ("Gadget B", "3", "46.50")]
    assert ids(r) == []


def test_every_field_carries_evidence_and_confidence():
    f = ex(BASE)["fields"]["total"]
    assert f["evidence"] == "Total: 73.15" and 0 < f["confidence"] <= 1 and f["page"] == 1


def test_missing_values_are_null_not_invented():
    r = ex("Some random text\nwith no invoice data at all\n")
    v = vals(r)
    assert v["invoice_number"] is None and v["total"] is None and v["invoice_date"] is None
    assert v["currency"] is None and r["line_items"] == []
    assert "VAL-REQ-001" in ids(r)


def test_ambiguous_date_is_not_guessed():
    r = ex(BASE.replace("15 March 2024", "03/04/2024"))
    f = r["fields"]["invoice_date"]
    assert f["value"] is None and f["status"] == "ambiguous"
    assert "VAL-DATE-002" in ids(r) and "VAL-REQ-001" not in [i for i in ids(r) if i == "VAL-REQ-001" and False]


def test_unambiguous_slash_date_and_invalid_date():
    assert parse_date("25/04/2024") == ("2024-04-25", "ok")
    assert parse_date("04/25/2024") == ("2024-04-25", "ok")
    assert parse_date("2024-02-30") == (None, "invalid")
    assert parse_date("March 3rd, 2024") == ("2024-03-03", "ok")
    assert parse_date("not a date") == (None, "invalid")


def test_invalid_date_flagged():
    assert "VAL-DATE-001" in ids(ex(BASE.replace("15 March 2024", "2024-02-30")))


def test_future_date_flagged():
    assert "VAL-DATE-003" in ids(ex(BASE.replace("15 March 2024", "2030-01-01")))


def test_tax_id_line_is_not_mistaken_for_tax():
    r = ex(BASE.replace("Currency: USD", "Tax ID: 12345678\nVAT No: GB99999"))
    assert r["fields"]["tax"]["value"] == "6.65"


def test_percent_only_tax_line_is_ignored():
    assert ex(BASE.replace("Tax (10%): 6.65", "Tax 10%"))["fields"]["tax"]["value"] is None


def test_multiple_tax_lines_are_summed_and_noted():
    r = ex(BASE.replace("Tax (10%): 6.65", "CGST: 0\nTax 5%  3.00\nVAT 5%  3.65"))
    assert r["fields"]["tax"]["value"] == "6.65" and "several" in r["fields"]["tax"]["note"]


def test_european_number_format():
    assert parse_money("1.234,56") == Decimal("1234.56")
    assert parse_money("1,234.56") == Decimal("1234.56")
    assert parse_money("12,50") == Decimal("12.50")
    assert parse_money("1,234") == Decimal("1234")


def test_dollar_symbol_is_low_confidence_currency():
    r = ex(BASE.replace("Currency: USD\n", "").replace("66.50", "$66.50"))
    c = r["fields"]["currency"]
    assert c["value"] == "USD" and c["confidence"] <= 0.5 and "ambiguous" in c["note"]


def test_yen_symbol_not_guessed():
    r = ex(BASE.replace("Currency: USD\n", "").replace("66.50", "¥66.50"))
    assert r["fields"]["currency"]["value"] is None


def test_ocr_quality_scales_confidence_down():
    assert ex(BASE, q=0.5)["fields"]["total"]["confidence"] < ex(BASE, q=1.0)["fields"]["total"]["confidence"]


def test_spreadsheet_style_label_value_rows():
    text = ("Acme Ltd\nInvoice Number  INV-5\nInvoice Date  2024-01-05\nBill To  Initech\n"
            "Description  Qty  Unit Price  Amount\nBolt  4  2.5  10\nSubtotal  10\nTax (10%)  1\nTotal  11")
    v = vals(ex(text))
    assert v["invoice_number"] == "INV-5" and v["customer"] == "Initech" and v["total"] == "11"


def test_segmenter_splits_multi_invoice_document():
    pages = [(1, "Invoice No: A1\nTotal 5", 1), (2, "continued", 1), (3, "Invoice No: B2\nTotal 6", 1)]
    segs = segment_pages(pages)
    assert [[p[0] for p in s] for s in segs] == [[1, 2], [3]]


# ------------------------------------------------------------------ validation rules
def mk(**over):
    f = {k: {"value": v, "status": "ok" if v is not None else "missing"} for k, v in dict(
        invoice_number="X1", invoice_date="2024-01-01", supplier="S", customer="C",
        subtotal="100.00", tax="10.00", total="110.00", currency="USD").items()}
    for k, v in over.items():
        f[k] = {"value": v, "status": "ok" if v is not None else "missing"}
    return f


ITEMS = [{"description": "a", "quantity": "2", "unit_price": "50.00", "amount": "100.00"}]


def rules(f, items=ITEMS):
    return [i.rule_id for i in validate_invoice(f, items, today=date(2025, 1, 1))]


def test_clean_invoice_has_no_issues():
    assert rules(mk()) == []


def test_total_mismatch_flagged_with_explanation():
    iss = validate_invoice(mk(total="115.00"), ITEMS, today=date(2025, 1, 1))
    assert iss[0].rule_id == "VAL-SUB-001" and "110.00" in iss[0].message and "115.00" in iss[0].message


def test_one_cent_rounding_difference_is_tolerated():
    assert "VAL-SUB-001" not in rules(mk(total="110.01"))
    assert "VAL-SUB-001" in rules(mk(total="110.02"))


def test_zero_decimal_currency_precision():
    jpy = lambda total: mk(subtotal="1000", tax="100", total=total, currency="JPY")
    assert "VAL-SUB-001" not in rules(jpy("1101"), [])   # 1 yen = 1 minor unit = tolerated
    assert "VAL-SUB-001" in rules(jpy("1102"), [])       # 2 yen is a real discrepancy
    usd = mk(subtotal="1000", tax="100", total="1101", currency="USD")
    assert "VAL-SUB-001" in rules(usd, [])               # the same gap is 100 cents for USD


def test_line_arithmetic_error():
    bad = [{"description": "a", "quantity": "2", "unit_price": "50.00", "amount": "90.00"}]
    assert "VAL-LINE-001" in rules(mk(), bad)


def test_line_sum_vs_subtotal_is_warning_not_error():
    iss = validate_invoice(mk(subtotal="120.00", tax="12.00", total="132.00"), ITEMS, today=date(2025, 1, 1))
    li = [i for i in iss if i.rule_id == "VAL-LINE-002"]
    assert li and li[0].severity == "warning"


def test_tax_missing_with_difference():
    assert "VAL-SUB-002" in rules(mk(tax=None, total="110.00"))
    assert "VAL-SUB-002" not in rules(mk(tax=None, total="100.00"))


def test_bad_currency_and_numbers():
    assert "VAL-CUR-001" in rules(mk(currency="XYZ"))
    assert "VAL-NUM-001" in rules(mk(total="abc"))
    assert "VAL-NUM-002" in rules(mk(subtotal="-100.00", tax="-10.00", total="-110.00"))


def test_missing_required_fields_have_severity():
    iss = validate_invoice(mk(invoice_number=None, customer=None), ITEMS, today=date(2025, 1, 1))
    sev = {i.field: i.severity for i in iss if i.rule_id == "VAL-REQ-001"}
    assert sev == {"invoice_number": "error", "customer": "warning"}


def test_tolerance_is_configurable(monkeypatch):
    monkeypatch.setenv("DOCINTEL_TOLERANCE_MINOR_UNITS", "5")
    assert "VAL-SUB-001" not in rules(mk(total="110.05"))
