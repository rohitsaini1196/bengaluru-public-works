"""Phase 6: gather and read every document that could confirm or dissolve a candidate case.

For each candidate job (CANDIDATES), from the IFMS attachment listings already cached:
  * variation documents  — work slip, EIRL, supplementary Schedule B, deviation statement, revised
                           estimate, extra / excess items (by attachment type or file name)
  * final-bill documents — every Bill Form / M.B. attached to a final bill
  * contract documents   — work orders, agreements, completion certificates
Files are fetched from their public URLs (1 request/s, cached under data/raw/bbmp_direct/cases/ unless
already cached by an earlier phase) and read with the quantity pipeline (text layer, else 2x OCR).

Writes data/case_evidence.json: {job: {documents: [...], variation_items: [...], final_bill: {...}}}
"""
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from ingest import quantities as Q

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "raw" / "bbmp_direct" / "cases"
OUT = ROOT / "data" / "case_evidence.json"
CACHE_DIRS = [ROOT / "data" / "raw" / "bbmp_direct" / d for d in ("qty", "files", "eot", "cases")]

# Candidate pool: the Phase 5 flagships plus every project carrying a case-type signal of material value.
CANDIDATES = [
    "186-23-000001", "151-17-000064", "151-20-000096", "151-23-000003", "151-16-000019", "151-16-000025",
    "304-17-000105", "304-15-000370", "304-15-000371", "174-24-000004", "151-17-000007", "174-24-000005",
    "307-19-000009", "151-21-000002", "151-18-000012", "174-25-000010", "304-20-000212", "152-20-000049",
    "148-23-000003", "174-25-000007", "151-14-000060", "151-14-000065", "151-17-000006",
]
VARIATION = re.compile(r"work\s*slip|\bw\.?\s*s\b|eirl|supplement|deviation|revised|variation|extra\s*item|excess", re.I)


def find(name):
    for d in CACHE_DIRS:
        p = d / name.replace("/", "_")
        if p.exists():
            return p
    return None


def kind_of(f, final_wbids):
    t, n = (f.get("rFileType") or "").strip(), f["rFileName"]
    if t == "Supplementary Schedule B" or VARIATION.search(n):
        return "variation"
    if t in Q.BILL_TYPES and f["wbid"] in final_wbids:
        return "final_bill"
    if t in ("Completion Certificate", "Work Order", "Agreement"):
        return t.lower()
    return None


def documents(listing, job):
    """Relevant attachments of a job, one entry per file name, with the bills that carry it."""
    bills = {w: d for w, (j, d, _) in listing.items() if j == job}
    finals = {w for w, d in bills.items() if "final" in (d.get("billtype") or "").lower()}
    out = {}
    for w, (j, d, fs) in listing.items():
        if j != job:
            continue
        for f in fs:
            f = {**f, "wbid": w}
            k = kind_of(f, finals)
            if not k or Q._is_placeholder(f["rFileName"]):
                continue
            e = out.setdefault(f["rFileName"], {"document_id": f["rFileName"], "type": (f.get("rFileType") or "").strip(),
                                                "kind": k, "url": Q._url(f), "listing_url": f.get("listing_url"),
                                                "work_bills": []})
            if w not in e["work_bills"]:
                e["work_bills"].append(w)
            if k == "final_bill":
                e["kind"] = "final_bill"
    return list(out.values()), finals


def fetch(listing, jobs=CANDIDATES):
    from ingest import fetch_bbmp_direct as F
    DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for job in jobs:
        docs, _ = documents(listing, job)
        for d in docs:
            if find(d["document_id"]):
                continue
            body, ct = F._open(d["url"], timeout=180)
            time.sleep(F.DELAY)
            n += 1
            if body and len(body) <= F.MAX_FILE:
                (DIR / d["document_id"].replace("/", "_")).write_bytes(body)
    return n


def read_all(listing, jobs=CANDIDATES):
    from concurrent.futures import ThreadPoolExecutor
    paths = [p for job in jobs for d in documents(listing, job)[0] if (p := find(d["document_id"]))]
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(Q.page_texts, paths))


if __name__ == "__main__":
    import sys
    listing = Q._load_listing()
    n = fetch(listing) if "--no-fetch" not in sys.argv else 0
    read_all(listing)
    print(json.dumps({"requests": n}))


# ---------------------------------------------------------------- work slip / comparative statement

HEADER = [
    ("sanctioned_estimate", re.compile(r"sanctioned\s+estimate\s+amount\s*[:\-]?\s*Rs\.?\s*([\d,]+\.?\d*)", re.I)),
    ("sanctioned_tender", re.compile(r"sanctioned\s+tender\s+amount\s*[:\-]?\s*Rs\.?\s*([\d,]+\.?\d*)", re.I)),
    ("tender_premium", re.compile(r"tender\s+premium\s*[:\-]?\s*((?:above|below|less|excess)\s*[\d.]+\s*%)", re.I)),
    ("agreement_amount", re.compile(r"agreement\s+amount\s*[=:\-]?\s*(?:Rs\.?\s*)?([\d,]+\.?\d*)", re.I)),
    ("work_slip_excess", re.compile(r"work\s*slip\s+cost\s*\(?\s*excess\s*\)?\s*[\[\|=:\-_\s]*([\d,]+\.?\d*)", re.I)),
    ("excess_percent", re.compile(r"(?:EIRL|percentage\s+of\s+excess)[^\n]{0,60}?\[?\s*(\d{1,2}\.\d{1,2})\s*%", re.I)),
]


def _dec(tok):
    """Printed with decimals: '730.20' or the decimal comma '2,00'."""
    return bool(re.search(r"[.,]\d{1,4}$", tok or ""))


def _triple_at(vals, toks, start=0):
    """First (i, j, k) with vals[i] * vals[j] == vals[k] (two of three printed with decimals)."""
    n = len(vals)
    for k in range(start + 2, n):
        for i in range(start, k - 1):
            for j in range(i + 1, k):
                q, r, a = vals[i], vals[j], vals[k]
                if not q or not r or not a:
                    continue
                if sum(_dec(toks[x]) for x in (i, j, k)) >= 2 and Q._close(q * r, a) and Q._plausible(q, r, a):
                    return i, j, k
    return None


def parse_work_slip_row(line):
    """A comparative-statement row: As per tender (qty, rate, amount) | executed qty | up to 125 % | above 125 %
    | total as per actuals | excess | savings. Returns a dict or None.
    Checks: tender qty x rate = tender amount; executed qty x rate = actual amount; and, when printed,
    tender amount - actual = savings (or actual - tender = excess)."""
    toks = Q.NUM_TOKEN.findall(line)
    vals = [Q.num(t) for t in toks]
    t = _triple_at(vals, toks)
    if not t:
        return None
    i, j, k = t
    qt, rate, at = vals[i], vals[j], vals[k]
    rest = list(range(k + 1, len(vals)))
    exe = act = None
    pairs = [(vals[a], vals[b], b) for a in rest for b in rest
             if b > a and vals[a] is not None and vals[b] and Q._close(vals[a] * rate, vals[b])]
    # split layout: executed qty | up to 125 % (qty, rate, amount) | above 125 % (qty, other rate, amount) | total
    split = None
    if k + 1 < len(vals) and vals[k + 1]:
        e0 = vals[k + 1]
        trip = []
        x = k + 2
        while x < len(vals):
            t2 = _triple_at(vals, toks, x)
            if not t2:
                break
            trip.append(t2)
            x = t2[2] + 1
        for a_ in range(len(trip)):
            for b_ in range(a_ + 1, len(trip)):
                (i1, j1, k1), (i2, j2, k2) = trip[a_], trip[b_]
                if abs(vals[i1] + vals[i2] - e0) <= 0.011 and abs(vals[j1] - rate) <= 0.005 * rate:
                    split = (e0, vals[k1] + vals[k2], k2)
                    break
            if split:
                break
    if split:
        exe, act, bi = split
        pairs = []
    derived = False
    # an amount that is an exact multiple of the rate, larger than any paired amount: its quantity was misread
    for b in ([] if split else rest):
        v = vals[b]
        if v and v > at and abs(v - rate) > 0.01 and v / rate <= 50 * max(qt, 1) and abs(v / rate - round(v / rate, 2)) < 1e-6 \
                and not any(Q._close(v, a2) for _, a2, _ in pairs) and v > max([a2 for _, a2, _ in pairs] + [0]):
            pairs.append((round(v / rate, 2), v, b))
            derived = True
    if split:
        pass
    elif pairs:                          # several (qty, amount) pairs: up to 125 %, above 125 %, TOTAL EXECUTED, excess
        exe, act, bi = max(pairs, key=lambda x: (x[0], -x[2]))     # the total executed is the largest
        derived = derived and not any(Q._close(e2, exe) and b2 != bi for e2, _, b2 in pairs)
    if exe is None and not split:
        zero = [x for x in rest if vals[x] == 0]
        if zero and any(vals[x] and Q._close(vals[x], at) for x in rest):     # nothing executed: savings = tender amount
            exe, act, bi = 0.0, 0.0, zero[0]
        else:
            return None
    if exe > 20 * max(qt, 1):            # implausible: a mis-split row, not an executed quantity
        return None
    checks = ["tender qty x rate = amount", "executed = up to 125 % + above 125 % (each qty x rate = amount)" if split else
              "executed qty x rate = actual amount" if exe else "executed 0, savings = tender amount"]
    excess = savings = None
    for x in range(bi + 1, len(vals)):
        v = vals[x]
        if v is None or abs(v - rate) <= 0.01:            # the rate repeated is not an excess / saving
            continue
        if act < at and (Q._close(at - act, v) or abs(at - act - v) <= 2):
            savings = v
            checks.append("tender - actual = savings")
            break
        if act > at and (Q._close(act - at, v) or abs(act - at - v) <= 2):
            excess = v
            checks.append("actual - tender = excess")
            break
        digits = re.sub(r"\D", "", toks[x])        # savings printed without its decimal point
        for diff in (at - act, act - at):
            if diff > 0 and digits and digits == re.sub(r"\D", "", f"{diff:.2f}").lstrip("0"):
                if diff == at - act:
                    savings = round(diff, 2)
                    checks.append("tender - actual = savings (decimal restored)")
                else:
                    excess = round(diff, 2)
                    checks.append("actual - tender = excess (decimal restored)")
                break
        if savings is not None or excess is not None:
            break
    if derived:
        checks[1] = "executed qty = actual amount / rate (printed qty unreadable)"
    return {"tender_qty": qt, "rate": rate, "tender_amount": at, "executed_qty": exe, "actual_amount": act,
            "excess": excess, "savings": savings, "checks": checks,
            "confidence": "high" if len(checks) >= 3 and not derived else "medium"}


def parse_work_slip(pages):
    """Rows and header totals of a work slip / comparative statement."""
    rows, header = [], {}
    for pg in pages:
        text = pg.get("text") or ""
        for key, rx in HEADER:
            m = rx.search(text)
            if m and key not in header:
                header[key] = {"value": m.group(1) if key == "tender_premium" else
                               float(m.group(1)) if key == "excess_percent" else Q.num(m.group(1).replace(",", "")),
                               "page": pg.get("page"), "raw": m.group(0)}
        block = []
        for ln in text.splitlines():
            r = parse_work_slip_row(ln)
            if not r:
                block.append(ln)
                block = block[-6:]
                continue
            ctx = " ".join(block[-5:] + [ln])
            us = [Q.norm_unit(m.group(1)) for m in Q.UNIT_TOKEN.finditer(ln.split(Q.NUM_TOKEN.findall(ln)[0])[0])]
            us = [u for u in us if u]
            cm = list(Q.CODE.finditer(ctx))
            rows.append({**r, "unit": us[-1] if us else None, "code": Q.norm_code(cm[-1]) if cm else None,
                         "description": re.sub(r"\s+", " ", Q.NUM_TOKEN.sub(" ", re.sub(r"[|\[\]{}]", " ", ctx))).strip()[-220:],
                         "raw": ln.strip()[:260], "page": pg.get("page"), "method": pg.get("method")})
            block = []
    return rows, header
