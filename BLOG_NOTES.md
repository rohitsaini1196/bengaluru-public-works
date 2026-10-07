# Blog notes (raw material, not the post)

## Central narrative

> I reconstructed hundreds of Koramangala public works from BBMP records. The interesting part was not finding anomalies. It was watching most of them disappear when better evidence was found.

The arc:

1. **The goal.** Answer "what happened to this road project?" for every public work in one Bengaluru locality, from public records alone.
2. **The difficulty.** The records live in four systems that share no identifiers:
   - bill registers;
   - an IFMS portal full of scanned attachments;
   - a procurement portal;
   - archived PDFs.
3. **The first pass produced dramatic signals:**
   - "paid 220% of contract";
   - "gym equipment billed at double the tendered quantity, no approval";
   - "same agreement on two jobs";
   - "final bills with no measurement book".
4. **The second pass read the documents properly.** Sideways scans, upside-down work orders, files named "WS". Most signals dissolved.
5. **What survives is smaller, sharper and checkable.** Five cases, each with the specific record that would settle it.
6. **Lesson:** a civic-data tool earns trust by showing what it *ruled out*, not just what it found.

## Important metrics (as of the 7 Oct 2026 build; re-check before publishing)

| | |
|---|---|
| Projects reconstructed | 432 (388 with BBMP bills); 200 before city-wide discovery |
| Paid bills | 592, ₹543.99 Cr gross, Jun 2015 → Sep 2026 |
| City-wide payment grid scanned | 90,525 bills; 686 Koramangala bills on 484 job numbers (94 jobs new) |
| Completeness checks | 310/310, 338/338 and 820/820 known paid bills and jobs found in the grid |
| Attachments listed / read | 8,489 listed in Phase 4; thousands downloaded and OCR'd; only 8 of 144 measurement books machine-readable |
| Candidate cases reviewed → retained | 23 → 5 (7 explained, 6 artefacts, 3 low confidence, 2 no issue) |
| Targeted RTI questions | 7 |
| Tests | 174 |

## Biggest technical challenges

- **No shared identifiers.** Tenders and bills never cite each other, so the link is scored on description, place, ward, work type, dates and contractor.
- **Ward numbers are reused across three delimitations.** Job 186-23-000001 is a Koramangala job *and* a Jaraganahalli job. Ward 174 is HSR Layout on one map and Koramangala on another.
- **Scans.** Rotated pages, upside-down work orders, decimal commas ("2,00" read as 200), table rulings. The fix was not a better OCR engine but **arithmetic**: accept a row only if quantity × rate = amount.
- **Column semantics.** Bill forms have Previous / Present / Up-to-date columns; comparative statements split executed quantities at 125%. Reading the wrong column turned "+47%" into "identical to the estimate".
- **Access boundaries.** Bill attachments from mid-2024 need a login. The honest answer is "unknown", not "missing".

## Signals that disappeared (the best part)

| Signal | Resolution |
|---|---|
| Outdoor gym: 4 units billed vs 2 tendered, "no variation document" | Approved work slip, filed as "BTM WS & EIRL", records 5 executed per item |
| Mesthri Palya park paid 220% of contract | Two contracts (₹3.51 Cr + ₹3.00 Cr); the first work order was scanned upside down |
| Same agreement on two jobs | One ₹7.50 Cr package of 13 works under two job codes; payments are 99.8% of it |
| Quantities "identical to Schedule B" (two projects) | Running-bill snapshots; final statement shows 1,205 → 1,772 cum |
| 13 final bills "without MB" | 11 were the login boundary; 2 had the MB filed under another type |
| 5 of 6 "same document under two jobs" | A shared "Not Applicable" placeholder PDF |

## Strongest remaining cases (neutral framing)

1. **Ejipura–Sony World elevated corridor.**
   - ₹160.7 Cr paid.
   - Extensions to Dec 2020 and Dec 2021 on the original contract. The balance contract was due Feb 2025: no extension is visible, there is no final bill, and ₹15 Cr of bills are pending.
   - Penalties in the extension orders total ₹1.58 Cr against ₹31 L deducted as "fine".
2. **151-17-000064 roads (₹21.2 Cr).** Four items whose final quantity equals the tender quantity to the decimal, on a ₹1 "final" bill never paid since 2019.
3. **Beauty Spot park.** Paid 139% of its work order.
4. **Street-light O&M (ward 174).** An 8-month contract billed for 15 months, 226% of the award.
5. **Footpath work.** An ₹8.1 L fine with no order stating why.

## Methodology lessons

- Make every number traceable: page, row, URL.
- Label confidence and never let OCR overwrite a direct source.
- Write the resolution attempt *before* the accusation.
- Arithmetic is a better validator than any OCR confidence score.
- Absence in a public portal ≠ absence in the world.

## Screenshots worth showing

- Home page (metrics and "observations, not findings").
- A project page: the six-stage story with confirmed / inferred labels and source links.
- The cases page's "Reviewed and set aside" table.
- The corridor case pack.
- The upside-down work order next to its OCR result. The rotated comparative statement with 125% split columns.
- The coverage page's limitations list.

## Possible titles

- "Most of my red flags were wrong, and that was the point"
- "Reconstructing a neighbourhood's public works from scanned bills"
- "What 592 bills say about Koramangala's roads (and what they can't)"
- "From signals to cases: building an honest civic-data pipeline"

## Avoid

- Calling anything fraud, corruption or a scam.
- Naming contractors or officials as wrongdoers.
- Implying the 5 cases are proven problems.
