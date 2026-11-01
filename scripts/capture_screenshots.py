"""Drive the real UI through the demo flow and save screenshots to docs/screenshots/ (servers must be running on :8000/:5173)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / "docs" / "screenshots"; OUT.mkdir(parents=True, exist_ok=True)
files = sorted((ROOT / "demo_files").iterdir())
def chips(pg): return {r.locator(".name").inner_text(): r.locator(".chip").inner_text() for r in pg.locator(".docrow").all()}
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1360, "height": 900}); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("http://localhost:5173"); pg.wait_for_selector("text=Document Intelligence")
    pg.set_input_files("input[type=file]", [str(f) for f in files])
    pg.wait_for_selector(".report li")
    pg.wait_for_function("() => document.querySelectorAll('.docrow .chip').length==10 && [...document.querySelectorAll('.docrow .chip')].every(c=>!['queued','processing'].includes(c.textContent))", timeout=90000)
    pg.screenshot(path=str(OUT / "01-upload-and-list.png")); print("1 list:", chips(pg))
    pg.locator(".docrow", has_text="04_tampered").click(); pg.wait_for_selector(".issues li")
    pg.screenshot(path=str(OUT / "02-validation-error.png"), full_page=True); print("2 issues:", [t[:60] for t in pg.locator(".issues li").all_inner_texts()])
    pg.click("button:has-text('Edit')"); pg.get_by_label("Total", exact=True).fill("836.00"); pg.fill("input[aria-label='Correction note']", "Printed total did not match subtotal + tax; confirmed with register")
    pg.screenshot(path=str(OUT / "03-inline-correction.png"), full_page=True)
    pg.click("button:has-text('Save corrections')"); pg.wait_for_selector("text=corrected manually"); pg.wait_for_timeout(700)
    print("3 after save: issues=", [t[:50] for t in pg.locator(".issues li").all_inner_texts()], "| chip:", chips(pg)["04_tampered_total.pdf"])
    pg.screenshot(path=str(OUT / "04-after-correction.png"), full_page=True)
    pg.locator(".docrow", has_text="06_invoice_register").click(); pg.wait_for_selector(".invoice")
    print("4 register after the correction:", [t[:70] for t in pg.locator(".issues li").all_inner_texts()])
    pg.locator(".docrow", has_text="01_clean").click(); pg.wait_for_selector(".issues li")
    pg.screenshot(path=str(OUT / "05-duplicate-and-crossdoc.png"), full_page=True); print("5 clean+dup:", [t[:60] for t in pg.locator(".issues li").all_inner_texts()])
    pg.locator(".docrow", has_text="09_low_quality").click(); pg.wait_for_selector(".issues li")
    pg.screenshot(path=str(OUT / "06-low-quality-scan.png"), full_page=True); print("6 low quality:", [t[:70] for t in pg.locator(".issues li").all_inner_texts()])
    with pg.context.expect_page() as popup:
        pg.click("button:has-text('Open original')")
    orig = popup.value; orig.wait_for_load_state(); print("6b original opened:", orig.url[:30], "| image loaded:", orig.evaluate("() => document.images.length ? document.images[0].naturalWidth : 0"))
    orig.set_viewport_size({"width": 700, "height": 700}); orig.screenshot(path=str(OUT / "06b-original-scan.png")); orig.close()
    pg.click("button:has-text('Edit')"); pg.get_by_label("Customer", exact=True).fill("Soylent Foods"); pg.fill("input[aria-label='Correction note']", "Read from the original scan")
    pg.click("button:has-text('Save corrections')"); pg.wait_for_selector("text=corrected manually"); pg.wait_for_timeout(600)
    print("6c after customer fix:", [t[:60] for t in pg.locator(".issues li").all_inner_texts()])
    pg.locator(".issues li:has-text('VAL-OCR-001') button:has-text('Mark reviewed')").click(); pg.wait_for_timeout(700)
    print("6d after mark reviewed -> chip:", chips(pg)["09_low_quality_scan.png"])
    pg.screenshot(path=str(OUT / "06c-reviewed.png"), full_page=True)
    pg.fill("input[aria-label='Search query']", "Kestrel"); pg.click("button:has-text('Search')"); pg.wait_for_selector(".hits li")
    pg.screenshot(path=str(OUT / "07-keyword-search.png")); print("7 keyword hits:", pg.locator(".hits > li").count(), "| marks:", pg.locator(".hits mark").count())
    pg.select_option("select[aria-label='Search mode']", "hybrid"); pg.fill("input[aria-label='Search query']", "stand for notebook computer"); pg.click("button:has-text('Search')"); pg.wait_for_selector(".hits li")
    pg.screenshot(path=str(OUT / "08-meaning-search.png")); print("8 meaning top:", pg.locator(".hits > li").first.inner_text()[:80].replace("\n", " "))
    pg.locator(".docrow", has_text="10_corrupt").click(); pg.wait_for_selector("text=Processing failed")
    pg.screenshot(path=str(OUT / "09-failed-document.png")); 
    pg.click("button:has-text('Retry all failed')"); pg.wait_for_timeout(2500); print("9 after retry:", chips(pg)["10_corrupt.pdf"])
    print("JS errors:", errs); b.close()
