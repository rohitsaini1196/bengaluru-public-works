"""Enumerate every BBMP work-bill payment in the public IFMS payment grid, then pick out Koramangala.

Endpoint (public, unauthenticated — what https://accounts.bbmp.gov.in/vssWB/ "Payments" tab calls):
  vss00CvStatusData.php?pAction=LoadPaymentGridData&pDateFrom=dd-Mon-yyyy&pDateTo=dd-Mon-yyyy
                       &pBudgetHeadID=-1&pWardIDs=&pDDOID=-1&pDDOIDs=
  -> [{id, slno, wodetails(job + description), contractor(name<br/>mobile), brnumber(BR/CBR/RTGS), amount, nett, deduction}]
Behaviour established in Phase 5:
  * empty pWardIDs/pDDOIDs = all wards/DDOs; the date range filters on the RTGS (payment) date
  * pWardIDs=<n> filters on the 198-ward numbering (e.g. 151 = Koramangala, 186 = Jaraganahalli)
  * the ward/DDO master lists (LoadCombo) currently fail server-side ("pg_query(): Cannot set
    connection to blocking mode"), so ward ids for the 225/243-ward maps cannot be looked up
  * records exist from mid-2015 (RTGS era) onward; nothing before May 2015
So we enumerate ALL payments city-wide, quarter by quarter (≈46 requests, 1 req/s, cached) and apply the
project's Koramangala scope rules to each row. Contractor mobile numbers are stripped before caching.

Usage: python -m ingest.discover_bbmp            # writes data/raw/bbmp_discovery/<from>_<to>.json
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from ingest import fetch_bbmp_direct as F
from ingest import parse, scope

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "bbmp_discovery"
START, END = date(2015, 4, 1), date(2026, 9, 30)
PHONE = re.compile(r"(?:<br\s*/?>)?\s*(?:\+?91)?[0-9]{10}\s*$")


def quarters(start=START, end=END):
    d = start
    while d <= end:
        m = d.month + 3
        nxt = date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
        yield d, min(nxt - timedelta(days=1), end)
        d = nxt


def fmt(d):
    return d.strftime("%d-%b-%Y")


def sanitize(rows):
    out = []
    for r in rows if isinstance(rows, list) else []:
        r = dict(r)
        r["contractor"] = PHONE.sub("", r.get("contractor") or "").replace("<br/>", " ").strip()
        out.append(r)
    return out


def fetch(force=False):
    OUT.mkdir(parents=True, exist_ok=True)
    F._open(F.PAGE)
    n_req = 0
    for a, b in quarters():
        dest = OUT / f"{a.isoformat()}_{b.isoformat()}.json"
        if dest.exists() and not force:
            continue
        rows, url = F.call("LoadPaymentGridData", pDateFrom=fmt(a), pDateTo=fmt(b), pBudgetHeadID=-1,
                           pWardIDs="", pDDOID=-1, pDDOIDs="")
        n_req += 1
        if not isinstance(rows, list):
            print("error", a, b, rows)
            continue
        dest.write_text(json.dumps({"from": a.isoformat(), "to": b.isoformat(), "url": url, "rows": sanitize(rows)}))
        print(a, b, len(rows), flush=True)
    return n_req


ROW_JOB = re.compile(r"vssFillData\((\d+),\s*'([^']+)'\)")
BR = re.compile(r"BR\s*-\s*(\d*)\s*/\s*(\d{1,2}-[A-Za-z]{3}-\d{4})?")
CBR = re.compile(r"CBR\s*-\s*([\d-]*)\s*/\s*(\d{1,2}-[A-Za-z]{3}-\d{4})?")
RTGS = re.compile(r"Rtgs\s*-\s*(\d*)\s*/\s*(\d{1,2}-[A-Za-z]{3}-\d{4})?")


def parse_row(r, url):
    wd = r.get("wodetails") or ""
    m = ROW_JOB.search(wd)
    job = parse.norm_job(m.group(2)) if m else None
    desc = parse.clean(re.sub(r"<[^>]+>", " ", wd.split("<br/>", 1)[1] if "<br/>" in wd else wd))
    brs = (r.get("brnumber") or "").replace("<br/>", " ")
    br, cbr, rt = BR.search(brs), CBR.search(brs), RTGS.search(brs)
    return {
        "bill_id": str(r.get("id")), "job_number": job, "description": desc,
        "ward_prefix": job.replace("R-", "").split("-")[0] if job else None, "job_year": job.split("-")[1] if job else None,
        "contractor": parse.clean_contractor(r.get("contractor")),
        "br_no": (br.group(1) or None) if br else None, "br_date": parse.parse_date(br.group(2)) if br and br.group(2) else None,
        "cbr_no": (cbr.group(1) or None) if cbr else None, "cbr_date": parse.parse_date(cbr.group(2)) if cbr and cbr.group(2) else None,
        "rtgs_no": (rt.group(1) or None) if rt else None, "rtgs_date": parse.parse_date(rt.group(2)) if rt and rt.group(2) else None,
        "gross": parse.money(r.get("amount")), "nett": parse.money(r.get("nett")), "deduction": parse.money(r.get("deduction")),
        "source_url": url,
    }


def regime_for(prefix, year):
    """Which ward map a job number's ward prefix refers to. Jobs are numbered with the ward of the map
    in force when the job code was created: 198-ward map until FY 2022-23; 243-ward map for most FY
    2023-24 codes; 225-ward map from FY 2024-25. Departmental codes (3xx) are not wards."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    if prefix and prefix.isdigit() and int(prefix) >= 300:
        return "dept"
    return "198" if y <= 22 else "243" if y == 23 else "225"


def load(with_scope=True, area=None):
    rows = []
    for p in sorted(OUT.glob("*.json")):
        d = json.loads(p.read_text())
        for r in d["rows"]:
            x = parse_row(r, d["url"])
            x["window"] = f"{d['from']}..{d['to']}"
            rows.append(x)
    if with_scope:
        for x in rows:
            reg = regime_for(x["ward_prefix"], x["job_year"])
            x["regime"] = reg
            text_reason = scope.scope_reason(x["description"], area=area)       # description names the area
            ward_reason = scope.scope_reason("", reg if reg in ("198", "225", "243") else None,
                                             x["ward_prefix"], x["job_number"] if reg == "198" else None, area=area)
            named = WARD_NAME.search(x["description"] or "")
            name = named.group(1).strip().lower() if named else ""
            first = name.split()[0] if name else ""
            conflict = bool(name) and first not in NOT_A_NAME and not (area or scope.AREA).locality.search(name)
            if text_reason:
                x["scope_reason"], x["scope_basis"] = text_reason, "description"
            elif ward_reason and not conflict:
                x["scope_reason"], x["scope_basis"] = ward_reason + " (ward map inferred from the job-code year)", "ward_prefix"
            else:
                x["scope_reason"], x["scope_basis"] = None, ("ward_conflict" if ward_reason and conflict else None)
    return rows


NOT_A_NAME = {"and", "of", "in", "the", "for", "under", "package", "pkg", "south", "zone", "east", "west", "north", "sub",
              "division", "constituency", "bbmp", "old", "new", "no", "nos", "ward", "wards", "at", "on", "to", "with",
              "jurisdiction", "limits", "limit", "area", "areas", "s", "w", "name", "work", "works"}
# "ward No.186 Jaraganahalli" -> the locality named next to the ward number
WARD_NAME = re.compile(r"ward\s*(?:no)?\.?\s*[:\-]?\s*\d{1,3}\s*[(\-,]?\s*([A-Za-z][A-Za-z .]{3,30})", re.I)


if __name__ == "__main__":
    print("requests:", fetch(force="--force" in sys.argv))


def in_area(rows=None, area=None):
    """The area's rows, one per bill id (a bill can appear in several windows when released in parts)."""
    rows = rows if rows is not None else load(area=area)
    by = {}
    for r in rows:
        if not r["scope_reason"]:
            continue
        k = by.get(r["bill_id"])
        if k is None:
            by[r["bill_id"]] = dict(r, payment_dates=[r["rtgs_date"]] if r["rtgs_date"] else [])
        elif r["rtgs_date"] and r["rtgs_date"] not in k["payment_dates"]:
            k["payment_dates"].append(r["rtgs_date"])
    return list(by.values())


koramangala = in_area                    # name used by the Koramangala build


def as_bills(source):
    """Discovered Koramangala bills in the pipeline's bill-record shape (confirmed: from BBMP's own grid)."""
    out = []
    for r in koramangala():
        out.append({
            "source": source, "source_row": None, "job_number": r["job_number"], "description": r["description"],
            "ward": r["ward_prefix"], "contractor": r["contractor"], "gross": r["gross"], "nett": r["nett"],
            "deduction": r["deduction"], "br_no": r["br_no"], "br_date": r["br_date"],
            "payment_ref": f"RTGS {r['rtgs_no']}" if r["rtgs_no"] else None, "payment_date": min(r["payment_dates"]) if r["payment_dates"] else None,
            "payment_status": "Paid (RTGS)" if r["rtgs_no"] else None, "regime": r["regime"],
            "scope_reason": r["scope_reason"], "extra": {"ifms_id": r["bill_id"], "grid_url": r["source_url"], "scope_basis": r["scope_basis"]},
        })
    return out
