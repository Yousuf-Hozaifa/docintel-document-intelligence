import { useCallback, useEffect, useRef, useState } from "react";
import { api, safeSnippet } from "./api.js";

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

function Inspector({ id }) {
  const [doc, setDoc] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let live = true, timer;
    const load = async () => {
      try {
        const d = await api.getDocument(id);
        if (!live) return;
        setDoc(d); setErr(null);
        if (ACTIVE.has(d.status)) timer = setTimeout(load, 1500);
      } catch (e) { if (live) setErr(e.message); }
    };
    setDoc(null); load();
    return () => { live = false; clearTimeout(timer); };
  }, [id]);

  if (err) return <p className="err">Could not load document: {err}</p>;
  if (!doc) return <p className="empty">Loading…</p>;
  return (
    <div className="inspector">
      <header>
        <h2>{doc.filename}</h2>
        <StatusChip doc={doc} />
      </header>
      {doc.status === "failed" && <p className="banner bad">Processing failed ({doc.error_code}): {doc.error_message}</p>}
      {doc.status === "partial" && <p className="banner warn">{doc.pages_failed} page(s) could not be processed. Results cover the remaining pages.</p>}
      {ACTIVE.has(doc.status) && <p className="banner busy">Processing… {doc.job?.progress_done ?? 0}/{doc.job?.progress_total ?? "?"} pages</p>}
      {doc.invoices.map((inv) => (
        <article key={inv.id} className="invoice">
          <h3>Invoice {inv.seq}{inv.page_start !== inv.page_end ? ` (pages ${inv.page_start}–${inv.page_end})` : ""}</h3>
          {inv.issues.length > 0 && (
            <ul className="issues">
              {inv.issues.map((i) => (
                <li key={i.id} className={i.severity}>
                  <code>{i.rule_id}</code> {i.message}
                </li>
              ))}
            </ul>
          )}
          <table className="fields">
            <thead><tr><th>Field</th><th>Value</th><th title="Heuristic rule confidence, not a calibrated probability">Confidence</th><th>Source text</th></tr></thead>
            <tbody>
              {Object.entries(FIELD_LABELS).map(([k, label]) => {
                const f = inv.fields[k];
                return (
                  <tr key={k} className={f.value == null ? "missing" : ""}>
                    <th scope="row">{label}</th>
                    <td className="num">{f.value ?? <em>not found</em>}</td>
                    <td className="num">{f.value != null ? `${Math.round(f.confidence * 100)}%` : "—"}</td>
                    <td className="ev">{f.evidence ?? ""}{f.note ? <em> — {f.note}</em> : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <h4>Line items</h4>
          {inv.line_items.length ? (
            <table className="items">
              <thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead>
              <tbody>{inv.line_items.map((li) => (
                <tr key={li.idx}><td>{li.description}</td><td className="num">{li.quantity}</td><td className="num">{li.unit_price}</td><td className="num">{li.amount}</td></tr>
              ))}</tbody>
            </table>
          ) : <p className="empty">No line items were extracted.</p>}
        </article>
      ))}
      <details>
        <summary>Processing history</summary>
        <ol className="history">{doc.history.map((h, i) => <li key={i}><time>{h.ts}</time> {h.event} {h.detail && <small>{h.detail}</small>}</li>)}</ol>
      </details>
    </div>
  );
}

function Search({ onOpen }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const run = async (e) => {
    e.preventDefault();
    try { setRes((await api.search(q.trim() ? { q } : {})).results); setErr(null); } catch (x) { setErr(x.message); }
  };
  return (
    <section aria-label="Search documents" className="search">
      <form onSubmit={run}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search text, supplier, invoice number…" aria-label="Search query" />
        <button type="submit">Search</button>
      </form>
      {err && <p className="err">{err}</p>}
      {res && (
        <ul className="hits">
          {res.length === 0 && <li className="empty">No matching documents.</li>}
          {res.map((r) => (
            <li key={`${r.document_id}-${r.invoice_id}`}>
              <button onClick={() => onOpen(r.document_id)}>
                <strong>{r.filename}</strong> {r.invoice_number && <span>{r.invoice_number}</span>} {r.supplier && <span>{r.supplier}</span>}
                {r.total && <span className="num">{r.total} {r.currency}</span>}
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

  const refresh = useCallback(async () => {
    try { setDocs((await api.listDocuments()).documents); setError(null); }
    catch (e) { setError(`Cannot reach the API (${e.message}). Is the backend running on port 8000?`); }
  }, []);

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
      <main>
        <aside><Uploader onUploaded={refresh} /><DocList docs={docs} selected={selected} onSelect={setSelected} /></aside>
        <section className="detail">{selected ? <Inspector id={selected} /> : <p className="empty">Select a document to inspect its extracted fields.</p>}</section>
      </main>
    </div>
  );
}
