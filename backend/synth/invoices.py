"""Synthetic labeled invoices. One Spec -> many file formats, with exact ground truth.
All values are generated, never copied from real documents."""
import io
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter

SUPPLIERS = ["Acme Supplies Ltd", "Northwind Traders", "Blue Harbor Logistics", "Sunrise Office Co",
             "Delta Precision Parts", "Kestrel Cloud Services", "Maple Leaf Stationers", "Orion Medical Supply"]
CUSTOMERS = ["Globex Corporation", "Initech Holdings", "Umbrella Retail", "Stark Manufacturing",
             "Wayne Enterprises", "Hooli Networks", "Vandelay Imports", "Soylent Foods"]
ITEMS = ["Widget A", "Gadget B", "Consulting hour", "Cable bundle", "Printer toner", "Server rack",
         "Support plan", "Safety gloves", "Shipping crate", "Laptop stand", "Calibration service"]


SYMBOLS = {"USD": "$", "GBP": "£"}   # the built-in PDF font cannot render the euro sign


@dataclass
class Spec:
    number: str
    date_iso: str
    supplier: str
    customer: str
    currency: str
    items: list = field(default_factory=list)   # (description, qty:int, unit:Decimal)
    tax_rate: Decimal = Decimal("0.10")
    date_text: str | None = None                # how the date is printed (defaults to ISO)
    # ---- evaluation variants (defaults reproduce the original layout exactly)
    layout: str = "A"                           # A | B | C
    omit: frozenset = frozenset()               # subset of {"customer","tax","date","number","currency"}
    currency_style: str = "code"                # code | symbol (only $ and £ render) | none
    thousands: bool = False                     # print 1,234.50 instead of 1234.50
    date_ambiguous: bool = False                # printed like 03/04/2024: truth value is null
    total_override: Decimal | None = None       # printed total differs from subtotal + tax (tampered)
    line_override: dict = field(default_factory=dict)   # {index: printed amount} (line arithmetic error)

    @staticmethod
    def q(d: Decimal) -> Decimal:
        return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def amounts(self):
        base = [self.q(Decimal(q) * u) for _, q, u in self.items]
        return [self.line_override.get(i, a) for i, a in enumerate(base)]

    @property
    def subtotal(self): return sum(self.amounts, Decimal(0))
    @property
    def tax(self): return self.q(self.subtotal * self.tax_rate)
    @property
    def total(self):
        if self.total_override is not None:
            return self.total_override
        return self.subtotal if "tax" in self.omit else self.subtotal + self.tax

    def fmt(self, v: Decimal, symbol: bool = False) -> str:
        t = f"{v:,.2f}" if self.thousands else f"{v:.2f}"
        return (SYMBOLS.get(self.currency, "") + t) if (symbol and self.currency_style == "symbol") else t

    def truth(self) -> dict:
        cur = None if self.currency_style == "none" else self.currency
        return {"invoice_number": None if "number" in self.omit else self.number,
                "invoice_date": None if ("date" in self.omit or self.date_ambiguous) else self.date_iso,
                "supplier": self.supplier, "customer": None if "customer" in self.omit else self.customer,
                "subtotal": str(self.subtotal), "tax": None if "tax" in self.omit else str(self.tax),
                "total": str(self.total), "currency": cur,
                "line_items": [{"description": d, "quantity": str(q), "unit_price": str(u), "amount": str(a)}
                               for (d, q, u), a in zip(self.items, self.amounts)]}


def random_spec(rng: random.Random, n: int, n_items: int | None = None) -> Spec:
    items = [(rng.choice(ITEMS), rng.randint(1, 9), Decimal(rng.randint(500, 99900)) / 100)
             for _ in range(n_items or rng.randint(2, 6))]
    return Spec(number=f"INV-{2024000 + n}", date_iso=f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                supplier=rng.choice(SUPPLIERS), customer=rng.choice(CUSTOMERS),
                currency=rng.choice(["USD", "EUR", "GBP"]), items=items,
                tax_rate=rng.choice([Decimal("0.05"), Decimal("0.10"), Decimal("0.15"), Decimal("0.20")]))


# ------------------------------------------------------------------ renderers
def _draw_invoice(page: "pymupdf.Page", s: Spec) -> None:
    {"A": _draw_a, "B": _draw_b, "C": _draw_c}[s.layout](page, s)


def _t(page, x, y, txt, size=10, bold=False):
    page.insert_text((x, y), txt, fontsize=size, fontname="hebo" if bold else "helv")


def _cur_line(s: Spec) -> str | None:
    return None if (s.currency_style != "code" or "currency" in s.omit) else s.currency


def _rows(page, s: Spec, y: int, xs) -> int:
    for (d, q, u), a in zip(s.items, s.amounts):
        _t(page, xs[0], y, d); _t(page, xs[1], y, str(q))
        _t(page, xs[2], y, s.fmt(u, True)); _t(page, xs[3], y, s.fmt(a, True)); y += 18
    return y + 12


def _draw_a(page, s: Spec) -> None:
    y = 60
    _t(page, 50, y, s.supplier, 16, True); y += 24
    _t(page, 50, y, "INVOICE", 12, True); y += 24
    if "number" not in s.omit: _t(page, 50, y, f"Invoice Number: {s.number}")
    if "date" not in s.omit: _t(page, 330, y, f"Invoice Date: {s.date_text or s.date_iso}")
    y += 20
    if "customer" not in s.omit: _t(page, 50, y, f"Bill To: {s.customer}"); y += 20
    if _cur_line(s): _t(page, 50, y, f"Currency: {_cur_line(s)}")
    y += 30
    for x, h in ((50, "Description"), (300, "Qty"), (360, "Unit Price"), (460, "Amount")): _t(page, x, y, h, 10, True)
    y = _rows(page, s, y + 18, (50, 300, 360, 460))
    _t(page, 360, y, "Subtotal:"); _t(page, 460, y, s.fmt(s.subtotal, True)); y += 18
    if "tax" not in s.omit: _t(page, 360, y, f"Tax ({int(s.tax_rate * 100)}%):"); _t(page, 460, y, s.fmt(s.tax, True)); y += 18
    _t(page, 360, y, "Total:", 10, True); _t(page, 460, y, s.fmt(s.total, True), 10, True)


def _draw_b(page, s: Spec) -> None:          # supplier is only the first line (no label); "Inv #", "Sold To", "VAT", "Amount Due"
    y = 60
    _t(page, 50, y, s.supplier, 16, True); _t(page, 430, y, "INVOICE", 14, True); y += 26
    if "number" not in s.omit: _t(page, 50, y, f"Inv #: {s.number}")
    if "date" not in s.omit: _t(page, 330, y, f"Date: {s.date_text or s.date_iso}")
    y += 20
    if "customer" not in s.omit: _t(page, 50, y, f"Sold To: {s.customer}"); y += 20
    y += 16
    for x, h in ((50, "Item"), (300, "Qty"), (360, "Rate"), (460, "Total")): _t(page, x, y, h, 10, True)
    y = _rows(page, s, y + 18, (50, 300, 360, 460))
    _t(page, 360, y, "Subtotal"); _t(page, 460, y, s.fmt(s.subtotal, True)); y += 18
    if "tax" not in s.omit: _t(page, 360, y, f"VAT ({int(s.tax_rate * 100)}%)"); _t(page, 460, y, s.fmt(s.tax, True)); y += 18
    _t(page, 360, y, "Amount Due", 10, True); _t(page, 460, y, s.fmt(s.total, True), 10, True); y += 30
    if _cur_line(s): _t(page, 50, y, f"All amounts in {_cur_line(s)}")


def _draw_c(page, s: Spec) -> None:          # labels on their own lines, "Vendor:", "Date of Issue", "GST", "Total Amount"
    y = 60
    _t(page, 50, y, "TAX INVOICE", 14, True); y += 24
    if "number" not in s.omit: _t(page, 50, y, f"Invoice No.: {s.number}"); y += 16
    if "date" not in s.omit: _t(page, 50, y, f"Date of Issue: {s.date_text or s.date_iso}"); y += 16
    _t(page, 50, y, f"Vendor: {s.supplier}"); y += 16
    if "customer" not in s.omit: _t(page, 50, y, "Bill To:"); _t(page, 62, y + 14, s.customer); y += 30
    y += 12
    for x, h in ((50, "Description"), (260, "Quantity"), (340, "Unit Price"), (450, "Line Total")): _t(page, x, y, h, 10, True)
    y = _rows(page, s, y + 18, (50, 260, 340, 450))
    _t(page, 340, y, "Sub Total"); _t(page, 450, y, s.fmt(s.subtotal, True)); y += 18
    if "tax" not in s.omit: _t(page, 340, y, f"GST ({int(s.tax_rate * 100)}%)"); _t(page, 450, y, s.fmt(s.tax, True)); y += 18
    _t(page, 340, y, "Total Amount", 10, True); _t(page, 450, y, s.fmt(s.total, True), 10, True); y += 30
    if _cur_line(s): _t(page, 50, y, f"Currency: {_cur_line(s)}")


def write_pdf(s: Spec, path: Path) -> Path:
    doc = pymupdf.open()
    _draw_invoice(doc.new_page(), s)
    doc.save(path); doc.close()
    return path


def render_image(s: Spec, dpi: int = 150, degrade: str | None = None) -> Image.Image:
    doc = pymupdf.open()
    _draw_invoice(doc.new_page(), s)
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    if degrade == "blur":
        img = img.filter(ImageFilter.GaussianBlur(1.2))
    elif degrade == "lowres":
        img = img.resize((img.width // 2, img.height // 2))
    elif degrade == "rotate":
        img = img.rotate(2, expand=True, fillcolor="white")
    return img


def write_image(s: Spec, path: Path, degrade: str | None = None, dpi: int = 150) -> Path:
    img = render_image(s, dpi=dpi, degrade=degrade)
    img.save(path, quality=85 if path.suffix.lower() in (".jpg", ".jpeg") else None)
    return path


def write_tiff(specs: list[Spec], path: Path, dpi: int = 150) -> Path:
    imgs = [render_image(s, dpi=dpi) for s in specs]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], compression="tiff_lzw")
    return path


def write_scanned_pdf(specs: list[Spec], path: Path, degrade: str | None = None, dpi: int = 150) -> Path:
    """Image-only PDF (no text layer): forces the OCR path."""
    doc = pymupdf.open()
    for s in specs:
        buf = io.BytesIO()
        render_image(s, dpi=dpi, degrade=degrade).save(buf, format="JPEG", quality=75)
        p = doc.new_page()
        p.insert_image(p.rect, stream=buf.getvalue())
    doc.save(path); doc.close()
    return path


def write_digital_pdf(specs: list[Spec], path: Path) -> Path:
    doc = pymupdf.open()
    for s in specs:
        _draw_invoice(doc.new_page(), s)
    doc.save(path); doc.close()
    return path


def sheet_rows(s: Spec) -> list[list]:
    alt = s.layout == "B"
    L = {"num": "Invoice #" if alt else "Invoice Number", "date": "Date" if alt else "Invoice Date", "cust": "Customer" if alt else "Bill To",
         "sup": "Vendor" if alt else None, "item": ("Item", "Qty", "Price", "Amount") if alt else ("Description", "Qty", "Unit Price", "Amount"),
         "sub": "Net" if False else "Subtotal", "tax": "VAT" if alt else f"Tax ({int(s.tax_rate * 100)}%)", "tot": "Grand Total" if alt else "Total"}
    rows = [[f"Vendor", s.supplier] if L["sup"] else [s.supplier]]
    if "number" not in s.omit: rows.append([L["num"], s.number])
    if "date" not in s.omit: rows.append([L["date"], s.date_text or s.date_iso])
    if "customer" not in s.omit: rows.append([L["cust"], s.customer])
    if s.currency_style != "none" and "currency" not in s.omit: rows.append(["Currency", s.currency])   # sheets have no symbols: always print the code
    rows += [[], list(L["item"])]
    rows += [[d, q, float(u), float(a)] for (d, q, u), a in zip(s.items, s.amounts)]
    rows += [[], [L["sub"], None, None, float(s.subtotal)]]
    if "tax" not in s.omit: rows.append([L["tax"], None, None, float(s.tax)])
    rows.append([L["tot"], None, None, float(s.total)])
    return rows


def write_xlsx(specs: list[Spec], path: Path) -> Path:
    import openpyxl
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for i, s in enumerate(specs, 1):
        ws = wb.create_sheet(f"Invoice{i}")
        for r in sheet_rows(s):
            ws.append(r)
    wb.save(path)
    return path


def write_xls(specs: list[Spec], path: Path) -> Path:
    import xlwt
    wb = xlwt.Workbook()
    for i, s in enumerate(specs, 1):
        ws = wb.add_sheet(f"Invoice{i}")
        for ri, r in enumerate(sheet_rows(s)):
            for ci, v in enumerate(r):
                if v is not None:
                    ws.write(ri, ci, v)
    wb.save(str(path))
    return path


def write_csv(s: Spec, path: Path, delimiter: str = ",") -> Path:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=delimiter)
        for r in sheet_rows(s):
            w.writerow(["" if v is None else v for v in r])
    return path


_WORDS = ("account balance statement payment received remittance reference purchase order delivery goods services "
          "quarterly review adjustment credit note terms net thirty days warehouse shipment carrier customs duty "
          "contract amendment schedule appendix approved reconciled outstanding supplier ledger entry posted").split()


def text_page_lines(rng: random.Random, n_lines: int = 52, words_per_line: int = 14) -> list[str]:
    return [" ".join(rng.choice(_WORDS) for _ in range(words_per_line)) for _ in range(n_lines)]


def write_text_pdf(n_pages: int, path: Path, scanned: bool, dpi: int = 200, seed: int = 0) -> Path:
    """Text-heavy pages (~52 lines of 10pt prose): a stress case denser than a typical invoice."""
    rng = random.Random(seed)
    doc = pymupdf.open()
    for n in range(n_pages):
        lines = [f"Invoice No: TXT-{n + 1:05d}"] + text_page_lines(rng)
        page = doc.new_page()
        for i, ln in enumerate(lines):
            page.insert_text((40, 40 + i * 14), ln, fontsize=9)
        if scanned:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            buf = io.BytesIO(); img.save(buf, format="JPEG", quality=75)
            doc.delete_page(len(doc) - 1)
            p2 = doc.new_page(); p2.insert_image(p2.rect, stream=buf.getvalue())
    doc.save(path, garbage=1, deflate=True); doc.close()
    return path


def write_register_xlsx(specs: list[Spec], path: Path, totals: dict | None = None, header: int = 0) -> Path:
    """One invoice per row (a ledger / register). `totals` overrides the printed total for given invoice numbers."""
    import openpyxl
    heads = [["Invoice No", "Date", "Supplier", "Customer", "Subtotal", "Tax", "Total", "Currency"],
             ["Invoice Number", "Invoice Date", "Vendor", "Customer Name", "Net Amount", "VAT", "Grand Total", "Currency"]][header]
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Register"
    ws.append(["Invoice register"]); ws.append([]); ws.append(heads)
    for s in specs:
        tot = (totals or {}).get(s.number, s.total)
        ws.append([s.number, s.date_iso, s.supplier, s.customer, float(s.subtotal), float(s.tax), float(tot), s.currency])
    wb.save(path)
    return path
