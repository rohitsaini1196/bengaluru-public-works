"""What changed between two public builds — the monthly "what's new" for monitoring.

    python -m ingest.changes OLD.db NEW.db              # print the report
    python -m ingest.changes OLD.db NEW.db --log        # also prepend it to CHANGELOG_DATA.md

Compares the sanitised public databases (data/public/koramangala_public.db before and after a refresh):
projects added or dropped, new bills and bills newly paid, status changes, final bills, signals that appeared or
disappeared, and investigation cases that opened, closed or changed status. Facts only, no judgement.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "CHANGELOG_DATA.md"


def _con(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def snapshot(path):
    c = _con(path)
    projects = {r["slug"]: dict(r) for r in c.execute(
        "SELECT slug, title, status, payments_gross, n_bills, job_numbers FROM projects")}
    slug_of = {r["id"]: r["slug"] for r in c.execute("SELECT id, slug FROM projects")}
    bills = {}
    for r in c.execute("""SELECT project_id, job_number, bill_type, gross, br_no, br_date, payment_date, contractor
                          FROM records WHERE record_type = 'bill'"""):
        key = (r["job_number"], r["br_no"] or "", r["br_date"] or "", round(r["gross"] or 0))
        bills[key] = {**dict(r), "slug": slug_of.get(r["project_id"])}
    signals = {(slug_of.get(r["project_id"]), r["code"]) for r in c.execute("SELECT project_id, code FROM signals")}
    finals = {slug_of.get(r[0]): r[1] for r in c.execute("SELECT project_id, value FROM facts WHERE field = 'final_bill' AND value IS NOT NULL")}
    cases = {}
    if c.execute("SELECT name FROM sqlite_master WHERE name = 'cases'").fetchone():
        cases = {r["case_id"]: dict(r) for r in c.execute("SELECT case_id, job_number, retained, status, primary_issue FROM cases")}
    meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
    c.close()
    return {"projects": projects, "bills": bills, "signals": signals, "finals": finals, "cases": cases, "meta": meta}


def inr(v):
    v = float(v or 0)
    return f"₹{v / 1e7:,.2f} Cr" if abs(v) >= 1e7 else f"₹{v / 1e5:,.1f} L" if abs(v) >= 1e5 else f"₹{v:,.0f}"


def diff(old, new):
    o, n = old, new
    added = sorted(set(n["projects"]) - set(o["projects"]))
    dropped = sorted(set(o["projects"]) - set(n["projects"]))
    new_bills = [b for k, b in n["bills"].items() if k not in o["bills"]]
    newly_paid = [b for k, b in n["bills"].items() if k in o["bills"] and b["payment_date"] and not o["bills"][k]["payment_date"]]
    status = [(s, o["projects"][s]["status"], p["status"]) for s, p in n["projects"].items()
              if s in o["projects"] and (o["projects"][s]["status"] or "") != (p["status"] or "")]
    finals = sorted(s for s in n["finals"] if s and s not in o["finals"])
    sig_new = sorted(n["signals"] - o["signals"], key=lambda x: (x[0] or "", x[1]))
    sig_gone = sorted(o["signals"] - n["signals"], key=lambda x: (x[0] or "", x[1]))
    case_changes = []
    for cid in sorted(set(o["cases"]) | set(n["cases"])):
        a, b = o["cases"].get(cid), n["cases"].get(cid)
        if not a:
            case_changes.append((cid, "new candidate", b["status"]))
        elif not b:
            case_changes.append((cid, "removed", a["status"]))
        elif (a["retained"], a["status"]) != (b["retained"], b["status"]):
            case_changes.append((cid, f"{'open' if a['retained'] else 'set aside'} → {'open' if b['retained'] else 'set aside'}",
                                 f"{a['status']} → {b['status']}"))
    paid_old = sum(p["payments_gross"] or 0 for p in o["projects"].values())
    paid_new = sum(p["payments_gross"] or 0 for p in n["projects"].values())
    return {"added": added, "dropped": dropped, "new_bills": new_bills, "newly_paid": newly_paid, "status": status,
            "finals": finals, "sig_new": sig_new, "sig_gone": sig_gone, "cases": case_changes,
            "paid_old": paid_old, "paid_new": paid_new, "n_old": len(o["projects"]), "n_new": len(n["projects"])}


def render(d, new, when=None):
    when = when or date.today().isoformat()
    title = lambda s: (new["projects"].get(s) or {}).get("title", s)[:90]
    L = [f"## Data refresh {when}", "",
         f"- Projects: {d['n_old']} → {d['n_new']}; paid (gross): {inr(d['paid_old'])} → {inr(d['paid_new'])} "
         f"({'+' if d['paid_new'] >= d['paid_old'] else ''}{inr(d['paid_new'] - d['paid_old'])})",
         f"- New bills: {len(d['new_bills'])} ({inr(sum(b['gross'] or 0 for b in d['new_bills']))}); bills newly paid: {len(d['newly_paid'])}",
         f"- Signals: +{len(d['sig_new'])} / −{len(d['sig_gone'])}; case changes: {len(d['cases'])}", ""]
    if not any((d["added"], d["dropped"], d["new_bills"], d["newly_paid"], d["status"], d["finals"], d["sig_new"], d["sig_gone"], d["cases"])):
        return "\n".join(L + ["No changes.", ""])
    if d["cases"]:
        L += ["**Investigation cases**", ""] + [f"- {cid}: {what} ({st})" for cid, what, st in d["cases"]] + [""]
    if d["finals"]:
        L += ["**Final bills recorded**", ""] + [f"- {s}: {title(s)}" for s in d["finals"]] + [""]
    if d["added"]:
        L += ["**New projects**", ""] + [f"- {s}: {title(s)}" for s in d["added"][:40]] + ([f"- … and {len(d['added']) - 40} more"] if len(d["added"]) > 40 else []) + [""]
    if d["dropped"]:
        L += ["**Projects no longer in the build**", ""] + [f"- {s}" for s in d["dropped"]] + [""]
    big = sorted(d["new_bills"], key=lambda b: -(b["gross"] or 0))[:15]
    if big:
        L += ["**Largest new bills**", ""] + [f"- {b['slug']}: {b['bill_type'] or 'bill'} {inr(b['gross'])}"
                                              + (f", paid {b['payment_date']}" if b["payment_date"] else ", not yet paid")
                                              + (f" to {b['contractor']}" if b["contractor"] else "") for b in big] + [""]
    if d["status"]:
        L += ["**Status changes**", ""] + [f"- {s}: {a or '—'} → {b or '—'}" for s, a, b in d["status"][:30]] + [""]
    if d["sig_new"] or d["sig_gone"]:
        L += ["**Signals**", ""] + [f"- + {s}: {c}" for s, c in d["sig_new"][:30]] + [f"- − {s}: {c}" for s, c in d["sig_gone"][:30]] + [""]
    return "\n".join(L)


def main(argv=None):
    a = argv or sys.argv[1:]
    old, new = Path(a[0]), Path(a[1])
    o, n = snapshot(old), snapshot(new)
    report = render(diff(o, n), n)
    print(report)
    if "--log" in a:
        head = "# Data changelog\n\nWhat changed in each data refresh (generated by `python -m ingest.changes`).\n\n"
        body = LOG.read_text().split("\n\n", 2)[2] if LOG.exists() and LOG.read_text().startswith("# Data changelog") else ""
        LOG.write_text(head + report + "\n" + body)


if __name__ == "__main__":
    main()
