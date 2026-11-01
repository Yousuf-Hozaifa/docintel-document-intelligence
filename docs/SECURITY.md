# Security notes

Scope: a proportionate, single-tenant design for an assessment. It is **not** hardened for hostile multi-user or internet-facing use. Statements marked ✔ are covered by an automated test or were directly exercised; ✖ means not implemented.

## Implemented
| area | control | evidence |
|---|---|---|
| Upload | extension allow-list; per-file **streamed** size limit (`DOCINTEL_MAX_UPLOAD_MB`, default 200) with partial files deleted; magic-byte check that must agree with the extension (a ZIP that is not `.xlsx`, text named `.pdf`, etc. are rejected); rejected files are never left on disk | ✔ `test_security.py`, `test_readers.py` |
| Storage | files stored under random UUID names inside one directory; the user's filename is sanitised and only kept as a display label; path traversal and Windows-style names neutralised | ✔ `test_path_traversal_*`, `test_windows_style_*` |
| File serving | `GET /documents/{id}/file` serves only detected PDF/image/spreadsheet types, type from content (not the client), `nosniff`, CSP `default-src 'none'`, no caching, spreadsheets as attachment; the resolved path must stay inside the uploads directory (also enforced on delete) | ✔ `test_original_file_*`, `test_poisoned_stored_name_*` (mutation-checked) |
| Parser resource limits | OCR timeout per page (90 s), image decompression-bomb limit (100 MP), spreadsheet row cap (100k), CSV size cap, encrypted PDFs refused, page-level failure isolation, bounded concurrency/memory | ✔ readers tests; benchmarks |
| API access | optional shared secret (`DOCINTEL_API_KEY` → `X-API-Key`, constant-time compare) on every endpoint except `/api/health`; applies to file download, deletion and semantic endpoints | ✔ `test_api_key_*`, others |
| CORS | explicit origin allow-list (`DOCINTEL_CORS_ORIGINS`, default the Vite dev origins), limited methods/headers | ✔ `test_cors_*` |
| Injection | all SQL parameterised (dynamic column names come from fixed allow-lists); FTS queries are tokenised and quoted; the UI renders server text through React (escaped), and search snippets are escaped except `<mark>` | ✔ search-injection test; browser checks |
| Information leakage | failure messages exclude server paths; API errors are generic | ✔ `test_failure_messages_do_not_leak_server_paths` |
| Data lifecycle | `DELETE /api/documents/{id}` removes file, text, extracted data, embeddings and history | ✔ `test_delete_*` |
| Integrity | corrections are audited (old/new value, note, time); results show evidence and OCR confidence | ✔ |
| Secrets | none in the repository; the API key comes from the environment. Default bind address in the documented commands is `127.0.0.1` | – |

## Not implemented (known gaps)
- ✖ **User accounts, roles, per-tenant isolation.** One shared key (if set); anyone holding it can read, correct and delete everything. With no key configured the API is open (development default).
- ✖ **Rate limiting / quotas / abuse protection**, and request-size limits other than the upload limit.
- ✖ **Parser sandboxing.** PyMuPDF, Pillow, openpyxl and Tesseract process untrusted files in the server's own OS user; a memory-safety bug in a parser would affect the whole process. Mitigation for production: run workers in a locked-down container or separate user.
- ✖ **TLS**, **encryption at rest** (SQLite and uploads are plain files), **backups**, **antivirus/malware scanning**, **audit log of reads**, **automatic retention/expiry**, **PII redaction**.
- ✖ The UI keeps the API key in `sessionStorage` (readable by any script running in the page); a cookie-based session with CSRF protection would be needed for production.
- ✖ No dependency vulnerability scanning or SBOM was run.
- ✖ No penetration test; the controls above were tested only by the unit/integration tests listed.
- Untested: behaviour under disk-full or very large concurrent uploads.
