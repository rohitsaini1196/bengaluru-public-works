"""Show that another Bengaluru area runs through the same discovery and scope rules, using only the cached
city-wide payment grid (no new collection).

    python -m experiments.area_demo hsr_layout      -> data/area_demo_<slug>.json
"""
import json
import sys
from collections import Counter
from pathlib import Path

from ingest import area as A, discover_bbmp as D

ROOT = Path(__file__).resolve().parent.parent


def run(slug):
    cfg = A.load(slug)
    rows = D.in_area(area=cfg)
    base = {r["bill_id"] for r in D.in_area(area=A.load("koramangala"))}
    out = {
        "area": cfg.area_name, "verified_config": cfg.verified, "source_of_ward_numbers": cfg.source_of_ward_numbers,
        "bills": len(rows), "jobs": len({r["job_number"] for r in rows}),
        "gross": round(sum(r["gross"] or 0 for r in rows)), "scope_basis": dict(Counter(r["scope_basis"] for r in rows)),
        "first_payment": min((d for r in rows for d in r["payment_dates"]), default=None),
        "last_payment": max((d for r in rows for d in r["payment_dates"]), default=None),
        "bills_also_in_koramangala": len({r["bill_id"] for r in rows} & base),
        "sample": [{k: r[k] for k in ("job_number", "description", "gross", "scope_basis")} for r in rows[:5]],
        "next_steps": ["verify ward numbers against the delimitation notification", "AREA=<slug> python -m ingest.fetch_bbmp_direct --no-files",
                       "AREA=<slug> KORA_DB_OUT=data/<slug>.db python -m ingest.build"],
    }
    (ROOT / "data" / f"area_demo_{cfg.slug}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1] if len(sys.argv) > 1 else "hsr_layout"), indent=1, ensure_ascii=False)[:1500])
