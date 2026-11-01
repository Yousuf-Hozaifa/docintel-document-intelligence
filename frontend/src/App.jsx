import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiKey, ApiError, openOriginal, safeSnippet } from "./api.js";

const FIELD_LABELS = {
  invoice_number: "Invoice number", invoice_date: "Invoice date", supplier: "Supplier", customer: "Customer",
  subtotal: "Subtotal", tax: "Tax", total: "Total", currency: "Currency",
};
const ACTIVE = new Set(["queued", "processing"]);

function StatusChip({ doc }) {
  const label = doc.status === "completed" && doc.review_required ? "needs review" : doc.status;
  const tone = doc.status === "failed" ? "bad" : label === "needs review" || doc.status === "partial" ? "warn"
    : doc.status === "completed" ? "ok" : "busy";
  return <span className={`chip ${tone}`}>{label}</span>;
}

function Progress({ doc }) {
  const j = doc.job;
  if (!ACTIVE.has(doc.status) || !j) return null;
  const pct = j.progress_total ? Math.round((100 * j.progress_done) / j.progress_total) : 0;
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}
         aria-label={`Processing ${doc.filename}`}>
      <div style={{ width: `${pct}%` }} />
      <span>{j.progress_total ? `${j.progress_done}/${j.progress_total} pages` : "waiting"}{j.attempts > 1 ? ` · attempt ${j.attempts}` : ""}</span>
    </div>
  );
}

function Uploader({ onUploaded }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState([]);
  const [drag, setDrag] = useState(false);

  const send = async (files) => {
    if (!files.length) return;
    setBusy(true);
    try {
      const { results } = await api.upload(files);
      setReport(results);
      onUploaded();
    } catch (e) {
      setReport([{ filename: "Upload", status: "rejected", error: e.message }]);
    } finally { setBusy(false); }
  };

  return (
    <section aria-label="Upload documents">
      <div className={`drop ${drag ? "over" : ""}`}
           onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
           onDrop={(e) => { e.preventDefault(); setDrag(false); send([...e.dataTransfer.files]); }}>
        <p>Drop invoices here, or</p>
        <button onClick={() => input.current.click()} disabled={busy}>{busy ? "Uploading…" : "Choose files"}</button>
        <input ref={input} type="file" multiple hidden accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff,.xlsx,.xls,.csv"
               onChange={(e) => { send([...e.target.files]); e.target.value = ""; }} />
        <small>PDF, JPG, PNG, TIFF, XLSX, XLS, CSV</small>
      </div>
      {report.length > 0 && (
        <ul className="report" aria-live="polite">
          {report.map((r, i) => (
            <li key={i} className={r.status === "rejected" ? "bad" : r.status === "duplicate" ? "warn" : "ok"}>
              <strong>{r.filename}</strong>{" "}
              {r.status === "queued" && "queued for processing"}
              {r.status === "duplicate" && `identical file already uploaded (document ${r.duplicate_of})`}
              {r.status === "rejected" && `rejected: ${r.error}`}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DocList({ docs, selected, onSelect }) {
  if (!docs.length) return <p className="empty">No documents yet. Upload invoices to begin.</p>;
  return (
    <ul className="doclist">
      {docs.map((d) => (
        <li key={d.id}>
          <button className={`docrow ${selected === d.id ? "sel" : ""}`} onClick={() => onSelect(d.id)}>
            <span className="name">{d.filename}</span>
            <StatusChip doc={d} />
            <span className="meta">{d.file_type || "…"}{d.page_count ? ` · ${d.page_count} p` : ""}{d.invoice_count > 1 ? ` · ${d.invoice_count} invoices` : ""}</span>
            <Progress doc={d} />
            {d.status === "failed" && <span className="err">{d.error_message}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

const FIELD_KEYS = Object.keys(FIELD_LABELS);
const ITEM_ATTRS = ["description", "quantity", "unit_price", "amount"];

function IssueList({ issues, onResolve, busy }) {
  if (!issues.length) return null;
  return (
    <ul className="issues">
      {issues.map((i) => (
        <li key={i.id} className={`${i.severity} ${i.resolved ? "done" : ""}`}>
          <code>{i.rule_id}</code> {i.message}
          {i.severity !== "info" && (
            <button className="link" disabled={busy} onClick={() => onResolve(i.id, !i.resolved)}>
              {i.resolved ? "Reopen" : "Mark reviewed"}
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function InvoiceCard({ inv, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const start = () => {
    setDraft({
      fields: Object.fromEntries(FIELD_KEYS.map((k) => [k, inv.fields[k].value ?? ""])),
      items: inv.line_items.map((li) => ({ idx: li.idx, description: li.description ?? "", quantity: li.quantity ?? "", unit_price: li.unit_price ?? "", amount: li.amount ?? "", delete: false })),
      added: [],
    });
    setNote(""); setErr(null); setEditing(true);
  };

  const save = async () => {
    const fields = {};
    for (const k of FIELD_KEYS) if ((inv.fields[k].value ?? "") !== draft.fields[k].trim()) fields[k] = draft.fields[k].trim() || null;
    const line_items = [];
    for (const it of draft.items) {
      const orig = inv.line_items.find((x) => x.idx === it.idx);
      if (it.delete) { line_items.push({ idx: it.idx, delete: true }); continue; }
      const ch = {};
      for (const a of ITEM_ATTRS) if ((orig[a] ?? "") !== it[a].trim()) ch[a] = it[a].trim() || null;
      if (Object.keys(ch).length) line_items.push({ idx: it.idx, ...ch });
    }
    for (const n of draft.added) if (ITEM_ATTRS.some((a) => n[a].trim())) line_items.push(Object.fromEntries(ITEM_ATTRS.filter((a) => n[a].trim()).map((a) => [a, n[a].trim()])));
    if (!Object.keys(fields).length && !line_items.length) { setEditing(false); return; }
    setBusy(true); setErr(null);
    try { await api.patchInvoice(inv.id, { fields, line_items, note: note.trim() || null }); setEditing(false); await onChanged(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const resolve = async (issueId, resolved) => {
    setBusy(true); setErr(null);
    try { await api.resolveIssue(issueId, resolved); await onChanged(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const setF = (k, v) => setDraft((d) => ({ ...d, fields: { ...d.fields, [k]: v } }));
  const setItem = (i, a, v) => setDraft((d) => ({ ...d, items: d.items.map((x, j) => (j === i ? { ...x, [a]: v } : x)) }));
  const setAdded = (i, a, v) => setDraft((d) => ({ ...d, added: d.added.map((x, j) => (j === i ? { ...x, [a]: v } : x)) }));

  return (
    <article className="invoice">
      <div className="invhead">
        <h3>Invoice {inv.seq}{inv.page_start !== inv.page_end ? ` (pages ${inv.page_start}–${inv.page_end})` : ""}
          {inv.correction_count > 0 && <span className="chip busy" title="Number of audited manual changes"> {inv.correction_count} correction(s)</span>}</h3>
        {!editing && <button onClick={start}>Edit</button>}
      </div>
      {err && <p className="err" role="alert">{err}</p>}
      <IssueList issues={inv.issues} onResolve={resolve} busy={busy} />
      <table className="fields">
        <thead><tr><th>Field</th><th>Value</th><th title="Heuristic rule confidence, not a calibrated probability">Confidence</th><th>Source text</th></tr></thead>
        <tbody>
          {FIELD_KEYS.map((k) => {
            const f = inv.fields[k];
            return (
              <tr key={k} className={f.value == null && !editing ? "missing" : ""}>
                <th scope="row"><label htmlFor={`f-${inv.id}-${k}`}>{FIELD_LABELS[k]}</label></th>
                <td className={editing ? "" : "num"}>
                  {editing ? <input id={`f-${inv.id}-${k}`} value={draft.fields[k]} onChange={(e) => setF(k, e.target.value)} />
                    : (f.value ?? <em>not found</em>)}
                </td>
                <td className="num">{f.value != null ? `${Math.round(f.confidence * 100)}%` : "—"}</td>
                <td className="ev">{f.source === "manual" ? <em>corrected manually{f.original_value != null ? ` (was ${f.original_value})` : ""}</em> : <>{f.evidence ?? ""}{f.note ? <em> — {f.note}</em> : null}{f.ocr_confidence != null ? <em className={f.ocr_confidence < 60 ? "lowocr" : ""}> · OCR word confidence {Math.round(f.ocr_confidence)}%</em> : null}</>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <h4>Line items</h4>
      {(editing ? draft.items.length + draft.added.length : inv.line_items.length) ? (
        <table className="items">
          <thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th>{editing && <th />}</tr></thead>
          <tbody>
            {!editing && inv.line_items.map((li) => (
              <tr key={li.idx} title={li.evidence}><td>{li.description}</td><td className="num">{li.quantity ?? <em>unreadable</em>}</td><td className="num">{li.unit_price}</td><td className="num">{li.amount}</td></tr>
            ))}
            {editing && draft.items.map((it, i) => (
              <tr key={it.idx} className={it.delete ? "deleted" : ""}>
                {ITEM_ATTRS.map((a) => <td key={a}><input aria-label={`${a} of line ${it.idx}`} value={it[a]} disabled={it.delete} onChange={(e) => setItem(i, a, e.target.value)} /></td>)}
                <td><button className="link" onClick={() => setItem(i, "delete", !it.delete)}>{it.delete ? "Undo" : "Delete"}</button></td>
              </tr>
            ))}
            {editing && draft.added.map((it, i) => (
              <tr key={`n${i}`}>
                {ITEM_ATTRS.map((a) => <td key={a}><input aria-label={`new ${a} ${i + 1}`} value={it[a]} onChange={(e) => setAdded(i, a, e.target.value)} /></td>)}
                <td><button className="link" onClick={() => setDraft((d) => ({ ...d, added: d.added.filter((_, j) => j !== i) }))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="empty">No line items.</p>}
      {editing && (
        <div className="editbar">
          <button className="secondary" onClick={() => setDraft((d) => ({ ...d, added: [...d.added, { description: "", quantity: "", unit_price: "", amount: "" }] }))}>Add line item</button>
          <input className="note" placeholder="Reason for correction (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Correction note" />
          <button onClick={save} disabled={busy}>{busy ? "Saving…" : "Save corrections"}</button>
          <button className="secondary" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
        </div>
      )}
    </article>
  );
}

function Inspector({ id, onChanged, onDeleted }) {
  const [doc, setDoc] = useState(null);
  const [limit, setLimit] = useState(25);
  const [err, setErr] = useState(null);
  const timer = useRef(null);
  const load = useCallback(async () => {
    clearTimeout(timer.current);
    try {
      const d = await api.getDocument(id, limit);
      setDoc(d); setErr(null);
      if (ACTIVE.has(d.status)) timer.current = setTimeout(load, 1500);
    } catch (e) { setErr(e.message); }
  }, [id, limit]);
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, [load]);

  const changed = async () => { await load(); onChanged(); };
  const reprocess = async () => {
    if (!window.confirm("Reprocess this document? Results and manual corrections on it will be replaced.")) return;
    try { await api.reprocess(id); await changed(); } catch (e) { setErr(e.message); }
  };

  const remove = async () => {
    if (!window.confirm("Permanently delete this document, its extracted data and the stored file?")) return;
    try { await api.deleteDocument(id); onDeleted(); } catch (e) { setErr(e.message); }
  };

  if (err && !doc) return <p className="err">Could not load document: {err}</p>;
  if (!doc) return <p className="empty">Loading…</p>;
  return (
    <div className="inspector">
      <header>
        <h2>{doc.filename}</h2>
        <span className="actions"><StatusChip doc={doc} />
          <button className="secondary" onClick={() => openOriginal(id, doc.filename).catch((e) => setErr(e.message))}>Open original</button>
          {!ACTIVE.has(doc.status) && <button className="secondary" onClick={reprocess}>Reprocess</button>}
          {!ACTIVE.has(doc.status) && <button className="secondary danger" onClick={remove}>Delete</button>}</span>
      </header>
      {err && <p className="err" role="alert">{err}</p>}
      {doc.status === "failed" && <p className="banner bad">Processing failed ({doc.error_code}): {doc.error_message}</p>}
      {doc.status === "partial" && <p className="banner warn">{doc.pages_failed} page(s) could not be processed. Results cover the remaining pages.</p>}
      {ACTIVE.has(doc.status) && <p className="banner busy">Processing… {doc.job?.progress_done ?? 0}/{doc.job?.progress_total ?? "?"} pages</p>}
      {doc.invoice_total > doc.invoices.length && (
        <p className="banner busy">Showing {doc.invoices.length} of {doc.invoice_total} invoices in this document.{" "}
          <button className="link" onClick={() => setLimit((l) => Math.min(l + 25, 500))}>Show 25 more</button></p>
      )}
      {doc.invoices.map((inv) => <InvoiceCard key={`${inv.id}-${inv.correction_count}-${inv.issues.length}-${inv.needs_review}`} inv={inv} onChanged={changed} />)}
      <details>
        <summary>Processing history</summary>
        <ol className="history">{doc.history.map((h, i) => <li key={i}><time>{h.ts}</time> {h.event} {h.detail && <small>{h.detail}</small>}</li>)}</ol>
      </details>
    </div>
  );
}

function Search({ onOpen }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("keyword");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const run = async (e) => {
    e.preventDefault();
    try {
      const r = mode === "keyword" ? await api.search(q.trim() ? { q } : {}) : await api.semanticSearch({ q: q.trim(), mode: "hybrid" });
      setRes(r.results); setErr(null);
    } catch (x) {
      setRes(null);
      setErr(x instanceof ApiError && x.status === 503 ? "Search by meaning is not enabled on this server (start it with DOCINTEL_SEMANTIC=glove)." : x.message);
    }
  };
  return (
    <section aria-label="Search documents" className="search">
      <form onSubmit={run}>
        <select value={mode} onChange={(e) => { setMode(e.target.value); setRes(null); setErr(null); }} aria-label="Search mode">
          <option value="keyword">Keywords</option>
          <option value="hybrid">Meaning + keywords</option>
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={mode === "keyword" ? "Search text, supplier, invoice number…" : "Describe what you are looking for…"} aria-label="Search query" required={mode !== "keyword"} />
        <button type="submit">Search</button>
      </form>
      {err && <p className="err" role="alert">{err}</p>}
      {res && (
        <ul className="hits">
          {res.length === 0 && <li className="empty">No matching documents.</li>}
          {res.map((r) => (
            <li key={`${r.document_id}-${r.invoice_id ?? "x"}`}>
              <button onClick={() => onOpen(r.document_id)}>
                <strong>{r.filename}</strong> {r.invoice_number && <span>{r.invoice_number}</span>} {r.supplier && <span>{r.supplier}</span>}
                {r.total && <span className="num">{r.total} {r.currency}</span>}
                {r.sources && r.sources.map((s) => <span key={s} className={`chip ${s === "semantic" ? "busy" : "ok"}`} title={s === "semantic" ? "matched by meaning" : "matched by words"}>{s}</span>)}
              </button>
              {r.snippets?.map((s, i) => <p key={i} className="snippet" dangerouslySetInnerHTML={{ __html: `p.${s.page_no}: ${safeSnippet(s.snippet)}` }} />)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function App() {
  const [docs, setDocs] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const [needKey, setNeedKey] = useState(false);
  const [keyInput, setKeyInput] = useState("");

  const refresh = useCallback(async () => {
    try { setDocs((await api.listDocuments()).documents); setError(null); setNeedKey(false); }
    catch (e) {
      if (e instanceof ApiError && e.status === 401) { setNeedKey(true); setError(null); }
      else setError(`Cannot reach the API (${e.message}). Is the backend running on port 8000?`);
    }
  }, []);
  const retryFailed = async () => { try { await api.retryFailed(); await refresh(); } catch (e) { setError(e.message); } };

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    if (!docs.some((d) => ACTIVE.has(d.status))) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [docs, refresh]);

  return (
    <div className="app">
      <header className="top"><h1>Document Intelligence</h1><Search onOpen={setSelected} /></header>
      {error && <p className="banner bad">{error}</p>}
      {needKey && (
        <form className="banner warn keyform" onSubmit={(e) => { e.preventDefault(); apiKey.set(keyInput.trim()); refresh(); }}>
          <label>The API requires a key: <input type="password" value={keyInput} onChange={(e) => setKeyInput(e.target.value)} aria-label="API key" /></label>
          <button type="submit">Use key</button>
        </form>
      )}
      <main>
        <aside><Uploader onUploaded={refresh} />
          {docs.some((d) => d.status === "failed") && <button className="secondary wide" onClick={retryFailed}>Retry all failed ({docs.filter((d) => d.status === "failed").length})</button>}
          <DocList docs={docs} selected={selected} onSelect={setSelected} /></aside>
        <section className="detail">{selected ? <Inspector key={selected} id={selected} onChanged={refresh} onDeleted={() => { setSelected(null); refresh(); }} /> : <p className="empty">Select a document to inspect its extracted fields.</p>}</section>
      </main>
    </div>
  );
}
