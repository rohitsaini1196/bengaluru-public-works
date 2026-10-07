# RTI requests needed to close the 2019–23 gap

Phase 3 searched every public source we could reach for tender and work-order records covering Koramangala works from 2019–2023. Nothing usable was found:

- **eproc.karnataka.gov.in.** The old Karnataka e-Procurement portal is still reachable, but every search returns "No Tenders Found", even a search with no filters.
- **KPPP.** Records start in May 2023.
- **OpenCity.** The *BBMP Tenders* dataset stops at 2017-18.
- **BBMP sites.** account.bbmpgov.in and site.bbmp.gov.in refuse connections from outside India. The Internet Archive holds only scattered copies, which are already used: FY 2015-17 ward works lists, two 2018-19 BTM division tender notices, POW 2022-23 and the Amrutha Nagarothana list.

As a result, **59 Koramangala projects with job numbers from 2019-20 to 2024-25 have bills but no tender**. They have no estimate, no contract value, no contractor-award record and no stipulated period.

The requests below are written under the RTI Act 2005, section 6(1), with the information described under section 2(f).

## 1. Office of the Executive Engineer, BTM Layout Division

The division was under BBMP South Zone and is now part of Bengaluru South City Corporation (GBA).

For each BBMP job number listed at the end of this document, please provide:

1. The tender notification number and date, and the KW form used.
2. The estimated cost put to tender (ECV), and the administrative and technical sanction order with its amount.
3. The comparative statement of bids: every bidder with the quoted amount, and the L1 bidder.
4. The work order, or Letter of Acceptance: its number, date, contractor name, IFMS contractor code, contract value, and stipulated period of completion.
5. The agreement number and date.
6. The work completion certificate, with its date. If the work is not complete, its current status.
7. The final bill: bill register (BR) number and date, and the measurement book reference.
8. Any extension of time granted, and any penalty or liquidated damages levied.

**Also request:** the e-procurement tender register of the BTM Layout Division for FY 2018-19 to FY 2022-23, covering wards 147, 148, 151, 172 and 173 (198-ward numbering) and the Koramangala sub-division.

## 2. Centre for e-Governance (CeG), Government of Karnataka (eproc.karnataka.gov.in)

For tenders published by Department "BBMP – Bruhat Bengaluru Mahanagara Palike", location *EE BTM Layout* or *Koramangala*, from 1 Apr 2018 to 31 Mar 2023, please provide in CSV format:

- tender number and title
- ECV and published date
- number of bids received
- awarded bidder and awarded value
- award date

## 3. Chief Accounts Officer, BBMP/GBA (IFMS)

For the same job numbers, please provide:

- the job-code register entry: estimate, budget head and sanction date
- the work-order register entry linking the job number to its tender number and contract value

This would give the missing deterministic tender↔job identifier.

## Job numbers (bills found, no tender found)

147-19-000049, 147-20-000061, 148-20-000026, 148-20-000053, 148-23-000003, 151-19-000009, 151-19-000014, 151-19-000022, 151-19-000032, 151-19-000035, 151-19-000039, 151-19-000054, 151-19-000056, 151-19-000063, 151-20-000005, 151-20-000021, 151-20-000023, 151-20-000024, 151-20-000029, 151-20-000071, 151-20-000074, 151-20-000076, 151-20-000077, 151-20-000078, 151-20-000079, 151-20-000080, 151-20-000081, 151-20-000095, 151-20-000096, 151-20-000102, 151-20-000103, 151-20-000106, 151-20-000107, 151-20-000108, 151-20-000110, 151-20-000111, 151-20-000112, 151-21-000001, 151-21-000002, 151-22-000001, 151-22-000002, 151-22-000004, 151-22-000005, 151-22-000006, 151-23-000001, 151-23-000002, 151-23-000003, 152-20-000049, 174-24-000001, 174-24-000004, 174-24-000005, 176-24-000003, 186-23-000001, 186-23-000002, 186-23-000003, 186-23-000007, 186-23-000008, 186-23-000010, 304-20-000212

*Regenerate this list with:*

```sql
SELECT job_numbers FROM projects
WHERE n_bills > 0 AND tender_numbers IS NULL
  AND CAST(substr(job_numbers, 5, 2) AS INT) BETWEEN 19 AND 24;
```
