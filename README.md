# Bengaluru Public Works Explorer

A source-linked reconstruction of public works in Bengaluru, starting with **Koramangala**, built from public BBMP and Karnataka procurement records. For each project it shows:
- what was promised;
- who received the work;
- what was paid;
- what documents say was completed;
- where questions remain.

> Signals and cases are observations for follow-up, not findings of wrongdoing.

## What it does

- **Rebuilds each project's lifecycle.** Planned → contracted → work documented → paid → completed → physical evidence. It draws on BBMP's IFMS works-bill service, the Karnataka Public Procurement Portal (KPPP) and archived BBMP documents.
- **Labels every fact.** Each fact is `confirmed` (read from a source record), `inferred` (derived, matched or OCR-read, with the reasoning shown) or `unknown`. Each links to its source: an API URL, attachment URL and page, or export row.
- **Reads quantities from scanned documents.** It reads Schedule B, bill forms and work slips, and accepts a row only when *quantity × rate = amount* on the printed line.
- **Turns surviving signals into evidence packs.** It generates signals, tries to *resolve* each one from the public documents, and publishes the few that survive. Each pack comes with specific RTI questions.
- **Serves it all as a read-only public website,** either as a static export for GitHub Pages or behind Gunicorn, with JSON data files.

## Why it exists

Ward-level public works spending is published, but scattered across systems that don't share identifiers:
- bill registers;
- an IFMS portal whose attachments are scanned images;
- a procurement portal;
- archived PDFs.

Answering "what happened to this road project?" takes hours of cross-referencing. This project does the cross-referencing once, keeps every link to the original record, and is honest about what the public record cannot show.

## What the dataset covers

Generated from the current data (see the site's Coverage page for live numbers):

| | |
|---|---|
| Projects reconstructed (≥ ₹5 lakh) | 432, of which 388 have BBMP bills |
| Paid bills | 592, ₹543.99 Cr gross, Jun 2015 → Sep 2026 |
| Investigation cases | 5 retained, from 23 reviewed |
| Completeness | Paid bills since mid-2015: every known paid bill from independent sources was found in BBMP's payment grid (310/310, 338/338 jobs, 820/820) |

## How the pipeline works

```
discover (IFMS payment grid, city-wide) ─┐
OpenCity bill registers / tender lists  ─┼─► scope rules (area config) ─► bills by job ─► direct IFMS records + attachments
KPPP tenders, bids, documents           ─┘                                                   │
                                     tender ↔ job scored matching ◄──────────────────────────┤
                                     truth model (facts with confidence + provenance) ◄──────┤
                                     quantities (OCR + arithmetic validation) ◄──────────────┤
                                     signals ─► case review (try to resolve) ─► cases + RTI questions
                                     publish (scrub contacts, read-only DB, dataset) ─► public web app
```

| Step | Module |
|---|---|
| Area configuration | `ingest/area.py`, `config/areas/*.json` |
| Discovery (payment grid) | `ingest/discover_bbmp.py` |
| Direct IFMS records and attachments | `ingest/fetch_bbmp_direct.py`, `ingest/direct.py` |
| Bills, tenders, plans | `ingest/bills.py`, `ingest/tenders.py`, `ingest/plans.py` |
| Scope rules | `ingest/scope.py` |
| Tender ↔ job linking | `ingest/match.py` |
| Contractor entity resolution | `ingest/contractors.py` |
| Truth model | `ingest/truth.py` |
| Signals | `ingest/signals.py` |
| Quantities | `ingest/quantities.py` |
| Extension-of-time orders | `ingest/eot.py` |
| Case evidence and review | `ingest/case_evidence.py`, `ingest/cases.py` |
| Database build | `ingest/build.py` |
| Public read model and dataset | `ingest/publish.py` |
| Web app | `app/` (`create_app("public")` / `create_app("dev")`) |

## Investigation methodology

1. **Signals are rules with stated thresholds.** Examples: payments above 125% of contract, quantities outside ±25% of Schedule B, past completion with no final bill, identical documents on different jobs.
2. **Before a signal becomes a case, the system tries to explain it.** It fetches and reads the job's work slips, comparative statements, final bill forms, work orders and extension-of-time orders.
3. **Each issue ends in one of five states:** `explained`, `artefact`, `partially_explained`, `insufficient_evidence` or `unexplained_in_public_record`. Only high- and medium-confidence open questions become cases.
4. **Cases are ranked by a transparent additive score:** evidence strength, financial materiality, unresolved, documentation gap, timeline deviation and independent sources. The score orders follow-up work only.
5. **Language is neutral.** "No explaining approval was found in the public records examined" never becomes "no approval exists".

## Example findings: resolved signals

Most of the strongest early signals disappeared under better evidence. That is the method working:

| Earlier signal | What the documents showed |
|---|---|
| Gym equipment billed at 2× the tendered quantity, "no variation document" | An approved work slip (filed as "WS") records 5 units executed per item |
| A park "paid 220% of contract" | Two contracts under one job; the first work order was scanned upside down |
| Same agreement on two jobs | One 13-work package contract booked under two job codes; payments are 99.8% of it |
| Quantities "identical to Schedule B" | Running-bill snapshots; the final comparative statement differs |
| 13 "final bills without a measurement book" | For bills from mid-2024 IFMS shows no bill-level attachments without a login; the rest were filed under another type |

Remaining cases (see `CASE_INDEX.md`):
- an elevated corridor past three completion dates with no final bill;
- items billed at exactly the tendered quantity on a ₹1 final bill;
- two payments above contract;
- an unexplained fine.

## Running locally

Requirements: Python 3.12+; `tesseract` for OCR (pipeline only).

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# public site from the sanitised database (local preview on http://127.0.0.1:8000)
.venv/bin/python -m ingest.publish        # needs data/koramangala.db (built by the pipeline)
.venv/bin/python -m app.public

# development site over the full working database (http://127.0.0.1:5000)
.venv/bin/python -m app.app

.venv/bin/python -m pytest -q             # 179 tests
```

Rebuilding the data from scratch takes several hours at the polite request rate:
1. `ingest.fetch_opencity`, `ingest.fetch_kppp`, `ingest.fetch_kppp_docs`, `ingest.discover_bbmp`
2. `ingest.fetch_bbmp_direct --no-files`, then `ingest.build`
3. `ingest.quantities`, `ingest.eot`, `ingest.case_evidence`, `ingest.cases`
4. `ingest.build`, then `ingest.publish`

Hosting is described in [DEPLOYMENT.md](DEPLOYMENT.md):
- **free:** GitHub Pages, as a static export (`python -m app.freeze`) built and deployed by `.github/workflows/pages.yml`;
- **self-hosted:** Docker with Gunicorn and Caddy.

### Another area

Area-specific rules live in `config/areas/<slug>.json`: name spellings, exclusions, ward numbers per ward map, sub-localities and procurement keywords. `AREA=hsr_layout` runs the same pipeline for HSR Layout (an unverified demo config). `python -m experiments.area_demo hsr_layout` runs discovery from the cached city-wide grid with no new collection; it finds 549 bills on 342 jobs.

## Data sources

| Source | What | Access |
|---|---|---|
| BBMP IFMS works-bill service (`accounts.bbmp.gov.in/vssWB`) | Bills, deductions, approval steps, attachment lists, payment grid; attachments at `/vssIFMS/Files…` | Public, no login |
| Karnataka Public Procurement Portal | Tenders, awards, bidders, comparative statements, tender documents | Public API |
| OpenCity | Mirrors of BBMP bill registers and 2013–18 tender lists | Public |
| Wayback Machine | Archived BBMP ward works lists and notices | Public |

Collection was cached and rate-limited to about one request per second. Contractor phone numbers and e-mails were removed before storage. No login, CAPTCHA or access control was bypassed; where a view needed a session, collection stopped. See [docs/PRIVACY.md](docs/PRIVACY.md).

## Limitations

- The paid-bill list is reliable only from mid-2015; unpaid bills may be missing.
- Bill-level attachments for bills from about mid-2024 are not public without a login.
- Absence of a document from public attachments does not prove it does not exist.
- Handwritten measurement books are not machine-readable, so on-site measurement is mostly unverified.
- OCR can fail; OCR-read values are marked `inferred` and used only when the arithmetic checks out.
- There is no GPS on site photos. Administrative documents do not prove physical existence or quality.
- Ward numbers mean different areas on different ward maps. Tender ↔ job links are inferred.

## Ethics and responsible use

- Treat every signal and case as a question, not a conclusion. Read the "possible legitimate explanations" in each case.
- Quote facts with their confidence labels. Check the linked source before publishing anything.
- Contractors and officials named in the data appear in their public, official capacity. Do not use the data to target individuals.
- Corrections are welcome. A document that explains a case should be reported and will close it.

## Contributing

- Issues and pull requests are welcome, especially corrections with a source link, area configs (with a citation for the ward numbers) and parser fixes with a test.
- Run `pytest -q` before submitting. Keep the language neutral.

## License

- **Code:** MIT ([LICENSE](LICENSE)).
- **Generated data, reports, case packs and site content:** CC BY 4.0 ([DATA_LICENSE.md](DATA_LICENSE.md)).
- **Government documents behind the source links** belong to their publishers and are linked, not redistributed ([LICENSE_NOTES.md](LICENSE_NOTES.md)).

