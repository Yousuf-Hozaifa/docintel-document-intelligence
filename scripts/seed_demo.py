"""Create demo_files/ (deterministic synthetic invoices) for the recorded walkthrough:  python scripts/seed_demo.py"""
import copy, random, shutil, sys
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import pymupdf
from synth import invoices as S

OUT = ROOT / "demo_files"
if OUT.exists(): shutil.rmtree(OUT)
OUT.mkdir()
rng = random.Random(2024)

def spec(n, layout, items, supplier, customer, cur="USD", **kw):
    s = S.Spec(number=f"INV-2024-{n:04d}", date_iso=f"2024-0{rng.randint(3, 9)}-{rng.randint(10, 28)}", supplier=supplier, customer=customer, currency=cur,
               items=[(d, q, Decimal(u)) for d, q, u in items], layout=layout, thousands=True, **kw)
    return s

a = spec(1001, "B", [("Server rack", 2, "1450.00"), ("Support plan", 12, "89.50"), ("Cable bundle", 5, "42.00")], "Kestrel Cloud Services", "Globex Corporation")
b = spec(1002, "A", [("Consulting hour", 40, "125.00"), ("Calibration service", 3, "310.00")], "Northwind Traders", "Initech Holdings")
c = spec(1003, "C", [("Printer toner", 6, "74.90"), ("Laptop stand", 4, "39.00")], "Maple Leaf Stationers", "Umbrella Retail", cur="GBP", currency_style="symbol")
t = spec(1004, "A", [("Safety gloves", 50, "6.40"), ("Shipping crate", 8, "55.00")], "Orion Medical Supply", "Wayne Enterprises")
t.total_override = t.subtotal + t.tax + Decimal("250.00")             # printed total is wrong by 250.00
d = spec(1005, "A", [("Widget A", 10, "19.99"), ("Gadget B", 3, "249.00")], "Blue Harbor Logistics", "Hooli Networks")
e = spec(1006, "B", [("Cable bundle", 25, "12.50")], "Delta Precision Parts", "Soylent Foods")

S.write_pdf(a, OUT / "01_clean_invoice.pdf")
S.write_scanned_pdf([b], OUT / "02_scanned_invoice.pdf", dpi=200)
S.write_image(c, OUT / "03_photo_invoice.jpg", dpi=200)
S.write_pdf(t, OUT / "04_tampered_total.pdf")
dup = S.Spec(**{**a.__dict__, "layout": "C"}); S.write_pdf(dup, OUT / "05_duplicate_of_01.pdf")
reg_rows = [a, t, d, e]
S.write_register_xlsx(reg_rows, OUT / "06_invoice_register.xlsx", totals={t.number: t.subtotal + t.tax}, header=0)   # register has the CORRECT total for 04
f = spec(1009, "A", [("Calibration service", 2, "310.00"), ("Support plan", 6, "89.50")], "Sunrise Office Co", "Vandelay Imports")
g = spec(1010, "C", [("Laptop stand", 12, "39.00")], "Acme Supplies Ltd", "Initech Holdings", cur="EUR")
S.write_xlsx([f, g], OUT / "07_two_sheet_workbook.xlsx")
S.write_csv(S.Spec(**{**b.__dict__, "number": "INV-2024-1007", "layout": "A"}), OUT / "08_vendor_export.csv", delimiter=";")
lq = spec(1008, "A", [("Cable bundle", 25, "12.50"), ("Support plan", 4, "89.50"), ("Laptop stand", 6, "39.00")], "Delta Precision Parts", "Soylent Foods")
from PIL import Image
base = S.render_image(lq, dpi=150)            # a poor-quality scan: ~60 dpi
base.resize((int(base.width * 0.4), int(base.height * 0.4)), Image.LANCZOS).save(OUT / "09_low_quality_scan.png")
good = (OUT / "01_clean_invoice.pdf").read_bytes(); (OUT / "10_corrupt.pdf").write_bytes(b"%PDF-1.4\n" + good[20:200])
(OUT / "11_not_an_invoice.docx").write_bytes(b"PK\x03\x04" + b"\0" * 64)
print("wrote", len(list(OUT.iterdir())), "files to", OUT)
for p in sorted(OUT.iterdir()): print(f"  {p.name:28} {p.stat().st_size / 1024:7.1f} KB")
