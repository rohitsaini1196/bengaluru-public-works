"""Shared, read-only building blocks of the web app: labels, formatting, the database layer and queries.
Nothing here writes to the database or touches the filesystem beyond opening the configured DB read-only."""
import json
import sqlite3
from datetime import date
from pathlib import Path

from flask import abort, current_app, g

LABELS = {
    "description": "Work", "location": "Where", "ward": "Ward", "estimated_cost": "Estimated cost",
    "provisional_amount": "Budget provision", "tender_published": "Tender published", "tender_calls": "Tender calls",
    "period_days": "Time allowed", "contractor": "Contractor", "contract_value": "Contract value",
    "awarded_date": "Awarded on", "bidders": "Bidders", "work_order_date": "Work order date",
    "payments_gross": "Paid (gross)", "payments_nett": "Paid (nett)", "n_bills": "Bills", "first_payment": "First payment",
    "last_payment": "Last payment", "paid_vs_contract": "Paid vs contract", "status": "Status", "start_date": "Work started",
    "expected_completion": "Expected completion", "final_bill": "Final bill", "completion_date": "Completed on",
    "sanctioned": "On sanctioned works list", "planned_amount": "Planned amount", "plan_status": "Plan status",
    "contractor_identity": "Contractor identity", "imagery": "Satellite imagery",
    "work_order_ref": "Work order", "agreement_ref": "Agreement", "loa_ref": "Letter of acceptance",
    "commencement_date": "Commencement (work order)", "completion_due": "Stipulated completion",
    "bill_types": "Bills recorded", "work_documents": "Work documents", "completed_on_certificate": "Completion certificate date",
    "partial_release": "Partial release", "penalties_deductions": "Fines / withheld / recoveries",
    "bill_to_payment_days": "Bill → payment (median days)", "approval_notes": "Approval remarks",
    "site_photos": "Site photos", "placeholder_photos": "Placeholder photos", "pending_bills": "Unpaid / pending bills", "photo_location": "Photo location (GPS)",
    "quantity_check": "Quantities: planned vs billed", "time_extensions": "Extensions of time", "extended_completion": "Extended completion",
}
STAGE_FIELDS = {
    "planned": ["description", "location", "ward", "sanctioned", "estimated_cost", "planned_amount", "plan_status", "provisional_amount",
                "tender_published", "tender_calls", "period_days"],
    "awarded": ["contractor", "contractor_identity", "contract_value", "bidders", "awarded_date", "work_order_ref", "work_order_date",
                "agreement_ref", "loa_ref", "commencement_date", "completion_due"],
    "work": ["bill_types", "work_documents", "quantity_check"],
    "money": ["payments_gross", "payments_nett", "paid_vs_contract", "n_bills", "first_payment", "last_payment", "partial_release",
              "penalties_deductions", "pending_bills", "bill_to_payment_days", "approval_notes"],
    "outcome": ["status", "start_date", "expected_completion", "final_bill", "completion_date", "completed_on_certificate",
                "time_extensions", "extended_completion"],
    "evidence": ["site_photos", "placeholder_photos", "photo_location", "imagery"],
}
STAGE_TITLES = {"planned": "1 · What was planned", "awarded": "2 · Who got the work (contract)", "work": "3 · Work documented",
                "money": "4 · Money paid", "outcome": "5 · Completion", "evidence": "6 · Physical evidence"}
MONEY = {"estimated_cost", "provisional_amount", "contract_value", "payments_gross", "payments_nett", "planned_amount"}
DATES = {"extended_completion", "commencement_date", "completion_due", "completed_on_certificate", "tender_published", "awarded_date", "work_order_date", "first_payment", "last_payment", "start_date",
         "expected_completion", "completion_date", "final_bill"}
SIGNAL_NAMES = {
    "paid_over_contract": "Paid more than contract", "front_loaded_payment": "Paid early in contract period", "paid_over_estimate": "Paid well over estimate",
    "over_stipulated_period": "Much longer than time allowed", "long_running": "Long-running (3+ years)",
    "past_expected_completion": "Past expected completion, no final bill",
    "no_bills_after_expected_completion": "No bill after expected completion", "stale_running": "Running bill, no final bill",
    "recalled_tender": "Tender re-called", "single_bidder": "Single bidder", "bidder_name_overlap": "Bidders with overlapping names", "award_above_estimate": "Awarded above estimate",
    "deep_discount": "Awarded far below estimate", "payment_burst": "Many bills paid same day",
    "bills_after_final": "Bills after final bill", "large_deduction": "Large deduction on a bill",
    "late_payment": "Paid years after work ended", "contractor_concentration": "Contractor concentration",
    "related_work": "Near-identical work elsewhere", "frequent_co_bidders": "Same bidders meet repeatedly",
    "repeat_single_bid_winner": "Repeat single-bid winner",
    "paid_before_award": "Payment before award", "past_stipulated_completion": "Past stipulated completion",
    "fine_levied": "Fine levied", "partial_release": "Partial release", "slow_payment": "Slow payment",
    "photo_gps_outside_area": "Photo GPS outside area", "photo_reused": "Photo reused across works", "document_reused": "Same document under two job numbers",
    "placeholder_reused": "Placeholder reused across works", "placeholder_photos": "Placeholder photos", "year_end_payment": "Paid at financial-year end",
    "qty_measured_below_plan": "Quantity well below plan", "qty_billed_above_plan": "Quantity above plan",
    "qty_above_plan_no_variation": "Above plan, no variation visible", "qty_billed_item_not_in_plan": "Billed item not in Schedule B",
    "qty_major_item_not_billed": "Major planned item not billed", "qty_unreadable_mb": "Measurement book unreadable",
    "final_bill_without_mb": "Final bill without MB",
}
SORTS = {
    "recent": "last_activity DESC",
    "value": "MAX(COALESCE(payments_gross,0), COALESCE(contract_value,0), COALESCE(estimated_cost,0)) DESC",
    "paid": "COALESCE(payments_gross,0) DESC",
    "depth": "lifecycle DESC, last_activity DESC",
    "signals": "CASE signal_level WHEN 'attention' THEN 3 WHEN 'notice' THEN 2 WHEN 'info' THEN 1 ELSE 0 END DESC, n_signals DESC",
    "oldest": "COALESCE(first_date, tender_published) ASC",
}


class DatabaseMissing(Exception):
    """Raised when the configured database file does not exist (the message never reaches a client)."""


def db():
    """Read-only connection for this request. Public mode also passes immutable=1 (the file never changes while served)."""
    if "db" not in g:
        path = Path(current_app.config["DB_PATH"])
        if not path.exists():
            current_app.logger.error("database file missing: %s", path)
            raise DatabaseMissing()
        flags = "mode=ro&immutable=1" if current_app.config.get("PUBLIC") else "mode=ro"
        g.db = sqlite3.connect(f"file:{path}?{flags}", uri=True)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


# ------------------------------------------------------------------ formatting
def inr(v):
    if v is None or v == "":
        return None
    v = float(v)
    if abs(v) >= 1e7:
        return f"₹{v / 1e7:,.2f} Cr"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:,.2f} L"
    return f"₹{v:,.0f}"


def inr_full(v):
    if v is None or v == "":
        return None
    s = f"{int(round(float(v))):d}"
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail]) if parts else "₹" + tail


def dt(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10]).strftime("%d %b %Y")
    except ValueError:
        return v


def fromjson(v):
    return json.loads(v) if v else None


def fmt_fact(field, value):
    if value is None:
        return None
    if field in MONEY:
        return inr(value)
    if field in DATES:
        return dt(value) or value
    if field == "period_days":
        d = int(float(value))
        return f"{d} days" + (f" (~{round(d / 30)} months)" if d >= 60 else "")
    if field == "paid_vs_contract":
        return f"{float(value):.0%}"
    if field == "tender_calls":
        return "1 (first call)" if str(value) == "1" else f"{value} calls"
    if field == "bidders":
        return f"{value}"
    return value


def status_class(s):
    s = (s or "").lower()
    if "completed" in s:
        return "done"
    if "running" in s or "awarded" in s:
        return "active"
    if "evaluation" in s:
        return "pending"
    return "unknown"


def install(app):
    """Template filters / globals and the per-request DB teardown."""
    app.add_template_filter(inr, "inr")
    app.add_template_filter(inr_full, "inr_full")
    app.add_template_filter(dt, "dt")
    app.add_template_filter(fromjson, "fromjson")
    app.jinja_env.globals.update(status_class=status_class, LABELS=LABELS, STAGE_FIELDS=STAGE_FIELDS,
                                 STAGE_TITLES=STAGE_TITLES, fmt_fact=fmt_fact, SIGNAL_NAMES=SIGNAL_NAMES)
    app.teardown_appcontext(close_db)

    @app.context_processor
    def _site():
        return {"site_name": SITE_NAME, **area_info()}


SITE_NAME = "Bengaluru Public Works Explorer"


def area_info():
    """The area this database covers (meta 'area', written by ingest.publish); Koramangala for older builds."""
    if "area" not in g:
        info = {"area": "Koramangala", "city": "Bengaluru", "data_as_of": None}
        try:
            row = db().execute("SELECT value FROM meta WHERE key = 'area'").fetchone()
            if row:
                a = json.loads(row[0])
                info.update(area=a.get("area_name") or info["area"], city=a.get("city") or info["city"])
            row = db().execute("SELECT value FROM meta WHERE key = 'published_at'").fetchone()
            if row:
                info["data_as_of"] = row[0][:10]
        except Exception:                # no database (error pages): keep the defaults
            pass
        g.area = info
    return g.area


# ------------------------------------------------------------------ queries
FLAGS = {"has_contract": "Contract value", "has_bills": "Bills / payments", "has_completion": "Completion record",
         "has_photos": "Site photos", "has_quantities": "Quantity evidence", "has_signals": "Signals", "has_case": "Investigation case"}


def query_projects(q="", category="", status="", dept="", signal="", depth="", link="", sort="recent", flags=()):
    where, args = [], []
    for f in flags:
        if f in FLAGS:                   # whitelisted column names only
            where.append(f"id IN (SELECT project_id FROM project_flags WHERE {f} = 1)")
    for tok in (q or "").lower().split():
        where.append("search_text LIKE ?")
        args.append(f"%{tok}%")
    for col, val in (("category", category), ("status", status), ("department", dept), ("link_confidence", link)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if signal:
        where.append("id IN (SELECT project_id FROM signals WHERE code = ?)")
        args.append(signal)
    if depth:
        try:
            where.append("lifecycle >= ?")
            args.append(int(depth))
        except ValueError:
            where.pop()
    sql = "SELECT * FROM projects" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY " + SORTS.get(sort, SORTS["recent"])
    return db().execute(sql, args).fetchall()


def facets():
    d = db()
    return {
        "category": d.execute("SELECT category, COUNT(*) FROM projects GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "status": d.execute("SELECT status, COUNT(*) FROM projects GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "dept": d.execute("SELECT department, COUNT(*) FROM projects GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "signal": d.execute("SELECT code, COUNT(DISTINCT project_id) FROM signals GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "link": d.execute("SELECT link_confidence, COUNT(*) FROM projects GROUP BY 1 ORDER BY 2 DESC").fetchall(),
    }


def summarize(rows):
    return {
        "n": len(rows), "paid": sum(r["payments_gross"] or 0 for r in rows),
        "contract": sum(r["contract_value"] or 0 for r in rows),
        "n_paid": sum(1 for r in rows if r["payments_gross"]), "n_contract": sum(1 for r in rows if r["contract_value"]),
        "full": sum(1 for r in rows if r["lifecycle"] == 6), "linked": sum(1 for r in rows if r["n_bills"] and r["tender_numbers"]),
        "flagged": sum(1 for r in rows if r["signal_level"] in ("notice", "attention")),
    }


def stats():
    row = db().execute("SELECT value FROM meta WHERE key='stats'").fetchone()
    return json.loads(row[0]) if row else {}


def load_project(slug):
    d = db()
    p = d.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if not p:
        abort(404)
    facts = {r["field"]: dict(r, evidence=json.loads(r["evidence"] or "[]")) for r in d.execute(
        "SELECT * FROM facts WHERE project_id = ?", (p["id"],))}
    recs = d.execute("""SELECT r.*, s.name AS source_name FROM records r LEFT JOIN sources s ON s.id = r.source_id
                        WHERE project_id = ? ORDER BY record_type DESC, COALESCE(br_date, payment_date, wo_date, start_date)""",
                     (p["id"],)).fetchall()
    links = [dict(r, support=json.loads(r["support"]), conflict=json.loads(r["conflict"]))
             for r in d.execute("SELECT * FROM links WHERE project_id = ? ORDER BY status, score DESC", (p["id"],))]
    sigs = [dict(r, evidence=json.loads(r["evidence"]), related=json.loads(r["related"]))
            for r in d.execute("""SELECT * FROM signals WHERE project_id = ? ORDER BY
                                  CASE severity WHEN 'attention' THEN 0 WHEN 'notice' THEN 1 ELSE 2 END""", (p["id"],))]
    return p, facts, recs, links, sigs


def timeline(facts, recs):
    ev = []
    add = lambda d, label, kind, conf="confirmed", url=None: d and ev.append(
        {"date": str(d)[:10], "label": label, "kind": kind, "conf": conf, "url": url})
    for r in recs:
        if r["record_type"] == "tender":
            x = json.loads(r["extra"] or "{}")
            conf = "confirmed" if r["link"] == "exact" else "inferred"
            add(r["start_date"], f"Tender {r['tender_number']} published" + (f" — estimate {inr(x.get('ecv'))}" if x.get("ecv") else ""),
                "tender", conf, r["source_url"])
            if x.get("awarded_date"):
                add(x["awarded_date"], f"Awarded to {r['contractor'] or 'unknown'}" + (f" for {inr(r['gross'])}" if r["gross"] else ""),
                    "award", conf, x.get("bid_url") or r["source_url"])
    wo = facts.get("work_order_date", {})
    add(wo.get("value"), "Work order issued", "milestone", "confirmed", (wo.get("evidence") or [{}])[0].get("url"))
    ec = facts.get("expected_completion", {})
    add(ec.get("value"), "Expected completion (award/work order + time allowed)", "expected", "inferred")
    for r in recs:
        if r["record_type"] == "bill":
            d = r["payment_date"] or r["br_date"] or r["wo_date"]
            what = f"{r['bill_type'] + ' bill' if r['bill_type'] else 'Bill'} {inr(r['gross']) or ''}"
            what += " paid" if r["payment_date"] else (" registered" if r["br_date"] else " (register entry)")
            add(d, what, "final" if "final" in (r["bill_type"] or "").lower() else "bill", "confirmed", r["source_url"])
    cd = facts.get("completion_date", {})
    add(cd.get("value"), "Work end date on final bill", "milestone", "confirmed")
    ev.sort(key=lambda e: e["date"])
    return ev


def bill_chart(recs):
    """Cumulative gross payments over time as SVG polyline points (0..100 x 0..40)."""
    pts = sorted(((r["payment_date"] or r["br_date"] or r["wo_date"]), r["gross"] or 0)
                 for r in recs if r["record_type"] == "bill" and (r["payment_date"] or r["br_date"] or r["wo_date"]))
    if len(pts) < 2:
        return None
    d0, d1 = date.fromisoformat(pts[0][0][:10]), date.fromisoformat(pts[-1][0][:10])
    span = max(1, (d1 - d0).days)
    total = sum(a for _, a in pts) or 1
    run, out = 0, []
    for d, a in pts:
        run += a
        x = 100 * (date.fromisoformat(d[:10]) - d0).days / span
        out.append(f"{x:.1f},{40 - 38 * run / total:.1f}")
    return {"points": " ".join(out), "start": pts[0][0], "end": pts[-1][0], "total": total}


