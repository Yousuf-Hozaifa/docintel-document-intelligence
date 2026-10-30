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

    @staticmethod
    def q(d: Decimal) -> Decimal:
        return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def amounts(self):
        return [self.q(Decimal(q) * u) for _, q, u in self.items]

    @property
    def subtotal(self): return sum(self.amounts, Decimal(0))
    @property
    def tax(self): return self.q(self.subtotal * self.tax_rate)
    @property
    def total(self): return self.subtotal + self.tax

    def truth(self) -> dict:
        return {"invoice_number": self.number, "invoice_date": self.date_iso, "supplier": self.supplier,
                "customer": self.customer, "subtotal": str(self.subtotal), "tax": str(self.tax),
                "total": str(self.total), "currency": self.currency,
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
    def t(x, y, txt, size=10, bold=False):
        page.insert_text((x, y), txt, fontsize=size, fontname="hebo" if bold else "helv")
    y = 60
    t(50, y, s.supplier, 16, True); y += 24
    t(50, y, "INVOICE", 12, True); y += 24
    t(50, y, f"Invoice Number: {s.number}"); t(330, y, f"Invoice Date: {s.date_text or s.date_iso}"); y += 20
    t(50, y, f"Bill To: {s.customer}"); y += 20
    t(50, y, f"Currency: {s.currency}"); y += 30
    for x, h in ((50, "Description"), (300, "Qty"), (360, "Unit Price"), (460, "Amount")):
        t(x, y, h, 10, True)
    y += 18
    for (d, q, u), a in zip(s.items, s.amounts):
        t(50, y, d); t(300, y, str(q)); t(360, y, f"{u:.2f}"); t(460, y, f"{a:.2f}"); y += 18
    y += 12
    for label, v in (("Subtotal:", s.subtotal), (f"Tax ({int(s.tax_rate * 100)}%):", s.tax), ("Total:", s.total)):
        t(360, y, label, 10, label == "Total:"); t(460, y, f"{v:.2f}", 10, label == "Total:"); y += 18


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


def write_image(s: Spec, path: Path, degrade: str | None = None) -> Path:
    img = render_image(s, degrade=degrade)
    img.save(path, quality=85 if path.suffix.lower() in (".jpg", ".jpeg") else None)
    return path


def write_tiff(specs: list[Spec], path: Path) -> Path:
    imgs = [render_image(s) for s in specs]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], compression="tiff_lzw")
    return path


def write_scanned_pdf(specs: list[Spec], path: Path, degrade: str | None = None) -> Path:
    """Image-only PDF (no text layer): forces the OCR path."""
    doc = pymupdf.open()
    for s in specs:
        buf = io.BytesIO()
        render_image(s, degrade=degrade).save(buf, format="JPEG", quality=80)
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
    rows = [[s.supplier], ["Invoice Number", s.number], ["Invoice Date", s.date_text or s.date_iso],
            ["Bill To", s.customer], ["Currency", s.currency], [],
            ["Description", "Qty", "Unit Price", "Amount"]]
    rows += [[d, q, float(u), float(a)] for (d, q, u), a in zip(s.items, s.amounts)]
    rows += [[], ["Subtotal", None, None, float(s.subtotal)], [f"Tax ({int(s.tax_rate * 100)}%)", None, None, float(s.tax)],
             ["Total", None, None, float(s.total)]]
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
