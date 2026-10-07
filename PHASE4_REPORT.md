# Koramangala Projects — Phase 4 Report: Direct BBMP Evidence

*6 Oct 2026. Scope: Koramangala only. Collected from Bengaluru (Tata Play broadband). Tests: 40 passing.*

## The key metric

How many Koramangala projects can now answer: **what was promised, who received the work, what was paid, what work was documented, where it happened, and what evidence supports each claim?**

| Question (of 200 projects) | Before Phase 4 | After Phase 4 |
|---|---|---|
| What was promised (estimate / sanction / contract) | 104 | **146** |
| Who received the work | 193 | 193 |
| What was paid | 154 | 154 |
| What work was documented (IFMS bill types, MB, completion and quality certificates, final bill) | 71 | **153** |
| Where (place names, ward; no photo GPS found) | 182 | 182 |
| Physical evidence (genuine site photos; satellite) | 1 | **136** |
| **All six** | **0** | **83** |
| All six, with contractor and payments *confirmed* | 0 | **77** |

Every claim carries a source link: an IFMS API URL, an attachment URL with page number, an export row, or a tender record. Facts stand at 3,002 confirmed, 848 inferred and 987 unknown.

## 1. Direct BBMP access

- **The old host is dead, not geo-blocked.** account.bbmpgov.in (117.236.190.54) refuses connections even from an Indian residential connection. So does `ifms.gba.karnataka.gov.in`, which points to the same IP.
- **The service moved** to **`https://accounts.bbmp.gov.in/vssWB/`** and `/PublicView/` (164.164.52.3). It is public and needs no login. The link was found in the GBA portal's own JavaScript.
  - `vssWB` works.
  - The `/PublicView/` search API returns HTTP 500 server-side, so it is not used.
- **TLS:** the server omits its GoDaddy G2 intermediate certificate. We added the published intermediate (`ingest/certs/`) and **kept verification on**.
- **Respectful collection:**
  - 1 request per second, everything cached.
  - Contractor mobile and e-mail removed before writing.
  - No authentication, CAPTCHA or access control touched.
- **An endpoint we deliberately did not use:** `LoadAllPhotos` takes a DC-bill id. Called with a work-bill id it returned 76 photos located 10–15 km away, which belong to other works. It is excluded.

## 2. New records, documents and photos

| | Count |
|---|---|
| Work bills fetched from IFMS | **379**: 167 from known ids, 212 newly found through the job → bill lookup across 154 job numbers |
| Matched to bills we already held | 301 |
| **New paid bills** (with RTGS) not in any public export | **46** |
| Draft or unpaid bills (kept separately, not counted as paid) | 32 |
| Approval-chain steps with officials' remarks | 3,446 |
| Bills with a fine deducted | 97 |
| Attachments listed | **8,489**, including: 975 photos, 594 MB pages, 554 quality certificates, 308 completion certificates, 302 agreements, 230 work orders |
| Attachments downloaded (prioritised: work orders, agreements, completion certificates, up to 3 photos per job and type) | 1,560 (2.2 GB) |
| OCR'd | 1,375 |

**OCR accuracy check.** Where a work order's OCR'd contract value could be compared with an independent source (the KPPP award), it matched exactly: Kuduremukha roads ₹12.38 Cr, Package-02 roads ₹9.15 Cr. Those facts are now confirmed.

## 3. Physical evidence

- **136 projects** now have genuine before / during / after site photos attached in IFMS. They are usually printed sheets labelled with the work name and signed by the AE/EE.
  - Example: 304-14-000214, 7th Cross asphalting, shows the road mid-work.
- **0 photos carry GPS.** EXIF GPS is absent, and none of the 69 camera photos OCR'd has a GPS overlay. Many are scanned sheets, whose EXIF time is the scan time, so it is labelled as such. **Location therefore still comes from the work description, not from photos.**
- **Placeholder evidence:**
  - 14 projects have photo slots filled with blank files (literally named *"emty page"*, or a few KB in size).
  - The same placeholder file was uploaded on 5 different Adugodi works (147-17-000038/39/63/83/86).
- **Same document under different job numbers:** 16 projects. Examples:
  - identical work order and agreement on 151-16-000019 and 151-16-000025
  - identical completion report and work order on 151-13-000029 and 151-13-000030
  - one street-light O&M agreement across five job numbers (151-16-000001 → 174-25-000010). This fits a multi-year contract booked under annual job codes.

## 4. Lifecycle coverage

| Stage (projects with evidence) | Phase 3 | Phase 4 |
|---|---|---|
| Planned | 109 | 109 |
| Contracted | 139 | 176 |
| Work documented | — | 153 |
| Payments | 154 | 154 |
| Completion | 117 | 184 |
| Physical evidence | 1 (satellite) | 136 |
| **All six stages** | — | 47 |

Facts that grew:
- **Final bill confirmed:** 71 → **137**. IFMS bill types fill the 2022–26 gap.
- **Contract value:** 43 → **94**: 42 confirmed, the rest OCR-inferred.
- **Dates:** work-order commencement for 29 projects; completion-certificate dates for 25.
- **Payments:** partial releases for 41 projects; fines, withholdings or recoveries for 55.

## 5. Important new facts

**Ejipura–Sony World elevated corridor (304-17-000105):**
- *Balance-portion* work order, 15 Nov 2023, to BSCPL Infrastructure for **₹176.11 Cr** (excluding GST).
- Tender EE/PC-9/TEND/01/2022-23, LoA 22 Sep 2023, agreement 15 Nov 2023, GO UDD 316 MNY 2023.
- **Stipulated completion: 14 Feb 2025.**
- IFMS shows **₹150.1 Cr paid gross**, against ₹96.1 Cr in the public exports.
- Deductions: ₹18.97 Cr mobilisation-advance recovery, a **₹31 L fine**.
- **6 unpaid bills (₹15.0 Cr)** at AE / SC(F) levels.
- No final bill. Approval remarks: "*since it is mobilization advance, hence balance 25% is paid now*".

**A data-quality bug in our own pipeline, found and fixed.** The 2015–18 departmental register duplicated Nagarothana-scheme bills. Dedupe compared gross *and* nett, and that register has no nett. Fixing it removed 5 duplicated bills; Mesthri Palya park went from ₹10.1 Cr to ₹6.6 Cr paid. This was the Phase 3 "possible 2016–19 double counting".

**Payments vs contract.** Many projects were paid 111–118% of their work-order value. That is consistent with GST on contract values quoted excluding GST, so the signal now fires only above 125%. Six projects remain:

| Project | Paid as % of contract value |
|---|---|
| 151-23-000001 | 331% |
| Street-light O&M, 174-25-000010 | 226% |
| Mesthri Palya park, 304-15-000370 | 220% |
| 186-23-000002 | 202% |
| 186-23-000001 | 188% |
| 304-15-000371 | 139% |

All are inferred: the contract value may cover only one of several work orders under the job.

**New signals:**
- past stipulated completion with no final bill (corridor)
- fines levied (44 projects)
- partial release (41)
- slow payment, median BR→RTGS over a year (89)
- placeholder photos (14)
- same document under two job numbers (16)

## 6. Remaining gaps

- **No GPS for any work.** Photos are uploaded without location data. `LoadAllPhotos` (which has latitude/longitude) is keyed by a DC-bill id we cannot derive from public data.
- **MB contents are not extracted.** 594 MB pages are listed but mostly handwritten; quantities are not OCR-able reliably, so they were not downloaded in bulk.
- **Tender ↔ job is still mostly fuzzy.** No work order in Koramangala cites a KPPP tender number we hold, so there are 0 document-exact links. Older work orders cite office tender numbers that exist in no public list.
- **Completion is still administrative.** "Completed" means a final bill or completion certificate exists. Twenty completion-certificate slots on the corridor are placeholders, so documents are counted but their presence is not proof.
- **Coverage is limited to job numbers we already knew.** We fetched 154 jobs. Koramangala works booked under unknown job numbers remain invisible.
- **The Public View search returns HTTP 500,** so ward- or date-wide discovery of new jobs is not possible through it.

## 7. Recommended Phase 5

1. **Discover jobs we don't know.** Use `vssWB` `LoadPaymentGridData` with ward and DDO filters (Koramangala wards and the BTM / BSCC divisions) to list every Koramangala work bill. Then run the same pipeline on new job numbers.
2. **Extract measurement-book quantities for the top 30 projects by value.** Use layout-aware OCR on the MB pages, plus Schedule B (BOQ), then compare billed quantities with estimate quantities. This is "work performed" in physical units.
3. **Geolocate the site photos.** Match photo sheets (work name and street) to OSM street segments. Where a work names a street, show it on a map, and repeat the satellite check on that exact segment.
4. **Track overdue works.** Re-poll IFMS monthly for the corridor and other projects past stipulated completion. Watch for final bills, extensions of time (look for EoT documents) and levied penalties.
5. **File RTI for the gaps that need it:** extension-of-time orders, the work-order ↔ tender register, and MB extracts for the projects with paid-over-contract and document-reuse signals.

Signals in this report are observations for follow-up. None is a finding of wrongdoing.
