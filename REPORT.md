# Koramangala Projects — Run Report

*Run date: 5 Oct 2026. Data: BBMP bills (via OpenCity exports) to Mar 2026; KPPP tenders to Sep 2026.*

The full pipeline (fetch → build → tests → serve) ran successfully. The app serves at http://127.0.0.1:5000 (`./run.sh`).

## 1. Pipeline run

| Step | Result | Time |
|---|---|---|
| Fetch BBMP data (OpenCity) | 45 source files (cached) | 0.5 s |
| Fetch KPPP tenders (live) | 129 Koramangala works tenders | 17 s |
| Build database | 223,579 rows parsed → **200 projects** | 22 s |
| Tests | 24 passed | 0.5 s |
| Smoke test | `/`, search, filters, sorts, `/sources`, API and **all 200 project pages** return HTTP 200 (3–20 ms each) | — |

### How 223,579 rows became 200 projects

| Stage | Count |
|---|---|
| Rows parsed | 223,579 |
| Koramangala bill rows | 1,361 |
| Unique bills after de-duplication | 531 |
| Koramangala tender groups (re-calls merged) | 109 |
| Tenders dropped as not public works (staff-quarter interiors, events, services) | 59 |
| Bill-based projects dropped as not public works | 43 |
| Candidate projects | 391 |
| Candidates worth ≥ ₹5 lakh | 355 |
| **Projects published** | **200** |

### Fixes made during this run

- **Department names merged.** BBMP appeared both as "BBMP" and as "Bruhat Bengaluru Mahanagara Palike"; these are now one department.
- **Contractor ranking.** Payees are now ranked by amount billed instead of number of bills. The main works contractor now comes first. Quality-inspection consultants and escrow accounts billed under the same job are listed in a note.

## 2. Dataset overview

- **₹327.9 Cr** paid (gross) across the 154 projects with bills.
- **₹116.2 Cr** awarded tender value across 43 tenders.
- **₹168 Cr** estimated value of 46 tenders with no bills yet (39 awarded, 7 under evaluation).
- **Date range:** payments from Jun 2015 to Mar 2026; tenders up to Sep 2026.

### Why each project counts as Koramangala

| Basis | Projects |
|---|---|
| Booked to the Koramangala ward number (151 / 174 / 186, depending on the ward map) | 127 |
| Work description names Koramangala, a Koramangala block or NGV | 27 |
| Tender title names Koramangala | 46 |

### By category

| Category | Projects | Paid | Contract value |
|---|---|---|---|
| Roads, drains & footpaths (combined) | 84 | ₹121.4 Cr | ₹59.8 Cr |
| Junctions / flyovers (Ejipura corridor) | 1 | ₹96.1 Cr | — |
| Roads | 25 | ₹32.4 Cr | ₹0.6 Cr |
| Buildings & civic facilities | 17 | ₹30.2 Cr | ₹32.3 Cr |
| Parks & playgrounds | 10 | ₹15.5 Cr | ₹0.4 Cr |
| Water supply & sewerage | 16 | ₹8.6 Cr | ₹21.9 Cr |
| Drains & storm-water | 19 | ₹8.6 Cr | ₹0.2 Cr |
| Street lighting & electrical | 8 | ₹6.9 Cr | ₹0.2 Cr |
| Other (general civic, solid waste, footpaths) | 20 | ₹8.1 Cr | ₹0.9 Cr |

### By status

| Status | Projects |
|---|---|
| Bills paid; completion not stated | 83 |
| Completed (final bill raised) | 68 |
| Tender awarded, no bills yet | 39 |
| Tender under evaluation | 7 |
| Running bill only (no final bill found) | 3 |

### By department

| Department | Projects |
|---|---|
| Bruhat Bengaluru Mahanagara Palike (BBMP) | 178 |
| Bengaluru Water Supply and Sewerage Board (BWSSB) | 10 |
| Bengaluru South City Corporation (BSCC) | 4 |
| Rural Development and Panchayat Raj (RDPR) | 3 |
| KPTCL | 2 |
| PWD, KSPHIDCL, BMTC | 1 each |

### Payments by year

Only bills with a recorded payment date are counted.

| Year | Bills | Paid |
|---|---|---|
| 2015 | 1 | ₹0.4 Cr |
| 2016 | 10 | ₹5.1 Cr |
| 2017 | 14 | ₹7.6 Cr |
| 2018 | 16 | ₹29.1 Cr |
| 2019 | 11 | ₹3.1 Cr |
| 2020 | 13 | ₹5.3 Cr |
| 2021 | 1 | ₹0.1 Cr |
| 2022 | 26 | ₹37.9 Cr |
| 2023 | 29 | ₹23.2 Cr |
| 2024 | 40 | ₹51.0 Cr |
| 2025 | 34 | ₹93.5 Cr |
| 2026 (to Mar) | 8 | ₹20.3 Cr |

Spending has risen sharply since 2022.

## 3. Largest projects

| Project | ID | Status | Money |
|---|---|---|---|
| Ejipura–Inner Ring Road elevated corridor (Sony World / Kendriya Sadan junctions) | 304-17-000105 | Bills paid, not completed | ₹96.1 Cr paid over 11 bills, 2017–2025. Contractor was Simplex Infrastructures; now an escrow account of BSCPL Infrastructure |
| 20 MLD sewage treatment plant at Koramangala (BWSSB) | BWSSB/2025-26/WT/WORK_INDENT2527 | Tender under evaluation | ₹73.6 Cr estimate |
| Footpath, RCC drain and culvert works | 148-23-000003 | Bills paid | ₹19.7 Cr paid |
| 72 KSRP police quarters, Koramangala (KSPHIDCL) | KSPHIDCL/2023-24/BD/WORK_INDENT69 | Tender awarded | ₹19.0 Cr contract |
| Comprehensive road development with white-topping edges | BBMP/2025-26/OW/WORK_INDENT7214 | Tender awarded | ₹16.7 Cr contract |
| Multipurpose building, Tank Bund Road | 186-23-000001 | Bills paid | ₹15.0 Cr paid |
| Kuduremukha Colony and Blocks 1–6 roads | 174-26-000008 | Bills paid | ₹12.4 Cr contract; ₹10.1 Cr paid (81%) |
| ESI hospital and BBMP office, 5th Block (BSCC) | BSCC/2025-26/OW/WORK_INDENT20 | Tender awarded | ₹10.7 Cr contract |

### Top contractors

| Contractor | Projects | Paid | Contract value |
|---|---|---|---|
| M Ramesh | 8 | ₹41.3 Cr | ₹38.6 Cr |
| Ramachandra Raju K | 3 | ₹37.3 Cr | — |
| Dasaradha Rami Reddy | 2 | ₹26.3 Cr | — |
| KRIDL (state agency; listed under two name variants) | 30 | ₹17.2 Cr | — |

There are 98 distinct contractor names in total.

## 4. Findings

- **Tender pricing.** Across 41 tenders with both an estimate (ECV) and an award value, winning bids averaged **5% below** the estimate. 25 came in below and 16 above; the range was −43% to +5%.
- **Time to award.** Tenders took 148 days on average from publication to award (41 tenders).
- **Duration of completed works.** Projects with a final bill took 270 days on average from start to completion. The longest is a biogas plant (151-11-000024): about 6 years, Dec 2012 to Feb 2019.
- **Possible overrun.** Street-light O&M (174-25-000010) has been paid ₹23.8 L against a ₹15.4 L contract (154%). The tender covers 8 months, so the bills likely run past the period that tender covers.
- **Possibly stalled.** 16 projects show "bills paid; completion not stated" and have had no payment since 2022.

## 5. Field coverage (200 projects)

| Field | Known for |
|---|---|
| Contractor | 193 |
| Payments | 154 |
| Location (taken from the description) | 134 |
| Start / work-order date | 96 |
| Completion date | 68 |
| Estimated cost | 48 |
| Contract value | 43 |
| Expected completion | **0** (not published by any source) |

Every known value links to a source URL and row; this is enforced by a test. Contractor phone numbers are removed and never stored.

## 6. Limitations

1. **Bills and tenders rarely connect.** BBMP bills carry no tender number, and KPPP tenders carry no BBMP job number. Only 4 tenders could be matched to bills, each by an identical description. Most projects therefore show either contract value or payments, not both.
2. **No real progress status.** "Completed" means only that a final bill was raised. No source publishes an expected completion date.
3. **BBMP data comes from mirrors.** account.bbmpgov.in is not reachable from outside India, so BBMP data comes from OpenCity's public exports of it. The latest export runs to about Mar 2026.
4. **Possible double counting, 2016–19.** Overlapping registers from these years lack bill-register numbers, so a few bills may still be counted twice.
5. **Contractor names are not merged.** Spelling variants, such as "KRIDL" and "M/s KRIDL", are listed separately.
