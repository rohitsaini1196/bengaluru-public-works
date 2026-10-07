"""Parsers: turn each raw source format into uniform bill/tender-level records.

source_row is the 1-based data-row index after the header (not the physical line number,
since some descriptions contain embedded newlines).

A record is a dict with (where available):
  source_file, source_row, kind, regime, job_number, tender_number, description, ward,
  contractor, gross, deduction, nett, bill_type, br_no, br_date, payment_ref, payment_date,
  payment_status, wo_no, wo_date, start_date, end_date, budget_head, office, estimate, extra
Values are never invented: missing data is None.
"""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None


def parse_date(s):
    """Return ISO yyyy-mm-dd for the many date formats in the sources, else None."""
    s = clean(s)
    if not s:
        return None
    s = s.split(" ")[0]
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            if 2000 <= d.year <= 2035:
                return d.date().isoformat()
        except ValueError:
            pass
    return None


def epoch_ms_date(v):
    try:
        return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def money(s):
    s = clean(s)
    if not s:
        return None
    s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return round(v, 2)


PHONE = re.compile(r"\s*(\+?91)?[6-9]\d{9}\s*$|\s*0{10}\s*$")
CODE = re.compile(r"^\s*\d{5,6}\s+")


PHONE_ANY = re.compile(r"(?:\+?91)?([6-9]\d{9})\s*$")


def contractor_ids(raw, mobile=None):
    """Identifiers carried by an IFMS contractor string: 5-6 digit IFMS contractor code and the
    registered mobile. The mobile is used only in memory to join name variants of one contractor;
    it is never written to the database or shown."""
    raw = clean(raw) or ""
    code = re.match(r"^\s*(\d{5,6})\s+", raw)
    ph = PHONE_ANY.search(raw) or (PHONE_ANY.search(clean(mobile) or "") if mobile else None)
    return (code.group(1) if code and code.group(1).strip("0") else None), (ph.group(1) if ph else None)


def clean_contractor(s):
    """Strip IFMS contractor codes and trailing phone numbers (we don't republish phones)."""
    s = clean(s)
    if not s:
        return None
    s = PHONE.sub("", s)
    s = CODE.sub("", s)
    s = s.strip(" ,.-")
    if not s or s.lower() in {"na", "nil", "-"}:
        return None
    return s


def norm_job(j):
    j = clean(j)
    if not j:
        return None
    j = j.upper().replace(" ", "")
    m = re.match(r"^(R-)?(\d{3})-(\d{2})-(\d{6})$", j)
    return j if m else None


def _read_csv(path, skip=0):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    return rows[skip:]


# ---------------------------------------------------------------- BBMP Works Bill Public View
def parse_publicview(path, src):
    rows = _read_csv(path)
    # header is the row that contains "Job Number"
    hi = next(i for i, r in enumerate(rows) if "Job Number" in r)
    hdr = rows[hi]
    ix = {h: i for i, h in enumerate(hdr)}
    out = []
    for n, r in enumerate(rows[hi + 1:], start=1):
        if len(r) < len(hdr):
            continue
        g = lambda k: clean(r[ix[k]]) if k in ix else None
        job = norm_job(g("Job Number"))
        if not job:
            continue
        ward = g("Ward") or ""
        out.append(dict(
            source_row=n, job_number=job, description=g("Name of Work"),
            ward=(re.match(r"\d+", ward).group(0) if re.match(r"\d+", ward) else None), ward_name=ward,
            office=g("Office"), budget_head=g("Budget Head"), contractor=clean_contractor(g("Contractor")),
            contractor_code=contractor_ids(g("Contractor"))[0], _phone=contractor_ids(g("Contractor"), g("Mobile"))[1],
            bill_type=g("Bill Type"), wo_no=g("Order Number"), wo_date=parse_date(g("Order Date")),
            br_no=g("BR Number"), br_date=parse_date(g("BR Date")),
            payment_ref=g("CBR Number"), payment_date=parse_date(g("CBR Date")),
            payment_status=g("Payment"), start_date=parse_date(g("Start Date")), end_date=parse_date(g("End Date")),
            gross=money(g("Gross")), deduction=money(g("Deduction")), nett=money(g("Nett")),
        ))
    return out


# ---------------------------------------------------------------- OpenCity IFMS work-order scrape
BR_RE = re.compile(r"(?<![CS])BR\s*-\s*(\d+)\s*/\s*(\d{1,2}-[A-Za-z]{3}-\d{4})")
RTGS_RE = re.compile(r"Rtgs\s*-\s*(\d+)\s*/\s*(\d{1,2}-[A-Za-z]{3}-\d{4})")


def parse_wodetails(path, src):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for n, r in enumerate(rows, start=1):
        r = {(k or "").strip().lower(): v for k, v in r.items()}
        wd = r.get("wodetails") or ""
        if "</a>" not in wd:
            continue
        job, desc = wd.split("</a>", 1)
        job = norm_job(job)
        if not job:
            continue
        brs = r.get("brnumber") or ""
        br = BR_RE.search(brs)
        rt = RTGS_RE.search(brs)
        ward = clean(r.get("ward") or r.get("ward num") or r.get("ward no"))
        out.append(dict(
            source_row=n, job_number=job, description=clean(desc), ward=ward,
            contractor=clean_contractor(r.get("contractor")),
            contractor_code=contractor_ids(r.get("contractor"))[0], _phone=contractor_ids(r.get("contractor"))[1],
            br_no=br.group(1) if br else None, br_date=parse_date(br.group(2)) if br else None,
            payment_ref=("RTGS " + rt.group(1)) if rt else None, payment_date=parse_date(rt.group(2)) if rt else None,
            payment_status="Paid (RTGS)" if rt else None,
            gross=money(r.get("amount")), nett=money(r.get("nett")), deduction=money(r.get("deduction")),
            extra={"ifms_id": clean(r.get("id"))},
        ))
    return out


# ---------------------------------------------------------------- 2010-18 departmental/zone registers
def parse_dept(path, src):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    hdr = [h.strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}
    out = []
    for n, r in enumerate(rows[1:], start=1):
        if len(r) < 9:
            continue
        g = lambda k: clean(r[ix[k]]) if k in ix and ix[k] < len(r) else None
        job = norm_job(g("Job Number"))
        if not job:
            continue
        out.append(dict(
            source_row=n, job_number=job, description=g("Work Order Details"),
            ward=g("Ward"), contractor=clean_contractor(g("Contractor")),
            wo_no=g("Work Order Number"), wo_date=parse_date(g("Work Order Date")),
            gross=money(g("Total Amount in Rs")), deduction=money(g("Deduction in Rs")), nett=money(g("Net Payment in Rs")),
        ))
    return out


def parse_billreg(path, src):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for n, r in enumerate(rows, start=1):
        r = {(k or "").strip(): v for k, v in r.items()}
        job = norm_job(r.get("Job_Code"))
        if not job:
            continue
        lakh = lambda k: round(money(r.get(k)) * 1e5, 2) if money(r.get(k)) is not None else None
        out.append(dict(
            source_row=n, job_number=job, description=clean(r.get("Job_Description")),
            ward=clean(r.get("Ward_No")), ward_name=clean(r.get("Ward_Name")),
            contractor=clean_contractor(r.get("Contractor_Name")), budget_head=clean(r.get("Budget_Head")),
            office=clean(r.get("Engineer Details")),
            wo_no=clean(r.get("Work_ Order")), wo_date=parse_date(r.get("Work_Order_Date")),
            br_no=clean(r.get("Bill Register No")), br_date=parse_date(r.get("Bill Register Date")),
            payment_ref=("RTGS " + clean(r.get("RTGS_No"))) if clean(r.get("RTGS_No")) else None,
            payment_date=parse_date(r.get("RTGS_Date")),
            payment_status="Paid (RTGS)" if clean(r.get("RTGS_No")) else None,
            gross=lakh("Gross_ Amount In Lakhs"), deduction=lakh("Deduction In Lakhs"), nett=lakh("Nett_ Amount In Lakhs"),
            extra={"amount_precision": "source amounts in lakhs (2 dp)"},
        ))
    return out


def parse_jobcodes(path, src):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for n, r in enumerate(rows, start=1):
        job = norm_job(r.get("Job_Code"))
        if not job:
            continue
        out.append(dict(
            source_row=n, job_number=job, description=clean(r.get("Job_Description")),
            ward=clean(r.get("Ward_No")), ward_name=clean(r.get("Ward_Name")), budget_head=clean(r.get("Budget_Head")),
            estimate=money(r.get("Amount in Rs.")), estimate_date=parse_date(r.get("Date")),
        ))
    return out


def parse_nagarothana(path, src):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for n, r in enumerate(rows, start=1):
        r = {(k or "").strip().lstrip("﻿"): v for k, v in r.items()}
        job = norm_job(r.get("Job Code"))
        if not job:
            continue
        # dates in this file are wrapped across lines ("16-Oct-\n2018")
        br = re.sub(r"-\s+", "-", clean(r.get("BR")) or "")
        rt = re.sub(r"-\s+", "-", clean(r.get("RTGS")) or "")
        brm = re.match(r"(\d+)\s*/\s*(\S+)", br)
        rtm = re.match(r"(\d+)\s*/\s*(\S+)", rt)
        out.append(dict(
            source_row=n, job_number=job, description=clean(r.get("Name of work")),
            ward=job.split("-")[-3], contractor=clean_contractor(r.get("Name of contractor")),
            budget_head=clean(r.get("Budget head")), office=clean(r.get("DDO")),
            br_no=brm.group(1) if brm else None, br_date=parse_date(brm.group(2)) if brm else None,
            payment_ref=("RTGS " + rtm.group(1)) if rtm else None, payment_date=parse_date(rtm.group(2)) if rtm else None,
            payment_status="Paid (RTGS)" if rtm else None,
            gross=money(r.get("Gross")),
        ))
    return out


PARSERS = {
    "bbmp_publicview": parse_publicview, "oc_wodetails": parse_wodetails, "bbmp_dept": parse_dept,
    "bbmp_billreg": parse_billreg, "bbmp_jobcodes": parse_jobcodes, "bbmp_nagarothana": parse_nagarothana,
}


# ---------------------------------------------------------------- KPPP tenders
def parse_kppp(path):
    j = json.loads(Path(path).read_text())
    s, full, bid = j["search"], j.get("full") or {}, j.get("selected_bid") or {}
    ts = full.get("tenderSchedule") or {}
    nit = full.get("noticeInvitingTenderDTO") or {}
    award = full.get("tenderAwardDatesDTO") or {}
    pbg = (award.get("listOfBidderDonePBGDTO") or [{}])[0] or {}
    contract = money(bid.get("negotiatedValue")) or money(bid.get("bidValue"))
    return dict(
        nit_id=s["nitId"], tender_number=s["tenderNumber"], description=clean(s.get("title")),
        long_description=clean(s.get("description")), dept=s.get("deptName"), location_name=s.get("locationName"),
        status=s.get("status"), status_text=s.get("statusText"), work_category=s.get("workCategoryName"),
        published_date=parse_date(s.get("publishedDate")), closing_date=parse_date(s.get("tenderClosureDate")),
        ecv=money(ts.get("ecv")) if ts.get("ecv") is not None else money(s.get("ecv")),
        provisional_amount=money(ts.get("provisionalAmount")), file_number=ts.get("fileNumber"),
        contractor=clean(bid.get("supplierName")) or clean(pbg.get("name")),
        supplier_id=bid.get("supplierId"),
        bid_value=money(bid.get("bidValue")), negotiated_value=money(bid.get("negotiatedValue")), contract_value=contract,
        awarded_date=epoch_ms_date(award.get("awardedDates")),
        files=[f.get("fileName") for f in (j.get("files") or []) if isinstance(f, dict)],
    )
