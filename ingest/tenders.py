"""Unified tender records from KPPP (2023-26, with documents) and OpenCity BBMP tender lists (2013-18).

A tender record:
  source ("KPPP" | "OC-TENDERS"), tender_number, base (number without /CALL-n), call (int),
  description, dept, office, published_date, closing_date, status, ecv, provisional_amount,
  contract_value, negotiated_value, bid_value, contractor, awarded_date, period_days,
  period_text, period_url, bidders [{name, amount, rank}], comparative_url, files [...],
  url (evidence URL for tender facts), bid_url, scope_reason, category
"""
import csv
import html
import json
import re
from pathlib import Path

from ingest import parse, scope
from ingest.sources import KPPP_API

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OC_TENDER_PAGE = "https://data.opencity.in/dataset/bbmp-tenders"

# 2013-18 tender titles often say "ward no 151" without naming Koramangala.
WARD151 = re.compile(r"ward\s*no\.?\s*[:\-]?\s*151\b|\bw-?151\b", re.I)

WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty four": 24, "thirty": 30}


def base_tender(tn):
    return re.sub(r"/CALL-\d+$", "", (tn or "").strip(), flags=re.I)


def call_no(tn):
    m = re.search(r"/CALL-(\d+)$", tn or "", re.I)
    return int(m.group(1)) if m else 1


# ------------------------------------------------------------------ document parsing
PERIOD_HDR = re.compile(r"period\s+of\s+completion|time\s+(?:allowed\s+)?for\s+completion|stipulated\s+period|completion\s+period|"
                        r"time\s+limit", re.I)
PERIOD_VAL = re.compile(r"(\d{1,3})\s*(?:\(\s*[a-z ]+\s*\))?\s*(calendar\s+)?(days?|months?)\b", re.I)
PERIOD_WORD = re.compile(r"\b(" + "|".join(WORDNUM) + r")\s*(calendar\s+)?(days?|months?)\b", re.I)


def period_from_text(txt):
    """Find the stipulated period of completion in a tender document.
    Looks only in a window after a 'Period of Completion'-type heading and ignores
    boiler-plate (defect liability, bid validity, payment days)."""
    for m in PERIOD_HDR.finditer(txt):
        window = txt[m.end(): m.end() + 900]
        # stop at the next heading-ish boiler-plate that carries unrelated day counts
        window = re.split(r"defects?\s+liability|bid\s+validity|validity\s+period|final\s+payment|Note\s*:", window, flags=re.I)[0]
        for rx in (PERIOD_VAL, PERIOD_WORD):
            v = rx.search(window)
            if v:
                n = int(v.group(1)) if v.group(1).isdigit() else WORDNUM[v.group(1).lower()]
                unit = v.group(3).lower()
                days = n * 30 if unit.startswith("month") else n
                if 7 <= days <= 1460:
                    return days, re.sub(r"\s+", " ", v.group(0)).strip()
    return None, None


def parse_comparative(path):
    """Bidders (name, quoted total, L-rank) from a KPPP commercial comparative statement."""
    try:
        import openpyxl
        import warnings
        warnings.filterwarnings("ignore")
        ws = openpyxl.load_workbook(path).active  # read_only mode loses rows in these files
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    except Exception:
        return []
    names_row = next((r for r in rows[:12] if r and sum(1 for c in r[10:] if isinstance(c, str) and c.strip()) >= 1
                      and not any(isinstance(c, str) and "Quoted" in c for c in r)), None)
    total_row = next((r for r in rows if r and any(isinstance(c, str) and "Total Bid Amount" in c for c in r)), None)
    if not names_row or not total_row:
        return []
    out = []
    for i, c in enumerate(names_row):
        if i < 10 or not (isinstance(c, str) and c.strip()):
            continue
        cell = next((total_row[j] for j in (i + 1, i) if j < len(total_row) and total_row[j]), None)
        if not cell:
            continue
        m = re.match(r"\s*([\d.]+)\s*(?:\((L\d+)\))?", str(cell))
        if m:
            out.append({"name": c.strip(), "amount": float(m.group(1)), "rank": m.group(2)})
    return out


def doc_enrichment(nit):
    d = RAW / "kppp_docs" / str(nit)
    man = d / "manifest.json"
    if not man.exists():
        return {}
    m = json.loads(man.read_text())
    out = {"documents": m.get("files", [])}
    for f in m.get("files", []):
        t = d / (f["name"] + ".txt")
        if t.exists():
            days, txt = period_from_text(t.read_text(errors="replace"))
            if days:
                out.update(period_days=days, period_text=txt, period_url=f["url"], period_doc=f["name"])
                break
    if m.get("comparative"):
        bidders = parse_comparative(d / "comparative.xlsx")
        if bidders:
            out.update(bidders=bidders, comparative_url=m["comparative"]["url"])
    return out


# ------------------------------------------------------------------ loaders
def load_kppp():
    out = []
    for p in sorted((RAW / "kppp").glob("[0-9]*.json")):
        t = parse.parse_kppp(p)
        text = " ".join(filter(None, [t["description"], t["long_description"], t["location_name"]]))
        why = scope.scope_reason(text)
        if not why:
            continue
        t.update(source="KPPP", base=base_tender(t["tender_number"]), call=call_no(t["tender_number"]),
                 office=t["location_name"], scope_reason=why.replace("Work description", "Tender title"),
                 category=scope.classify(t["description"]),
                 url=f"{KPPP_API}/{t['nit_id']}/works-tender-full-view",
                 bid_url=f"{KPPP_API}/bids/{t['nit_id']}/tender-category/WORKS/get-selected-bid-for-lumpsum")
        t.update(doc_enrichment(t["nit_id"]))
        out.append(t)
    return out


def load_oc_tenders():
    """BBMP tender lists 2013-18 (OpenCity). Title, ECV, dates, tender number — no award data."""
    manifest = RAW / "tenders_oc" / "list.tsv"
    if not manifest.exists():
        return []
    urls = dict(line.rstrip("\n").split("\t") for line in manifest.read_text().splitlines() if "\t" in line)
    out = []
    for fname, url in urls.items():
        path = RAW / "tenders_oc" / fname
        if not path.exists():
            continue
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
            for n, r in enumerate(csv.DictReader(f), start=1):
                title = html.unescape(parse.clean(r.get("Tender Title")) or "")
                why = scope.scope_reason(title)
                if not why and WARD151.search(title) and not scope.NOT_CITY.search(title):
                    why = 'Tender title names ward 151 (Koramangala on the 198-ward map)'
                if not why or (r.get("Category") or "").upper() != "WORKS":
                    continue
                tn = parse.clean(r.get("Tender Number")) or ""
                out.append(dict(
                    source="OC-TENDERS", tender_number=tn, base=base_tender(tn), call=call_no(tn), description=title,
                    dept="Bruhat Bengaluru Mahanagara Palike", office=parse.clean(r.get("Department-Location")),
                    published_date=parse.parse_date(r.get("Published Date")), closing_date=parse.parse_date(r.get("Last Date")),
                    status=None, status_text="Tender published (outcome not in this list)",
                    ecv=parse.money(r.get("Tender Value in Rs")), provisional_amount=None, contract_value=None,
                    negotiated_value=None, bid_value=None, contractor=None, awarded_date=None,
                    work_category=parse.clean(r.get("Sub-Category")), url=url, row=n, file=fname,
                    scope_reason=why.replace("Work description", "Tender title"), category=scope.classify(title),
                ))
    return out


def group_by_base(tenders):
    groups = {}
    for t in tenders:
        groups.setdefault((t["source"], t["base"]), []).append(t)
    for g in groups.values():
        g.sort(key=lambda t: (t["call"], t["published_date"] or ""))
    return groups
