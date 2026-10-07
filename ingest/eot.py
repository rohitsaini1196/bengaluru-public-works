"""Extension-of-time (EoT) orders attached in IFMS, for every Koramangala job.

IFMS lists them as "Others" attachments named EOT / Extension of time. Each is fetched once (public
URL, 1 request/s, cached under data/raw/bbmp_direct/eot/), read with the quantity pipeline's OCR
(native text layer first), and parsed conservatively:
  "extension of time is accorded from 31/12/2020 to 31/12/2021 (365 days) with a nominal fine of Rs 1.09 crore"
Only a parsed "to" date is used downstream; everything stays inferred (OCR) with the document URL,
page and raw sentence as provenance. Writes data/eot.json  {job: [order, ...]}.
"""
import json
import re
import time
from datetime import date
from pathlib import Path

from ingest import quantities as Q

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "raw" / "bbmp_direct" / "eot"
OUT = ROOT / "data" / "eot.json"
NAME = re.compile(r"\beot\b|extension\s*of\s*time|time\s*extension", re.I)
D = r"(\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4})"
FROM_TO = re.compile(r"from\s*" + D + r"\s*(?:to|till|upto|up\s*to)\s*" + D, re.I)
UPTO = re.compile(r"(?:extended|extension[^.]{0,60}?)\s*(?:up\s*to|upto|till|to)\s*" + D, re.I)
DAYS = re.compile(r"\(?\s*(\d{1,4})\s*days\s*\)?", re.I)
FINE = re.compile(r"(?:nominal\s+)?(?:fine|penalty|liquidated\s+damages?)\s+of\s+Rs\.?\s*([\d,]+(?:\.\d+)?)\s*(crores?|lakhs?|lacs?)?", re.I)


def iso(s):
    """'31/12/2020' / '4.11.19' -> '2020-12-31'; None if not a real date."""
    m = re.match(r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})", s or "")
    if not m:
        return None
    d, mo, y = (int(x) for x in m.groups())
    y = y + 2000 if y < 100 else y
    try:
        v = date(y, mo, d)
    except ValueError:
        return None
    return v.isoformat() if 2005 <= y <= 2035 else None


def parse(text):
    """One EoT order's fields from its text: [{from, to, days, fine, raw}] (one per extension sentence)."""
    out = []
    flat = re.sub(r"\s+", " ", text or "")
    for m in FROM_TO.finditer(flat):
        a, b = iso(m.group(1)), iso(m.group(2))
        if not b or (a and b <= a):
            continue
        tail = flat[m.end(): m.end() + 200]
        dm, fm = DAYS.search(tail[:40]), FINE.search(tail)
        fine = None
        if fm:
            v = Q.num(fm.group(1).replace(",", ""))
            unit = (fm.group(2) or "").lower()
            fine = v * (1e7 if unit.startswith("crore") else 1e5 if unit.startswith(("lakh", "lac")) else 1) if v else None
        out.append({"from": a, "to": b, "days": int(dm.group(1)) if dm else None, "fine": fine,
                    "raw": flat[max(0, m.start() - 60): m.end() + 120].strip()})
    if not out:
        for m in UPTO.finditer(flat):
            b = iso(m.group(1))
            if b:
                out.append({"from": None, "to": b, "days": None, "fine": None,
                            "raw": flat[max(0, m.start() - 60): m.end() + 80].strip()})
    return out


def files(listing):
    by_job = {}
    for wbid, (job, d, fs) in listing.items():
        for f in fs:
            if NAME.search(f"{f['rFileName']} {f.get('rFileType') or ''}") and not Q._is_placeholder(f["rFileName"]):
                by_job.setdefault(job, {}).setdefault(f["rFileName"], {**f, "wbid": wbid})
    return by_job


def run(fetch=True):
    from ingest import fetch_bbmp_direct as F
    DIR.mkdir(parents=True, exist_ok=True)
    listing = Q._load_listing()
    n_req, data = 0, {}
    todo = files(listing)
    if fetch:                                # 1. download (1 request/s) ...
        for job, fs in sorted(todo.items()):
            for name, f in sorted(fs.items()):
                dest = DIR / name.replace("/", "_")
                if not dest.exists():
                    body, ct = F._open(Q._url(f), timeout=180)
                    time.sleep(F.DELAY)
                    n_req += 1
                    if body and len(body) <= F.MAX_FILE:
                        dest.write_bytes(body)
    from concurrent.futures import ThreadPoolExecutor   # 2. ... then OCR in parallel
    paths = [DIR / n.replace("/", "_") for fs in todo.values() for n in fs]
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda q: q.exists() and Q.page_texts(q, max_pages=4), paths))
    for job, fs in sorted(todo.items()):
        orders, seen = [], set()
        for name, f in sorted(fs.items()):
            dest = DIR / name.replace("/", "_")
            url = Q._url(f)
            if not dest.exists():
                orders.append({"document_id": name, "url": url, "status": "not downloaded"})
                continue
            pages = Q.page_texts(dest, max_pages=4)
            found = False
            for pg in pages:
                for e in parse(pg["text"]):
                    key = (e["from"], e["to"])
                    if key in seen:          # the same order re-uploaded on several bills
                        found = "duplicate"
                        continue
                    seen.add(key)
                    found = True
                    orders.append({**e, "document_id": name, "url": url, "work_bill_id": f["wbid"], "page": pg["page"],
                                   "method": pg["method"], "status": "parsed"})
            if found == "duplicate":
                orders.append({"document_id": name, "url": url, "status": "duplicate of a parsed order"})
            elif not found:
                orders.append({"document_id": name, "url": url, "status": "no extension date read"})
        data[job] = orders
    OUT.write_text(json.dumps(data, indent=1))
    return n_req, data


def load():
    return json.loads(OUT.read_text()) if OUT.exists() else {}


if __name__ == "__main__":
    import sys
    n, d = run(fetch="--no-fetch" not in sys.argv)
    parsed = [o for v in d.values() for o in v if o["status"] == "parsed"]
    print(json.dumps({"requests": n, "jobs": len(d), "jobs_with_parsed_extension": sum(1 for v in d.values()
                      if any(o["status"] == "parsed" for o in v)), "orders_parsed": len(parsed)}))
