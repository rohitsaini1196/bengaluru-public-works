"""Phase 6: investigation cases.

Turns signals into small, checkable cases. Each case references existing data — project facts and
signals in data/koramangala.db, work items and provenance in data/quantities.json, EoT orders in
data/eot.json, and documents read by ingest.case_evidence — and adds only what was found while trying
to *resolve* the signal (work slips, final bill forms, header totals).

Every sentence in a rendered case is generated from a structured field of the case, and every field
carries its evidence (source type, document, page, URL, method, confidence).

Outputs: data/investigation_cases.json, table `cases` (via build), cases/<case_id>.md, CASE_INDEX.md,
internal/RTI_QUESTIONS.md (working notes, not published).
"""
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from ingest import case_evidence as CE, eot as EOT, quantities as Q

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "koramangala.db"
OUT = ROOT / "data" / "investigation_cases.json"
CASES_DIR = ROOT / "cases"
AS_OF = date(2026, 10, 7)
IFMS_API = "https://accounts.bbmp.gov.in/vssWB/vss00CvStatusData.php"

# ---------------------------------------------------------------- scoring (transparent, additive)
#   case_score = evidence_strength + financial_materiality + unresolved + documentation_gap
#                + timeline_deviation + independent_sources            (0–100, prioritisation only)
WEIGHTS = {"evidence_strength": 25, "financial_materiality": 25, "unresolved": 20, "documentation_gap": 10,
           "timeline_deviation": 10, "independent_sources": 10}
STATUS_UNRESOLVED = {"explained": 0, "artefact": 0, "partially_explained": 12, "insufficient_evidence": 8,
                     "unexplained_in_public_record": 20}
DEAD = ("explained", "artefact")            # an issue in either state is not live
CONF_SCORE = {"high": 25, "medium": 15, "low": 5}


def materiality(rupees):
    """0 at ≤ ₹1 lakh, 25 at ≥ ₹10 crore, logarithmic in between."""
    if not rupees or rupees <= 1e5:
        return 0
    return round(min(25, 25 * math.log10(rupees / 1e5) / 3), 1)


def score(issue_list, n_sources):
    """Components of a case's priority score. The case takes its strongest issue."""
    best = max(issue_list, key=lambda i: STATUS_UNRESOLVED[i["status"]] + CONF_SCORE[i["confidence"]] + materiality(i.get("value_at_issue")))
    comp = {
        "evidence_strength": CONF_SCORE[best["confidence"]],
        "financial_materiality": materiality(best.get("value_at_issue")),
        "unresolved": STATUS_UNRESOLVED[best["status"]],
        "documentation_gap": min(10, sum(i.get("documentation_gap", 0) for i in issue_list)),
        "timeline_deviation": min(10, max((i.get("timeline_days", 0) or 0) for i in issue_list) / 365 * 5),
        "independent_sources": min(10, 2 * n_sources),
    }
    comp = {k: round(v, 1) for k, v in comp.items()}
    return comp, round(sum(comp.values()), 1)


def classify_confidence(sources):
    """high: direct API data or a readable official document, cross-validated by an independent one.
    medium: official scanned document read through validated OCR, or an incomplete supporting record.
    low: partial / inferred matching."""
    kinds = {s["kind"] for s in sources}
    validated = [s for s in sources if s.get("cross_validated")]
    if ("api" in kinds and len(kinds) >= 2) or len({s["kind"] for s in validated}) >= 2:
        return "high"
    if validated or "api" in kinds:
        return "medium"
    return "low"


# ---------------------------------------------------------------- loading

def _facts(con, pid):
    out = {}
    for f, v, c, ev, ex in con.execute("SELECT field, value, confidence, evidence, explanation FROM facts WHERE project_id=?", (pid,)):
        try:
            v = json.loads(v)
        except (TypeError, ValueError):
            pass
        out[f] = {"value": v, "confidence": c, "evidence": json.loads(ev or "[]"), "explanation": ex}
    return out


def load_project(con, job):
    con.row_factory = sqlite3.Row
    p = con.execute("SELECT * FROM projects WHERE ',' || job_numbers || ',' LIKE ?", (f"%,{job},%",)).fetchone()
    if not p:
        return None
    p = dict(p)
    p["facts"] = _facts(con, p["id"])
    p["signals"] = [dict(r) for r in con.execute("SELECT * FROM signals WHERE project_id=?", (p["id"],))]
    p["payees"] = [{"contractor": r[0], "first": r[1], "last": r[2], "bills": r[3], "gross": r[4]} for r in con.execute(
        "SELECT contractor, MIN(COALESCE(br_date, payment_date)), MAX(COALESCE(br_date, payment_date)), COUNT(*), SUM(gross) "
        "FROM records WHERE project_id=? AND record_type='bill' AND contractor IS NOT NULL GROUP BY contractor ORDER BY 2", (p["id"],))]
    for s in p["signals"]:
        s["evidence"], s["related"] = json.loads(s["evidence"] or "[]"), json.loads(s["related"] or "[]")
    return p


def ev(kind, label, url, page=None, raw=None, method=None, confidence="high", cross_validated=False, field=None):
    """One evidence reference. kind: api (IFMS API), document (official attachment), export (OpenCity),
    derived (computed from other evidence)."""
    return {"kind": kind, "label": label, "url": url, "page": page, "raw": (raw or "")[:240] or None, "method": method,
            "confidence": confidence, "cross_validated": cross_validated, "field": field}


def fact_ev(p, field):
    f = p["facts"].get(field) or {}
    out = []
    for e in f.get("evidence", [])[:3]:
        kind = "api" if e.get("source_id") in ("IFMS", "IFMS-GRID") else "document" if str(e.get("source_id", "")).startswith("IFMS") else "export"
        out.append(ev(kind, e.get("label"), e.get("url"), raw=e.get("note"), confidence=f.get("confidence"), field=field))
    return out


def prov_ev(x):
    return ev("document", f"{x['attachment_type']} {x['document_id']}", x["attachment_url"], x.get("page"), x.get("raw"),
              x.get("method"), x.get("confidence"), x.get("cross_validated"))


# ---------------------------------------------------------------- documents read for a job

def read_documents(listing, job):
    """Variation documents (work slip / comparative statement rows + header totals) and final-bill rows."""
    docs, finals = CE.documents(listing, job)
    ws_rows, header, var_docs, final_rows, other = [], {}, [], [], []
    for d in docs:
        path = CE.find(d["document_id"])
        entry = {**d, "status": "not downloaded"}
        if path:
            pages = Q.page_texts(path)
            entry["status"] = "read"
            entry["pages"] = len(pages)
            if d["kind"] == "variation":
                rows, hdr = CE.parse_work_slip(pages)
                entry["rows"] = len(rows)
                for r in rows:
                    ws_rows.append({**r, "document_id": d["document_id"], "url": d["url"]})
                for k, v in hdr.items():
                    header.setdefault(k, {**v, "document_id": d["document_id"], "url": d["url"]})
            elif d["kind"] == "final_bill":
                items, stats = Q.parse_items(pages, d["type"])
                entry["rows"] = len(items)
                for it in items:
                    final_rows.append({**it, "document_id": d["document_id"], "url": d["url"], "work_bills": d["work_bills"]})
        (var_docs if d["kind"] == "variation" else other).append(entry)
    return {"variation_documents": var_docs, "work_slip_rows": dedupe_rows(ws_rows), "work_slip_header": header,
            "final_bill_rows": dedupe_rows(final_rows, key=("quantity", "rate", "to_date_quantity")),
            "other_documents": other, "final_wbids": sorted(finals)}


def dedupe_rows(rows, key=("tender_qty", "rate", "executed_qty")):
    """The same statement / bill form is often uploaded on several bills: keep one row per value tuple."""
    seen, out = set(), []
    for r in rows:
        k = tuple(round(r.get(x) or 0, 2) for x in key)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def amount_rows(plan_qty, rate, docs):
    """Fallback when a statement row's quantities are illegible: on one line, a total amount T and an excess
    amount E that are exact multiples of the contract rate with T/rate - E/rate == the Schedule B quantity.
    Read as executed = T/rate, confidence medium."""
    out = []
    for d in docs.get("variation_documents", []):
        path = CE.find(d["document_id"]) if d.get("status") == "read" else None
        if not path:
            continue
        for pg in Q.page_texts(path):
            for ln in (pg.get("text") or "").splitlines():
                vals = [v for v in (Q.num(t) for t in Q.NUM_TOKEN.findall(ln)) if v and v > rate]
                mult = [v / rate for v in vals if abs(v / rate - round(v / rate)) < 1e-6]
                for t_ in mult:
                    for e_ in mult:
                        if t_ > e_ > 0 and abs(t_ - e_ - plan_qty) < 1e-6 and t_ > plan_qty:
                            out.append({"tender_qty": plan_qty, "rate": rate, "executed_qty": t_, "confidence": "medium",
                                        "checks": [f"total amount = {t_:g} x rate", f"excess amount = {e_:g} x rate",
                                                   "total - excess = Schedule B quantity"],
                                        "document_id": d["document_id"], "url": d["url"], "page": pg.get("page"),
                                        "raw": ln.strip()[:240], "method": pg.get("method"), "unit": None})
    return out[:1]


def match_rate(item_rate, rows, key="rate", unit=None):
    """Rows whose rate equals the item's contract rate (±0.5 %); unit must be compatible when known."""
    return [r for r in rows if r.get(key) and item_rate and abs(r[key] - item_rate) <= 0.005 * item_rate
            and Q._units_ok(unit, r.get("unit"))]


# ---------------------------------------------------------------- issues

def reconcile(plan_qty, billed_qty, ws):
    """Variation reconciliation for one item billed above plan, from matching work-slip rows.
    explained: the approved statement's total executed quantity covers the billed quantity;
    partially_explained: it raises the quantity above plan, but not to the billed level;
    contradicted: a fully cross-checked row records no more than the plan (only high-confidence rows may contradict);
    None: no usable row."""
    if not ws:
        return None
    best = max(ws, key=lambda r: r["executed_qty"])
    exe = best["executed_qty"]
    if exe >= 0.98 * billed_qty:
        return "explained", exe
    if exe > 1.02 * plan_qty:
        return "partially_explained", exe
    if best["confidence"] == "high":
        return "contradicted", exe
    return None


def issue_quantity_above_plan(p, qres, docs):
    out = []
    for w in qres["work_items"] if qres else []:
        got, est = w.get("compared_quantity"), w.get("estimated_quantity")
        if got is None or not est or got <= 1.25 * est or (w.get("estimated_amount") or 0) < Q.MIN_ITEM_AMOUNT:
            continue
        if not w["bills"] or not all(x["match"] in ("code", "rate") for x in w["bills"]):
            continue
        rate = w["estimated_rate"]
        ws = match_rate(rate, docs["work_slip_rows"], unit=w.get("unit")) or amount_rows(est, rate, docs)
        rec = reconcile(est, got, ws)
        var_read = [d for d in docs["variation_documents"] if d["status"] == "read"]
        if rec and rec[0] == "explained":
            status = "explained"
        elif rec and rec[0] == "partially_explained":
            status = "partially_explained"
        elif rec and rec[0] == "contradicted":
            status = "unexplained_in_public_record"
        elif var_read and any(d.get("rows") for d in var_read):
            status = "insufficient_evidence"       # a statement was read but this item's row was not found
        elif var_read:
            status = "insufficient_evidence"       # variation document present but unreadable
        else:
            status = "unexplained_in_public_record"
        sources = [{"kind": "document", "cross_validated": True}] + [{"kind": "bill_form", "cross_validated": True}]
        evid = [prov_ev(x) for x in w["provenance"][:4]]
        evid += [ev("document", f"Work slip / comparative statement {r['document_id']}", r["url"], r["page"], r["raw"], r["method"],
                    r["confidence"], len(r["checks"]) >= 2) for r in ws[:2]]
        evid += [ev("document", f"Variation document {d['document_id']} ({d['status']}, {d.get('rows', 0)} rows read)", d["url"],
                    confidence="medium") for d in docs["variation_documents"][:3]]
        out.append({
            "type": "quantity_above_plan", "status": status,
            # a cumulative column (bill form up to date) is one checked figure; a sum over the running-bill abstracts we
            # happened to read can double count or miss bills, so it is low confidence and never a case on its own
            "confidence": "high" if w["compared_basis"] == "bill form total up to date" else "low",
            "item": w["description"][-120:], "unit": w.get("unit"), "plan_qty": est, "billed_qty": got, "rate": rate,
            "billed_basis": w["compared_basis"], "from_final_bill": w.get("from_final_bill"),
            "work_slip_executed_qty": rec[1] if rec else None,
            "value_at_issue": round((got - est) * rate, 0),
            "variation_documents": [d["document_id"] for d in docs["variation_documents"]],
            "documentation_gap": 5 if not docs["variation_documents"] else 0,
            "evidence": evid, "n_source_kinds": 2 + (1 if ws else 0),
        })
    return out


def final_cumulative(plan_qty, rate, docs):
    """Final-bill evidence for one item: the up-to-date column, or Previous (= tendered qty) + Present."""
    for r in match_rate(rate, docs["final_bill_rows"]):
        toks = [Q.num(t) for t in Q.NUM_TOKEN.findall(r["raw"])]
        if r.get("to_date_quantity"):
            fq, how = r["to_date_quantity"], "final bill: total up to date"
        elif any(t is not None and abs(t - plan_qty) <= 0.011 for t in toks) and r["quantity"] and abs(r["quantity"] - plan_qty) > 0.011:
            fq, how = round(plan_qty + r["quantity"], 3), "final bill: previous (= tendered) + present"
        else:
            continue
        return {"executed_qty": fq, "confidence": "high" if r.get("check") == "arithmetic" else "medium", "checks": [how, r.get("check")],
                "document_id": r["document_id"], "url": r["url"], "page": r["page"], "raw": r["raw"], "method": r["method"]}
    return None


def issue_identical(p, qres, docs):
    same = [w for w in (qres["work_items"] if qres else []) if w.get("compared_quantity") is not None and w.get("estimated_quantity")
            and w["bills"] and all(x["match"] in ("code", "rate") for x in w["bills"])
            and abs(w["compared_quantity"] - w["estimated_quantity"]) <= Q.IDENTICAL * w["estimated_quantity"] + 0.011]
    if len(same) < Q.MIN_IDENTICAL:
        return []
    items = []
    for w in same:
        ws = match_rate(w["estimated_rate"], docs["work_slip_rows"], unit=w.get("unit"))
        if not ws and not w.get("from_final_bill"):
            fin = final_cumulative(w["estimated_quantity"], w["estimated_rate"], docs)
            if fin:
                ws = [fin]
        items.append({"item": w["description"][-100:], "unit": w.get("unit"), "qty": w["estimated_quantity"], "rate": w["estimated_rate"],
                      "amount": round(w["estimated_quantity"] * w["estimated_rate"], 2), "from_final_bill": w.get("from_final_bill"),
                      "work_slip_executed_qty": ws[0]["executed_qty"] if ws else None,
                      "evidence": [prov_ev(x) for x in w["provenance"][:2]] +
                                  [ev("document", f"Comparative statement {r['document_id']}", r["url"], r["page"], r["raw"], r["method"],
                                      r["confidence"], len(r["checks"]) >= 2) for r in ws[:1]]})
    ws_agree = [i for i in items if i["work_slip_executed_qty"] is not None
                and abs(i["work_slip_executed_qty"] - i["qty"]) <= Q.IDENTICAL * i["qty"] + 0.011]
    ws_disagree = [i for i in items if i["work_slip_executed_qty"] is not None and i not in ws_agree]
    confirmed = [i for i in items if i["from_final_bill"] or i in ws_agree]
    if ws_disagree and not all(i["from_final_bill"] for i in ws_disagree):
        # the comparative statement (final executed quantities) differs from a running bill's snapshot:
        # the item was not finally executed at the tendered quantity
        items = [i for i in items if i not in ws_disagree]
        if len(items) < Q.MIN_IDENTICAL:
            return [{"type": "quantities_identical_to_plan", "status": "artefact", "confidence": "high", "items": items + ws_disagree,
                     "n_identical": len(items), "n_other_compared": 0, "work_slip_agrees": len(ws_agree), "work_slip_differs": len(ws_disagree),
                     "value_at_issue": 0,
                     "artefact_reason": "the 'identical' quantities came from a running bill's up-to-date column; the comparative statement "
                                        "records different final executed quantities (" + "; ".join(
                                            f"{q(i['qty'])} tendered → {q(i['work_slip_executed_qty'])} executed" for i in ws_disagree) +
                                        "), leaving fewer than three identical items",
                     "evidence": [e for i in ws_disagree for e in i["evidence"]][:6], "n_source_kinds": 2}]
    others = [w for w in qres["work_items"] if w.get("compared_quantity") is not None and w not in same
              and all(x["match"] in ("code", "rate") for x in w["bills"])]
    confirmed = [i for i in items if i["from_final_bill"] or i in ws_agree]
    final_bills = sorted({x["wbid"] for w in same for x in w["bills"] if w.get("from_final_bill")})
    fb = [m for m in (bill_meta(w) for w in final_bills) if m]
    return [{
        "final_bill_details": fb,
        "type": "quantities_identical_to_plan",
        # an identical quantity is not an anomaly by itself: it needs the MB to settle. Only identities confirmed by a
        # final bill or the comparative statement make a case; running-bill snapshots alone are low confidence
        "status": "insufficient_evidence", "confidence": "high" if len(confirmed) >= Q.MIN_IDENTICAL else "low",
        "low_reason": None if len(confirmed) >= Q.MIN_IDENTICAL else
                      f"only {len(confirmed)} of the {len(items)} identical items are confirmed by a final bill or comparative statement; "
                      "the rest are running-bill snapshots, which Phase 6 showed can change by the final bill",
        "n_confirmed": len(confirmed), "items": items,
        "n_identical": len(items), "n_other_compared": len(others),
        "work_slip_agrees": len(ws_agree), "work_slip_differs": len(ws_disagree),
        "value_at_issue": round(sum(i["amount"] for i in items), 0), "documentation_gap": 3,
        "evidence": [e for i in items[:4] for e in i["evidence"]][:8] + [ev("api", f"IFMS work bill {b['wbid']} ({b['bill_type']}, gross {inr(b['gross'])})", b["url"], confidence="high") for b in fb],
        "n_source_kinds": 2 + (1 if ws_agree else 0),
    }]


def bill_meta(wbid):
    """IFMS bill details for one work bill: type, gross, registration date, payment, approval level."""
    path = ROOT / "data" / "raw" / "bbmp_direct" / "bills" / f"{wbid}.json"
    if not path.exists():
        return None
    d = json.loads(path.read_text()).get("details") or {}
    if not isinstance(d, dict):
        return None
    return {"wbid": str(wbid), "bill_type": d.get("billtype"), "gross": Q.num(d.get("gross") or ""), "registered": d.get("sbrdate"),
            "paid": bool((d.get("rtgs") or "").strip()), "level": d.get("currentlevelname"),
            "url": f"{IFMS_API}?pAction=LoadDetails&pWorkBillID={wbid}"}


def bill_level_files(wbid):
    rec = json.loads((ROOT / "data" / "raw" / "bbmp_direct" / "bills" / f"{wbid}.json").read_text())
    return bool(rec.get("bill_files")) if isinstance(rec.get("bill_files"), list) else False


MB_LIKE = re.compile(r"\bm\.?\s*b\b|measurement|mb\s*book|m\s*book", re.I)


def issue_final_bill_without_mb(p, job, qty, listing):
    mp = (qty or {}).get("mb_presence", {}).get(job)
    if not mp or not mp["final_bills"] or mp["mb_real"]:
        return []
    # resolution attempt: an MB filed under another attachment type ("Others", "Additional Documents")
    elsewhere = sorted({f["rFileName"] for w, (j, d, fs) in listing.items() if j == job for f in fs
                        if MB_LIKE.search(f["rFileName"]) and (f.get("rFileType") or "").strip() not in Q.BILL_TYPES
                        and not Q._is_placeholder(f["rFileName"])})
    paid = p["facts"].get("payments_gross", {}).get("value")
    if not any(bill_level_files(w) for w, (j, d, fs) in listing.items() if j == job):
        # IFMS returns no bill-level attachments at all for bills from mid-2024 (work-bill ids ≳ 690000) without a
        # login; the absence of an MB there says nothing about whether one exists
        return [{"type": "final_bill_without_mb", "status": "artefact", "confidence": "low", "final_bills": mp["final_bills"],
                 "mb_placeholders": mp["mb_placeholders"], "mb_filed_elsewhere": [], "value_at_issue": paid,
                 "artefact_reason": "IFMS exposes no bill-level attachments (of any type) for this job's bills to public requests, "
                                    "so a missing MB cannot be inferred",
                 "evidence": [ev("api", f"IFMS attachment listing for final bill {w} (empty)",
                                 f"{IFMS_API}?pAction=LoadFilesDetails&pMainID={w}&pCheck=1", confidence="high") for w in mp["final_bills"][:2]],
                 "n_source_kinds": 1}]
    api = [ev("api", f"IFMS attachment listing for final bill {w}", f"{IFMS_API}?pAction=LoadFilesDetails&pMainID={w}&pCheck=1",
              confidence="high", field="mb_presence") for w in mp["final_bills"][:2]]
    return [{
        "type": "final_bill_without_mb",
        "status": "explained" if elsewhere else "unexplained_in_public_record",
        "confidence": "high", "final_bills": mp["final_bills"], "mb_placeholders": mp["mb_placeholders"],
        "mb_filed_elsewhere": elsewhere, "value_at_issue": paid, "documentation_gap": 0 if elsewhere else 10,
        "evidence": api + fact_ev(p, "payments_gross")[:1], "n_source_kinds": 1,
    }]


def issue_timeline(p):
    F = p["facts"]
    due = (F.get("completion_due") or {}).get("value")
    if not due or (F.get("final_bill") or {}).get("value"):
        return []
    ext = (F.get("extended_completion") or {}).get("value")
    eff = max(due, ext) if ext else due
    try:
        late = (AS_OF - date.fromisoformat(eff[:10])).days
    except ValueError:
        return []
    if late < 90:
        return []
    orders = [o for j in p["job_numbers"].split(",") for o in EOT.load().get(j, []) if o.get("status") == "parsed"]
    fines_ordered = sum(o.get("fine") or 0 for o in orders)
    pen = (F.get("penalties_deductions") or {}).get("value") or ""
    m = re.search(r"fine ₹([\d,]+)", str(pen))
    fine_deducted = float(m.group(1).replace(",", "")) if m else None
    evid = fact_ev(p, "completion_due") + fact_ev(p, "time_extensions") + fact_ev(p, "pending_bills") + fact_ev(p, "penalties_deductions")
    evid += [ev("document", f"EoT order {o['document_id']}", o["url"], o.get("page"), o.get("raw"), o.get("method"), "medium", True)
             for o in orders[:3]]
    return [{
        "type": "past_completion_no_final_bill",
        "status": "unexplained_in_public_record" if not ext or ext < due else "partially_explained",
        "confidence": "high" if F.get("completion_due", {}).get("confidence") in ("confirmed", "inferred") and orders else "medium",
        "completion_due": due, "extended_completion": ext, "days_past": late,
        "last_payment": (F.get("last_payment") or {}).get("value"),
        "pending_bills": (F.get("pending_bills") or {}).get("value"),
        "eot_orders": [{"from": o["from"], "to": o["to"], "days": o.get("days"), "fine": o.get("fine"), "document_id": o["document_id"]}
                       for o in orders],
        "fines_in_orders": fines_ordered or None, "fine_deducted_ifms": fine_deducted,
        "value_at_issue": (F.get("payments_gross") or {}).get("value"), "timeline_days": late,
        "documentation_gap": 3, "evidence": evid, "n_source_kinds": 2 if orders else 1,
    }]


WO_PRICE = re.compile(r"(?:tender\s+price|at\s+an\s+amount|contract\s+price|total\s+value\s+of\s+the\s+work\s+is)\s+(?:of\s+)?Rs\.?\s*[:\-]?\s*([\d,]{4,}(?:\.\d+)?)", re.I)


def work_order_values(docs, kinds=("work order",)):
    """Distinct contract prices stated in the job's work orders ('at Tender Price of Rs. 3,50,81,648/-')."""
    seen, out = set(), []
    for d in docs["other_documents"]:
        if d["kind"] not in kinds or d["status"] != "read":
            continue
        for pg in Q.page_texts(CE.find(d["document_id"])):
            for m in WO_PRICE.finditer(re.sub(r"\s+", " ", pg.get("text") or "")):
                v = Q.num(m.group(1).rstrip(",").replace(",", ""))
                if v and v >= 1e4 and v not in seen:
                    seen.add(v)
                    out.append({"value": v, "document_id": d["document_id"], "url": d["url"], "page": pg.get("page"),
                                "raw": m.group(0), "method": pg.get("method")})
    return out


def issue_paid_over_contract(p, docs):
    s = next((x for x in p["signals"] if x["code"] == "paid_over_contract"), None)
    if not s:
        return []
    F = p["facts"]
    paid, cv = F["payments_gross"]["value"], F["contract_value"]["value"]
    hdr = docs["work_slip_header"]
    wos = work_order_values(docs)
    wo_total = sum(v["value"] for v in wos)
    if len(wos) >= 2 and paid <= 1.25 * wo_total:
        # several contracts under one job: the contract value fact held only one of them
        return [{"type": "paid_over_contract", "status": "explained", "confidence": "high", "paid": paid, "contract_value": cv,
                 "ratio": round(paid / cv, 2) if cv else None, "work_orders": wos, "work_orders_total": wo_total,
                 "work_slip_tender_amount": None, "work_slip_actual_total": None, "value_at_issue": 0,
                 "evidence": [ev("document", f"Work order {w['document_id']}", w["url"], w["page"], w["raw"], w["method"], "medium", True)
                              for w in wos[:4]] + fact_ev(p, "payments_gross"), "n_source_kinds": 2}]
    tender = (hdr.get("sanctioned_tender") or {}).get("value")
    total_actual = sum(r["actual_amount"] for r in docs["work_slip_rows"] if r.get("actual_amount"))
    explained = tender and paid <= 1.25 * tender     # a sanctioned tender amount covering the payments (GST included)
    evid = fact_ev(p, "payments_gross") + fact_ev(p, "contract_value")
    if tender:
        h = hdr["sanctioned_tender"]
        evid.append(ev("document", f"Work slip header {h['document_id']}", h["url"], h["page"], h["raw"], "OCR", "medium", False))
    src = (F["contract_value"].get("evidence") or [{}])[0]
    cv_source = ("the awarded (L1) bid on KPPP" if src.get("source_id") == "KPPP" else "the work order")
    period = (F.get("period_days") or {}).get("value")
    fp, lp = (F.get("first_payment") or {}).get("value"), (F.get("last_payment") or {}).get("value")
    span = None
    try:
        span = (date.fromisoformat(lp[:10]) - date.fromisoformat(fp[:10])).days if fp and lp else None
    except ValueError:
        pass
    return [{
        "contract_value_source": cv_source, "period_days": period, "billing_span_days": span, "first_payment": fp, "last_payment": lp,
        "type": "paid_over_contract", "status": "explained" if explained else "unexplained_in_public_record",
        "confidence": "medium" if F["contract_value"]["confidence"] != "confirmed" else "high",
        "paid": paid, "contract_value": cv, "ratio": round(paid / cv, 2) if cv else None,
        "work_slip_tender_amount": tender, "work_slip_actual_total": round(total_actual, 2) or None,
        "value_at_issue": round(paid - 1.18 * cv, 0) if cv else None, "documentation_gap": 3 if not docs["variation_documents"] else 0,
        "evidence": evid, "n_source_kinds": 2,
    }]


def issue_document_reuse(p, qty_by_job, con, docs=None):
    out = []
    placeholder = []
    for s in p["signals"]:
        if s["code"] != "document_reused":
            continue
        url = s["evidence"][0]["url"] if s["evidence"] else ""
        (placeholder if Q._is_placeholder(url.rsplit("/", 1)[-1].replace("%20", " ")) else out).append(s)
    if not out and placeholder:
        return [{"type": "document_reuse", "status": "artefact", "confidence": "low", "other_jobs": sorted({j for s in placeholder for j in s["related"]}),
                 "files": [], "n_files": len(placeholder), "identical_schedule_b": {}, "other_jobs_paid": {}, "value_at_issue": 0,
                 "artefact_reason": "the only file shared with other jobs is a 'Not Applicable' placeholder PDF",
                 "evidence": [ev("document", "Shared placeholder file", s["evidence"][0]["url"], confidence="high") for s in placeholder[:2] if s["evidence"]],
                 "n_source_kinds": 1}]
    if not out:
        return []
    others = sorted({j for s in out for j in s["related"]})
    files = [{"document": (s["evidence"][0]["url"] if s["evidence"] else None), "detail": s["detail"]} for s in out]
    # identical Schedule B (parsed items) across the jobs
    mine = {(w["estimated_quantity"], w["estimated_rate"]) for j in p["job_numbers"].split(",")
            for w in (qty_by_job.get(j) or {}).get("work_items", [])}
    shared_sb = {}
    for o in others:
        theirs = {(w["estimated_quantity"], w["estimated_rate"]) for w in (qty_by_job.get(o) or {}).get("work_items", [])}
        if mine and theirs:
            shared_sb[o] = {"shared_items": len(mine & theirs), "of": max(len(mine), len(theirs))}
    con.row_factory = sqlite3.Row
    other_paid = {o: (con.execute("SELECT payments_gross FROM projects WHERE ',' || job_numbers || ',' LIKE ?", (f"%,{o},%",)).fetchone() or [None])[0]
                  for o in others}
    strong = any(v["shared_items"] >= 5 and v["shared_items"] >= 0.8 * v["of"] for v in shared_sb.values())
    # resolution attempt: one package contract booked under several job codes — the shared contract price covers the
    # combined payments of all the jobs that carry it
    from urllib.parse import unquote
    base = lambda u: unquote((u or "").rsplit("/", 1)[-1])
    shared_names = {base(f["document"]) for f in files}
    wos = [w for w in work_order_values(docs, ("work order", "agreement")) if base(w["url"]) in shared_names] if docs else []
    mine_paid = (p["facts"].get("payments_gross") or {}).get("value") or 0
    combined = mine_paid + sum(v or 0 for v in other_paid.values())
    if wos:
        cv = max(w["value"] for w in wos)
        if combined <= 1.25 * cv:
            return [{"type": "document_reuse", "status": "explained", "confidence": "high", "other_jobs": others, "files": files[:6],
                     "n_files": len(out), "identical_schedule_b": shared_sb, "other_jobs_paid": other_paid, "contract_price": cv,
                     "combined_paid": combined, "value_at_issue": 0, "strong": strong,
                     "evidence": [ev("document", f"Shared work order / agreement {w['document_id']}", w["url"], w["page"], w["raw"], w["method"], "medium", True)
                                  for w in wos[:2]], "n_source_kinds": 2}]
    return [{
        "type": "document_reuse", "status": "insufficient_evidence",
        "confidence": "high", "other_jobs": others, "files": files[:6], "n_files": len(out),
        "identical_schedule_b": shared_sb, "other_jobs_paid": other_paid,
        "value_at_issue": min([(p["facts"].get("payments_gross") or {}).get("value") or 0] + [v or 0 for v in other_paid.values()]),
        "documentation_gap": 5 if strong else 2,
        "evidence": [ev("document", "Identical file attached to both jobs (same MD5)", f["document"], confidence="high", cross_validated=True)
                     for f in files[:4] if f["document"]],
        "n_source_kinds": 2 if strong else 1,
        "strong": strong,
    }]


def issue_fine(p):
    """A fine deducted in IFMS of at least 5 % of gross payments, read against any EoT order on the job."""
    F = p["facts"]
    pen = str((F.get("penalties_deductions") or {}).get("value") or "")
    m = re.search(r"fine ₹([\d,]+)", pen)
    paid = (F.get("payments_gross") or {}).get("value") or 0
    if not m or not paid:
        return []
    fine = float(m.group(1).replace(",", ""))
    if fine < 0.05 * paid or fine < 2e5:
        return []
    orders = [o for j in p["job_numbers"].split(",") for o in EOT.load().get(j, []) if o.get("status") == "parsed"]
    return [{"type": "significant_fine", "status": "insufficient_evidence", "confidence": "high", "fine": fine, "paid": paid,
             "share": round(fine / paid, 3), "eot_orders": [{"to": o["to"], "fine": o.get("fine"), "document_id": o["document_id"]} for o in orders],
             "value_at_issue": fine, "documentation_gap": 2,
             "evidence": fact_ev(p, "penalties_deductions") + fact_ev(p, "payments_gross")[:1], "n_source_kinds": 1}]


# ---------------------------------------------------------------- case assembly

ISSUE_TITLE = {
    "significant_fine": "Significant fine deducted from bills",
    "quantity_above_plan": "Billed quantity above Schedule B",
    "quantities_identical_to_plan": "Executed quantities identical to Schedule B",
    "final_bill_without_mb": "Final bill paid without a measurement book in the public record",
    "past_completion_no_final_bill": "Past completion date, no final bill",
    "paid_over_contract": "Payments above contract value",
    "document_reuse": "Same contract documents under different job numbers",
}
STATUS_LABEL = {
    "significant_fine": "needs RTI",
    "quantity_above_plan": "needs RTI", "quantities_identical_to_plan": "needs field verification",
    "final_bill_without_mb": "needs RTI", "past_completion_no_final_bill": "timeline follow-up",
    "paid_over_contract": "needs RTI", "document_reuse": "document-reuse follow-up",
}


NEXT_STEP = {
    "timeline follow-up": "File the targeted RTI for the extension / penalty records; monitor IFMS monthly",
    "needs RTI": "File the targeted RTI listed in the case",
    "needs field verification": "Measure the listed items on site; file the RTI for the MB pages",
    "incomplete public record": "File the targeted RTI for the missing order",
    "document-reuse follow-up": "Ask for the work-order to job-code mapping",
    "explained by variation": "None: explained by an approved variation document",
    "explained": "None: explained by documents in the public record",
    "set aside": "None",
}


def case_status(primary):
    if primary["status"] == "explained":
        return "explained by variation" if primary["type"] == "quantity_above_plan" else "explained"
    if primary["status"] == "insufficient_evidence" and primary["type"] not in ("quantities_identical_to_plan", "document_reuse"):
        return "incomplete public record"
    return STATUS_LABEL[primary["type"]]


def build_case(job, p, qres, docs, qty, listing, qty_by_job, con):
    issues = (issue_quantity_above_plan(p, qres, docs) + issue_identical(p, qres, docs)
              + issue_final_bill_without_mb(p, job, qty, listing) + issue_timeline(p)
              + issue_paid_over_contract(p, docs) + issue_document_reuse(p, qty_by_job, con, docs)
              + issue_fine(p))
    F = p["facts"]
    v = lambda k: (F.get(k) or {}).get("value")
    base = {
        "case_id": f"KP6-{job}", "project_id": p["id"], "slug": p["slug"], "job_number": job, "title": p["title"],
        "project_summary": {"title": p["title"], "category": p["category"], "location": p["location"] or v("location"),
                            "ward": p["wards"], "agency": p["department"], "office": p["office"],
                            "contractor": v("contractor"), "contractor_confidence": (F.get("contractor") or {}).get("confidence"),
                            "payees": p.get("payees", [])},
        "contract": {"estimated_cost": v("estimated_cost"), "contract_value": v("contract_value"),
                     "contract_value_confidence": (F.get("contract_value") or {}).get("confidence"),
                     "work_order_date": v("work_order_date"), "work_order_ref": v("work_order_ref"), "agreement_ref": v("agreement_ref"),
                     "commencement_date": v("commencement_date"), "stipulated_completion": v("completion_due"),
                     "work_slip_header": {k: x["value"] for k, x in docs["work_slip_header"].items()}},
        "execution": {"final_bill": v("final_bill"), "status": v("status"), "completion_date": v("completion_date"),
                      "completion_certificate": v("completed_on_certificate"), "extended_completion": v("extended_completion"),
                      "time_extensions": v("time_extensions"), "quantity_check": v("quantity_check"),
                      "site_photos": v("site_photos"), "placeholder_photos": v("placeholder_photos")},
        "payments": {"gross_paid": v("payments_gross"), "net_paid": v("payments_nett"), "n_bills": v("n_bills"),
                     "first_payment": v("first_payment"), "last_payment": v("last_payment"),
                     "pending_bills": v("pending_bills"), "deductions": v("penalties_deductions"), "partial_release": v("partial_release")},
        "signals": [{"code": s["code"], "title": s["title"], "severity": s["severity"]} for s in p["signals"]
                    if s["severity"] in ("notice", "attention")][:12],
        "documents_examined": {"variation": docs["variation_documents"], "final_bill_rows_read": len(docs["final_bill_rows"]),
                               "work_slip_rows_read": len(docs["work_slip_rows"]),
                               "other": [{k: d[k] for k in ("document_id", "kind", "status", "url")} for d in docs["other_documents"]]},
        "source_links": {"project_page": f"/p/{p['slug']}",
                         "ifms_bills": sorted({w for w, (j, _, _) in listing.items() if j == job}, key=int)[:40]},
        "issues": issues,
    }
    if not issues:
        return {**base, "retained": False, "why_not_retained": "no case-type issue survives on this project", "status": "no issue"}
    # an above-plan item whose statement row is illegible, while sibling items in the same statement are explained by it
    explained_q = [i for i in issues if i["type"] == "quantity_above_plan" and i["status"] == "explained"]
    for i in issues:
        if i["type"] == "quantity_above_plan" and i["status"] == "insufficient_evidence" and explained_q:
            i["confidence"] = "low"
            i["low_reason"] = (f"the same approved statement covers {len(explained_q)} sibling item(s) "
                               f"({'; '.join(f'{q(x['plan_qty'])} → {q(x['work_slip_executed_qty'])} executed' for x in explained_q)}); "
                               "this item's row is illegible, so it is set aside rather than presented")
    live = [i for i in issues if i["status"] not in DEAD and i["confidence"] != "low"]
    primary = max(live or issues, key=lambda i: (STATUS_UNRESOLVED[i["status"]] + CONF_SCORE[i["confidence"]]
                                                 + materiality(i.get("value_at_issue"))))
    n_src = len({e["kind"] for i in issues for e in i["evidence"]}) + (1 if docs["work_slip_rows"] else 0)
    comp, total = score(live or issues, n_src)
    base.update({
        "primary_issue": primary["type"], "status": case_status(primary),
        "confidence": primary["confidence"], "score_components": comp, "case_score": total,
        "retained": bool(live) and primary["confidence"] != "low",
        "why_selected": why_selected(primary, base, comp),
        "explanations": benign(primary, base),
        "resolves": resolvers(primary),
        "questions": questions(primary, base),
        "rti": rti(primary, base),
        "resolved_issues": [{"type": i["type"], "outcome": i["status"] if i["status"] in DEAD else "low confidence",
                             "why": explained_why(i)} for i in issues if i["status"] in DEAD or i["confidence"] == "low"],
    })
    if not live:
        base["why_not_retained"] = "every issue was explained, was an artefact, or rests only on low-confidence evidence"
        base["status"] = "set aside"
    return base


# ---------------------------------------------------------------- generated text (from fields only)

def inr(x):
    if x is None:
        return "unknown"
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:,.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:,.1f} L"
    return f"₹{x:,.0f}"


def q(x):
    return f"{x:,.2f}".rstrip("0").rstrip(".") if isinstance(x, (int, float)) else str(x)


def why_selected(i, c, comp):
    t = i["type"]
    if t == "quantity_above_plan":
        return (f"A line item billed at {q(i['billed_qty'])} {i['unit'] or 'units'} against {q(i['plan_qty'])} in Schedule B "
                f"({(i['billed_qty'] / i['plan_qty'] - 1):+.0%}), worth {inr(i['value_at_issue'])} at the tendered rate; "
                f"variation reconciliation: {i['status'].replace('_', ' ')}.")
    if t == "quantities_identical_to_plan":
        return (f"{i['n_identical']} items billed in the final bill at exactly the Schedule B quantity ({inr(i['value_at_issue'])} at tendered rates).")
    if t == "final_bill_without_mb":
        return f"Final bill(s) {', '.join(i['final_bills'])} paid ({inr(i['value_at_issue'])} paid on the job) with no measurement book attached in IFMS."
    if t == "past_completion_no_final_bill":
        return (f"Completion date {i['extended_completion'] if i['extended_completion'] and i['extended_completion'] > i['completion_due'] else i['completion_due']} "
                f"passed {i['days_past']} days ago with no final bill; {inr(i['value_at_issue'])} paid.")
    if t == "paid_over_contract":
        return f"Paid {inr(i['paid'])}, {i['ratio']:.0%} of the {inr(i['contract_value'])} contract value from {i.get('contract_value_source', 'the work order')}."
    if t == "significant_fine":
        return f"A fine of {inr(i['fine'])} ({i['share']:.0%} of payments) was deducted; the reason is not stated in the public record."
    if t == "document_reuse":
        return (f"{i['n_files']} identical document file(s) attached to this job and to {', '.join(i['other_jobs'])}"
                + ("; the parsed Schedule B is identical as well." if i.get("strong") else "."))
    return ""


def benign(i, c):
    t = i["type"]
    if t == "quantities_identical_to_plan" and any((b.get("gross") or 0) < 1000 for b in i.get("final_bill_details", [])):
        return ["A token (₹1) final bill can be registered to close the account and attach the final measurement abstract when the running "
                "bills have already paid out the work.",
                "Quantities fixed by drawings (e.g. counted items, standard sections) are executed exactly as designed.",
                "The estimate itself was prepared from detailed measurements of the site."]
    common = ["The approval exists on paper but was not uploaded to IFMS (IFMS attachments are not a complete record)."]
    return {
        "quantity_above_plan": ["An approved deviation / work slip increased the quantity."] + common
                               + ["A second installation location was added to the work.", "An OCR misreading of one figure (each row passed quantity × rate = amount, which makes this unlikely)."],
        "quantities_identical_to_plan": ["Quantities fixed by drawings (e.g. counted items, standard sections) are executed exactly as designed.",
                                         "The item was measured once and the measurement equalled the estimate.",
                                         "The estimate itself was prepared from detailed measurements of the site."],
        "final_bill_without_mb": ["The MB was scanned under another attachment type or name.", "Older bills predate mandatory MB upload."] + common,
        "past_completion_no_final_bill": ["An extension of time was granted but not uploaded.", "Work is complete and the final bill is pending approval.",
                                          "Scope was re-packaged into another contract or job."],
        "paid_over_contract": ["Further work orders or a supplementary agreement under the same job.", "Escalation / price adjustment.",
                               "GST and statutory additions above a contract value quoted without them."] + common,
        "document_reuse": ["One contract funded under two job codes (split budget heads).", "A wrong upload (document classification error)."],
        "significant_fine": ["Liquidated damages for delay, levied as the contract provides.", "A quality deduction later refunded.",
                             "A recovery booked under the 'fine' head by convention."],
    }[t]


def resolvers(i):
    return {
        "quantity_above_plan": ["Approved deviation statement / work slip covering the item", "Supplementary Schedule B", "MB pages for the item", "Site inspection counting installed units"],
        "quantities_identical_to_plan": ["MB pages with the actual dimensions for these items", "Site measurement of the items"],
        "final_bill_without_mb": ["The MB (or MB extract) on which the final bill was passed", "Completion certificate and check-measurement record"],
        "past_completion_no_final_bill": ["EoT order for the current contract", "Penalty / liquidated-damages calculation", "Current progress report"],
        "paid_over_contract": ["All work orders and supplementary agreements under the job", "Final bill abstract with totals", "Revised estimate / EIRL"],
        "document_reuse": ["Work-order ↔ job-code register for both jobs", "Budget-head allocation for each job", "Bill abstracts of both jobs"],
        "significant_fine": ["The penalty order stating the reason and calculation", "EoT / delay record for the work"],
    }[i["type"]]


def questions(i, c):
    j = c["job_number"]
    t = i["type"]
    if t == "quantity_above_plan":
        return [f"Was the increase of '{i['item'][-60:]}' from {q(i['plan_qty'])} to {q(i['billed_qty'])} {i['unit'] or 'units'} on job {j} approved through a deviation statement or work slip? If so, give the approval number, date and approving authority.",
                f"Which MB pages record the measurement of the additional {q(i['billed_qty'] - i['plan_qty'])} {i['unit'] or 'units'}?",
                "Where were the additional units installed?",
                "Was the additional quantity paid at the tendered rate or a revised rate?"]
    if t == "quantities_identical_to_plan":
        return [f"Which MB pages record the measurements of the {i['n_identical']} items on job {j} whose executed quantity equals the tendered quantity exactly?",
                *([f"Why was final bill {b['wbid']} registered for {inr(b['gross'])} and left at {b['level']} since {b['registered']}?"
                   for b in i.get("final_bill_details", []) if (b.get("gross") or 0) < 1000]),
                "Who recorded and who check-measured these items, and on what dates?",
                "Were these items measured on site or carried over from the estimate?"]
    if t == "final_bill_without_mb":
        return [f"On the basis of which MB (book number and pages) was final bill {i['final_bills'][0]} on job {j} passed?",
                "Was the final measurement check-measured by the AEE/EE as required?",
                "Why is the MB not attached to the bill in IFMS?"]
    if t == "past_completion_no_final_bill":
        return [f"Has an extension of time been granted for job {j} beyond {i['completion_due']}? If so, provide the order.",
                "What liquidated damages / penalties have been calculated and recovered, bill by bill?",
                "What is the current physical progress and the expected completion date?",
                "Why are the pending bills held, and at which approval level?"]
    if t == "paid_over_contract":
        return [f"List every work order and supplementary agreement issued under job {j} with their values.",
                f"What explains gross payments of {inr(i['paid'])} against a contract value of {inr(i['contract_value'])}?",
                "Was a revised estimate / EIRL approved, and by whom?"]
    if t == "significant_fine":
        return [f"Why was a fine of {inr(i['fine'])} deducted on job {j}, and under which contract clause?",
                "Was it for delay (liquidated damages) or for quality?", "Was any part of it later refunded?"]
    if t == "document_reuse":
        return [f"Is the agreement attached to job {j} the same contract as the one on {', '.join(i['other_jobs'])}? If so, why is it booked under two job numbers?",
                "How much of the contract value was paid under each job number?",
                "Is the work described under each job number physically distinct?"]
    return []


def rti(i, c):
    """Specific records only — never a fishing request."""
    j = c["job_number"]
    t = i["type"]
    if t == "quantity_above_plan":
        return [{"record": f"Approved deviation statement / work slip / supplementary Schedule B covering '{i['item'][-60:]}'",
                 "job": j, "detail": f"Schedule B quantity {q(i['plan_qty'])}, billed {q(i['billed_qty'])} {i['unit'] or ''} @ ₹{i['rate']:,.2f}",
                 "why": "Shows whether the extra quantity was approved before payment."},
                {"record": "Measurement-book pages recording this item", "job": j,
                 "detail": f"{q(i['billed_qty'])} {i['unit'] or 'units'} cumulative", "why": "Shows where and when the additional units were measured."}]
    if t == "quantities_identical_to_plan":
        return [{"record": "Measurement-book pages for the items listed", "job": j,
                 "detail": "; ".join(f"{q(x['qty'])} {x['unit'] or ''} @ ₹{x['rate']:,.2f}" for x in i["items"][:5]),
                 "why": "Shows whether the quantities were measured on site or copied from the estimate."}]
    if t == "final_bill_without_mb":
        return [{"record": "Measurement book (or certified extract) and check-measurement record for the final bill", "job": j,
                 "detail": f"final bill(s) {', '.join(i['final_bills'])}", "why": "The final bill has no MB attached in IFMS."}]
    if t == "past_completion_no_final_bill":
        return [{"record": "Extension-of-time order(s) for the current contract and the penalty calculation", "job": j,
                 "detail": f"stipulated completion {i['completion_due']}", "why": "No EoT for this date is attached in IFMS."},
                {"record": "Statement of fines / liquidated damages levied and recovered, bill by bill", "job": j,
                 "detail": f"EoT orders levy {inr(i['fines_in_orders'])}; IFMS shows {inr(i['fine_deducted_ifms'])} deducted as fine" if i.get("fines_in_orders") else "",
                 "why": "Reconciles penalties ordered with penalties recovered."}]
    if t == "paid_over_contract":
        out = [{"record": "All work orders, supplementary agreements and the revised estimate (EIRL) under the job", "job": j,
                "detail": f"paid {inr(i['paid'])} vs contract {inr(i['contract_value'])}", "why": "Identifies the authority for payments above the contract value."}]
        if i.get("period_days") and i.get("billing_span_days") and i["billing_span_days"] > 1.25 * i["period_days"]:
            out.insert(0, {"record": "Order extending or renewing the contract beyond its stipulated period", "job": j,
                           "detail": f"{i['period_days'] // 30}-month contract; bills from {i['first_payment']} to {i['last_payment']}",
                           "why": "Shows whether payments after the contract period were authorised, and at what rate."})
        return out
    if t == "significant_fine":
        return [{"record": "Penalty order and calculation sheet", "job": j, "detail": f"fine {inr(i['fine'])} deducted ({i['share']:.0%} of gross payments)",
                 "why": "States why the penalty was levied and how it was computed."}]
    if t == "document_reuse":
        return [{"record": "Work-order ↔ job-code mapping and budget-head allocation", "job": f"{j} and {', '.join(i['other_jobs'])}",
                 "detail": "same agreement / work order attached to both", "why": "Shows whether one contract was split across job numbers."}]
    return []


def explained_why(i):
    t = i["type"]
    if i["status"] == "artefact":
        return i["artefact_reason"] + "."
    if i["confidence"] == "low" and i["status"] not in DEAD:
        if i.get("low_reason"):
            return i["low_reason"] + "."
        if t == "quantity_above_plan":
            return (f"billed {q(i['billed_qty'])} vs plan {q(i['plan_qty'])} rests on a sum of running-bill abstracts, "
                    "not on a cumulative or final figure; not reliable enough to present.")
        return "evidence is low confidence."
    if t == "quantity_above_plan":
        return (f"A work slip / comparative statement records an executed quantity of {q(i['work_slip_executed_qty'])} "
                f"for the item (billed {q(i['billed_qty'])}), so the increase is covered by a variation document.")
    if t == "final_bill_without_mb":
        return f"An MB-like file is attached under another type: {', '.join(i['mb_filed_elsewhere'][:2])}."
    if t == "quantity_above_plan" and i["status"] == "explained":
        return (f"the approved work slip / comparative statement records {q(i['work_slip_executed_qty'])} executed against "
                f"{q(i['plan_qty'])} tendered, covering the billed {q(i['billed_qty'])}.")
    if t == "document_reuse" and i["status"] == "explained":
        return (f"the shared work order / agreement is one package contract worth {inr(i['contract_price'])}; payments on this job and "
                f"{', '.join(i['other_jobs'])} total {inr(i['combined_paid'])} ({i['combined_paid'] / i['contract_price']:.0%} of it), consistent "
                "with one contract booked under several job codes.")
    if t == "paid_over_contract":
        if i.get("work_orders"):
            return ("the job carries " + str(len(i["work_orders"])) + " work orders (" + ", ".join(inr(w["value"]) for w in i["work_orders"])
                    + f", total {inr(i['work_orders_total'])}); payments of {inr(i['paid'])} are {i['paid'] / i['work_orders_total']:.0%} of that total. "
                    "The earlier signal compared payments with only one work order.")
        return f"A work slip header records a sanctioned tender amount of {inr(i['work_slip_tender_amount'])}, which covers the payments."
    return "Explained by documents found in Phase 6."


# ---------------------------------------------------------------- run and render

def run(jobs=None):
    jobs = jobs or CE.CANDIDATES
    listing = Q._load_listing()
    qty = Q.load() or {"projects": [], "mb_presence": {}}
    qty_by_job = {r["job"]: r for r in qty["projects"]}
    con = sqlite3.connect(DB)
    reviewed, seen_projects = [], set()
    for job in jobs:
        p = load_project(con, job)
        if not p or p["id"] in seen_projects:
            continue
        seen_projects.add(p["id"])
        docs = read_documents(listing, job)
        reviewed.append(build_case(job, p, qty_by_job.get(job), docs, qty, listing, qty_by_job, con))
    con.close()
    # one case per contract: two jobs that share the same agreement / Schedule B become a single case
    by_job = {c["job_number"]: c for c in reviewed}
    for c in reviewed:
        dr = next((i for i in c["issues"] if i["type"] == "document_reuse" and i.get("strong")), None)
        if not dr or not c.get("retained"):
            continue
        for o in dr["other_jobs"]:
            oc = by_job.get(o)
            if oc and oc.get("retained") and (oc["payments"]["gross_paid"] or 0) <= (c["payments"]["gross_paid"] or 0):
                oc["retained"] = False
                oc["why_not_retained"] = f"merged into {c['case_id']} (same contract documents)"
                c.setdefault("related_jobs", []).append(o)
    retained = sorted([c for c in reviewed if c.get("retained")], key=lambda c: -c["case_score"])
    for n, c in enumerate(retained, 1):
        c["rank"] = n
    for c in reviewed:                   # rendered text travels with the data, so the web app needs no pipeline code
        for i in c["issues"]:
            i["text"] = issue_text(i)
            i["title"] = ISSUE_TITLE[i["type"]]
            i["live"] = i["status"] not in DEAD and i["confidence"] != "low"
        if c.get("primary_issue"):
            prim = next(x for x in c["issues"] if x["type"] == c["primary_issue"])
            c["primary_title"], c["primary_text"] = prim["title"], prim["text"]
            c["core_question"] = (c.get("questions") or [""])[0]
            c["next_step"] = NEXT_STEP.get(c["status"], "Review the evidence")
    data = {"as_of": AS_OF.isoformat(), "weights": WEIGHTS, "status_points": STATUS_UNRESOLVED, "confidence_points": CONF_SCORE,
            "reviewed": len(reviewed), "retained": len(retained), "cases": reviewed}
    OUT.write_text(json.dumps(data, indent=1, default=str))
    write_markdown(data)
    return data


def _ev_line(e):
    bits = [e["label"] or "source"]
    if e.get("page"):
        bits.append(f"p.{e['page']}")
    if e.get("method"):
        bits.append(e["method"])
    bits.append(f"confidence {e.get('confidence') or 'n/a'}" + (", cross-validated" if e.get("cross_validated") else ""))
    url = e.get("url")
    line = " · ".join(str(b) for b in bits)
    return f"- {line}" + (f" — <{url}>" if url else "") + (f"\n  > {e['raw']}" if e.get("raw") else "")


def issue_text(i):
    """Neutral, field-generated description of one issue."""
    t = i["type"]
    if t == "quantity_above_plan":
        s = (f"'{i['item'][-90:]}' was tendered at {q(i['plan_qty'])} {i['unit'] or 'units'} @ ₹{i['rate']:,.2f} and billed at "
             f"{q(i['billed_qty'])} ({i['billed_basis']}{', final bill' if i['from_final_bill'] else ', running bill'}). "
             f"At the tendered rate the additional quantity is about {inr(i['value_at_issue'])}. ")
        if i["status"] == "explained":
            s += f"A work slip / comparative statement records {q(i['work_slip_executed_qty'])} executed, which covers it."
        elif i["status"] == "partially_explained":
            s += f"A work slip / comparative statement records {q(i['work_slip_executed_qty'])} executed: part of the increase is covered."
        elif i["work_slip_executed_qty"] is not None:
            s += f"The comparative statement records only {q(i['work_slip_executed_qty'])} executed for this item. No explaining approval was found in the public records examined."
        elif i["variation_documents"]:
            s += (f"Variation documents are attached ({', '.join(i['variation_documents'][:3])}) but no row for this item could be read in them; "
                  "the public record examined neither confirms nor rules out an approval.")
        else:
            s += "No supplementary Schedule B, work slip, deviation statement or revised estimate explaining this quantity was found in the public records examined."
        return s
    if t == "quantities_identical_to_plan":
        s = (f"{i['n_identical']} items were billed at exactly their Schedule B quantity (to the decimal), "
             f"worth {inr(i['value_at_issue'])} at tendered rates"
             + (f"; {i['n_other_compared']} other items compared on the same project differ from Schedule B. " if i["n_other_compared"] else
                f", confirmed by the final bill ({i.get('n_confirmed', 0)} of {i['n_identical']} items). "))
        for b in i.get("final_bill_details", []):
            s += (f"That final bill ({b['bill_type']}, IFMS work bill {b['wbid']}, registered {b['registered']}) has a gross value of "
                  f"{inr(b['gross'])}, is {'paid' if b['paid'] else 'not paid'} and sits at {b['level']}. ")
        if i["work_slip_agrees"]:
            s += f"The project's comparative statement also records the tendered quantity as executed for {i['work_slip_agrees']} of them. "
        if i["work_slip_differs"]:
            s += f"For {i['work_slip_differs']} of them the comparative statement records a different executed quantity than the bill form. "
        return s + "Only the measurement book can show whether these quantities were measured on site."
    if t == "final_bill_without_mb":
        if i["status"] == "explained":
            return f"Final bill(s) {', '.join(i['final_bills'])} carry no M.B. attachment, but an MB-like file is attached under another type ({', '.join(i['mb_filed_elsewhere'][:2])})."
        return (f"Final bill(s) {', '.join(i['final_bills'])} were paid; no measurement book or bill form is attached to any bill of this job in IFMS"
                + (f" (slots filled with: {', '.join(i['mb_placeholders'][:3])})" if i["mb_placeholders"] else "") + ".")
    if t == "past_completion_no_final_bill":
        s = f"Stipulated completion was {i['completion_due']}"
        if i["eot_orders"]:
            s += "; extension-of-time orders in IFMS: " + "; ".join(
                f"{o['from'] or '?'} → {o['to']}" + (f" ({o['days']} days)" if o.get("days") else "") + (f", penalty {inr(o['fine'])}" if o.get("fine") else "")
                for o in i["eot_orders"])
            if i["extended_completion"] and i["extended_completion"] < i["completion_due"]:
                s += f". These cover an earlier period; none is visible for {i['completion_due']}"
        s += (f". As of {AS_OF.isoformat()}, {i['days_past']} days have passed since "
              f"{max(i['completion_due'], i['extended_completion'] or '')}; no final bill is recorded and the last payment was on {i['last_payment']}. "
              f"Pending bills: {i['pending_bills'] or 'none recorded'}.")
        if i.get("fines_in_orders"):
            s += f" Penalties stated in the EoT orders total {inr(i['fines_in_orders'])}; IFMS deductions labelled 'fine' total {inr(i['fine_deducted_ifms'])}."
        return s
    if t == "paid_over_contract":
        s = (f"Gross payments of {inr(i['paid'])} are {i['ratio']:.0%} of the {inr(i['contract_value'])} contract value from "
             f"{i.get('contract_value_source', 'the work order')} (GST explains up to ~118%). ")
        if i.get("period_days") and i.get("billing_span_days") and i["billing_span_days"] > 1.25 * i["period_days"]:
            s += (f"The tender's stipulated period is {i['period_days'] // 30} months; payments run from {i['first_payment']} to "
                  f"{i['last_payment']} ({i['billing_span_days'] // 30} months). ")
        if i["work_slip_tender_amount"]:
            s += f"A work slip header records a sanctioned tender amount of {inr(i['work_slip_tender_amount'])}. "
        return s + ("No document explaining the difference was found in the public records examined." if i["status"] != "explained" else "")
    if t == "significant_fine":
        s = f"IFMS deductions labelled 'fine' total {inr(i['fine'])}, {i['share']:.0%} of gross payments of {inr(i['paid'])}. "
        s += ("Extension-of-time orders on the job: " + "; ".join(f"to {o['to']}" + (f" (penalty {inr(o['fine'])})" if o.get('fine') else '') for o in i["eot_orders"]) + ". "
              if i["eot_orders"] else "No extension-of-time or penalty order stating the reason is attached in IFMS. ")
        return s
    if t == "document_reuse":
        s = f"{i['n_files']} file(s) with identical content (same MD5) are attached to this job and to {', '.join(i['other_jobs'])}. "
        for o, v in i["identical_schedule_b"].items():
            s += f"The parsed Schedule B of {o} shares {v['shared_items']} of {v['of']} items (same quantity and rate). "
        s += "Payments: " + "; ".join(f"{o} {inr(v)}" for o, v in i["other_jobs_paid"].items()) + "."
        return s
    return ""


def render(c):
    P, K, X, M = c["project_summary"], c["contract"], c["execution"], c["payments"]
    i = next(x for x in c["issues"] if x["type"] == c["primary_issue"])
    L = [f"# {c['case_id']} — {ISSUE_TITLE[i['type']]}", "",
         f"**Status:** {c['status']} · **Confidence:** {c['confidence']} · **Priority score:** {c['case_score']} / 100 (ranking aid only; not a judgement)", "",
         "## Project", "",
         f"- **Work:** {c['title']}",
         f"- **Job number:** {c['job_number']} · **Category:** {P['category'] or 'unknown'}",
         f"- **Where:** {P['location'] or 'not stated'}" + (f" (ward {P['ward']})" if P["ward"] else ""),
         f"- **Contractor:** {P['contractor'] or 'unknown'}" + (f" ({P['contractor_confidence']})" if P["contractor"] else ""),
         *([f"- **Paid to, per IFMS bills:** " + "; ".join(f"{x['contractor']} {inr(x['gross'])} ({x['bills']} bills, {x['first']} → {x['last']})"
                                                         for x in P.get("payees", []))] if len(P.get("payees", [])) > 1 else []),
         f"- **Agency:** {P['agency'] or 'BBMP'}" + (f", {P['office']}" if P["office"] else ""), "",
         "## What was promised", "",
         f"- **Contract / work-order value:** {inr(K['contract_value'])}" + (f" ({K['contract_value_confidence']})" if K["contract_value"] else "")
         + (f"; estimate {inr(K['estimated_cost'])}" if K["estimated_cost"] else ""),
         f"- **Work order:** {K['work_order_ref'] or 'not read'}" + (f", {K['work_order_date']}" if K["work_order_date"] else "")
         + (f" · commencement {K['commencement_date']}" if K["commencement_date"] else ""),
         f"- **Stipulated completion:** {K['stipulated_completion'] or 'not read'}"]
    if K["work_slip_header"]:
        L.append("- **Comparative statement header:** " + "; ".join(f"{k.replace('_', ' ')} {inr(v) if isinstance(v, (int, float)) else v}" for k, v in K["work_slip_header"].items()))
    L += ["", "## What happened", "",
          f"- **Paid (gross / net):** {inr(M['gross_paid'])} / {inr(M['net_paid'])} in {M['n_bills'] or '?'} bills, {M['first_payment'] or '?'} → {M['last_payment'] or '?'}",
          f"- **Final bill:** {X['final_bill'] or 'none recorded'} · **Status:** {X['status'] or 'unknown'}"]
    for k, lab in (("completion_certificate", "Completion certificate"), ("time_extensions", "Extensions of time"), ("quantity_check", "Quantities"),
                   ("site_photos", "Site photos")):
        if X.get(k):
            L.append(f"- **{lab}:** {X[k]}")
    for k, lab in (("pending_bills", "Pending bills"), ("deductions", "Deductions")):
        if M.get(k):
            L.append(f"- **{lab}:** {M[k]}")
    L += ["", "## Why this case deserves follow-up", "", issue_text(i), ""]
    others = [x for x in c["issues"] if x is not i]
    if others:
        live_o = [x for x in others if x["status"] not in DEAD and x["confidence"] != "low"]
        if live_o:
            L += ["Other observations on this project:", ""] + [f"- *{ISSUE_TITLE[x['type']]}* ({x['status'].replace('_', ' ')}): {issue_text(x)}" for x in live_o] + [""]
    if c.get("resolved_issues"):
        L += ["Signals examined and set aside:", ""] + [f"- {r['type'].replace('_', ' ')} ({r['outcome']}): {r['why']}" for r in c["resolved_issues"]] + [""]
    L += ["## Possible legitimate explanations", ""] + [f"- {e}" for e in c["explanations"]] + ["",
          "## What would resolve it", ""] + [f"- {e}" for e in c["resolves"]] + ["",
          "## Questions an investigator could ask", ""] + [f"{n}. {x}" for n, x in enumerate(c["questions"], 1)] + ["",
          "## Evidence", ""]
    seen = set()
    for e in [e for x in c["issues"] if x["status"] not in DEAD and x["confidence"] != "low" for e in x["evidence"]]:
        k = (e.get("url"), e.get("page") or 1)
        if k in seen:
            continue
        seen.add(k)
        L.append(_ev_line(e))
    L += ["", f"Project page in the app: `{c['source_links']['project_page']}` · IFMS work bills: {', '.join(c['source_links']['ifms_bills'][:12])}",
          "", "*Investigation lead, not an accusation. Every figure above is generated from `data/investigation_cases.json`.*", ""]
    return "\n".join(L)


def write_markdown(data):
    CASES_DIR.mkdir(exist_ok=True)
    for f in CASES_DIR.glob("KP6-*.md"):
        f.unlink()
    retained = sorted([c for c in data["cases"] if c.get("retained")], key=lambda c: c["rank"])
    for c in retained:
        (CASES_DIR / f"{c['case_id']}.md").write_text(render(c))
    # index
    L = ["# Case index — Bengaluru Public Works Explorer: Koramangala", "",
         f"{data['retained']} investigation-ready cases out of {data['reviewed']} candidates reviewed (as of {data['as_of']}). "
         "Ranked by a transparent priority score (components below); the score orders follow-up work and does not imply wrongdoing.", "",
         "| Rank | Case | Project | Value at issue | Paid | Core issue | Confidence | Status | Score |",
         "|---:|---|---|---:|---:|---|---|---|---:|"]
    for c in retained:
        i = next(x for x in c["issues"] if x["type"] == c["primary_issue"])
        L.append(f"| {c['rank']} | [{c['case_id']}](cases/{c['case_id']}.md) | {c['job_number']} — {c['title'][:70]} | {inr(i.get('value_at_issue'))} | "
                 f"{inr(c['payments']['gross_paid'])} | {ISSUE_TITLE[i['type']]} | {c['confidence']} | {c['status']} | {c['case_score']} |")
    L += ["", "## Score components", "", "| Case | " + " | ".join(WEIGHTS) + " |", "|---|" + "---:|" * len(WEIGHTS)]
    for c in retained:
        L.append(f"| {c['case_id']} | " + " | ".join(str(c['score_components'][k]) for k in WEIGHTS) + " |")
    L += ["", "Maximum points: " + ", ".join(f"{k} {v}" for k, v in WEIGHTS.items()) + ".",
          "Unresolved points: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in STATUS_UNRESOLVED.items()) + ".", "",
          "## Reviewed but not retained", "", "| Project | Reason |", "|---|---|"]
    for c in data["cases"]:
        if not c.get("retained"):
            why = c.get("why_not_retained") or ("low confidence" if c.get("confidence") == "low" else "")
            res = "; ".join(f"{r['type'].replace('_', ' ')} ({r['outcome']}): {r['why']}" for r in c.get("resolved_issues", []))
            L.append(f"| {c['job_number']} — {c['title'][:60]} | {why}{(' — ' + res) if res else ''} |")
    (ROOT / "CASE_INDEX.md").write_text("\n".join(L) + "\n")
    # RTI
    R = ["# RTI questions — targeted records only", "",
         "Each request names one record, the job number and the item or date it concerns, and why that record would settle the open question. "
         "Grouped by project, in case-rank order.", ""]
    n = 0
    for c in retained:
        reqs = [r for x in c["issues"] if x["status"] not in DEAD and x["confidence"] != "low" for r in rti(x, c)]
        if not reqs:
            continue
        R += [f"## {c['job_number']} — {c['title'][:90]}", f"Case: [{c['case_id']}](cases/{c['case_id']}.md)", ""]
        for r in reqs:
            n += 1
            R.append(f"{n}. **{r['record']}** — job {r['job']}" + (f"; {r['detail']}" if r["detail"] else "") + f". *Why:* {r['why']}")
        R.append("")
    R.insert(2, f"{n} requests across {sum(1 for c in retained if any(x['status'] not in DEAD and x['confidence'] != 'low' for x in c['issues']))} cases.")
    (ROOT / "internal").mkdir(exist_ok=True)          # RTI drafts are working notes, not published (git-ignored)
    (ROOT / "internal" / "RTI_QUESTIONS.md").write_text("\n".join(R) + "\n")
    data["rti_count"] = n


if __name__ == "__main__":
    d = run()
    print(json.dumps({"reviewed": d["reviewed"], "retained": d["retained"], "rti": d.get("rti_count")}))
