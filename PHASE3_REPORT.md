# Koramangala Projects — Phase 3 Report: Completing the Graph

*5 Oct 2026. Scope: Koramangala only. App: `./run.sh` → http://127.0.0.1:5000. Tests: 37 passing.*

## The main question

**How close can we get to a trustworthy end-to-end account of what the government planned, contracted, paid for, and what actually happened?**

| Stage | Projects with evidence (of 200) | How solid it is |
|---|---|---|
| **Paid for** | 154 | Strong. Bill-level BBMP records: 131 confirmed, 23 inferred (de-duplicated by amount). |
| **Contracted** (contractor + value/date) | 139 | Contractor identity is good (193 projects; 85 resolve to a fully confirmed entity). Contract value exists for only 43 projects. |
| **Planned** (estimate/tender/sanction) | 109 | Up from 94. Half is confirmed by an exact id; half comes through matching. |
| **What happened** (completion) | 117 | Only "a final bill was raised" (68) or tender status. There is no physical-progress record. |
| **Real-world evidence** | 13 tried, **1 positive** | Satellite imagery confirms only large structures. |

**Answer.** For about **one project in four** (44 of 200 with evidence at all four stages), the trail runs from plan → contractor → payments → final bill, and every claim is sourced. For the rest:
- **The money trail is reliable.** Bills are copied from BBMP's own records.
- **The planned → contracted link is the weak joint.** Tenders and bills share no identifier, so all 59 tender/plan↔bill links are inferred.
- **"What actually happened" is mostly unknowable from outside India.** Bill types stop in the public data after 2022. BBMP's geotagged site photos and approval chains exist, but sit behind a geo-block.

The most trustworthy end-to-end chain available today needs one more step: running the direct BBMP collector, built in this phase, from an Indian IP.

## 1. Contractor identity

A new module, `ingest/contractors.py`, resolves contractor entities.

| | Phase 2 | Phase 3 |
|---|---|---|
| How contractors were identified | Raw name strings (98 in projects) | **335 name variants → 278 entities** |
| Entities with an official identifier | 0 | 29 with an IFMS contractor code, 62 with a KPPP `supplierId` |
| Entities seen in both BBMP bills and KPPP tenders | 0 | 11 (all inferred; shown with the evidence) |
| Identity basis | — | 245 confirmed (official ids only), 33 inferred (mobile number or name joins) |

**Rules:**
- **Confirmed joins:** same IFMS code, same KPPP supplierId, identical KPPP account display name.
- **Inferred joins:**
  - same registered mobile on BBMP bills, *plus* a similar name
  - normalised or truncated name (IFMS cuts names at 20 characters)
  - KPPP person/firm name equal to an IFMS payee

**Guards, each tested against real over-merges found during the build:**
- A mobile number that spans three or more contractor codes is ignored. One shared office phone had merged several unrelated "KRIDL"-era payees.
- Generic words ("enterprises", "constructions") are not name evidence. One phone had merged four different "… Enterprises".
- A firm name used by several KPPP accounts is never joined. "Balaji Constructions" is three different accounts.
- Short person names ("M Ramesh") join across systems only when both appear on the same linked project.

Mobile numbers are used **in memory only**. They are never stored, hashed into the database, or shown.

Each contractor now has its own page (`/c/<entity>`) showing identifiers, the joins made, projects, bids, wins, and co-bidders.

## 2. Direct BBMP sources

| Endpoint | Status from here | What we learned |
|---|---|---|
| account.bbmpgov.in (Works Bill Public View, IFMS) | **Blocked.** DNS resolves to 117.236.190.54, but TCP 443 times out. Recorded in `data/raw/bbmp_direct/ACCESS_REPORT.json`. | The API was reconstructed from Internet Archive copies of the pages (2023-05, 2024-02). Details below the table. |
| site.bbmp.gov.in, bbmp.gov.in | Blocked (same network) | Internet Archive copies of key documents were retrieved (section 3). |
| eproc.karnataka.gov.in (old e-Procurement, 2012–23) | Reachable, but **every search returns "No Tenders Found"**, including a search with no filters | Archive no longer publicly searchable. |
| KPPP | Reachable | Records start May 2023. |

**The BBMP IFMS API (`vss00CvStatusData.php`):**

| Action | Returns |
|---|---|
| `LoadPaymentGridData` | Bill rows, with exactly the columns of the OpenCity exports. So **OpenCity `id` = IFMS work-bill id**: we already hold the key for 167 Koramangala bills. |
| `LoadDetails` | Bill type (running/final) for 2022–26 bills, which the exports lack, plus release % |
| `LoadDeductions` | Deductions, including **fines/penalties** |
| `LoadGridApprovalLevels` | The approval chain |
| `LoadWBFiles` | Attached files: measurement books, documents |
| `LoadAllPhotos` | **Geotagged site photos (latitude/longitude)** |

**Collector:** `ingest/fetch_bbmp_direct.py`.
- Run it from an Indian IP and it caches all of the above for every Koramangala bill.
- `ingest.build` then merges it automatically: bill type → final-bill status; photo coordinates → evidence.
- **Not found in any endpoint:** a tender↔job identifier or a work-order value. These remain RTI items.

## 3. New sources and the 2019–23 gap

These are Internet Archive copies of documents on BBMP's own sites (`ingest/plans.py`):

| Document | Gives | Used as | Result |
|---|---|---|---|
| FY 2015-16 / 2016-17 ward works lists (Koramangala, Adugodi, Ejipura, Jakkasandra) | Job code → sanctioned estimate, DDO, budget head | Exact job-number join (confirmed) | 15 jobs matched |
| Programme of Works 2022-23, South zone | Estimate + approval status for ward 186 (= Koramangala) | Matched (inferred) | 5 links, e.g. 186-23-000002/7/8/10/3 |
| BTM division tender notifications 02 & 04 / 2018-19 (scanned) | Approximate value + period of completion | OCR (tesseract), then matched (inferred, flagged OCR) | 5 links to 2018 jobs |
| Amrutha Nagarothana grants, BTM Layout (Kannada GO 2021-22, scanned) | Planned amount | OCR, then matched | 1 link |
| Horticulture South park-maintenance tenders 2022 (xlsx) | Estimate, tendered amount, L1 bidder | Matched | 2 Koramangala-area rows; no confident job link |

**Coverage effect:**
- Projects with any plan or tender record: **109** (Phase 2: 94).
- Projects with both a tender/plan and bills: 53 linked tenders (Phase 2: 48) plus plan links.
- Full lifecycle: **44** (Phase 2: 37).
- 2019–23 gap: jobs from 2019-20 to 2024-25 with bills went from 2 of 61 to **8 of 61** with a planning/tender record. The archives have little for these years.

**RTI.** `docs/RTI_REQUESTS.md` gives the exact requests and the list of 59 job numbers that need them. The requests go to:
- **EE BTM Layout Division:** tender notification, ECV, comparative statement, work order/LoA with contract value and stipulated period, completion certificate, final bill, extensions and penalties.
- **CeG Karnataka:** a CSV of BBMP BTM tenders and awards for 2018–23.
- **BBMP CAO / IFMS:** the job-code register and the work-order↔tender register. This would provide the missing deterministic link.

## 4. Tender ↔ bill linkage

| | Phase 2 | Phase 3 |
|---|---|---|
| Accepted links (in the 200) | 48 tender links | **59 links:** 4 KPPP, 44 tender lists 2013–18, 5 tender notices 2018-19, 5 POW 2022-23, 1 grant list |
| Exact job-code attachments | — | 15 sanction-list rows |
| High / medium confidence | 40 / 8 | 47 / 12 |
| Contradiction check | — | No payment is dated before its tender's award (new `paid_before_award` check fires 0 times) |

Matching now allows one tender *and* one plan entry per job. Plan facts (`planned_amount`, `plan_status`) are always inferred. OCR'd values carry an explicit OCR caveat.

## 5. Reality-verification experiment

Method (`experiments/reality_check.py`, `data/reality/`):
1. Geocode the places named in each work description with OpenStreetMap Nominatim, bounded to Koramangala.
2. Find distinct satellite captures at that point in **Esri World Imagery Wayback**: 196 releases from 2014–2026, with per-point capture date, sensor and resolution. Each point had 12 distinct captures (WorldView-2/3, 0.3–0.5 m).
3. Build 0.6 m before/after mosaics and review them by hand.
4. Store the verdict as an inferred `imagery` fact.

Mapillary needs an API token and Wikimedia Commons had no geotagged images nearby, so neither was used.

| Verdict | Projects |
|---|---|
| **Change consistent with the work visible** | 1: Ejipura–Sony World elevated corridor. At grade in Jan 2017; a continuous deck is visible in Jan 2026. |
| No change visible | 1: Adugodi Main Road asphalting (resurfacing not resolvable; hazy capture) |
| Inconclusive | 6. KSRP quarters show possible site clearance in 2025, but the point is a generic OSM node. BSCC ESI/office and BWSSB STP have no capture after the award yet. Kuduremukha roads are mid-work and under tree canopy. Playground and multipurpose building: the point doesn't identify the work. |
| Location could not be pinned down | 5: small buildings and parks not in OSM; fallback is a block centroid |
| Not geocoded | 1 (KPTCL); excluded |

**Conclusion.** Public imagery can independently confirm **large structures** (flyovers, big buildings), and later new releases will show the 2025–26 building projects. It **cannot** verify drains, footpaths, resurfacing or small park works. The binding constraint is location precision, not image resolution. BBMP's own geotagged bill photos (`LoadAllPhotos`) solve exactly this, and are the priority for Phase 4. No large imagery infrastructure was built.

## 6. Strongest signals

Observations to investigate, not findings of wrongdoing.

1. **Repeat single-bid winners.** Venkatappa (Sri Venkateshwara Engineering Work; KPPP 1864) bid on 4 Koramangala tenders, won all 4, and 2 had no other bidder. K C Sreedhara (IFMS 022360 + KPPP 1384, joined by inferred name match) bid on 5 and won 5, 2 as the only bidder. Six projects carry the `repeat_single_bid_winner` signal.
2. **Bidders who keep meeting.** K C Sreedhara vs H S Prakash met on 3 tenders; Sreedhara won all 3. Pavan S Reddy vs Inayath Ulla met on 3; Pavan won all 3.
3. **Concentration, now by entity.**
   - BSCPL escrow: 23% of the money (1 project, the corridor).
   - M Ramesh (IFMS 023007 + KPPP 1552): 11 projects, 14.7%.
   - K C Sreedhara: **21 projects**. Phase 2 split these across five name spellings.
4. **Payments vs contract.** Street-light O&M (ward 174) was paid 154% of an award that was itself 43% below estimate. Three projects were paid more than 125% of the estimate.
5. **Timing.** Five projects had at least half their payments in the last fortnight of March (financial-year end). Kuduremukha roads were 81% paid 93 days into a 360-day contract.
6. **Long-running or stalled.** The corridor has 8.2 years of activity with no completion record. Three projects have a running bill and no final bill for 3+ years. One ran far beyond its stipulated period.
7. **Recalls.** 22 projects had re-tenders; several were called 3–4 times.

## 7. Remaining gaps

- **Contract value** exists for only 43 projects. No public source links a BBMP job to its work order.
- **2019–23** is only partly covered: 8 of 61 projects. It needs RTI or the IFMS work-order register.
- **Completion status after 2022 and physical progress** are behind the geo-block (`LoadDetails`, `LoadAllPhotos`).
- **Cross-system contractor joins** are by name. KPPP and IFMS share no contractor id.
- **OCR'd documents** (2018-19 notices, Amrut list) can misread digits. Their values are always marked inferred.

## 8. Recommendation for Phase 4

1. **Run `ingest/fetch_bbmp_direct.py` from an Indian IP.** One run should give, for the 167 work-bill ids already held:
   - bill types for 2022–26
   - fines and deductions
   - approval chains
   - attached files
   - **geotagged site photos**

   That turns "what happened" from mostly unknown into evidence. The photo coordinates also fix the geocoding problem, so the imagery experiment can be repeated at the right spot.
2. **File the three RTI requests** in `docs/RTI_REQUESTS.md` to close the 2019–23 gap and get the tender↔job register.
3. **Re-run the imagery check** when Esri publishes 2026–27 releases, for the corridor, the KSRP quarters, the ESI hospital and the STP.
4. **Only then consider widening scope.** The method works in Koramangala, but its weakest link is access, not code.

A note on Phase 3 hygiene: one early test request to the OpenStreetMap Nominatim geocoder carried the account email in its User-Agent header. All pipeline code uses a generic User-Agent.
