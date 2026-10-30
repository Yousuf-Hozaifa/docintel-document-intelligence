async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* non-JSON error body */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export const api = {
  listDocuments: () => request("/api/documents?limit=200"),
  getDocument: (id) => request(`/api/documents/${id}`),
  search: (params) => request(`/api/search?${new URLSearchParams(params)}`),
  upload: (files) => {
    const body = new FormData();
    for (const f of files) body.append("files", f);
    return request("/api/documents", { method: "POST", body });
  },
};

// Server snippets contain <mark> tags around hits; everything else is document text and must be escaped.
export function safeSnippet(s = "") {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");
}
