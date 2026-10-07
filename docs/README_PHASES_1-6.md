# Koramangala Projects

A public web app that rebuilds the history of government road and public-works projects in **Koramangala, Bengaluru**. Each project page answers four questions:

1. What was planned
2. Who got the work
3. How much money was paid
4. What happened

Every fact carries a confidence label and a link to its source:

| Label | Meaning |
|---|---|
| `confirmed` | Taken directly from a source record |
| `inferred` | Derived or matched; the reasoning is shown next to the fact |
| `unknown` | No public source we found publishes it |

Nothing is invented.

**What the data covers:**

- 432 projects (388 with BBMP bills), including every paid Koramangala work bill in BBMP's public payment grid since June 2015.
- 68 projects have both a tender and BBMP bills connected, which gives the full tender → contractor → payments story.
- Planned-vs-billed quantity comparisons for 14 of the 30 highest-value projects (Phase 5).
- Automated *signals*: patterns worth a closer look, not findings of wrongdoing.
- Records span 2012 to Sept 2026.
- Stack: Python 3, SQLite, Flask (server-rendered pages and one CSS file).

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m ingest.fetch_opencity    # BBMP bill exports + 2013-18 BBMP tender lists (~50 MB)
.venv/bin/python -m ingest.fetch_kppp        # KPPP tenders that mention Koramangala (live search)
.venv/bin/python -m ingest.fetch_kppp_docs   # tender documents + comparative statements (~240 MB, ~30 min)
# archived BBMP documents (Wayback) are in data/raw/wayback/ — scanned ones OCR'd with tesseract
.venv/bin/python -m ingest.fetch_bbmp_direct --no-files # BBMP IFMS work bills (public vssWB service; ~45 min, 1 req/s)
.venv/bin/python -m ingest.fetch_bbmp_direct            # + attachments: work orders, agreements, completion certs, photos (~2 GB)
.venv/bin/python -m experiments.reality_check          # optional: satellite before/after experiment (~2 h)
.venv/bin/python -m ingest.discover_bbmp       # Phase 5: every paid work bill city-wide from the IFMS payment grid (46 requests)
.venv/bin/python -m ingest.fetch_bbmp_direct --no-files # (re-run) direct records for newly discovered Koramangala jobs
.venv/bin/python -m ingest.build             # → data/koramangala.db (~90 s)
.venv/bin/python -m ingest.quantities        # Phase 5: Schedule B / estimate / MB / bill-form quantities, top 30 projects (~600 files)
.venv/bin/python -m ingest.eot               # Phase 5: extension-of-time orders attached in IFMS (~350 files)
.venv/bin/python -m ingest.build             # rebuild with quantities + EoT
.venv/bin/python -m experiments.phase5_metrics  # coverage / denominator / quantity metrics → data/phase5_metrics.json
.venv/bin/python -m app.app                  # http://127.0.0.1:5000  (PORT=… to change)

.venv/bin/python -m pytest -q                # 127 tests: parsers, scope, matching, truth model, signals, discovery, quantities, DB, pages
```

`./run.sh` runs the whole sequence and skips any step whose output already exists. Text is extracted from `.doc`/`.docx` tender documents with macOS `textutil`. On Linux, swap in `antiword` or `libreoffice`; `.docx` files fall back to plain XML parsing.

## Pages

| URL | What it shows |
|---|---|
| `/` | Search and filters (category, status, signal, lifecycle depth, department) plus sorting (recent, most complete story, most signals, value) |
| `/p/<slug>` | The project story, in order: signals, the four stages (each fact with its confidence label and source), a timeline, how the tender and bills were connected (accepted and "possible" candidates, with supporting and contradicting evidence), tender notices, bids, and every bill |
| `/signals` | Every signal, grouped by type, plus a contractor-concentration table |
| `/sources` | Data sources, the truth model, the matching method, and every file used |
| `/api/projects`, `/api/projects/<slug>` | JSON |

## Data sources

| Source | What it gives | Years |
|---|---|---|
| **BBMP Works Bill Public View / IFMS**, via [OpenCity](https://data.opencity.in) exports (BBMP's portal is unreachable from outside India) | Bills against BBMP *job numbers*: payee, gross/deduction/nett, bill type (running/final, published only up to 2022), work-order no./date, start/end date, payment reference | 2010 – Mar 2026 |
| **KPPP** ([kppp.karnataka.gov.in](https://kppp.karnataka.gov.in)) public JSON endpoints | Tender notice, estimated contract value, provisional amount, awarded bidder, bid and negotiated value, award date | 2023 – 2026 |
| **KPPP tender documents** (`/{nit}/works-tender-file/{uuid}/download-file`) | Stipulated **period of completion** | 2023 – 2026 |
| **KPPP commercial comparative statements** (`/tender-eval/{nit}/…/commercial-comparison/download-detailed`) | **Every bidder** and their quoted total (L1, L2, …) | 2023 – 2026 |
| **BBMP tender lists** ([OpenCity: bbmp-tenders](https://data.opencity.in/dataset/bbmp-tenders)) | Tender no., title, estimated value, publish and closing dates | 2013 – 2018 |

Sources investigated but not used:

- **The old Karnataka eProcurement portal (eproc.karnataka.gov.in).** It is reachable, but its search returns "No Tenders Found" for BBMP Koramangala tenders.
- **BBMP Road History PDFs.** There is no file for ward 151.
- **The Sony World junction geotechnical report.** It contains no lifecycle data.

## Phase 6: investigation packs

See [PHASE6_REPORT.md](PHASE6_REPORT.md), [CASE_INDEX.md](CASE_INDEX.md), [RTI_QUESTIONS.md](RTI_QUESTIONS.md) and `cases/`.

- **`ingest/case_evidence.py`** fetches, for each candidate job, every variation document, final-bill form / MB, work order, agreement and completion certificate (1 request/s, cached). It reads them, with orientation detection for scans that are sideways or upside down. It parses work slips / comparative statements row by row; each row is checked three ways: tender qty × rate = amount, executed qty × rate = actual, and tender − actual = savings/excess.
- **`ingest/cases.py`** builds one case per project. It runs every candidate signal through a resolution attempt:
  - variation reconciliation;
  - work-order totals;
  - shared package contracts;
  - final-bill confirmation;
  - public-listing gaps.

  Each issue is then classed `explained`, `artefact`, `partially_explained`, `insufficient_evidence` or `unexplained_in_public_record`. Cases are scored transparently (`case_score` = evidence strength + financial materiality + unresolved + documentation gap + timeline deviation + independent sources). Low-confidence and dead issues never become cases.
- **Outputs:**
  - `data/investigation_cases.json` and the DB table `cases`;
  - one Markdown pack per retained case in `cases/`;
  - `CASE_INDEX.md`;
  - `RTI_QUESTIONS.md`.

  Every sentence in a pack is generated from case fields that carry their evidence.

```
.venv/bin/python -m ingest.case_evidence   # fetch + read candidate documents
.venv/bin/python -m ingest.cases           # build cases, packs, index, RTI questions
.venv/bin/python -m ingest.build           # rebuild the DB (adds the `cases` table)
```

## Phase 5: coverage expansion and quantity verification

See [PHASE5_REPORT.md](PHASE5_REPORT.md).

- **Discovery** (`ingest/discover_bbmp.py`). BBMP's public payment grid (`LoadPaymentGridData`, no filters, one call per quarter since Apr 2015) lists all ~90,000 paid work bills city-wide. The Koramangala scope rules pick out 686 bills on 484 jobs: 305 bills and 94 jobs were new. Results are in the `discovery` table and `data/discovery_koramangala.csv`. Job numbers that repeat across ward maps are guarded against; foreign bills are logged in `data/raw/bbmp_direct/collisions.json`.
- **Quantities** (`ingest/quantities.py`). For the 30 highest-value projects, the pipeline:
  - reads Schedule B / estimates and IFMS bill forms / MBs;
  - accepts a line item only when quantity × rate = amount on the row;
  - reads the bill form's "Total up to date" column;
  - normalises units and matches items by schedule-of-rates code or contract rate.

  Results go to the `work_items` table, each with full provenance (attachment URL, page, raw row, method, confidence). Quantities never overwrite IFMS values.
- **Extensions of time** (`ingest/eot.py`). EoT orders attached in IFMS are read for "from X to Y", days and fine. The latest extended date feeds the time signals.
- **Projects.** No 200-project cap any more: every Koramangala job ≥ ₹5 L is a project.

## Phase 4: direct BBMP evidence

**Where the data lives.**
- BBMP's Works Bill service moved from account.bbmpgov.in, which is now dead even from Indian networks, to **`https://accounts.bbmp.gov.in/vssWB/`**. It is public and needs no login.
- The server omits its GoDaddy G2 intermediate certificate. We add the published intermediate (`ingest/certs/`) and keep TLS verification on.

**What we fetch** (`ingest/fetch_bbmp_direct.py`). The API is `vss00CvStatusData.php`:

| Action | Returns |
|---|---|
| `LoadTypeCombo&pJobNumber=<job>&pSelection=1\|2\|3` | Every work bill of a job, which finds bills the exports never had |
| `LoadDetails` | Bill type, BR/commonBR, RTGS, release %, approval level |
| `LoadDeductions` | Deductions, including fines, withheld amounts and mobilisation advance |
| `LoadGridApprovalLevels` | The approval chain, with officials' remarks |
| `LoadWBFiles` / `LoadFilesDetails` | Attachments, downloaded from `/vssIFMS/Files<raddl>/<name>` |

How the collector behaves:
- 1 request per second, everything cached.
- Contractor mobile and e-mail are dropped before writing.
- `LoadAllPhotos` is **not** used. Its id is a DC-bill id, and with a work-bill id it returns other works' photos.

**Merging** (`ingest/direct.py`).
- IFMS bills are matched to our bills by job + BR number/date, then by amount.
- Paid IFMS-only bills (those with an RTGS payment) are added as new bills.
- Drafts and unpaid bills are kept as "pending", not as payments.

**Reading the attachments** (`ingest/documents.py`). Work orders, agreements, letters of acceptance and completion reports are OCR'd (parallel tesseract). Extracted fields, each with document and page provenance:
- contract value
- time allowed, commencement, stipulated completion, actual completion
- work order / LoA / agreement / GO references
- sanctioned estimate, tender premium, work slip, time extension, delay

OCR values are inferred unless another source agrees. An OCR'd contract value under 10% of what was paid is discarded as a misread.

**Lifecycle.** planned → contracted → work documented → payments → completion → physical evidence (`truth.lifecycle`).

## Phase 3 additions

**Contractor identity** (`ingest/contractors.py`). Name variants are joined into contractor entities using union-find. Every join records its rule:

| Rule | Confidence |
|---|---|
| Same IFMS contractor code | confirmed |
| Same KPPP `supplierId` | confirmed |
| Identical KPPP account display name (KPPP adds "(1)" to duplicate person names) | confirmed |
| Same registered mobile on BBMP bills **and** a similar name | inferred |
| Same or truncated name (IFMS cuts names at 20 characters) | inferred |
| KPPP person/firm name equals an IFMS payee | inferred |

Guards against wrong merges:

- A phone shared by three or more contractor codes never merges anything.
- Generic business words ("enterprises", "constructions") don't count as name evidence.
- A firm name used by several KPPP accounts is never joined ("Balaji Constructions" is three different accounts).
- A short person name ("M Ramesh") joins across systems only when both names appear on the same linked project.

Mobile numbers are used in memory while building only. They are never stored or shown. Each contractor's page (`/c/<entity>`) shows its identifiers, the joins made, its projects, its bids and its co-bidders.

**Archived BBMP documents** (`ingest/plans.py`, Internet Archive copies of the geo-blocked BBMP sites):

| Document | What it gives | How it is used |
|---|---|---|
| FY 2015-16 / 2016-17 ward works lists | Job code → sanctioned estimate | Attached by exact job number (confirmed) |
| Programme of Works 2022-23 (South) | Estimate and approval status for ward 186 = Koramangala | Matched to jobs |
| BTM division tender notices 2018-19 (scanned, OCR) | Approximate value and period of completion | Matched to jobs |
| Amrutha Nagarothana grants for BTM Layout (2021-22 GO, OCR) | Planned amount | Matched to jobs |
| Horticulture South park-maintenance tenders 2022 | Estimate, tendered amount, L1 bidder | Matched to jobs |

Values read by OCR are always marked inferred.

**Direct BBMP collector** (`ingest/fetch_bbmp_direct.py`). The BBMP IFMS JSON API (`vss00CvStatusData.php`) was reconstructed from archived copies of its pages:

| Action | Returns |
|---|---|
| `LoadPaymentGridData` | Bill rows, with the same columns as the OpenCity exports (OpenCity `id` = work-bill id) |
| `LoadDetails` | Bill type for 2022–26 bills |
| `LoadDeductions` | Deductions, including fines |
| `LoadGridApprovalLevels` | The bill's approval chain |
| `LoadWBFiles` | Files attached to the bill |
| `LoadAllPhotos` | Site photos with latitude/longitude |

account.bbmpgov.in times out on TCP 443 from outside India. `data/raw/bbmp_direct/ACCESS_REPORT.json` records this. Run the collector from an Indian IP and `ingest.build` merges the results automatically.

**Reality check** (`experiments/reality_check.py`, results in `data/reality/`):

1. Geocode the places named in the work description with OpenStreetMap.
2. Find the distinct satellite captures at that point in Esri World Imagery Wayback (196 releases, 2014–2026, with capture date, sensor and resolution).
3. Assemble a 0.6 m before/after mosaic and review it by hand (`assessments.json`).

The result appears as an inferred `imagery` fact. See `PHASE3_REPORT.md` for findings.

**RTI.** `docs/RTI_REQUESTS.md` lists exactly which records to request, and which job numbers need them, to close the 2019–23 gap.

## Pipeline (`ingest/`)

| Module | Role |
|---|---|
| `parse.py` | Six bill formats and the KPPP JSON → one record shape. Contractor phone numbers are removed and never stored. |
| `scope.py` | Decides what counts as Koramangala and as public works (rules below) |
| `bills.py` | Loads and de-duplicates bills. Keys: IFMS bill id, then (job, BR no., BR date), then (job, amounts) for 2010–18 register rows. Each bill records which key matched it. |
| `tenders.py` | Builds KPPP and 2013–18 tender records and groups re-calls (`/CALL-n`). Reads the period of completion from tender documents and the bidders from comparative statements. |
| `match.py` | Entity resolution between tenders and bills (rules below) |
| `truth.py` | Assembles the facts for each project, grouped by stage, with confidence, explanation, evidence and how each record was linked |
| `signals.py` | Project-level and dataset-wide signals |
| `build.py` | Orchestrates the steps above, selects projects and writes SQLite |

### Scope rules (`scope.py`)

A record counts as Koramangala when either holds:

- It is booked to the Koramangala ward for its ward map: 151, 174 or 186, depending on which of the three ward maps the source uses.
- Its text names Koramangala, one of its blocks, or the NGV (National Games Village).

These are excluded:

- Matches that only mention the city-wide K-100 "Koramangala Valley" storm-drain system.
- Villages called Koramangala in rural districts.
- Non-works: events, consultancy, manpower contracts, and interiors of staff quarters.

### Entity resolution (`match.py`)

Tenders and bills share no identifier, so every link between them is an inference. Each candidate pair is scored on these pieces of evidence:

| Evidence | How it is measured |
|---|---|
| Description similarity | TF-IDF cosine and sequence ratio, on content words only. Boilerplate such as "in ward no 151 Koramangala" cannot create a match. |
| Same kind of work | road, drain, water, light, park, building… |
| Same places | Canonical keys: `block6`, `main17g`, localities |
| Ward numbers | Compared across the three ward maps |
| Awarded bidder | Must be among the payees on the bills |
| Timing | Bills must start after the tender and within 3 years of it. Job-number year must be close to the tender year. |
| Amount | Paid amount must be plausible against the tender value |
| Engineering division | Same division |

Confidence levels:

| Level | Conditions |
|---|---|
| **high** | Similarity ≥ 0.90, no contradiction, at least one corroboration, and bills start within 2 years |
| **medium** | Score ≥ 0.62, similarity ≥ 0.72, no hard contradiction, at least two corroborations, at least one of them specific (kind, place, contractor, or amount within ±20%) |

Hard contradictions:

- bills start before the tender
- different ward
- different place
- different kind of work

Assignment is one tender to one job, best evidence first. Weaker candidates are stored as `possible` and shown for review only.

### Truth model (`truth.py`)

Facts are grouped into four stages:

- **planned**: description, location, ward, estimate, budget provision, tender published, tender calls, time allowed
- **awarded**: contractor, contract value, bidders, award date, work-order date
- **money**: gross, nett, paid vs contract, bills, first and last payment
- **outcome**: status, start, expected completion, final bill, completion date

Confidence rules:

- **confirmed**: copied, or exactly summed, from records tied to the project by an exact id: the job number for bills, the tender number for tender-only projects.
- **inferred**: depends on a match or an interpretation:
  - any fact that came through a tender↔bills link
  - status derived from bill type
  - expected completion (work-order or award date + time allowed; the real start is the unpublished notice-to-proceed)
  - place names read from text
  - totals that needed de-duplication by amount

### Signals (`signals.py`)

A signal is a neutral flag, never an accusation. Each has a severity (info / notice / attention), a confidence label and evidence.

| Signal | Fires when |
|---|---|
| Paid over contract / estimate | Payments exceed the contract value or the estimate |
| Front-loaded payment | ≥ 75% of the contract is paid within 40% of the time allowed |
| Over stipulated period / long-running | Activity runs well past the time allowed, or longer than 3 years |
| Past expected completion with no final bill | Only checked where bill types are published |
| No bill after expected completion | Tender-only projects |
| Running bill but no final bill in 3+ years | — |
| Tender re-called | Two or more calls |
| Single bidder | One bid in the comparative statement |
| Bidders with overlapping names | Two bidders on one tender share a name |
| Awarded above / far below estimate | Award differs from the estimate beyond a margin |
| Many bills paid the same day | — |
| Bills after the final bill | — |
| Large deduction | Deduction above 30% of a bill |
| Paid years after work end | — |
| Near-identical work under another job number | Possible repeat or duplicate |
| Contractor concentration | — |

### Selection

Every project worth at least ₹5 lakh (Phase 5 removed the earlier 200-project cap). Value is the largest of paid amount, contract value and estimate.

## Schema (`ingest/schema.sql`)

| Table | Contents |
|---|---|
| `projects` | Flat summary for listing and search. Also: `lifecycle` (0–4), `stages`, `link_confidence` (exact / high / medium / none), `n_signals`, `signal_level` |
| `facts` | `field`, `stage`, `value`, `confidence`, `explanation`, `via` (how the record was linked), `evidence` (JSON `{label, url, rows}`) |
| `links` | Tender↔job links, accepted or possible: confidence, score, similarity, support, conflict |
| `signals` | `code`, `severity`, `title`, `detail`, `confidence`, `evidence`, `related` |
| `records` | Every bill and tender notice merged into a project, with `link` (exact / high / medium), `dedupe_key`, source URL and row. Tender extras include bidders, period and documents. |
| `discovery` | Phase 5: every Koramangala bill in the IFMS payment grid, with scope basis and "previously known" flags |
| `work_items` | Phase 5: planned vs billed / measured quantity per line item, with variance and provenance JSON |
| `contractors`, `sources`, `meta` | Supporting tables |

## Known limitations

- **Contract value is missing for most bill-based projects.** It exists only for 2023–26 KPPP tenders. The 2013–18 tender lists carry estimates but no awards.
- **The two sources barely overlap in time.** Most 2024–26 KPPP awards have no bills in the published BBMP exports yet, which end around Mar 2026. Bills from 2019–2023 have no public tender list to match against.
- **There is no completion or progress data after 2022.** Bill types stop being published after 2022, and no source publishes physical progress.
- **BBMP data comes from OpenCity mirrors.** The BBMP portal itself is geo-restricted.
