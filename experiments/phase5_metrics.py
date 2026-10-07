"""Phase 5 metrics: discovery coverage vs Phase 4, denominator (completeness) checks, quantity counts.

Usage: python -m experiments.phase5_metrics      -> prints and writes data/phase5_metrics.json
"""
import json
import sqlite3
from collections import Counter
from pathlib import Path

from ingest import bills as B, discover_bbmp as D, quantities as Q

ROOT = Path(__file__).resolve().parent.parent


def main():
    base = json.loads((ROOT / "data" / "phase4_baseline.json").read_text())
    con = sqlite3.connect(ROOT / "data" / "koramangala.db")
    con.row_factory = sqlite3.Row
    m = {}

    # ---- discovery table
    disc = [dict(r) for r in con.execute("SELECT * FROM discovery")]
    jobs = {r["job_number"] for r in disc}
    known_jobs = {r["job_number"] for r in disc if r["job_previously_known"]}
    m["discovery"] = {
        "bills": len(disc), "jobs": len(jobs),
        "bills_previously_known": sum(r["bill_previously_known"] for r in disc),
        "new_bills": sum(1 for r in disc if not r["bill_previously_known"]),
        "new_paid_bills": sum(1 for r in disc if not r["bill_previously_known"] and r["rtgs_no"]),
        "jobs_previously_known": len(known_jobs), "new_jobs": len(jobs - known_jobs),
        "new_bills_gross": round(sum(r["gross"] or 0 for r in disc if not r["bill_previously_known"])),
        "scope_basis": Counter(r["scope_basis"] for r in disc),
        "bills_in_a_project": sum(1 for r in disc if r["in_project"]),
        "jobs_in_a_project": len({r["job_number"] for r in disc if r["in_project"]}),
    }
    # ---- projects
    rows = [dict(r) for r in con.execute("SELECT id, slug, job_numbers, payments_gross, tender_numbers FROM projects")]
    p4_jobs = known_jobs | set(base["ifms_jobs"])
    new_proj = [r for r in rows if r["job_numbers"] and not (set(r["job_numbers"].split(",")) & p4_jobs)]
    nb = con.execute("SELECT COUNT(DISTINCT project_id) FROM records WHERE record_type='bill'").fetchone()[0]
    ifms_bills = len(list((ROOT / "data" / "raw" / "bbmp_direct" / "bills").glob("*.json")))
    ifms_jobs = len(list((ROOT / "data" / "raw" / "bbmp_direct" / "jobs").glob("*.json")))
    paid_bills = con.execute("SELECT COUNT(*) FROM records WHERE record_type='bill' AND payment_date IS NOT NULL").fetchone()[0]
    m["projects"] = {"phase4": base["projects"], "phase5": len(rows), "phase4_with_bills": base["projects_with_bills"],
                     "phase5_with_bills": nb, "new_projects": len(new_proj),
                     "new_projects_paid": round(sum(r["payments_gross"] or 0 for r in new_proj)),
                     "ifms_bills_phase4": len(base["ifms_bills"]), "ifms_bills_phase5": ifms_bills,
                     "ifms_jobs_phase4": len(base["ifms_jobs"]), "ifms_jobs_phase5": ifms_jobs,
                     "paid_bill_records_phase5": paid_bills,
                     "expansion_projects_pct": round((len(rows) / base["projects"] - 1) * 100, 1),
                     "expansion_projects_with_bills_pct": round((nb / base["projects_with_bills"] - 1) * 100, 1)}

    # ---- denominator: does the grid contain what independent sources say was paid?
    grid_all = D.load(with_scope=False)
    grid_ids = {r["bill_id"] for r in grid_all}
    grid_jobs = {r["job_number"] for r in grid_all}
    prior, _, _ = B.load_bills(B.load_sources())
    prior = [b for b in prior if b["source"].get("kind") != "ifms_grid"]
    oc = [b for b in prior if (b.get("extra") or {}).get("ifms_id") and (b.get("payment_date") or "") >= "2015-05-01"
          and b.get("payment_ref")]
    oc_in = [b for b in oc if str(b["extra"]["ifms_id"]) in grid_ids]
    pj = {b["job_number"] for b in prior if (b.get("payment_date") or "") >= "2015-05-01"}
    direct = {p.stem: json.loads(p.read_text()) for p in (ROOT / "data" / "raw" / "bbmp_direct" / "bills").glob("*.json")}
    paid_direct = [w for w, r in direct.items() if isinstance(r.get("details"), dict) and r["details"].get("rtgs")]
    first = min((r["rtgs_date"] for r in grid_all if r["rtgs_date"]), default=None)
    m["denominator"] = {
        "grid_rows_all_wards": len(grid_all), "grid_bills_all_wards": len(grid_ids), "grid_first_payment": first,
        "opencity_paid_bills_since_may_2015": len(oc), "opencity_paid_bills_in_grid": len(oc_in),
        "known_jobs_paid_since_may_2015": len(pj), "known_jobs_in_grid": len(pj & grid_jobs),
        "ifms_direct_paid_bills": len(paid_direct), "ifms_direct_paid_in_grid": len(set(paid_direct) & grid_ids),
        "ward_conflict_rows_rejected": sum(1 for r in D.load() if r["scope_basis"] == "ward_conflict"),
    }

    # ---- quantities
    q = Q.load() or {"projects": []}
    P = q["projects"]
    docs = [d for p in P for d in p["documents"]]
    got = [d for d in docs if d["status"] != "not downloaded"]
    items = [w for p in P for w in p["work_items"]]
    cmp_ = [w for w in items if w["compared_quantity"] is not None]
    m["quantities"] = {
        "projects_selected": len(P), "documents_listed": len(docs), "documents_downloaded": len(got),
        "documents_readable": sum(1 for d in got if d["status"] == "readable"),
        "by_type": {t: {"downloaded": sum(1 for d in got if d["type"] == t),
                        "readable": sum(1 for d in got if d["type"] == t and d["status"] == "readable")}
                    for t in Q.PLAN_TYPES + Q.BILL_TYPES},
        "projects_with_plan_items": sum(1 for p in P if p["work_items"]),
        "projects_with_billed_items": sum(1 for p in P if any(b["abstract_value"] for b in p["bills"])),
        "projects_with_comparison": sum(1 for p in P if any(w["compared_quantity"] is not None for w in p["work_items"])),
        "projects_all_paid_bills_read": sum(1 for p in P if p["all_paid_bills_read"]),
        "plan_items": len(items), "compared_items": len(cmp_),
        "compared_by_basis": Counter(w["compared_basis"] for w in cmp_),
        "items_confidence": Counter(min((x["confidence"] for x in w["provenance"]),
                                        key=lambda c: {"high": 2, "medium": 1}.get(c, 0)) for w in items),
        "measurement_totals": sum(len(p["measurement_totals"]) for p in P),
        "measurement_totals_self_consistent": sum(1 for p in P for t in p["measurement_totals"] if t["consistent"]),
        "billed_without_plan_item": sum(len(p["billed_without_plan_item"]) for p in P),
        "signals": Counter(s["code"] for p in P for s in p["signals"]),
        "final_bill_without_mb_jobs": sum(1 for j, v in q.get("mb_presence", {}).items() if v["final_bills"] and not v["mb_real"]),
    }
    m["signals_db"] = dict(Counter(r[0] for r in con.execute("SELECT code FROM signals")))
    m["signals_projects_db"] = dict(Counter(r[0] for r in con.execute("SELECT DISTINCT code, project_id FROM signals")))
    (ROOT / "data" / "phase5_metrics.json").write_text(json.dumps(m, indent=1, default=str))
    print(json.dumps(m, indent=1, default=str))


if __name__ == "__main__":
    main()
