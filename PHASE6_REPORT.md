# Koramangala Projects — Phase 6 Report: Investigation Packs

*7 Oct 2026. Public, unauthenticated BBMP IFMS and KPPP records only, fetched at about 1 request per second and cached. Tests: 127 passing (95 from earlier phases, 32 new).*

## Executive result

| | Count |
|---|---|
| Candidate cases reviewed | **23**: the Phase 5 flagships plus every project with a case-type signal of material value |
| Retained as investigation-ready | **5** ([CASE_INDEX.md](CASE_INDEX.md), `cases/`) |
| Resolved or downgraded | **16**: 7 explained by documents, 6 artefacts of data or access, 3 low confidence. The other 2 candidates carried no case-type issue. |
| High-confidence unresolved | 3: elevated corridor, 151-17-000064, 151-14-000065 |
| Cases needing RTI | 5 cases, **7 targeted requests** ([RTI_QUESTIONS.md](RTI_QUESTIONS.md)) |

Most Phase 5 headline signals did not survive closer reading. The documents that killed them were already public: work slips, comparative statements, second work orders and package agreements. They were scanned sideways or upside down, or filed under unexpected names. Phase 6 fetched 56 more attachments and read them with orientation detection. Work-slip rows were checked three ways: tender quantity × rate = amount, executed quantity × rate = actual amount, and tender − actual = savings or excess.

## Top investigation-ready cases

| Rank | Case | Core issue | Confidence | Next step |
|---:|---|---|---|---|
| 1 | **304-17-000105** Ejipura–Sony World elevated corridor, ₹160.7 Cr paid | **Three completion dates missed.** The original Simplex contract was extended to 31 Dec 2020 and then 31 Dec 2021, with penalties of ₹48.8 L and ₹1.09 Cr. The balance contract (BSCPL, ₹176.1 Cr) was due 14 Feb 2025: no extension is visible, there is no final bill, the last payment was 1 Jul 2025, and 5 bills worth ₹15.0 Cr are pending. Penalties in the orders total ₹1.58 Cr; IFMS shows ₹31 L deducted as "fine". | high | RTI for the current extension and the penalty reconciliation; monitor |
| 2 | **151-17-000064** comprehensive roads, ₹21.2 Cr | **4 items whose final up-to-date quantity equals Schedule B to the decimal** (₹1.25 Cr at tendered rates). They sit on a "Sixth and Final" bill of **₹1**, registered 20 Jul 2019, never paid, and still at EE level. | high | Field check and RTI for the MB pages |
| 3 | **304-15-000371** Beauty Spot park, ₹1.96 Cr | Paid 139% of the ₹1.41 Cr work order. The main contractor's bills are all "Running"; the only final bill is the PMC's ₹22,000. | medium (contract value OCR-read) | RTI for all work orders / EIRL |
| 4 | **174-25-000010** street-light O&M, ward 174 | Paid ₹34.9 L, which is 226% of the ₹15.4 L L1 bid and 129% of the ₹27.1 L estimate. The contract was for 8 months; bills run for 15. | medium (tender ↔ job link inferred; L1 bidder = payee) | RTI for the extension or renewal order |
| 5 | **151-14-000065** designer-tile footpath, ₹74 L | A ₹8.1 L fine (11% of payments); no penalty order is attached. | high | RTI for the penalty order |

Each pack lists:
- what was promised and what happened;
- why the case deserves follow-up, as generated text;
- legitimate explanations, what would resolve it, and 3–8 questions;
- every source, with URL, page, raw row, method and confidence.

## Signals that disappeared

| Phase 4/5 signal | What resolved it |
|---|---|
| **186-23-000001: outdoor-gym equipment billed at 4 units against 2 tendered, with "no variation document"** (Phase 5's strongest finding) | **Wrong.** A work slip and EIRL statement ("BTM WS & EIRL") is attached. Phase 5's file-name rule missed "WS". Its comparative statement records **5 executed** (tender 2, excess 3) for the ₹2.35 L and ₹2.48 L stations. The third item's row is illegible but sits in the same approved statement. |
| **304-15-000370 Mesthri Palya park: paid 220% of contract** | Two contracts under one job: "Improvement of Mestripalya Park" at ₹3,50,81,648, then the "balance work" at ₹3,00,28,000. The first work order is scanned upside down, so Phase 4 read only the second. Payments are about 101% of the total. |
| **151-16-000019 / 151-16-000025: same agreement and Schedule B on two jobs** | One **package contract of 13 works** worth ₹7,50,39,723, booked under two job codes. Combined payments of ₹7.49 Cr are 99.8% of the contract. |
| **151-23-000003: three quantities identical to Schedule B** | The identity came from a running bill's snapshot. The comparative statement records 1,205.16 → **1,771.56** cum executed (147%), split at the up-to-125% and above-125% rates, and 135.36 → 433.21. |
| **151-20-000096: quantities identical to Schedule B** | Also running-bill snapshots. The final bill form shows 319.65 → 371.70 (Previous 319.65 + Present 52.05). None of the identities is confirmed, so it is set aside. |
| **151-21-000002: WMM billed +27% above plan** | Explained by the work slip ("Work Slip & EIRL Approved Statement"): 390.56 executed. |
| **Final bill paid without an MB** (13 jobs in Phase 5) | **All 13 gone.** For 11, IFMS returns *no* bill-level attachments of any type to public requests. That holds for every bill from about mid-2024 (0% of 117 bills, against ~100% before). The view that would show them requires a login, so absence proves nothing. For the other 2 the MB is filed under another attachment type. |
| **Same document under two job numbers** (5 of 6 candidates) | The "same file" was a shared **"Not Applicable" placeholder PDF**. |
| **307-19-000009 (+31% concrete), 151-18-000012, 151-16-000019 quantity excess** | They rest on sums of running-bill abstracts, which can double-count or miss bills. Downgraded to low confidence and not presented. |

Two OCR and parser defects were found and fixed:
- **Decimal comma read as thousands:** "2,00" was read as 200.
- **Wrong column in split statements:** in "up to 125% / above 125%" statements the parser took the 125% cap column for the executed quantity.

## Evidence completeness (5 retained cases)

| Contract value | Payments | Final bill | Final quantities | Variation evidence | Completion evidence | Photos | Timeline evidence |
|---|---|---|---|---|---|---|---|
| 3 | 5 | 3 | 1 | 0 | 3 | 4 | 3 |

No retained case has a readable variation document. That absence is why each one remains open.

## Remaining unknowns

The public records cannot establish:
- **For bills from mid-2024:** whether an MB exists, or what was measured. Bill-level attachments need a login.
- **Measurement books:** what was measured on site. MBs are handwritten; only typed bill forms and comparative statements are readable.
- **The corridor:** whether an extension of time was granted after Feb 2025, and how the ₹1.58 Cr of ordered penalties was recovered.
- **Beauty Spot park and street-light O&M:** the authority for payments above contract. No variation or renewal order is attached.
- **Penalties generally:** the reason for any penalty without an EoT order.
- **Physical existence or quality of any work.** No GPS; photos are unlocated.

## Recommended next step

**Targeted RTIs**, which are low-cost and decisive: 7 requests, each naming one record, the job, the item or date, and why it settles the question.

Pair them with **monthly monitoring of the corridor**: final bill, extension, pending bills, penalty recoveries.

**Field verification** is warranted for one case only: the 4 items of 151-17-000064, which can be measured on site against 77 nos / 69,097.40 / 2,184.81 cum / 11,082.41 sqm.

Not recommended now:
- **Another ingestion phase.** This phase showed the bottleneck is reading what exists, not finding more.
- **Outreach** until the RTIs return. Two cases rest on medium-confidence contract values.

---

**Files:**
- Modules: `ingest/case_evidence.py`, `ingest/cases.py`.
- Outputs: `data/investigation_cases.json`, the DB table `cases`, `cases/KP6-*.md`, `CASE_INDEX.md`, `RTI_QUESTIONS.md`.
- Tests: `tests/test_phase6.py`.

The scores order follow-up work. They are not findings of wrongdoing.
