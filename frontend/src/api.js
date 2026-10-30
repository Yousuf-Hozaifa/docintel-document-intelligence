const KEY = "docintel_api_key";
export const apiKey = { get: () => sessionStorage.getItem(KEY) || "", set: (v) => sessionStorage.setItem(KEY, v) };

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (apiKey.get()) headers["X-API-Key"] = apiKey.get();
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { const body = await res.json(); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  listDocuments: () => request("/api/documents?limit=200"),
  getDocument: (id) => request(`/api/documents/${id}`),
  search: (params) => request(`/api/search?${new URLSearchParams(params)}`),
  upload: (files) => {
    const body = new FormData();
    for (const f of files) body.append("files", f);
    return request("/api/documents", { method: "POST", body });
  },
  patchInvoice: (id, patch) => request(`/api/invoices/${id}`, json("PATCH", patch)),
  corrections: (id) => request(`/api/invoices/${id}/corrections`),
  resolveIssue: (id, resolved) => request(`/api/issues/${id}/resolve`, json("POST", { resolved })),
  reprocess: (id) => request(`/api/documents/${id}/reprocess`, json("POST")),
  retryFailed: () => request("/api/jobs/retry-failed", json("POST")),
};

// Server snippets contain <mark> tags around hits; everything else is document text and must be escaped.
export function safeSnippet(s = "") {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");
}
