"""Run the real pipeline (API upload + background worker) over a labeled split and compute metrics.
  python eval/run_eval.py --split dev|test      -> docs/evaluation/results_<split>.json + errors_<split>.jsonl
Metric definitions (header fields = invoice_number, invoice_date, supplier, customer, subtotal, tax, total, currency):
  precision = correct / predicted-non-null        recall = correct / truth-non-null        (correct = exact value match)
  exact-match accuracy = exact matches / all field instances (a correct 'null' for an absent field counts as a match)
  wrong-value rate = (predicted-non-null - correct) / predicted-non-null   (includes values 'invented' for absent fields)
  line item = TP iff description (alnum, case-insensitive), quantity, unit price and amount all equal (Decimal)
  error-catch rate = share of invoices with >=1 wrong/missing value that were flagged needs_review
  false-review rate = share of fully correct invoices that were nevertheless flagged needs_review"""
import argparse, json, os, platform, re, shutil, statistics, subprocess, sys, tempfile, time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
FIELDS = ["invoice_number", "invoice_date", "supplier", "customer", "subtotal", "tax", "total", "currency"]
AMOUNTS = {"subtotal", "tax", "total"}
OUT = ROOT / "docs" / "evaluation"
BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01)]


def alnum(s) -> str: return re.sub(r"[^a-z0-9]", "", str(s).lower())
def dec(x):
    try: return Decimal(str(x))
    except (InvalidOperation, ValueError): return None


def same(name, pred, truth) -> bool:
    if pred is None or truth is None: return pred is None and truth is None
    if name in AMOUNTS:
        a, b = dec(pred), dec(truth); return a is not None and b is not None and a == b
    return str(pred).strip() == str(truth).strip()


def items_score(pred, truth):
    used, tp = set(), 0
    for t in truth:
        for j, p in enumerate(pred):
            if j in used: continue
            if (alnum(t["description"]) == alnum(p["description"] or "") and dec(t["quantity"]) == dec(p["quantity"])
                    and dec(t["unit_price"]) == dec(p["unit_price"]) and dec(t["amount"]) == dec(p["amount"])):
                used.add(j); tp += 1; break
    return tp, len(truth) - tp, len(pred) - tp


def match(truths, preds):
    pairs, ut, up = [], set(), set()
    for ti, t in enumerate(truths):
        k = alnum(t["fields"]["invoice_number"]) if t["fields"]["invoice_number"] else None
        if not k: continue
        for pj, p in enumerate(preds):
            if pj not in up and alnum(p["fields"]["invoice_number"]["value"] or "") == k:
                pairs.append((ti, pj)); ut.add(ti); up.add(pj); break
    lt, lp = [i for i in range(len(truths)) if i not in ut], [j for j in range(len(preds)) if j not in up]
    if lt and len(lt) == len(lp):  # a misread number is a field error, not a missed + spurious invoice
        pairs += list(zip(lt, lp)); ut.update(lt); up.update(lp)
    return pairs, [i for i in range(len(truths)) if i not in ut], [j for j in range(len(preds)) if j not in up]


def expected_keys(rules) -> set:
    return {(r[0], r[1] if r[0] == "VAL-REQ-001" else None) for r in rules}


def rule_keys(issues) -> set:
    return {(i["rule_id"], i["field"] if i["rule_id"] == "VAL-REQ-001" else None) for i in issues}


def build_records(manifest, results):
    recs, errors = [], []
    for m in manifest["files"]:
        r = results[m["file"]]
        if m["expect"] != "success" or r["detail"] is None:
            continue
        truths, preds = m["invoices"], r["detail"]["invoices"]
        pairs, missed, spurious = match(truths, preds)
        base = {"file": m["file"], "category": m["category"], "file_type": m["file_type"]}
        for ti, pj in pairs:
            t, p = truths[ti], preds[pj]
            pf = {f: p["fields"][f]["value"] for f in FIELDS}
            conf = {f: p["fields"][f]["confidence"] for f in FIELDS}
            exp = expected_keys(t["expected_rules"])
            sc = items_score(p["line_items"], t["line_items"]) if t["line_items"] is not None else None
            bad = [f for f in FIELDS if not same(f, pf[f], t["fields"][f])]
            for f in bad:
                errors.append({**base, "field": f, "expected": t["fields"][f], "got": pf[f], "confidence": conf[f], "evidence": p["fields"][f]["evidence"]})
            if sc and (sc[1] or sc[2]):
                errors.append({**base, "field": "line_items", "expected": f"{len(t['line_items'])} items", "got": f"tp={sc[0]} fn={sc[1]} fp={sc[2]}",
                               "evidence": "; ".join(f"{x['description']}|{x['quantity']}|{x['unit_price']}|{x['amount']}" for x in p["line_items"])[:300]})
            oc = {f: p["fields"][f].get("ocr_confidence") for f in FIELDS if pf[f] is not None}
            item_oc = [i.get("ocr_conf") for i in p["line_items"] if i.get("ocr_conf") is not None]
            recs.append({**base, "kind": "matched", "truth": t["fields"], "pred": pf, "conf": conf, "items": sc, "flagged": p["needs_review"],
                         "oc_min": min([v for v in oc.values() if v is not None] + item_oc) if (any(v is not None for v in oc.values()) or item_oc) else None,
                         "has_error": bool(bad) or bool(sc and (sc[1] or sc[2])), "expected": exp, "predicted": rule_keys(p["issues"])})
        for ti in missed:
            t = truths[ti]
            recs.append({**base, "kind": "missed", "truth": t["fields"], "pred": None, "conf": None, "items": (0, len(t["line_items"] or []), 0) if t["line_items"] is not None else None,
                         "flagged": None, "has_error": True, "expected": expected_keys(t["expected_rules"]), "predicted": set()})
            errors.append({**base, "field": "invoice", "expected": t["fields"]["invoice_number"], "got": "MISSED", "evidence": None})
        for pj in spurious:
            p = preds[pj]
            recs.append({**base, "kind": "spurious", "truth": None, "pred": {f: p["fields"][f]["value"] for f in FIELDS}, "conf": None, "items": None,
                         "flagged": p["needs_review"], "has_error": True, "expected": set(), "predicted": rule_keys(p["issues"])})
            errors.append({**base, "field": "invoice", "expected": None, "got": f"SPURIOUS {p['invoice_number']}", "evidence": None})
    return recs, errors


def ratio(a, b): return None if not b else round(a / b, 4)


def aggregate(recs):
    pf = {f: dict(tn=0, pn=0, c=0, em=0, n=0, halluc=0) for f in FIELDS}
    tp = fn = fp = 0
    n_truth = found = exact = 0
    err_n = err_flag = ok_n = ok_flag = prob_n = prob_flag = 0
    rules = defaultdict(lambda: [0, 0, 0])
    for r in recs:
        if r["truth"] is not None:
            n_truth += 1; found += r["kind"] == "matched"
            for f in FIELDS:
                t, p = r["truth"][f], (r["pred"] or {}).get(f)
                d = pf[f]; d["n"] += 1; d["tn"] += t is not None; d["pn"] += p is not None
                ok = same(f, p, t); d["em"] += ok; d["c"] += (ok and t is not None)
                d["halluc"] += (p is not None and t is None)
            if r["kind"] == "matched" and not r["has_error"]: exact += 1
        else:
            for f in FIELDS:
                pf[f]["pn"] += r["pred"][f] is not None
        if r["items"]: tp += r["items"][0]; fn += r["items"][1]; fp += r["items"][2]
        if r["kind"] == "matched":
            if r["has_error"]: err_n += 1; err_flag += bool(r["flagged"])                 # extraction mistakes
            elif r["expected"] - {("VAL-XDOC-002", None)}: prob_n += 1; prob_flag += bool(r["flagged"])               # correct extraction of a genuinely flawed document
            else: ok_n += 1; ok_flag += bool(r["flagged"])                                 # clean document (a passed cross-verification is info, not a flaw)
        for k in r["expected"] & r["predicted"]: rules[k[0]][0] += 1
        for k in r["predicted"] - r["expected"]: rules[k[0]][1] += 1
        for k in r["expected"] - r["predicted"]: rules[k[0]][2] += 1
    tot = {k: sum(pf[f][k] for f in FIELDS) for k in ("tn", "pn", "c", "em", "n", "halluc")}
    summ = lambda d: {"precision": ratio(d["c"], d["pn"]), "recall": ratio(d["c"], d["tn"]), "exact_match": ratio(d["em"], d["n"]),
                      "wrong_value_rate": ratio(d["pn"] - d["c"], d["pn"]), "hallucinated": d["halluc"], "n": d["n"], "truth_nonnull": d["tn"], "pred_nonnull": d["pn"], "correct": d["c"]}
    p, rc = ratio(tot["c"], tot["pn"]), ratio(tot["c"], tot["tn"])
    return {"invoices_truth": n_truth, "invoices_found": found, "invoice_found_rate": ratio(found, n_truth),
            "invoice_all_correct_rate": ratio(exact, n_truth),
            "fields_micro": {**summ(tot), "f1": None if not (p and rc) else round(2 * p * rc / (p + rc), 4)},
            "fields": {f: summ(pf[f]) for f in FIELDS},
            "line_items": {"tp": tp, "fn": fn, "fp": fp, "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn)},
            "review_flags": {"invoices_with_extraction_error": err_n, "flagged_of_error": err_flag, "error_catch_rate": ratio(err_flag, err_n),
                             "flawed_documents_correctly_extracted": prob_n, "flagged_of_flawed": prob_flag, "flaw_flag_rate": ratio(prob_flag, prob_n),
                             "clean_correct_invoices": ok_n, "flagged_of_clean": ok_flag, "false_review_rate": ratio(ok_flag, ok_n)},
            "rules": {k: {"tp": v[0], "fp": v[1], "fn": v[2], "precision": ratio(v[0], v[0] + v[1]), "recall": ratio(v[0], v[0] + v[2])} for k, v in sorted(rules.items())}}


def calibration(recs):
    rows = []
    for lo, hi in BINS:
        pts = [(r["conf"][f], same(f, r["pred"][f], r["truth"][f])) for r in recs if r["kind"] == "matched" for f in FIELDS
               if r["pred"][f] is not None and lo <= r["conf"][f] < hi]
        rows.append({"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "n": len(pts), "mean_confidence": round(statistics.mean(c for c, _ in pts), 3) if pts else None,
                     "empirical_accuracy": round(sum(ok for _, ok in pts) / len(pts), 3) if pts else None})
    return rows


def ocr_sweep(recs, thresholds=(0, 50, 60, 65, 70, 75, 80, 85, 90, 95)):
    """For each floor T: among OCR'd invoices with an extraction error, how many carry a value with weakest-word confidence < T
    (or were already flagged by other rules); among fully-correct OCR'd invoices of clean documents, how many would be falsely flagged."""
    ocr = [r for r in recs if r["kind"] == "matched" and r.get("oc_min") is not None]
    bad = [r for r in ocr if r["has_error"]]
    clean = [r for r in ocr if not r["has_error"] and not r["expected"]]
    rows = []
    for t in thresholds:
        caught = sum(bool(r["flagged"]) or r["oc_min"] < t for r in bad)
        false = sum(bool(r["flagged"]) or r["oc_min"] < t for r in clean)
        rows.append({"floor": t, "ocr_invoices_with_error": len(bad), "caught": caught, "catch_rate": ratio(caught, len(bad)),
                     "clean_ocr_invoices": len(clean), "falsely_flagged": false, "false_review_rate": ratio(false, len(clean))})
    return rows


def pct(xs, q): xs = sorted(xs); return round(xs[min(len(xs) - 1, int(len(xs) * q))], 3) if xs else None


def run(split, tag=""):
    manifest = json.loads((ROOT / "eval" / "dataset" / split / "manifest.json").read_text())
    files_dir = ROOT / "eval" / "dataset" / split / "files"
    tmp = Path(tempfile.mkdtemp(prefix="docintel_eval_"))
    os.environ.update(DOCINTEL_DATA_DIR=str(tmp), DOCINTEL_WORKERS="0", DOCINTEL_SEMANTIC="off", DOCINTEL_RETRY_BACKOFF_S="0")
    from fastapi.testclient import TestClient
    from app import worker
    from app.db import init_db
    from app.main import app
    init_db(); client = TestClient(app)
    results = {}
    t_all = time.perf_counter()
    for m in manifest["files"]:
        data = (files_dir / m["file"]).read_bytes()
        t0 = time.perf_counter()
        up = client.post("/api/documents", files=[("files", (m["file"], data, "application/octet-stream"))]).json()["results"][0]
        upload_s = time.perf_counter() - t0
        rec = {"upload": up["status"], "upload_error": up.get("error"), "upload_s": upload_s, "doc_id": up.get("document_id"), "process_s": None, "detail": None}
        if up["status"] == "queued":
            t0 = time.perf_counter()
            while worker.run_one(): pass
            rec["process_s"] = time.perf_counter() - t0
        results[m["file"]] = rec
    for f, rec in results.items():  # read final state after ALL files were ingested (cross-document rules need the pairs)
        if rec["doc_id"] and rec["upload"] == "queued":
            rec["detail"] = client.get(f"/api/documents/{rec['doc_id']}").json()
    total_s = time.perf_counter() - t_all

    recs, errors = build_records(manifest, results)
    valid = [m for m in manifest["files"] if m["expect"] == "success"]
    outcome = lambda r: "rejected" if r["upload"] == "rejected" else (r["detail"]["status"] if r["detail"] else r["upload"])
    status = defaultdict(lambda: defaultdict(int)); timing = defaultdict(lambda: {"s": [], "pages": 0})
    for m in valid:
        r = results[m["file"]]; o = outcome(r); st = status[m["file_type"]]
        st["n"] += 1; st[o] += 1
        ok = o in ("completed", "partial") and r["detail"]["invoices"]
        st["success"] += bool(ok)
        if r["process_s"] is not None:
            timing[m["file_type"]]["s"].append(r["process_s"]); timing[m["file_type"]]["pages"] += (r["detail"]["page_count"] or 0)
    allst = {k: sum(v[k] for v in status.values()) for k in ("n", "success")}
    neg = []
    for m in manifest["files"]:
        if m["expect"] == "success": continue
        r = results[m["file"]]; o = outcome(r)
        code = r["detail"]["error_code"] if r["detail"] else None
        neg.append({"file": m["file"], "category": m["category"], "expected": m["expect"], "actual": o, "expected_code": m["error_code"], "actual_code": code,
                    "handled": o == m["expect"], "code_ok": (m["error_code"] is None) or code == m["error_code"], "attempts": (r["detail"]["job"]["attempts"] if r["detail"] else None)})
    cats = sorted({r["category"] for r in recs}); fts = sorted({r["file_type"] for r in recs})
    cpu = platform.processor()
    try: cpu = next(l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name"))
    except Exception: pass
    import pymupdf
    tess = subprocess.run([os.environ.get("TESSERACT_CMD", "tesseract"), "--version"], capture_output=True, text=True)
    try: commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception: commit = None
    out = {"split": split, "seed": manifest["seed"], "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "git_commit": commit,
           "env": {"platform": platform.platform(), "cpu": cpu, "cores": psutil.cpu_count(), "ram_mb": psutil.virtual_memory().total // 1048576, "python": platform.python_version(),
                   "pymupdf": pymupdf.__version__, "tesseract": (tess.stdout or tess.stderr).splitlines()[0],
                   "settings": {"ocr_dpi": 200, "workers": "synchronous (one job at a time)", "semantic": "off"}},
           "dataset": {"files": len(manifest["files"]), "valid_files": len(valid), "negative_files": len(manifest["files"]) - len(valid),
                       "labeled_invoices": sum(len(m["invoices"]) for m in valid),
                       "by_file_type": {k: sum(1 for m in valid if m["file_type"] == k) for k in sorted({m["file_type"] for m in valid})}},
           "wall_total_s": round(total_s, 1),
           "overall": aggregate(recs), "by_file_type": {k: aggregate([r for r in recs if r["file_type"] == k]) for k in fts},
           "by_category": {k: aggregate([r for r in recs if r["category"] == k]) for k in cats},
           "calibration": calibration(recs), "ocr_floor_in_effect": float(os.environ.get("DOCINTEL_OCR_FIELD_MIN_CONF", "60")), "ocr_sweep": ocr_sweep(recs),
           "processing": {"success_rate": ratio(allst["success"], allst["n"]), "failure_rate": ratio(sum(v["failed"] for v in status.values()), allst["n"]),
                          "valid_files": allst["n"], "by_file_type": {k: dict(v) for k, v in sorted(status.items())}},
           "timing": {k: {"files": len(v["s"]), "pages": v["pages"], "median_s": pct(v["s"], .5), "p95_s": pct(v["s"], .95), "max_s": round(max(v["s"]), 2) if v["s"] else None,
                          "pages_per_min": ratio(v["pages"] * 60, sum(v["s"])) if v["s"] else None} for k, v in sorted(timing.items())},
           "negatives": {"n": len(neg), "handled": sum(n["handled"] for n in neg), "code_ok": sum(n["code_ok"] and n["handled"] for n in neg), "rows": neg}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"results_{split}{tag}.json").write_text(json.dumps(out, indent=1))
    with open(OUT / f"errors_{split}{tag}.jsonl", "w") as f:
        for e in errors: f.write(json.dumps(e) + "\n")
    shutil.rmtree(tmp, ignore_errors=True)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--split", choices=["dev", "test", "test2"], required=True)
    ap.add_argument("--ocr-floor", type=float, default=None, help="override DOCINTEL_OCR_FIELD_MIN_CONF (0 disables the rule)")
    ap.add_argument("--tag", default="", help="suffix for the output files")
    a = ap.parse_args()
    if a.ocr_floor is not None: os.environ["DOCINTEL_OCR_FIELD_MIN_CONF"] = str(a.ocr_floor)
    o = run(a.split, a.tag)
    f = o["overall"]["fields_micro"]
    print(f"{a.split}: {o['dataset']['files']} files / {o['dataset']['labeled_invoices']} invoices in {o['wall_total_s']} s")
    print(f"  success rate {o['processing']['success_rate']}  failure rate {o['processing']['failure_rate']}  negatives handled {o['negatives']['handled']}/{o['negatives']['n']}")
    print(f"  header fields micro: P={f['precision']} R={f['recall']} F1={f['f1']} exact-match={f['exact_match']} wrong-value-rate={f['wrong_value_rate']}")
    print(f"  line items: {o['overall']['line_items']}")
    print(f"  invoices all-correct {o['overall']['invoice_all_correct_rate']}  review flags {o['overall']['review_flags']}")
