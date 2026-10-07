# Koramangala Projects — Phase 5 Report: Coverage Expansion + Quantity Verification

*6 Oct 2026. Scope: Koramangala only. Collected from Bengaluru through BBMP's public, unauthenticated IFMS service. Rate: ~1 request/s, every response cached, contractor phone numbers and e-mails stripped before anything is written, TLS verification on. Tests: 95 passing (40 from earlier phases, 55 new).*

> **Corrections from Phase 6 (7 Oct 2026)** — see [PHASE6_REPORT.md](PHASE6_REPORT.md):
> - The 186-23-000001 gym-equipment finding is **explained**: an approved work slip ("BTM WS & EIRL") records 5 units executed per item. The "no variation document" claim was wrong.
> - The "identical to Schedule B" items on 151-23-000003 and 151-20-000096 came from running bills. The final records differ.
> - Mesthri Palya's "paid 220%" was two contracts.
> - The 13 "final bill without MB" flags were a public-listing gap (bills from mid-2024) or an MB filed under another attachment type.

## Executive result

- **Coverage more than doubled.** BBMP's public payment grid (`LoadPaymentGridData`) lists every work-bill payment city-wide since June 2015: 90,525 bills. Applying the project's Koramangala rules to it finds:
  - **686 bills on 484 job numbers**, of which **305 bills and 94 jobs were unknown** to every Phase 4 source;
  - ₹138.2 Cr gross in the newly found bills;
  - **432 projects** (Phase 4: 200) and **388 with bills** (Phase 4: 154).
- **The denominator is defensible for paid bills since June 2015.** The grid contains:
  - 310/310 paid OpenCity bills;
  - 338/338 known jobs paid in that period;
  - 820/820 paid bills fetched directly from IFMS.
  
  It is not defensible for unpaid bills, for anything before mid-2015, or beyond the reach of our text/ward-prefix scope rules (see §2).
- **Quantities were compared for 14 of the top 30 projects:**
  - 86 line items in total; 71 matched on schedule-of-rates code or contract rate.
  - Only 11 of those come from a *final* bill, and **10 of the 11 are within ±5% of Schedule B**.
  - The other comparisons are against running bills, so they show progress, not shortfalls.
- **Strongest new findings:**
  - **14 items on 4 large road projects are billed at exactly the Schedule B quantity, to the decimal.** An auditor would ask to see these measurements taken on site, but it is not evidence of wrongdoing.
  - **Outdoor-gym equipment on 186-23-000001 is billed at 4 units against 2 tendered, on 3 items** (~₹12.5 L above Schedule B at tendered rates), with no variation document attached in IFMS.
- **Measurement books are mostly not machine-readable:** 8 of 144 MB attachments yielded a checked quantity. The *bill forms* (typed IFMS abstracts) are what make verification possible: 75 of 195 were readable.
- **Extension-of-time orders are now read.** 352 EoT files on 131 jobs were fetched. 37 distinct orders on 33 jobs yielded an extended date; the rest are Kannada-language notes, contractor applications (not grants) or illegible. One time signal is resolved by an extension. The corridor's signal is **strengthened**: its two extensions (to 31 Dec 2020 and 31 Dec 2021, with penalties of ₹48.8 L and ₹1.09 Cr) belong to the original Simplex contract. Nothing extends the 14 Feb 2025 date of the 2023 balance contract.

## 1. Discovery coverage

**Method.** `ingest/discover_bbmp.py` calls `LoadPaymentGridData` with empty ward/DDO filters, one call per quarter from Apr 2015 to Sep 2026: 46 requests, cached. Empty filters return every paid bill, and there is no row cap; the four FY 2023-24 quarters sum to exactly the single-year call. Two paths were not usable:
- The ward/DDO filters cannot be used for Koramangala on the newer maps, because the ward and DDO master lists (`LoadCombo`) fail server-side.
- The Public View search returns HTTP 500.

So each row is scored with the same scope rules as the rest of the pipeline. A row is in scope if:
- (a) its work description names Koramangala, including misspellings (Koramanagala, Kormanagla, Koramanga), NGV and National Games Village; or
- (b) its job number carries Koramangala's ward prefix for the ward map in force when the job was created (151 on the 198-ward map up to FY22, 186 on the 243-ward map in FY23, 174 on the 225-ward map from FY24), **unless** its description names another locality ("ward no. 186 Jaraganahalli"). That exception rejects 12 rows.

Every new job then went through the existing collector (`LoadTypeCombo` → `LoadDetails`, deductions, approvals, attachments) and the same entity resolution and dedupe.

A new safeguard handles job numbers that repeat across ward maps. 186-23-000001 is both a Koramangala and a Jaraganahalli job, and 151-23-xxx is Maruthimandira's prefix on the 243-ward map. A directly-fetched bill is kept only if its description matches the job's known work or passes the scope rules. **11 foreign bills were removed** and logged to `data/raw/bbmp_direct/collisions.json`.

| | Phase 4 | Phase 5 | Change |
|---|---|---|---|
| Known projects (≥ ₹5 L) | 200 | **432** | +116% |
| Projects with bills | 154 | **388** | +152% |
| Koramangala job numbers in the payment grid | 390 known to our sources | **484** | **+94 new jobs** |
| Paid bills since Jun 2015 (grid) | 381 known | **686** | **+305 new paid bills** (₹138.2 Cr) |
| Work bills fetched directly from IFMS | 379 | **885** | +506 |
| Jobs with direct IFMS records | 154 | **484** | +330 |
| New projects (no Phase 4 job number) | — | **74** | ₹80.3 Cr paid |
| Bills / jobs placed in a project | — | 578 bills / 388 jobs | the rest are under ₹5 L, or out-of-scope work types (events, hiring) |

The canonical discovery table is the `discovery` table in the DB, also written to `data/discovery_koramangala.csv`. It has one row per bill with:
- bill id, job number, BR number and date, description, ward prefix, ward map, DDO, contractor;
- bill type, gross / nett / deduction, RTGS number, payment date(s), CBR;
- scope basis and reason, "job previously known", "bill previously known", the project it landed in, and the grid URL.

"Previously known" is measured against a frozen Phase 4 snapshot (`data/phase4_baseline.json`) so new bills cannot count themselves.

## 2. The denominator

**Known universe (what we hold):** 686 paid Koramangala bills on 484 jobs since June 2015, plus 1,000+ older bills from the 2010–18 registers.

**Likely complete public universe.** For *paid* bills from June 2015 onwards, we believe the known universe is close to complete:

| Independent check | Result |
|---|---|
| OpenCity-published paid bills since May 2015 found in the grid | **310 / 310** |
| Koramangala jobs paid since May 2015 (any source) found in the grid | **338 / 338** |
| Bills fetched directly from IFMS with an RTGS payment, found in the grid | **820 / 820** |
| Grid first payment date | 3 Jun 2015 |

**Where completeness cannot be established, and why:**
- **Unpaid bills.** The grid lists only RTGS-paid bills. Bills stuck at an approval level appear only when we already know the job: 32 + 5 found through `LoadTypeCombo`.
- **Before mid-2015.** The grid starts with RTGS. Older bills come only from the OpenCity registers, and their completeness is unknown.
- **Scope rules.**
  - A Koramangala work whose description does not name Koramangala, booked to a non-Koramangala ward prefix (e.g. a zonal or departmental job, 3xx), is invisible to the rules.
  - The ward and DDO master lists that would let us filter by division (BTM / BSCC) fail server-side.
  - 125 of 686 bills are in scope by ward prefix alone; the 12 rejected by the locality check are mostly 186-23 Jaraganahalli.
- **City-wide works that pass through Koramangala** (SWD K-100 valley, elevated corridor) are included only when the description names Koramangala.

## 3. Quantity verification

**Selection.** The 30 highest-value projects (by max of paid and contract value) whose IFMS listing has a plan document (Schedule B / Estimate) **and** a bill document (M.B. / Bill Form). Seven high-value projects were passed over: they list a plan document but no MB or bill form (e.g. 174-25-000008, ₹16.1 Cr; 174-24-000004, ₹7.5 Cr). The corridor (304-17-000105) is included.

**Pipeline** (`ingest/quantities.py`), in the order the brief asked for:
1. **Native text layer** if the PDF has real words.
2. **OCR:** page image upscaled 2× and run through tesseract `--psm 6`, which keeps table rows on one line.
3. **Preprocessing:** if fewer than 3 rows validate, long table rulings are removed (pure-PIL run-length filter) and the page is re-OCR'd; the version that yields more validated rows is kept.
4. **Structured parsing.** A line item is accepted only if a quantity, rate and amount on one row satisfy **quantity × rate = amount** (±0.5% or ₹2), with at least two of the three printed with decimals.
   - That arithmetic is the cross-validation.
   - A token whose decimal point OCR dropped is restored only when its digits equal the product's digits exactly; confidence is then *medium*.
   - Measurement (dimension) pages and handwritten pages are never read as quantities.

**IFMS bill forms** are typed tables: Previous (qty, amount) | Present (qty, rate, amount) | **Total up to date (qty, amount)**. The parser reads the up-to-date pair when present, which gives a cumulative executed quantity per item with its own arithmetic check.

**Matching** planned → billed, with units normalised (sqm / m², cum / m³, rmt → m, km ↔ m, sqft → sqm), by:
- (1) same schedule-of-rates code (KSRRB / KSRB / KBSR …); or
- (2) same contract rate, with some description overlap and a rate no other planned item has; or
- (3) description similarity ≥ 0.55.

Every match needs a billed rate within ±35% of the Schedule B rate. Rows OCR'd with quantity and rate swapped are re-oriented. A row re-uploaded in a later bill is counted once.

| | Count |
|---|---|
| Projects selected | 30 |
| Quantity documents fetched / readable | 551 / 215 |
| Schedule B (readable / downloaded) | 48 / 83 |
| Estimates | 84 / 128 |
| **M.B.** | **8 / 144** |
| Bill Form | 75 / 195 |
| Projects with planned items read | 26 (545 items: 291 high, 254 medium confidence) |
| Projects with billed items read | 25 |
| **Projects with a planned-vs-billed comparison** | **14** |
| Compared items | 86, of which **71 matched on code or rate** |
| … from a final bill | 11 |
| … from a running bill's up-to-date column | 23 |
| … summed across the bill abstracts we could read | 48 |
| Projects where *every* paid bill's abstract was read | **0** |

**Distribution of the 71 code/rate-matched comparisons** (billed or measured vs Schedule B):

| Band | Items |
|---|---|
| Identical (±0.05%) | 14 |
| Within ±5% | 8 |
| 5–25% below | 11 |
| More than 25% below | 29 |
| 5–25% above | 2 |
| More than 25% above | 7 |

Most "below" items come from running bills: the work was not finished at that bill. **No project had every paid bill read**, so no comparison can say a quantity was *finally* short. For that reason the "measured materially below estimate" and "major item never measured" signals did not fire anywhere: their conditions (final bill and all bills read) were never met.

Every quantity is stored in the `work_items` table, also on the project page as the "Quantities: planned vs billed" fact. Each carries its provenance:
- attachment URL;
- IFMS listing URL;
- page number;
- the raw OCR row;
- method (native / OCR / OCR with rulings removed);
- check (arithmetic / decimal restored / previous + this = total);
- confidence;
- `cross_validated`.

Quantities are separate *inferred* facts. **They never overwrite an IFMS amount, date or bill type.** Source precedence:

> IFMS API value > typed IFMS attachment read by text layer > OCR'd attachment validated by arithmetic > OCR'd attachment unvalidated (never used) > handwritten (never read).

## 4. Important findings

| Project | Expected (Schedule B) | Billed / measured | Variance | Source | Confidence | Likely explanations |
|---|---|---|---|---|---|---|
| **151-14-000060** designer tiles to footpath, Koramangala (₹1.5 Cr) | 6 items, e.g. 62.92 cum, 1,792 m, 138.16 | Final bill: 62.87 cum, 1,779.92 m, 132.06 | **−4.4% to +0.1%** | Schedule B + final bill form | high (arithmetic on every row) | Normal measurement variation. Billed quantities track the contract. |
| **151-17-000064** comprehensive roads (₹21.2 Cr) | 77 nos; 69,097.40; 11,082.41 sqm paver | Sixth & final bill, up to date: **identical** | 0.0% | Schedule B (3 scanned sheets) + bill form `bill form(7).pdf` pp. 2, 3, 6 | high / medium | Measured = estimated. Legitimate for items fixed by drawings; otherwise worth checking the MB. |
| **151-20-000096** 7th Block roads (₹10.1 Cr) | 826.46 cum, 319.65 cum, 1,987.52 cum, 1,465.16 | Running bill, up to date: **identical** | 0.0% | Schedule B + bill forms 495927, 503468 | high | As above. Other items on the same bill were 41–69% done, so the identical items were complete at exactly the tendered quantity. |
| **151-23-000003** roads + RCC drains (₹10.1 Cr) | 216 cum, 1,205.16 cum, 135.36 | Bill 562225, up to date: **identical** | 0.0% | Schedule B + bill form | high | As above. |
| **186-23-000001** multipurpose / outdoor gym (₹15.6 Cr) | 2 nos each of three gym stations (₹1.40 L, ₹2.48 L, ₹2.35 L) | Bill forms: **4 nos up to date** | **+100%** on 3 items, ~₹12.5 L above Schedule B at tendered rates. Two are in the signal; the third's up-to-date figure printed as "4,00", so it is reported but not signalled. | Accepted Schedule B p.7, 9 + bill forms pp. 12, 15 | high | A deviation/extra quantity approved on paper, or a second set installed. **No supplementary Schedule B, deviation statement or work slip is attached in IFMS.** |
| **307-19-000009** K-100 parallel drain (₹24.1 Cr) | 738.05 cum concrete; 100 nos railings | 967.85 cum; 150 nos | **+31%, +50%** | Schedule B + bill abstracts (sum of read bills) | medium (summed across bills) | "Work Slip 2" is attached, consistent with an approved variation. |
| **151-21-000002** multipurpose buildings (₹12.2 Cr) | 302.7 cum WMM | 385.11 cum up to date (running bill) | +27% | Schedule B + bill form | high | "Work Slip & EIRL Approved Statement" is attached, consistent with an approved variation. |
| **151-16-000019 / 151-16-000025** | — | — | — | Schedule B of both jobs | high | **Identical Schedule B on two jobs**: all 14 items, same quantities and rates, different uploads. 151-16-000019 is "roads in ward 151 and 173, BTM Layout" (₹5.8 Cr); 151-16-000025 is "Asphalting to 1st block Koramangala" (₹1.7 Cr). Strengthens Phase 4's finding of the identical work order and agreement on the same pair: one contract booked under two job numbers, or documents copied. |
| **304-17-000105** Ejipura–Sony World elevated corridor (₹160.7 Cr paid) | completion 4 Nov 2019 (original Simplex contract) | EoT 1: 4 Nov 2019 → 31 Dec 2020 (423 days, penalty ₹48,79,134). EoT 2: → 31 Dec 2021 (365 days, "nominal fine of Rs 1.09 crore" for delay in segment casting). Balance contract due 14 Feb 2025: no EoT, no final bill, last bill 1 Jul 2025 | three completion dates missed | EoT orders attached in IFMS (`WB-Ot--03006023`, `WB-Ot--04913968`) | inferred (typed letters, OCR) | Penalties in the orders total ₹1.58 Cr; IFMS shows ₹31 L deducted as "fine" on the bills we hold. The rest may sit under another deduction head or in bills not in IFMS. Not established. |

## 5. Phase 4 signals: resolved, weakened or strengthened

| Phase 4 signal | Phase 5 status |
|---|---|
| **Paid over contract** (6 projects) | **3 resolved.** 151-23-000001 (331%), 186-23-000001 (188%) and 186-23-000002 (202%) were artefacts of job-number collisions: Jaraganahalli / Maruthimandira bills under the same job number on another ward map. They disappeared when those 11 bills were removed. **3 remain:** street-light O&M 174-25-000010 (226%, a multi-year contract under annual job codes), Mesthri Palya park 304-15-000370 (220%; Schedule B read to ₹2.27 Cr against the ₹3.0 Cr work order, no single document explains the rest) and Beauty Spot park 304-15-000371 (139%). |
| **Corridor past stipulated completion** (304-17-000105) | **Strengthened.** The EoT orders now read from IFMS extend only the *original* contract (to 31 Dec 2020, then 31 Dec 2021, penalties ₹48.8 L + ₹1.09 Cr). Nothing is attached for the balance contract's 14 Feb 2025 date. As of the latest bill (1 Jul 2025) there is no final bill, and 5 bills (₹15.0 Cr) are pending. The signal text now says this instead of "not in the public record". |
| **Fines levied** (44 projects) | Now 122 projects (more projects, same rule). For the corridor, the EoT orders explain the fines' reason: delay, including segment casting. Elsewhere the reason is still not public. |
| **Placeholder photos** (14) | 22 projects: 14 + 8 in newly found projects. No Phase 4 case resolved. Placeholder files on MB / bill-form slots are now also counted: 12 jobs have a **final bill paid with no real MB attached** (largest: 174-24-000004, ₹7.5 Cr). |
| **Same document under two job numbers** (16) | 16, unchanged. **Strengthened** for 151-16-000019 / 151-16-000025 by the identical Schedule B. |
| **Partial release** (41) | 45. No resolution is visible: the grid shows no later release of the withheld balance for the corridor. |
| **Over stipulated period / past completion** | **1 resolved:** 151-18-000087 was "over stipulated period" and is now within its extension to 28 Feb 2022. 3 remain (151-20-000096, 174-25-000007, 174-25-000010); no readable extension covers them. EoT facts were added to 114 projects (32 with a readable extended date). |

## 6. New quantity signals and their thresholds

All are `inferred`, all are observations, and none says or implies wrongdoing. Each lists up to 5 items with their attachment pages.

| Signal | Fires when | Why this threshold | Fired |
|---|---|---|---|
| Measured / billed well below plan | quantity < **75%** of Schedule B, item ≥ ₹1 L, **and** (MB total, or final bill paid and every paid bill's abstract read) | 25% sits outside routine re-measurement. Without a final, complete set of bills a low number is just progress. | 0 (condition never met) |
| Billed above plan | > **125%** of Schedule B, item ≥ ₹1 L, every contributing row matched by code or rate (not description alone), and the bill scope is known (up-to-date column or checked against IFMS gross) | Symmetric band. Description-only matches can sum several different items. | 3 projects |
| Above plan with no variation visible | as above, and no Supplementary Schedule B, deviation statement, work slip or revised estimate attached in IFMS for the job | an EoT does not count, being time not quantity | 1 project |
| Billed item not in Schedule B | billed row ≥ ₹1 L with no plan match, **and** the Schedule B read covers 80–150% of the contract value | absence means something only when the plan was read almost completely | 0 (one earlier hit disappeared when OCR fixes removed a misread row) |
| Major planned item not billed | item ≥ 10% of the parsed plan and ≥ ₹1 L, final bill, every bill read | as for "below plan" | 0 |
| Final bill without MB | a final bill is paid and no non-placeholder M.B. / bill-form file is attached to any bill of the job | uses IFMS listings only, for all 484 jobs | 12 projects |
| High-value work with unreadable MB | paid ≥ ₹1 Cr, MB / bill-form files downloaded, none yielded a checked quantity | flags where verification from public data is impossible | 5 |
| Billed quantities identical to Schedule B *(info)* | ≥ 3 code/rate-matched items whose up-to-date quantity equals Schedule B within 0.05% | one or two identical items can be chance or drawings; three or more is a pattern | 3 |

## 7. Data-quality issues found

- **Job-number collisions across ward maps** (fixed; 11 bills).
- **Bill-form columns.** The first-generation parser read the Present column; reading the Up-to-date column changed several comparisons from "−60%" to "identical". Running-bill comparisons are labelled as such.
- **OCR artefacts that pass arithmetic:**
  - Two decimal points lost at once (1423104 × 563.16 = 801439289) balance at 100× scale. Fixed by requiring decimals on at least two of the three tokens.
  - Quantity and rate swapped on bill rows. Fixed by re-orientation against the plan rate.
- **The same Schedule B and the same bill-form row are often uploaded several times**, under different file names and on different bills; deduplicated by value.
- **Coverage ratios.** A bill's parsed abstract is compared with its IFMS gross (60–135% accepted). Most abstracts fall outside, because only some pages of a long bill form were readable.
- **"Not Applicable" placeholder PDFs** fill MB and completion slots on many jobs; they are excluded and counted.

## 8. Remaining gaps

- **No project's bills could all be read**, so final executed quantities are known only where a final bill form was readable: 11 items.
- **Measurement books stay unreadable:** 8/144, handwritten. Physical measurement remains unverified for the large majority of works.
- **Schedule B is read only partially** on most projects. The parsed plan value is 20–100% of the contract, because row-level arithmetic rejects any row OCR cannot read cleanly.
- **The denominator** holds for paid bills since June 2015 only; unpaid bills, pre-2015 bills and works not described or prefixed as Koramangala remain open.
- **No GPS:** still none for any work.

## 9. Recommended Phase 6

Decided from these results:

1. **Read whole bill forms, not pages.** The up-to-date column of the *final* bill form is the single most valuable quantity source; 75 readable bill forms already produced most comparisons. Fetch every final bill's bill form for all 484 jobs (about 400 files at 1 req/s). Read them with a column-aware parser that uses the printed header (Previous / Present / Up-to-date), so every completed project gets a final-quantity comparison. Today there are 11 items.
2. **Close the variation question with documents, not inference.** 151-21-000002 and 307-19-000009 have work-slip / EIRL statements attached. Read them item by item to confirm they cover the WMM and concrete excess. 186-23-000001 (gym equipment) has none, so it goes on the RTI list.
3. **RTI for the MB extracts and variation orders** of the identical-to-Schedule-B projects (151-17-000064, 151-20-000096, 151-23-000003), the gym-equipment items, and the 12 final bills paid without an MB attachment.
4. **Extend discovery by DDO** once `LoadCombo` recovers. Re-poll monthly, because the master-list failure is server-side. That would let the denominator include works not described as Koramangala.
5. **Track the corridor monthly** (final bill, EoT for the balance-portion contract, release of withheld amounts).

Satellite work, UI and dashboards remain out of scope.

---

**Files.**
- New modules: `ingest/discover_bbmp.py`, `ingest/quantities.py`, `ingest/eot.py`, `experiments/phase5_metrics.py`.
- New tables: `discovery`, `work_items`.
- Data: `data/quantities.json`, `data/eot.json`, `data/phase5_metrics.json`, `data/discovery_koramangala.csv`.
- Tests: `tests/test_phase5.py`.

Signals in this report are observations for follow-up. None is a finding of wrongdoing.
