"""Deterministic (rule-based) invoice extractor — the reliable, LLM-free baseline.

Confidence values here are HEURISTIC (rule strength x OCR quality). They are NOT
empirically calibrated probabilities. Missing / ambiguous values are returned as None
with an explanatory note; nothing is guessed."""
import re
from datetime import datetime
from decimal import Decimal

from .money import ISO_4217, MONEY_RE, SYMBOLS, last_money, parse_money

EXTRACTOR = "rules-v1"
FIELDS = ["invoice_number", "invoice_date", "supplier", "customer",
          "subtotal", "tax", "total", "currency"]

INV_NO_RE = re.compile(
    r"\binv(?:oice)?\s*(?:no\.?|number|num|#)\s*[:#\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/_.]*)"
    r"|\binvoice\s*[:#]\s*([A-Za-z0-9][A-Za-z0-9\-/_.]*)", re.I)
DATE_LABEL_RE = re.compile(
    r"^\s*(?:invoice\s+date|date\s+of\s+issue|issue(?:d)?\s+date|date)\s*[:\-]?\s*(.*)$", re.I)
SUPPLIER_RE = re.compile(
    r"^\s*(?:(?:supplier|vendor|seller|issued\s+by)(?:\s+name)?|from)\s*(?:[:\-]\s*|$)(.*)$", re.I)
CUSTOMER_RE = re.compile(
    r"^\s*(?:(?:bill(?:ed)?|sold)\s+to\s*[:\-]?\s*|(?:customer(?:\s+name)?|client|buyer)"
    r"(?!\s*(?:id\b|no\b|number|ref|#))\s*(?:[:\-]\s*|$))(.*)$", re.I)
SUBTOTAL_RE = re.compile(r"^\s*sub[\s\-]?total\b", re.I)
TAX_RE = re.compile(r"^\s*(?:(?:sales|total|state|service)\s+)?(?:tax|vat|gst|hst)\b"
                    r"(?!\s*(?:id|no\b|no\.|number|reg))", re.I)
TOTAL_RE = re.compile(r"^\s*(?:grand\s+total|total\s+due|amount\s+due|balance\s+due|"
                      r"total\s+amount|amount\s+payable|total)\b", re.I)
CUR_LABEL_RE = re.compile(r"\bcurrency\s*[:\-]?\s*([A-Za-z]{3})\b", re.I)
CUR_CODE_RE = re.compile(r"\b(" + "|".join(sorted(ISO_4217)) + r")\b")
N = rf"(?:[{SYMBOLS}]\s?)?-?\d[\d,]*(?:\.\d+)?"
ITEM_RE = re.compile(rf"^(?P<d>.*?\S)\s+(?P<q>-?\d+(?:\.\d+)?)\s+(?P<u>{N})\s+(?P<a>{N})\s*$")
ITEM_HEADER_RE = re.compile(r"\b(description|item|product|service)\b.*\b(qty|quantity|price|amount|rate)\b", re.I)
SYMBOL_CUR = {"€": "EUR", "£": "GBP", "৳": "BDT", "₹": "INR"}

_FMTS = ["%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
         "%d-%b-%Y", "%d-%B-%Y", "%d %B, %Y", "%d %b, %Y"]


def _field(value, conf, evidence, page, note=None, status=None, raw=None):
    return {"value": value, "confidence": round(conf, 2) if value is not None else 0.0,
            "evidence": evidence, "page": page, "note": note,
            "status": status or ("ok" if value is not None else "missing"), "raw": raw}


def parse_date(raw: str) -> tuple[str | None, str]:
    """Return (ISO date | None, status) with status ok | ambiguous | invalid."""
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw.strip().rstrip(".,"), flags=re.I)
    s = re.split(r"\s{2,}", s)[0]
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", s)
    try:
        if m:
            return datetime(int(m[1]), int(m[2]), int(m[3])).date().isoformat(), "ok"
        m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b", s)
        if m:
            a, b, y = int(m[1]), int(m[2]), int(m[3])
            y = y + 2000 if y < 100 else y
            if a > 12 >= b:
                d, mo = a, b
            elif b > 12 >= a or a == b:
                d, mo = (a, b) if a == b else (b, a)
            elif a <= 12 and b <= 12:
                return None, "ambiguous"
            else:
                return None, "invalid"
            return datetime(y, mo, d).date().isoformat(), "ok"
    except ValueError:
        return None, "invalid"
    for f in _FMTS:
        try:
            return datetime.strptime(s, f).date().isoformat(), "ok"
        except ValueError:
            pass
    return None, "invalid"


def find_invoice_number(text: str) -> str | None:
    for line in text.splitlines():
        m = INV_NO_RE.search(line)
        if m:
            tok = (m.group(1) or m.group(2)).rstrip(".")
            if any(c.isdigit() for c in tok):
                return tok
    return None


def segment_pages(pages: list[tuple[int, str, float]]) -> list[list[tuple[int, str, float]]]:
    """Split a multi-page document into per-invoice page groups. A new invoice starts on a
    page that carries a different invoice number than the current group."""
    segs: list[list] = []
    cur_no = None
    for p in pages:
        no = find_invoice_number(p[1])
        if not segs or (no and cur_no and no != cur_no):
            segs.append([])
        if no and no != cur_no:
            cur_no = no
        segs[-1].append(p)
    return segs


def _candidates(line: str):
    """The whole line, then each suffix that starts after a column gap (two-column layouts)."""
    yield line
    for m in re.finditer(r"\s{2,}", line):
        tail = line[m.end():]
        if tail:
            yield tail


def _next_value(lines, i):
    for j in range(i + 1, min(i + 3, len(lines))):
        v = re.split(r"\s{2,}", lines[j][1].strip())[0]
        if v:
            return v, lines[j]
    return None, None


def extract_invoice(pages: list[tuple[int, str, float]]) -> dict:
    """pages: (page_no, text, quality_factor 0..1). Returns {fields, line_items, extractor}."""
    lines = [(pn, ln.strip(), q) for pn, txt, q in pages for ln in txt.splitlines() if ln.strip()]
    F: dict[str, dict] = {}

    # invoice number
    F["invoice_number"] = _field(None, 0, None, None)
    for pn, ln, q in lines:
        m = INV_NO_RE.search(ln)
        if m:
            tok = (m.group(1) or m.group(2)).rstrip(".")
            if any(c.isdigit() for c in tok):
                F["invoice_number"] = _field(tok, 0.9 * q, ln, pn)
                break

    # invoice date
    F["invoice_date"] = _field(None, 0, None, None)
    for pn, ln, q in lines:
        hit = None
        for cand in _candidates(ln):
            m = DATE_LABEL_RE.match(cand)
            if m and not re.match(r"^\s*(due|payment)", cand, re.I) and m.group(1).strip():
                hit = m
                break
        if hit:
            raw = re.split(r"\s{2,}", hit.group(1).strip())[0]
            iso, status = parse_date(raw)
            if iso:
                F["invoice_date"] = _field(iso, 0.9 * q, ln, pn, raw=raw)
            else:
                note = ("Day/month order is ambiguous; not guessed" if status == "ambiguous"
                        else "Date text could not be parsed")
                F["invoice_date"] = _field(None, 0, ln, pn, note=note, status=status, raw=raw)
            break

    # supplier / customer (labelled; supplier falls back to first line)
    for name, rx in (("supplier", SUPPLIER_RE), ("customer", CUSTOMER_RE)):
        F[name] = _field(None, 0, None, None)
        for i, (pn, ln, q) in enumerate(lines):
            m = next((mm for c in _candidates(ln) if (mm := rx.match(c))), None)
            if not m:
                continue
            v = re.split(r"\s{2,}", m.group(1).strip())[0]
            ev = ln
            if not v:
                v, nxt = _next_value(lines, i)
                ev = f"{ln} / {nxt[1]}" if nxt else ln
            if v:
                F[name] = _field(v, 0.9 * q, ev, pn)
                break
    if F["supplier"]["value"] is None and lines:
        pn, ln, q = lines[0]
        first = re.split(r"\s{2,}", ln)[0]
        if (re.search(r"[A-Za-z]{3}", first) and not re.search(r"invoice|receipt|statement|^page\b", first, re.I)
                and sum(c.isdigit() for c in first) < len(first) / 3):
            F["supplier"] = _field(first, 0.4 * q, ln, pn, note="Positional guess: first line of document")

    # totals
    sub = tax_total = total = None
    tax_ev, tax_pages = [], []
    for pn, ln, q in lines:
        lm = last_money(ln)
        if not lm:
            continue
        if TAX_RE.match(ln):
            tax_total = (tax_total or Decimal(0)) + lm[0]
            tax_ev.append(ln)
            tax_pages.append((pn, q))
        elif SUBTOTAL_RE.match(ln):
            sub = (lm[0], ln, pn, q)
        elif TOTAL_RE.match(ln):
            total = (lm[0], ln, pn, q)
    F["subtotal"] = _field(str(sub[0]), 0.9 * sub[3], sub[1], sub[2]) if sub else _field(None, 0, None, None)
    F["total"] = _field(str(total[0]), 0.9 * total[3], total[1], total[2]) if total else _field(None, 0, None, None)
    if tax_ev:
        note = "Sum of several tax lines" if len(tax_ev) > 1 else None
        F["tax"] = _field(str(tax_total), 0.85 * tax_pages[0][1], " ; ".join(tax_ev), tax_pages[0][0], note=note)
    else:
        F["tax"] = _field(None, 0, None, None)

    # currency
    full = "\n".join(t for _, t, _ in pages)
    F["currency"] = _field(None, 0, None, None)
    m = CUR_LABEL_RE.search(full)
    if m:
        F["currency"] = _field(m.group(1).upper(), 0.95, m.group(0), None)
    else:
        codes = CUR_CODE_RE.findall(full)
        if codes:
            best = max(set(codes), key=codes.count)
            multi = len(set(codes)) > 1
            F["currency"] = _field(best, 0.5 if multi else 0.9, best, None,
                                   note="Multiple currency codes present" if multi else None)
        else:
            for ch in SYMBOLS:
                if ch in full:
                    if ch == "$":
                        F["currency"] = _field("USD", 0.5, "$", None, note="'$' symbol is ambiguous (USD assumed)")
                    elif ch == "¥":
                        F["currency"] = _field(None, 0, "¥", None, note="'¥' is ambiguous (JPY or CNY); not guessed",
                                               status="ambiguous")
                    else:
                        F["currency"] = _field(SYMBOL_CUR[ch], 0.7, ch, None, note=f"Inferred from '{ch}' symbol")
                    break

    # line items
    items, in_items = [], False
    for pn, ln, q in lines:
        if not in_items:
            in_items = bool(ITEM_HEADER_RE.search(ln))
            continue
        if SUBTOTAL_RE.match(ln) or TAX_RE.match(ln) or TOTAL_RE.match(ln):
            in_items = False
            continue
        m = ITEM_RE.match(ln)
        if m:
            items.append({"description": m["d"].strip(), "quantity": str(parse_money(m["q"])),
                          "unit_price": str(parse_money(m["u"])), "amount": str(parse_money(m["a"])),
                          "evidence": ln, "page": pn})
    return {"fields": F, "line_items": items, "extractor": EXTRACTOR}
