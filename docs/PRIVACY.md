# Privacy and data handling

## What the data is

Every record comes from **public government sources**:
- BBMP's IFMS works-bill service (public, no login);
- the Karnataka Public Procurement Portal;
- OpenCity mirrors of BBMP exports;
- archived BBMP documents on the Wayback Machine.

The site describes public works contracts: what public money was committed and paid, to which firm, and on what documents.

## Personal data

| Data | Handling |
|---|---|
| Contractor mobile numbers and e-mails (present in IFMS responses and the payment grid) | **Removed before anything was written to disk** (`redact()` in `ingest/fetch_bbmp_direct.py`, `sanitize()` in `ingest/discover_bbmp.py`). One step uses a mobile number to merge spelling variants of a firm name, in memory only; it is never stored. |
| Phone numbers and e-mails inside scanned documents (letterheads, addresses) | Free text read by OCR is scrubbed when the public database is built (`ingest/publish.py`). The build aborts if any pattern survives. URLs and identifiers are left intact. |
| Contractor and firm names | Published: they are the public counterparty of a public contract. |
| Officials' designations and approval remarks | Published as recorded in the public approval chain (official capacity only). |
| Raw cached responses and downloaded attachments (≈3.6 GB) | **Not published.** They stay on the build machine and are excluded from the repository and the Docker image. |
| Visitors to the site | No cookies, no analytics, no accounts, no forms. The server logs request lines with client IPs truncated (/24, /48) by Caddy. |

## How the data was collected

- About one request per second, every response cached, retries with back-off.
- Requests identify themselves as civic research.
- No authentication, CAPTCHA, rate limit or access control was bypassed. Where a view required a session, collection stopped. For example, bill-level attachments for bills from mid-2024 return a session error and were not pursued.
- TLS verification stayed on. The IFMS server omits an intermediate certificate, so the published GoDaddy G2 intermediate is added to the trust store (`ingest/certs/`).

## Corrections and removal

Report errors, or requests to remove personal data that should not be public, through the repository issues or the contact on the site's About page. Valid requests are applied in the next data publish.
