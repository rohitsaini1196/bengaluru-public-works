# Koramangala Projects — Phase 2 Report: Project Truth Layer

*5 Oct 2026. App: `./run.sh` → http://127.0.0.1:5000. Tests: 32 passing.*

## 1. What changed

- **Entity resolution** (`ingest/match.py`). Tenders and bills share no identifier, so each pair is scored on independent evidence:
  - description (TF-IDF and sequence ratio, on content words only)
  - kind of work
  - canonical places (block, main/cross road, locality)
  - ward numbers across the 198-, 225- and 243-ward maps
  - awarded bidder among the bill payees
  - timing (bills start after the tender; job-number year)
  - amount paid against the tender value
  - engineering division

  Links are rated **high** or **medium**; weaker candidates are kept as **possible** and never merged. Each tender links to at most one job and each job to at most one tender. Every link shows its supporting and contradicting evidence.
- **Truth model** (`ingest/truth.py`, `facts` table). Every fact is `confirmed`, `inferred` or `unknown`, with an explanation, evidence URLs (and row numbers), and `via` (how the record behind it was linked).
  - Facts that arrive through a tender↔bills match are always `inferred`. A test enforces this.
  - So are totals that needed de-duplication by amount, status read from bill types, and computed expected completion.
- **Signals** (`ingest/signals.py`, `/signals`). 17 types of neutral flag, each with severity, confidence and evidence.
- **New sources.** Three new KPPP endpoints (section 3) and the 2013–18 BBMP tender lists.
- **UI.** Each project page tells the story in order:
  1. Signals
  2. What was planned
  3. Who got the work
  4. Money paid (with a cumulative-payment chart)
  5. What happened
  6. Timeline
  7. How the tender and bills were connected
  8. Evidence tables

  The list page shows four dots for lifecycle evidence, a link-confidence chip and signal chips. It adds filters for signal type and lifecycle depth, and sorts by "most complete story" or "most signals".
- **Selection** now puts projects with both a tender and bills first. It is still 200 projects, so the totals shift slightly: ₹320.6 Cr paid in this set.

## 2. Match rate

| | Phase 1 | Phase 2 |
|---|---|---|
| Tender↔bills links accepted | 4 (exact text only) | **51** (4 KPPP high; 39 + 8 from 2013–18 lists, high/medium) |
| Projects (of 200) with both tender and bills | 4 | **48** |
| Projects with evidence for all 4 lifecycle stages | — | **37** (81 more have 3 of 4) |
| Possible candidates shown for review, not merged | — | 98 |

How precision was checked:
- I read every accepted link. An early version accepted false matches like "RO plant" → "CC roads" and "dust bin" → "drain slabs" because of shared boilerplate.
- Fixes: content-word similarity, work-type and place conflicts, and one-to-one assignment.
- The final 51 links all describe the same work.
- Independent check: across the 44 matched 2013–18 projects, **median paid ÷ tender estimate is 0.99**, and 35 of 44 are within ±10%. Amount was only one of eight pieces of evidence used in matching.

## 3. New sources

| Source | Gives | Coverage |
|---|---|---|
| KPPP tender documents (`/{nit}/works-tender-file/{uuid}/download-file`, .doc/.docx/.pdf) | **Stipulated period of completion** ("180 days", "12 months") | 109/109 Koramangala KPPP tenders |
| KPPP commercial comparative statements (`/tender-eval/{nit}/…/commercial-comparison/download-detailed`, xlsx) | **Every bidder and their quoted total** (L1, L2 …) | 103/109 |
| BBMP tender lists 2013–18 ([OpenCity bbmp-tenders](https://data.opencity.in/dataset/bbmp-tenders)) | Tender no., title, **estimated value**, dates, re-call number | 114 Koramangala works notices → 82 groups |

Sources investigated but not usable:
- **Old Karnataka eProcurement portal (eproc.karnataka.gov.in).** Reachable, but its search returns "No Tenders Found" for BBMP Koramangala tenders.
- **BBMP Road History PDFs.** There is no file for ward 151.
- **Sony World junction report.** Geotechnical only.
- **BBMP account.bbmpgov.in.** Still geo-blocked from outside India.

## 4. Field coverage (200 projects)

| Field | Phase 1 | Phase 2 | of which confirmed / inferred |
|---|---|---|---|
| Estimated cost | 48 | **88** | 44 / 44 |
| Tender published + number of calls | ~50 | **94** | 46 / 48 |
| Time allowed (stipulated period) | 0 | **50** | 46 / 4 |
| Expected completion | 0 | **41** | 0 / 41 (award or work-order date + period) |
| Bidders (count + list) | 0 | **44** | 40 / 4 |
| Contract value | 43 | 43 | 39 / 4 |
| Award date | ~40 | 41 | 37 / 4 |
| Contractor | 193 | 193 | 193 / 0 |
| Payments | 154 | 154 | 131 / 23 (23 needed de-duplication by amount) |
| Work-order date / start date | 96 | 96 / 72 | confirmed |
| Final bill / completion date | 68 | 68 | confirmed (status "Completed" itself is inferred) |

All facts: 1,865 confirmed · 533 inferred · 1,069 unknown.

## 5. Interesting findings

These are signals, not conclusions.

1. **Ejipura–Sony World elevated corridor** (job 304-17-000105).
   - Tender: estimate **₹157.66 Cr**, May 2014, second call (from the 2013–18 list; medium-confidence link).
   - Work order: May 2017.
   - Paid so far: **₹96.1 Cr gross over 8.2 years**, in 11 bills.
   - Payees moved from Simplex Infrastructures to an escrow account of BSCPL Infrastructure.
   - A ₹104 Cr bill (Nov 2024) had **88% deducted**.
   - Three bills totalling ₹45.4 Cr were paid on 1 Jul 2025.
   - No completion record exists.
2. **BWSSB Koramangala water-pipeline tenders, 2024–25.** Seven tenders, ₹20.7 Cr awarded in total.
   - 5 had a **single bidder**.
   - 5 were **re-called** (2–3 calls).
   - The 5 that publish an estimate were all awarded 2–4% above it.
   - 6 of 7 went to two firms whose proprietors share the name "Venkatappa". Both firms bid against each other on two of the tenders.

   Shared names are common and prove nothing, but this cluster is worth a look.
3. **Street-light O&M, ward 174.** Awarded **43% below estimate** (₹15.42 L vs ₹27.07 L). It has since been paid **154% of the award** (₹23.81 L). The tender says the contract covers 8 months, so the bills probably run past the period that tender covers.
4. **Kuduremukha Colony & Blocks 1–6 roads** (₹12.38 Cr, awarded Dec 2025, 360 days allowed). **81% (₹10.08 Cr) was paid in one bill 93 days after award**, on 27 Mar 2026, which is financial-year end.
5. **Competition.** Koramangala KPPP tenders have a median of **2 bidders**; 15 of 103 had only one.
6. **Re-tendering in 2017.** 22 projects had re-called tenders, several called 3–4 times.
7. **Concentration.** M Ramesh holds 11 projects and about 15% of the money in the dataset.

## 6. Remaining gaps

- **Contract (work-order) value for bill-based projects.** The 2013–18 tender lists have estimates but no award, and BBMP bill exports have no work-order value.
- **The two sources barely overlap in time.**
  - KPPP covers 2023–26 tenders, but most of those works have no bills yet in exports that end around Mar 2026.
  - For bills from 2019–23 there is no public tender list.
- **Completion and progress.** Bill types (final/running) stop after 2022. No source publishes physical progress or the notice-to-proceed date, so expected completion is approximate and inferred.
- **Contractor identity.** Payees and bidders are matched by name only. IFMS contractor codes are stripped in parsing, and the KPPP `supplierId` is not used yet.

## 7. Recommended Phase 3

1. **Use stable contractor ids.** Keep IFMS contractor codes and KPPP `supplierId`. This would give exact contractor matching and reliable concentration and repeat-bidder analysis.
2. **Fill the 2019–23 tender gap.** Ask OpenCity or BBMP for the 2018–23 tender lists, or file an RTI request for Koramangala ward work-order registers. These would bridge the remaining 106 bill-only projects.
3. **Fetch directly from India.** Run the fetchers from an Indian IP to reach BBMP IFMS and Works Bill Public View directly. That gives live bills, work-order screens, and possibly job↔tender references.
4. **Get progress evidence.** For the 37 full-lifecycle projects:
   - geocode the place keys
   - pull before/after satellite tiles
   - add a citizen "verified on ground" input
5. **Refresh on a schedule.** Re-run KPPP weekly and re-run matching as new BBMP exports land, so awarded 2024–26 tenders gain their bills.
