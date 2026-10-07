"""Archived BBMP planning / tender documents (Wayback Machine copies of site.bbmp.gov.in and
bbmp.gov.in, which are geo-blocked outside India).

  sanction   FY 2015-16 / 2016-17 ward works lists ("POW"): job code, description, estimate, DDO,
             budget head. Keyed by BBMP job code -> attach to projects exactly.
  pow        POW SOUTH1 (Programme of Works 2022-23, South zone): ward, work, estimate (lakh), status.
  notice     BTM Layout division short-term tender notifications 2018-19 (scanned, OCR): work,
             approximate value (lakh), EMD, period of completion.
  grant      Amrutha Nagarothana grants, BTM Layout constituency (2021-22 GO, scanned, OCR):
             work, amount (lakh).
  hort       Horticulture South zone park-maintenance tenders (xlsx, 2022): park, estimate,
             tendered amount, L1 bidder.

Everything except `sanction` and `hort` reaches projects only through the matcher, so facts from it
are inferred. OCR'd values are additionally flagged (`ocr=True`) because digits can be misread.
"""
import re
from pathlib import Path

from ingest import parse, scope

ROOT = Path(__file__).resolve().parent.parent
WB = ROOT / "data" / "raw" / "wayback"
WAYBACK = "https://web.archive.org/web/{ts}/{url}"

DOCS = {
    "koramangala_south.pdf": ("sanction", "20220823021249", "https://site.bbmp.gov.in/documents/Koramangala-south.pdf", "BBMP ward works list FY 2016-17 — Koramangala"),
    "adugodi_south.pdf": ("sanction", "20220823021151", "https://site.bbmp.gov.in/documents/Adugodi-South.pdf", "BBMP ward works list FY 2016-17 — Adugodi"),
    "adugodi.pdf": ("sanction", "20220823020425", "https://site.bbmp.gov.in/documents/Adugodi.pdf", "BBMP ward works list FY 2015-16 — Adugodi"),
    "ejipura_south.pdf": ("sanction", "20220823021212", "https://site.bbmp.gov.in/documents/Ejipura-South.pdf", "BBMP ward works list FY 2016-17 — Ejipura"),
    "ejipura.pdf": ("sanction", "20220823020432", "https://site.bbmp.gov.in/documents/Ejipura.pdf", "BBMP ward works list FY 2015-16 — Ejipura"),
    "jakkasandra_south.pdf": ("sanction", "20220823021231", "https://site.bbmp.gov.in/documents/Jakkasandra-South.pdf", "BBMP ward works list FY 2016-17 — Jakkasandra"),
    "jakkasandra.pdf": ("sanction", "20220823020439", "https://site.bbmp.gov.in/documents/Jakkasandra.pdf", "BBMP ward works list FY 2015-16 — Jakkasandra"),
    "pow_south1.pdf": ("pow", "20241130083212", "https://site.bbmp.gov.in/PDF/whatsnew/POW%20SOUTH1.pdf", "BBMP Programme of Works 2022-23 — South zone"),
    "ee_btm_eproc_02_2018_19.pdf": ("notice", "20190624070249", "http://bbmp.gov.in:80/documents/10180/11652738/EE-BTM-JE-E-PROC-02-2018-19.pdf/ec73d8cb-9ee8-45fd-ad4c-5c494b7ca1b3", "BBMP EE BTM Layout tender notification 02/2018-19"),
    "ee_btm_04.pdf": ("notice", "20190618225700", "http://bbmp.gov.in:80/documents/10180/11716281/EE+BTM+04.pdf/d641e042-e995-45f9-9eca-bb7f188f108b", "BBMP EE BTM Layout tender notification 04/2018-19"),
    "amrut_btm.pdf": ("grant", "20241130081413", "https://site.bbmp.gov.in/PDF/whatsnew/16.%20Amruth%20Nagrothana%20Grants_South%20Zone_BTM%20Layout%20Assembly%20Constituency.pdf", "Amrutha Nagarothana grants — BTM Layout constituency (GO 2021-22)"),
    "8zone_tenders.xlsx": ("hort", "20220706195407", "https://site.bbmp.gov.in/PDF/whatsnew/8%20zone%20tender%20details.xlsx", "BBMP Horticulture South zone park-maintenance tenders (2022)"),
}
KORA_WARDS_198 = {"151"}


def doc_url(fname):
    kind, ts, url, title = DOCS[fname]
    return WAYBACK.format(ts=ts, url=url)


def _text(fname):
    p = WB / (fname + ".ocr.txt")
    if p.exists():
        return p.read_text(errors="replace"), True
    p = WB / (fname + ".txt")
    return (p.read_text(errors="replace"), False) if p.exists() else ("", False)


def _rec(fname, **kw):
    kind, ts, url, title = DOCS[fname]
    r = dict(source=f"WB-{kind.upper()}", doc=fname, doc_title=title, url=doc_url(fname), kind=kind,
             tender_number=None, base=None, call=1, contractor=None, contract_value=None, negotiated_value=None,
             bid_value=None, awarded_date=None, provisional_amount=None, status=None, status_text=None,
             period_days=None, published_date=None, closing_date=None, ecv=None, bidders=None, office=None,
             dept="Bruhat Bengaluru Mahanagara Palike", ocr=False)
    r.update(kw)
    r["category"] = scope.classify(r.get("description") or "")
    return r


def in_scope(desc, ward=None, regime="198"):
    if scope.scope_reason(desc or "", regime, ward):
        return scope.scope_reason(desc or "", regime, ward)
    if ward in KORA_WARDS_198 and regime == "198":
        return "Booked to Koramangala ward (151 under the 198-ward map)"
    return None


# ------------------------------------------------------------------ FY ward sanction lists (text PDFs)
SANCTION_ROW = re.compile(r"(\d+)\s+South\s+B\s*T\s*M Layout\s+([A-Za-z ]+?)\s*\n(\d{3}-\d{2}-\d{6})\s*\n(.*?)\n\s*(\d+\.\d{2})\s*/\s*(.*?)(?=\n\d+\s+South\s+B\s*T\s*M|\Z)", re.S)


def parse_sanction(fname):
    txt, _ = _text(fname)
    fy = re.search(r"(?:FIN YEAR|fy)\s*(\d{2}-\d{2})", txt, re.I)
    out = []
    for m in SANCTION_ROW.finditer(txt):
        sl, ward_name, job, desc, est, rest = m.groups()
        desc = parse.clean(desc)
        parts = [parse.clean(x) for x in re.split(r"\s*/\s*", parse.clean(rest) or "")]
        ddo = parts[0] if parts else None
        bh = " / ".join(p for p in parts[1:] if p) or None
        out.append(_rec(fname, job_number=job, description=desc, ward=job[:3], ward_name=parse.clean(ward_name),
                        ecv=float(est), office=ddo, budget_head=bh, row=int(sl),
                        fy=("20" + fy.group(1)) if fy else None,
                        scope_reason=in_scope(desc, job[:3])))
    return out


# ------------------------------------------------------------------ POW South 2022-23
POW_ROW = re.compile(r"^\s*(\d{1,3})\s+BTM Layout\s+(\d{3})\s+(.+?)\s+(\d{1,4}\.\d{2})\s+(File Under Process|File in Process|Tender in Process|Work Order Issued|Works? Completed|Works? in Progress|Yet to Start)\b(.*)$", re.S)


def parse_pow(fname):
    txt, _ = _text(fname)
    # rows wrap across lines: buffer from one "N BTM Layout WWW" line to the next
    joined, buf = [], None
    for ln in txt.splitlines():
        if re.match(r"^\s*\d{1,3}\s+[A-Z][A-Za-z .]+?\s+\d{3}\s", ln):
            if buf:
                joined.append(buf)
            buf = ln.strip()
        elif buf is not None and ln.strip() and not re.match(r"^(SL|Page|BRUHAT|POW|No of|\d{2}\s+\d{2})", ln.strip()):
            buf += " " + ln.strip()
    if buf:
        joined.append(buf)
    out = []
    for ln in joined:
        m = POW_ROW.match(ln)
        if not m:
            continue
        sl, ward, desc, est, status, tail = m.groups()
        desc = desc + " " + tail if tail and len(tail) > 10 else desc
        why = scope.scope_reason(desc, "243", ward)
        if not why:
            continue
        out.append(_rec(fname, description=parse.clean(desc), ward=ward, regime="243", ecv=round(float(est) * 1e5, 2),
                        status_text=f"{status} (POW 2022-23)", plan_status=status, row=int(sl),
                        published_date="2022-04-01", fy="2022-23", scope_reason=why))
    return out


# ------------------------------------------------------------------ OCR'd tender notifications 2018-19
DAYS = re.compile(r"(\d{2,3})\s*[D0O]\s*ays", re.I)


def parse_notice(fname):
    txt, ocr = _text(fname)
    no = re.search(r"No\.?\s*E\.?E\.?\s*/?\s*\(?BTM\)?\s*/\s*J\s*E\s*/\s*e-proc\s*/\s*(\d+)\s*/\s*(\d{4}-\d{2})", txt)
    dated = re.search(r"Dated\s*:\s*(\d{2}-\d{2}-\d{4})", txt)
    pub = parse.parse_date(dated.group(1)) if dated else None
    notice_no = f"EE(BTM)/JE/e-proc/{no.group(1)}/{no.group(2)}" if no else fname
    out, prev = [], 0
    body = txt
    for m in DAYS.finditer(body):
        chunk = body[prev:m.start()]
        prev = m.end()
        chunk = chunk[-420:]
        if not re.search(r"k[o0]ra?mangala|\b15[1l]\b", chunk, re.I):
            continue
        amt = re.findall(r"\b(\d{1,3}\.\d{2})\b", chunk)
        emd = re.findall(r"(\d[\d,]{2,})\s*/-", chunk)
        desc = re.sub(r"\(\s*(SC|ST|Others)[^)]*\)?", " ", chunk)
        desc = re.sub(r"\b\d{1,3}\.\d{2}\b|\d[\d,]{2,}\s*/-|[|\[\]{}~=_—;]", " ", desc)
        desc = re.sub(r"^\W*\d{1,3}\s*\W*\s*\d{3}\s*\W", " ", desc)
        desc = parse.clean(re.sub(r"\s+", " ", desc.split("\n\n")[-1] if "\n\n" in desc else desc))
        if not desc or len(desc) < 15:
            continue
        why = in_scope(desc + " ward 151", "151") if re.search(r"\b15[1l]\b", chunk) else in_scope(desc)
        out.append(_rec(fname, description=desc[-300:], ward="151" if re.search(r"\b15[1l]\b", chunk) else None,
                        ecv=round(float(amt[-1]) * 1e5, 2) if amt else None, period_days=int(m.group(1)),
                        period_text=m.group(0), published_date=pub, tender_number=notice_no, base=f"{notice_no}#{len(out) + 1}",
                        ocr=ocr, scope_reason=why, status_text="Tender notified (outcome not in this document)"))
    return [r for r in out if r["scope_reason"]]


# ------------------------------------------------------------------ OCR'd Amrutha Nagarothana grant list
GRANT_ROW = re.compile(r"(?m)^\W*(\d{1,3})\s+(14[78]|151|17[36])\W{0,4}\s*(.+?)\s(\d{1,3}[.,]\d{2})\b(.*)$")


def parse_grant(fname):
    txt, ocr = _text(fname)
    lines = txt.splitlines()
    out = []
    for i, ln in enumerate(lines):
        m = GRANT_ROW.match(ln)
        if not m:
            continue
        sl, ward, desc, amt, tail = m.groups()
        # description often wraps onto the previous and next line
        ctx = " ".join(x.strip() for x in (lines[i - 1] if i else "", desc, lines[i + 1] if i + 1 < len(lines) else ""))
        ctx = parse.clean(re.sub(r"[|\[\]{}~=_—;@#:]|\b\d{1,3}[.,]\d{2}\b", " ", ctx))
        why = in_scope(ctx, ward)
        if not why:
            continue
        out.append(_rec(fname, description=ctx[:300], ward=ward, ecv=round(float(amt.replace(",", ".")) * 1e5, 2), row=int(sl),
                        published_date="2021-08-15", fy="2021-22", ocr=ocr, scope_reason=why,
                        status_text="Sanctioned under Amrutha Nagarothana (GO 2021-22)"))
    return out


# ------------------------------------------------------------------ Horticulture park tenders (xlsx)
def parse_hort(fname):
    import openpyxl
    import warnings
    warnings.filterwarnings("ignore")
    p = WB / fname
    if not p.exists():
        return []
    ws = openpyxl.load_workbook(p).active
    out, cur = [], None
    for n, row in enumerate(ws.iter_rows(values_only=True), start=1):
        sl, zone, ward, park_group, park, area, est, tendered, l1 = (list(row) + [None] * 9)[:9]
        if isinstance(sl, (int, float)) and ward:
            cur = dict(ward=str(int(ward)) if isinstance(ward, (int, float)) else str(ward), desc=parse.clean(park_group),
                       est=parse.money(str(est)) if est else None, tendered=parse.money(str(tendered)) if tendered else None,
                       l1=parse.clean_contractor(re.split(r",|\n|#", str(l1 or ""))[0] if l1 else None), row=n, parks=[])
            out.append(cur)
        if cur is not None and park:
            cur["parks"].append(parse.clean(str(park)))
    recs = []
    for c in out:
        if not (c["ward"] in KORA_WARDS_198 or re.search("koramangala|ngv", " ".join(c["parks"]) + (c["desc"] or ""), re.I)):
            continue
        desc = f"{c['desc']} ({', '.join(c['parks'][:6])})"
        recs.append(_rec(fname, description=desc, ward=c["ward"], ecv=c["est"], contract_value=c["tendered"],
                         contractor=c["l1"], row=c["row"], published_date="2022-04-01", fy="2022-23",
                         scope_reason=in_scope(desc, c["ward"]) or "Booked to Koramangala ward (151)",
                         status_text="Tendered (L1 bidder listed)"))
    return recs


def load_all():
    out = []
    for f, (kind, *_rest) in DOCS.items():
        if not ((WB / f).exists()):
            continue
        fn = {"sanction": parse_sanction, "pow": parse_pow, "notice": parse_notice, "grant": parse_grant, "hort": parse_hort}[kind]
        for r in fn(f):
            if r.get("scope_reason") and r.get("category"):
                r.setdefault("base", None)
                r["base"] = r["base"] or f"{f}#{r.get('row') or len(out)}"
                r["tender_number"] = r["tender_number"] or r["base"]
                out.append(r)
    return out
