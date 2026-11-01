"""Write docs/openapi.json from the FastAPI app (the contract the frontend and tests use)."""
import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DOCINTEL_DATA_DIR", tempfile.mkdtemp())
from app.main import app
out = ROOT / "docs" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=1))
paths = app.openapi()["paths"]
print(f"wrote {out} ({sum(len(v) for v in paths.values())} operations on {len(paths)} paths)")
