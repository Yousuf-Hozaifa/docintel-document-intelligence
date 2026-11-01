"""Render docs/diagrams/*.mmd to .svg and .png with Mermaid in headless Chromium (validates the syntax).
Needs: npm install mermaid@10 (in MERMAID_DIR) and `playwright install chromium`."""
import json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
MM = Path(os.environ.get("MERMAID_DIR", "/tmp/mm")) / "node_modules/mermaid/dist/mermaid.min.js"
html = "<html><body style='margin:0;background:white'><div id='out'></div></body></html>"
ok = True
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    pg.set_content(html); pg.add_script_tag(path=str(MM)); pg.wait_for_function("window.mermaid !== undefined")
    pg.evaluate("mermaid.initialize({startOnLoad:false, theme:'neutral', flowchart:{htmlLabels:true, curve:'basis'}})")
    for f in sorted((ROOT / "docs" / "diagrams").glob("*.mmd")):
        try:
            svg = pg.evaluate("async (t) => (await mermaid.render('d' + Math.random().toString(36).slice(2), t)).svg", f.read_text())
        except Exception as e:
            print("FAILED", f.name, str(e)[:300]); ok = False; continue
        f.with_suffix(".svg").write_text(svg)
        pg.evaluate("(s) => { document.getElementById('out').innerHTML = s }", svg)
        pg.locator("#out svg").first.screenshot(path=str(f.with_suffix(".png")))
        print("rendered", f.name)
    b.close()
sys.exit(0 if ok else 1)
