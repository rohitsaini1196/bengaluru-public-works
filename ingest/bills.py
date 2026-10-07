"""Load, scope-filter and de-duplicate BBMP bill records from every bill source."""
import json
from collections import Counter
from pathlib import Path

from ingest import parse, scope
from ingest.sources import BBMP_WORKS_BILL_PUBLIC_VIEW, BBMP_IFMS

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"


def ref(source, row=None, note=None):
    r = {"source_id": source["id"], "label": source["name"], "url": source["url"]}
    if row:
        r["rows"] = [row]
    if note:
        r["note"] = note
    return r


def grouped_refs(bills, note=None):
    """One reference per source file, listing the data rows used from it."""
    out = {}
    for b in bills:
        s = b["source"]
        r = out.setdefault(s["id"], {"source_id": s["id"], "label": s["name"], "url": s["url"], "rows": []})
        if note:
            r["note"] = note
        if b.get("source_row") and b["source_row"] not in r["rows"]:
            r["rows"].append(b["source_row"])
    return list(out.values())


def load_sources():
    manifest = json.loads((RAW / "manifest.json").read_text())
    for i, s in enumerate(manifest):
        s["id"] = f"S{i + 1:02d}"
        s["publisher"] = "BBMP (via OpenCity Data Portal mirror)"
        s["original_portal"] = BBMP_WORKS_BILL_PUBLIC_VIEW if s["kind"] in ("bbmp_publicview", "oc_wodetails") else BBMP_IFMS
    return manifest


def load_bills(sources):
    bills, estimates = [], {}
    stats = Counter()
    for s in sources:
        path = RAW / s["file"]
        if not path.exists():
            print("missing", path)
            continue
        recs = parse.PARSERS[s["kind"]](path, s)
        for r in recs:
            stats["parsed"] += 1
            r["source"] = s
            r["regime"] = s["regime"]
            why = scope.scope_reason(r.get("description"), s["regime"], r.get("ward"), r["job_number"])
            if not why:
                continue
            r["scope_reason"] = why
            if s["kind"] == "bbmp_jobcodes":
                if r.get("estimate"):
                    estimates[r["job_number"]] = r
                continue
            r["category"] = scope.classify(r.get("description"))
            stats["in_scope"] += 1
            bills.append(r)
    # BBMP IFMS payment grid (Phase 5 discovery): every Koramangala bill paid since mid-2015
    from ingest import discover_bbmp
    grid_src = {"id": "IFMS-GRID", "name": "BBMP IFMS payment grid (LoadPaymentGridData, all wards, quarterly)",
                "url": "https://accounts.bbmp.gov.in/vssWB/vss00CvStatusData.php?pAction=LoadPaymentGridData", "kind": "ifms_grid",
                "regime": None, "file": "bbmp_discovery/", "page": "https://accounts.bbmp.gov.in/vssWB/"}
    for b in discover_bbmp.as_bills(grid_src) if discover_bbmp.OUT.exists() else []:
        b["category"] = scope.classify(b.get("description"))
        stats["in_scope_grid"] += 1
        bills.append(b)
    return bills, estimates, stats


def dedupe_bills(bills):
    """The same bill can appear in several exports (e.g. 2013-22 ward file and 2022-23 file).
    Primary key: (job, BR number, BR date). Records without a BR (2010-18 registers) are
    dropped if a BR-keyed bill with the same job and amounts exists, and exact repeats
    within those registers collapse."""
    seen, out, with_br_amounts = set(), [], set()
    ordered = sorted(bills, key=lambda b: (b.get("br_no") is None, b["source"]["kind"] != "bbmp_publicview"))
    for b in ordered:
        job = b["job_number"]
        keys = []
        ifms = (b.get("extra") or {}).get("ifms_id")
        if ifms:  # OpenCity IFMS scrape carries a unique bill id
            keys.append(("IFMS", ifms))
        if b.get("br_no") and b.get("br_date"):
            keys.append(("BR", job, b["br_no"].lstrip("0"), b["br_date"]))
            with_br_amounts.add((job, b.get("gross")))   # gross only: some registers carry no nett
        elif not ifms:
            if (job, b.get("gross")) in with_br_amounts:
                continue
            # 2010-18 registers repeat rows heavily and carry typo'd WO numbers ("WO-000o51"),
            # so the amount triple is the identity here.
            keys.append(("ROW", job, b.get("gross"), b.get("deduction"), b.get("nett"),
                         (b["source"]["id"], b.get("source_row")) if b.get("gross") is None else None))
        if any(k in seen for k in keys):
            seen.update(keys)
            continue
        seen.update(keys)
        b["dedupe_key"] = keys[0][0] if keys else "ROW"  # IFMS / BR = exact identity; ROW = matched on amounts
        out.append(b)
    return out
