"""Project Truth model: every fact about a project, classified and evidenced.

confidence:
  confirmed  value is copied (or exactly summed) from records that belong to this project by an
             exact identifier (BBMP job number, KPPP tender number)
  inferred   value depends on an interpretation or a fuzzy link (tender<->bills match, bill type ->
             completion, place names read from text, completion date = award + period, dedupe by amounts)
  unknown    no public source we found publishes it

Facts are grouped into the lifecycle stages the UI tells as a story:
  planned -> awarded -> money -> outcome
"""
import re
from collections import Counter
from datetime import date, timedelta

from ingest import scope
from ingest.bills import grouped_refs, ref

STAGES = ("planned", "awarded", "money", "outcome")
PLAN_SOURCES = {"WB-POW", "WB-GRANT"}
SOURCE_RANK = {"KPPP": 0, "OC-TENDERS": 1, "WB-NOTICE": 2, "WB-HORT": 3}
OCR_NOTE = " (read by OCR from a scanned government document — digits may be misread)"


def fact(field, stage, value, confidence, explanation, evidence=None, via=None, label=None):
    return {"field": field, "stage": stage, "value": value, "confidence": confidence if value is not None else "unknown",
            "explanation": explanation, "evidence": evidence or [], "via": via, "label": label}


def unknown(field, stage, why):
    return fact(field, stage, None, "unknown", why)


def tender_ref(t, what, url=None):
    label = (f"KPPP tender {t['tender_number']}" if t["source"] == "KPPP" else
             f"BBMP tender list: {t['tender_number']}" if t["source"] == "OC-TENDERS" else
             f"{t.get('doc_title', 'Archived BBMP document')} (Wayback Machine)")
    r = {"source_id": t["source"], "label": label, "url": url or t["url"], "note": what}
    if t.get("row"):
        r["rows"] = [t["row"]]
    return r


def link_note(ev):
    if not ev:
        return None
    if ev.get("confidence") == "document":
        return "Linked exactly: " + "; ".join(ev["support"])
    why = "; ".join(ev["support"][:4]) or "description match"
    return (f"Linked by matching ({ev['confidence']} confidence, description similarity "
            f"{ev['text']:.0%}): {why}" + (f". Caveats: {'; '.join(ev['conflict'])}" if ev["conflict"] else ""))


def most_common(values):
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else None


def add_days(iso, days):
    try:
        return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ main assembly
def assemble(p, links, estimates):
    """p: project skeleton (bills grouped by job number, or a tender-only project).
    links: list of (tender_group, evidence|None). evidence None = the project *is* this tender."""
    F = {}
    bills = p["bills"]
    put = lambda f: F.__setitem__(f["field"], f)

    # ---------------- tenders vs plan entries; primary tender = exact identity first, then source rank, then score
    plan_links = [(g, ev) for g, ev in links if g[0]["source"] in PLAN_SOURCES]
    links = [(g, ev) for g, ev in links if g[0]["source"] not in PLAN_SOURCES]
    links = sorted(links, key=lambda ge: (ge[1] is not None, SOURCE_RANK.get(ge[0][0]["source"], 9), -(ge[1] or {}).get("score", 9)))
    prim, prim_ev = (links[0] if links else (None, None))
    all_tenders = [t for g, _ in links for t in g]
    conf_t = "confirmed" if prim is not None and (prim_ev is None or prim_ev.get("confidence") == "document") else "inferred"
    via = link_note(prim_ev)
    awarded = next((t for t in reversed(prim) if t.get("contractor")), None) if prim else None
    last_t = prim[-1] if prim else None
    first_t = prim[0] if prim else None

    # ---------------- PLANNED
    put(fact("description", "planned", p["title"], "confirmed",
             "Work description as written in the source record",
             grouped_refs(bills[:1]) if bills else [tender_ref(last_t, "Tender title")]))
    locs = scope.extract_location(" ".join(p["descriptions"]))
    put(fact("location", "planned", "; ".join(locs) or None, "inferred",
             "Place names read from the work description text", None) if locs
        else unknown("location", "planned", "No place name could be read from the description"))
    if p["wards"]:
        put(fact("ward", "planned", ", ".join(p["wards"]), "confirmed", "Ward the bills are booked to",
                 grouped_refs(bills[:3])))
    est = estimates.get(p["job_numbers"][0]) if p["job_numbers"] else None
    sanc = p.get("sanction")
    ecv_t = next((t for t in reversed(prim) if t.get("ecv")), None) if prim else None
    if est:
        put(fact("estimated_cost", "planned", est["estimate"], "confirmed", "Estimate on the BBMP job code (2018 job-code register)",
                 [ref(est["source"], est["source_row"], "Job code estimate")]))
    elif sanc and sanc.get("ecv"):
        put(fact("estimated_cost", "planned", sanc["ecv"], "confirmed",
                 f"Estimate on the BBMP ward works list FY {sanc.get('fy') or ''} for this job code ({sanc['office'] or 'DDO n/a'})",
                 [tender_ref(sanc, f"Job {sanc['job_number']}, row {sanc['row']}")]))
    elif ecv_t:
        put(fact("estimated_cost", "planned", ecv_t["ecv"], conf_t,
                 ("Estimated Contract Value (ECV) in the tender" if ecv_t["source"] in ("KPPP", "OC-TENDERS") else
                  "Approximate value of work in the tender notification") + (OCR_NOTE if ecv_t.get("ocr") else ""),
                 [tender_ref(ecv_t, "ECV")], via))
    else:
        put(unknown("estimated_cost", "planned", "No estimate published for this work in the sources used"))
    if prim and any(t.get("provisional_amount") for t in prim):
        t = next(t for t in reversed(prim) if t.get("provisional_amount"))
        put(fact("provisional_amount", "planned", t["provisional_amount"], conf_t,
                 "Provisional amount (budget provision) in the tender schedule", [tender_ref(t, "Provisional amount")], via))
    if prim:
        put(fact("tender_published", "planned", first_t["published_date"], conf_t,
                 "Date the first call of the tender was published", [tender_ref(first_t, "Published date")], via))
        calls = max(t["call"] for t in prim)
        put(fact("tender_calls", "planned", calls, conf_t,
                 "Number of times the tender was called (re-tendered as CALL-2, CALL-3 …)" if calls > 1 else "Tender awarded/listed on its first call",
                 [tender_ref(t, f"Call {t['call']}") for t in prim], via))
        pt = next((t for t in reversed(prim) if t.get("period_days")), None)
        if pt:
            if pt["source"] == "KPPP":
                put(fact("period_days", "planned", pt["period_days"], conf_t,
                         f"Stipulated period of completion in the tender document (“{pt['period_text']}”, {pt['period_doc']})",
                         [{"source_id": "KPPP", "label": f"Tender document {pt['period_doc']}", "url": pt["period_url"]}], via))
            else:
                put(fact("period_days", "planned", pt["period_days"], conf_t,
                         f"Period of completion in the tender notification (“{pt['period_text']}”)" + (OCR_NOTE if pt.get("ocr") else ""),
                         [tender_ref(pt, "Period of completion")], via))
        else:
            put(unknown("period_days", "planned", "Tender document not available or period not stated"))
    else:
        put(unknown("tender_published", "planned", "No tender found or matched for this work"))
        put(unknown("period_days", "planned", "No tender document found or matched"))

    # ---------------- PLAN entries (Programme of Works / grant lists) and exact sanction lists
    if sanc:
        put(fact("sanctioned", "planned", sanc.get("fy") and f"FY {sanc['fy']}" or "listed", "confirmed",
                 f"Listed with this job code on the BBMP ward works list ({sanc['doc_title']}); budget head {sanc.get('budget_head') or 'n/a'}",
                 [tender_ref(sanc, f"row {sanc['row']}")]))
    for g, ev in sorted(plan_links, key=lambda ge: -ge[1]["score"])[:1]:
        e = g[-1]
        put(fact("planned_amount", "planned", e["ecv"], "inferred",
                 f"Amount in {e['doc_title']}" + (OCR_NOTE if e.get("ocr") else ""), [tender_ref(e, "Planned amount")], link_note(ev)))
        if e.get("plan_status"):
            put(fact("plan_status", "planned", e["status_text"], "inferred",
                     "Approval status recorded in the programme of works at the time of the document", [tender_ref(e, "Status")], link_note(ev)))

    # ---------------- AWARDED
    payees = Counter()
    for b in bills:
        if b.get("contractor"):
            payees[b["contractor"]] += b.get("gross") or 0
    ranked = [c for c, _ in payees.most_common()]
    if ranked:
        note = "Payee with the largest billed amount under this job number"
        if len(ranked) > 1:
            note += f"; also billed: {', '.join(ranked[1:4])}{'…' if len(ranked) > 4 else ''}"
        if awarded and awarded.get("contractor"):
            note += f". Tender awarded to: {awarded['contractor']}"
        put(fact("contractor", "awarded", ranked[0], "confirmed", note, grouped_refs([b for b in bills if b.get("contractor")])))
    elif awarded:
        put(fact("contractor", "awarded", awarded["contractor"], conf_t, "Awarded (selected) bidder on the tender",
                 [tender_ref(awarded, "Selected bidder", awarded.get("bid_url"))], via))
    else:
        put(unknown("contractor", "awarded", "No contractor named in any source yet"))
    if awarded and awarded.get("contract_value"):
        what = "Negotiated value of the awarded bid" if awarded.get("negotiated_value") else "Quoted value of the awarded (L1) bid"
        put(fact("contract_value", "awarded", awarded["contract_value"], conf_t, what,
                 [tender_ref(awarded, what, awarded.get("bid_url"))], via))
    else:
        why = ("Tender not yet awarded" if last_t and last_t.get("status") and last_t["status"] != "AWARDED"
               else "Contract (work-order) value is not published in BBMP bill data and no awarded tender was matched")
        put(unknown("contract_value", "awarded", why))
    if awarded and awarded.get("awarded_date"):
        put(fact("awarded_date", "awarded", awarded["awarded_date"], conf_t, "Tender award date",
                 [tender_ref(awarded, "Award date")], via))
    bt = next((t for t in reversed(prim) if t.get("bidders")), None) if prim else None
    if bt:
        put(fact("bidders", "awarded", len(bt["bidders"]), conf_t,
                 "Bidders in the commercial comparative statement: " +
                 "; ".join(f"{b['rank'] or '?'} {b['name']} ₹{b['amount']:,.0f}" for b in bt["bidders"][:6]),
                 [{"source_id": "KPPP", "label": f"Comparative statement {bt['tender_number']}", "url": bt["comparative_url"]}], via))
    wos = sorted((b for b in bills if b.get("wo_date")), key=lambda b: b["wo_date"])
    if wos:
        put(fact("work_order_date", "awarded", wos[0]["wo_date"], "confirmed",
                 f"Work order date on the bill (order no. {wos[0].get('wo_no') or 'n/a'})", [ref(wos[0]["source"], wos[0]["source_row"])]))

    # ---------------- MONEY
    gross = [b for b in bills if b.get("gross") is not None]
    if gross:
        exact = all(b.get("dedupe_key") in ("IFMS", "BR") for b in gross)
        tot = round(sum(b["gross"] for b in gross), 2)
        put(fact("payments_gross", "money", tot, "confirmed" if exact else "inferred",
                 f"Sum of {len(gross)} bill(s) under the job number" +
                 ("" if exact else "; some 2010-18 register rows were de-duplicated by matching amounts, so the total may include a repeat"),
                 grouped_refs(gross)))
        nett = [b for b in bills if b.get("nett") is not None]
        if nett:
            put(fact("payments_nett", "money", round(sum(b["nett"] for b in nett), 2), "confirmed" if exact else "inferred",
                     "Gross minus statutory deductions (tax, security deposit, etc.)", grouped_refs(nett)))
        paid = sorted(b["payment_date"] for b in bills if b.get("payment_date"))
        if paid:
            put(fact("first_payment", "money", paid[0], "confirmed", "Earliest payment (CBR/RTGS) date", grouped_refs(bills[:1])))
            put(fact("last_payment", "money", paid[-1], "confirmed", "Latest payment (CBR/RTGS) date", grouped_refs(bills[-1:])))
        put(fact("n_bills", "money", len(bills), "confirmed", "Unique bills after merging duplicates across exports", grouped_refs(bills)))
        cv = F.get("contract_value", {}).get("value")
        if cv:
            put(fact("paid_vs_contract", "money", round(tot / cv, 3), "inferred",
                     f"Gross paid ÷ contract value (contract value is {F['contract_value']['confidence']})",
                     F["contract_value"]["evidence"] + grouped_refs(gross)[:2], F["contract_value"].get("via")))
    else:
        put(unknown("payments_gross", "money", "No bill or payment for this work found in published BBMP data"))

    # ---------------- OUTCOME
    starts = [b for b in bills if b.get("start_date")]
    if starts:
        s0 = min(starts, key=lambda b: b["start_date"])
        put(fact("start_date", "outcome", s0["start_date"], "confirmed", "Work start date on the bill", [ref(s0["source"], s0["source_row"])]))
    else:
        put(unknown("start_date", "outcome", "Actual start date is not published"))
    final = [b for b in bills if "final" in (b.get("bill_type") or "").lower()]
    running = [b for b in bills if (b.get("bill_type") or "").lower() == "running"]
    if final:
        fb = max(final, key=lambda b: b.get("end_date") or b.get("br_date") or "")
        put(fact("final_bill", "outcome", fb.get("br_date") or fb.get("payment_date") or fb.get("end_date") or "yes", "confirmed",
                 f"A bill of type “{fb['bill_type']}” exists", [ref(fb["source"], fb["source_row"])]))
        put(fact("status", "outcome", "Completed (final bill)", "inferred",
                 "Inferred from the final bill: BBMP raises the final bill when measurement of the completed work is recorded",
                 [ref(fb["source"], fb["source_row"])]))
        if fb.get("end_date"):
            put(fact("completion_date", "outcome", fb["end_date"], "confirmed", "Work end date recorded on the final bill",
                     [ref(fb["source"], fb["source_row"])]))
        else:
            put(unknown("completion_date", "outcome", "Final bill does not carry an end date"))
    elif running:
        rb = running[-1]
        put(fact("status", "outcome", "Running bill only (no final bill found)", "inferred",
                 "Latest bill is a part (running) bill; no final bill in the published data", [ref(rb["source"], rb["source_row"])]))
        put(unknown("completion_date", "outcome", "No final bill found"))
    elif bills:
        put(fact("status", "outcome", "Bills paid; completion not stated", "inferred",
                 "Bills were paid but these exports do not record bill type or completion", grouped_refs(bills)))
        put(unknown("completion_date", "outcome", "Not published"))
    elif last_t:
        st = {"AWARDED": "Tender awarded", "UNDER_EVALUATION": "Tender under evaluation"}.get(last_t.get("status"), last_t.get("status_text"))
        put(fact("status", "outcome", st, conf_t, "Tender status on the procurement portal; no bill found yet in BBMP data",
                 [tender_ref(last_t, "Status")], via))
        put(unknown("completion_date", "outcome", "No bills yet"))
    # expected completion = start reference + stipulated period
    pdays = F.get("period_days", {}).get("value")
    if pdays:
        base_field = next((k for k in ("work_order_date", "awarded_date") if F.get(k, {}).get("value")), None)
        if base_field:
            put(fact("expected_completion", "outcome", add_days(F[base_field]["value"], pdays), "inferred",
                     f"{F[base_field]['value']} ({base_field.replace('_', ' ')}) + {pdays} days stipulated period. "
                     "The contractual start is the notice-to-proceed date, which is not published, so this is approximate",
                     F["period_days"]["evidence"] + F[base_field]["evidence"], F["period_days"].get("via")))
        else:
            put(unknown("expected_completion", "outcome", f"Stipulated period is {pdays} days but no award/work-order date to count from"))
    else:
        put(unknown("expected_completion", "outcome", "Stipulated period of completion not found in any public record"))

    # ---------------- lifecycle completeness
    known = lambda k: F.get(k, {}).get("value") is not None
    stages = {
        "planned": known("estimated_cost") or known("tender_published") or known("sanctioned") or known("planned_amount"),
        "awarded": known("contractor") and (known("contract_value") or known("work_order_date") or known("awarded_date")),
        "money": known("payments_gross"),
        "outcome": known("final_bill") or known("completion_date") or (not bills and known("status")),
    }
    p["facts"] = F
    p["stages"] = stages
    p["tenders"] = all_tenders
    p["tender_numbers"] = sorted({t["tender_number"] for t in all_tenders})
    p["links"] = [dict(group=g[0]["base"], source=g[0]["source"], **{k: ev[k] for k in ("confidence", "score", "text", "support", "conflict")})
                  for g, ev in links + plan_links if ev]
    p["plan_entries"] = [t for g, _ in plan_links for t in g]
    if not bills and prim:
        p["department"] = last_t["dept"]
        p["office"] = last_t.get("office")
        p["first_date"] = first_t["published_date"]
    return p


RULE_TEXT = {"ifms_code": "same IFMS contractor code", "kppp_supplier": "same KPPP supplierId",
             "ifms_mobile": "same registered mobile on BBMP bills (not published) + similar name",
             "name": "same / truncated name", "cross_system_name": "KPPP name matches IFMS payee name"}


def attach_entities(p, ent_by_id):
    """Add a contractor-identity fact: which resolved contractor entity got the work and was paid."""
    F = p["facts"]
    payees = {}
    for b in p["bills"]:
        if b.get("entity"):
            payees[b["entity"]] = payees.get(b["entity"], 0) + (b.get("gross") or 0)
    main = max(payees, key=payees.get) if payees else None
    awarded = next((t["entity"] for t in reversed(p["tenders"]) if t.get("entity")), None)
    eid = main or awarded
    p["contractor_entity"] = eid
    if not eid:
        return
    e = ent_by_id[eid]
    how = ", ".join(f"{RULE_TEXT.get(r, r)} ×{n}" for r, n in e["rules"].items()) or "single name variant"
    ids = ", ".join(e["ids"]) or "no official identifier in the data"
    expl = (f"Resolved contractor {eid} “{e['name']}”; identifiers: {ids}; name variants joined by: {how}.")
    if e["aliases"]:
        expl += f" Also appears as: {', '.join(e['aliases'][:5])}{'…' if len(e['aliases']) > 5 else ''}."
    conf = e["basis"]
    if awarded and main:
        if awarded == main:
            expl += " The awarded bidder on the linked tender resolves to the same contractor as the main payee."
        else:
            expl += f" The awarded bidder on the linked tender resolves to a different entity ({awarded} “{ent_by_id[awarded]['name']}”)."
    F["contractor_identity"] = fact("contractor_identity", "awarded", f"{eid} · {e['name']}", conf, expl,
                                    F.get("contractor", {}).get("evidence", [])[:3])


# ---------------------------------------------------------------- Phase 4: direct BBMP IFMS evidence
def _ifms_ref(b, what):
    return {"source_id": "IFMS", "label": f"BBMP IFMS work bill {b['wbid']}", "url": b["url"], "note": what}


def _doc_ref(x):
    return {"source_id": "IFMS-DOC", "label": f"{x['doc_type']}: {x['doc']} (page {x['page']})", "url": x["url"],
            "note": f"OCR: “{x['snippet'][:140]}”"}


def add_direct(p, info):
    """Overlay direct IFMS evidence: bill types, deductions, approvals, documents, photos."""
    if not info:
        return
    F = p["facts"]
    put = lambda f: F.__setitem__(f["field"], f)
    dbs = info["bills"]
    docs = info["docs"]
    best = lambda field: next((x for x in docs if x["field"] == field), None)
    ocr = " (read by OCR from the scanned document)"

    # ---- contracted
    cands = [x for x in docs if x["field"] == "contract_value" and "pmc" not in (x["doc_type"] or "").lower()
             and not re.search(r"pmc|consult", x["snippet"], re.I)]
    paid_now = F.get("payments_gross", {}).get("value") or 0
    if paid_now:   # an OCR'd "contract value" below 10% of what was actually paid is a misread (EMD, fee, wrong figure)
        cands = [x for x in cands if x["value"] >= 0.1 * paid_now]
    cands.sort(key=lambda x: (not re.search(r"work order|agreement", x["doc_type"] or "", re.I), -x["value"]))
    cv = cands[0] if cands else None
    alts = sorted({round(x["value"]) for x in cands[1:] if abs(x["value"] - cv["value"]) > 0.01 * cv["value"]}) if cv else []
    if cv:
        cur = F.get("contract_value", {})
        agree = cur.get("value") and abs(cur["value"] - cv["value"]) <= 0.005 * cv["value"]
        if not cur.get("value") or agree:
            put(fact("contract_value", "awarded", cv["value"], "confirmed" if agree else "inferred",
                     f"Contract / tender price stated in the {cv['doc_type']}" + ("" if agree else ocr) +
                     (f"; matches {cur.get('explanation', '').lower()}" if agree else "") +
                     (f". Other amounts in this job's work orders: {', '.join(f'₹{a:,}' for a in alts[:3])} (supplementary or other orders)" if alts else ""),
                     [_doc_ref(cv)] + (cur.get("evidence") or [])[:1]))
    for field, label in (("work_order_ref", "Work order"), ("agreement_ref", "Contract agreement"), ("loa_ref", "Letter of acceptance")):
        x = best(field)
        if x:
            put(fact(field, "awarded", f"{x['value']['ref']} dated {x['value']['date'] or '?'}", "inferred",
                     f"{label} reference in the {x['doc_type']}" + ocr, [_doc_ref(x)]))
            if field == "work_order_ref" and x["value"]["date"] and not F.get("work_order_date", {}).get("value"):
                put(fact("work_order_date", "awarded", x["value"]["date"], "inferred", "Date of the work order / notice to proceed" + ocr, [_doc_ref(x)]))
    x = best("period")
    if x and not F.get("period_days", {}).get("value"):
        put(fact("period_days", "planned", x["value"], "inferred", f"Time of completion in the {x['doc_type']}" + ocr, [_doc_ref(x)]))
    for field, label in (("commencement_date", "Date of commencement"), ("completion_due", "Stipulated date of completion")):
        x = best(field)
        if x:
            put(fact(field, "awarded", x["value"], "inferred", f"{label} in the {x['doc_type']}" + ocr, [_doc_ref(x)]))
    if F.get("completion_due", {}).get("value"):
        F["expected_completion"] = dict(F["completion_due"], field="expected_completion", stage="outcome",
                                        explanation="Stipulated completion date written in the work order" + ocr)

    cv_f, pg_f = F.get("contract_value", {}), F.get("payments_gross", {})
    if cv_f.get("value") and pg_f.get("value") and not F.get("paid_vs_contract", {}).get("value"):
        put(fact("paid_vs_contract", "money", round(pg_f["value"] / cv_f["value"], 3), "inferred",
                 f"Gross paid ÷ contract value (contract value is {cv_f['confidence']}; it may cover only part of the job, e.g. one of several work orders)",
                 (cv_f.get("evidence") or [])[:1] + (pg_f.get("evidence") or [])[:2]))

    # ---- work performed (documents of record)
    recd = [b for b in dbs if b.get("in_records")]
    finals = [b for b in recd if "final" in (b["bill_type"] or "").lower()]
    running = [b for b in recd if (b["bill_type"] or "").lower() == "running"]
    unpaid_final = [b for b in (info.get("pending") or []) if "final" in (b["bill_type"] or "").lower()]
    if dbs:
        put(fact("bill_types", "work", f"{len(running)} running, {len(finals)} final paid/registered"
                 + (f"; {len(info.get('pending') or [])} unpaid" if info.get("pending") else ""), "confirmed",
                 "Bill types recorded in BBMP IFMS for this job's work bills", [_ifms_ref(b, b['bill_type']) for b in dbs[:4]]))
    kinds = defaultdict_list()
    seen_files = set()
    for b in dbs:
        for f in b["bill_files"] + b["wo_files"]:
            if f["rFileName"] in seen_files:
                continue
            seen_files.add(f["rFileName"])
            kinds[(f.get("rFileType") or "other").strip().rstrip(".")].append((b, f))
    work_docs = {k: v for k, v in kinds.items() if re.search(r"measurement|\bm\.?\s?b\b|completion|quality|inspection|lab|test|road history", k, re.I)}
    from ingest import documents as _docs

    def _placeholder(f):
        path = _docs.FILES / f["rFileName"].replace("/", "_")
        return bool(re.search(r"\b(e?mp?ty|emty|blank|dummy)\b", f["rFileName"], re.I)) or (path.exists() and path.stat().st_size < 8000)

    def _desc(k, v):
        ph = sum(1 for _, f in v if _placeholder(f))
        return f"{k} ×{len(v)}" + (f" ({ph} placeholder)" if ph else "")
    if work_docs:
        put(fact("work_documents", "work", "; ".join(_desc(k, v) for k, v in sorted(work_docs.items())), "confirmed",
                 "Documents attached to the bills in IFMS (their presence is recorded; contents are not verified)",
                 [{"source_id": "IFMS-DOC", "label": f"{k}: {v[0][1]['rFileName']}", "url": v[0][1].get("_url")} for k, v in work_docs.items()][:4]))
    x = best("completed_on")
    if x:
        put(fact("completed_on_certificate", "outcome", x["value"], "inferred", f"Completion date stated in the {x['doc_type']}" + ocr, [_doc_ref(x)]))

    # ---- completion (IFMS bill type is the official record)
    if finals and not F.get("final_bill", {}).get("value"):
        fb = max(finals, key=lambda b: b["br_date"] or "")
        put(fact("final_bill", "outcome", fb["br_date"] or fb["rtgs_date"] or "yes", "confirmed",
                 f"IFMS records bill type “{fb['bill_type']}” for work bill {fb['wbid']}", [_ifms_ref(fb, "Bill type")]))
        put(fact("status", "outcome", "Completed (final bill)", "inferred",
                 "Inferred from the final bill recorded in BBMP IFMS", [_ifms_ref(fb, "Bill type")]))
    elif unpaid_final and not F.get("final_bill", {}).get("value"):
        ub = unpaid_final[0]
        put(fact("status", "outcome", "Final bill submitted, not yet paid", "inferred",
                 f"IFMS holds a “{ub['bill_type']}” bill (work bill {ub['wbid']}) with no RTGS payment; level: {ub['level']}",
                 [_ifms_ref(ub, "Unpaid final bill")]))
    elif running and not finals and F.get("status", {}).get("value", "").startswith("Bills paid"):
        rb = max(running, key=lambda b: b["br_date"] or "")
        put(fact("status", "outcome", "Running bill only (no final bill in IFMS)", "inferred",
                 "All IFMS bills for this job are running (part) bills", [_ifms_ref(rb, "Bill type")]))

    pend = info.get("pending") or []
    if pend:
        lv = Counter(b["level"] or "?" for b in pend)
        put(fact("pending_bills", "money", f"{len(pend)} bill(s) not paid (₹{sum(b['gross'] or 0 for b in pend):,.0f}); at: "
                 + ", ".join(f"{k} ×{v}" for k, v in lv.most_common(3)), "confirmed",
                 "Bills present in IFMS without an RTGS payment (drafts or bills awaiting approval); not counted as paid",
                 [_ifms_ref(b, "Pending bill") for b in pend[:3]]))

    # ---- money details
    partial = [b for b in dbs if b["release_pct"] and b["release_pct"] < 100]
    if partial:
        put(fact("partial_release", "money", f"{len(partial)} bill(s) released partly ({', '.join(str(int(b['release_pct'])) + '%' for b in partial[:3])})",
                 "confirmed", "Release percentage recorded in IFMS (BBMP pays bills in tranches when funds are short)",
                 [_ifms_ref(b, "Release %") for b in partial[:3]]))
    pen = {k: sum(b["deductions"].get(k, 0) for b in dbs) for k in ("fine", "withheld", "audit_recovery", "mobilizationadvance")}
    pen = {k: v for k, v in pen.items() if v}
    if pen:
        put(fact("penalties_deductions", "money", "; ".join(f"{k.replace('_', ' ')} ₹{v:,.0f}" for k, v in pen.items()), "confirmed",
                 "Deduction heads recorded in IFMS across this job's bills",
                 [_ifms_ref(b, "Deductions") for b in dbs if any(b["deductions"].get(k) for k in pen)][:3]))
    lags = []
    for b in dbs:
        if b["br_date"] and b["rtgs_date"]:
            lags.append((date.fromisoformat(b["rtgs_date"]) - date.fromisoformat(b["br_date"])).days)
    if lags:
        put(fact("bill_to_payment_days", "money", int(sorted(lags)[len(lags) // 2]), "confirmed",
                 f"Median days from bill registration to RTGS payment over {len(lags)} IFMS bill(s)", [_ifms_ref(dbs[0], "BR / RTGS dates")]))
    remarks = [(b["wbid"], a) for b in dbs for a in b["approvals"] if a["remarks"] and len(a["remarks"]) > 25]
    if remarks:
        wb, a = remarks[0]
        put(fact("approval_notes", "money", f"{len(remarks)} remark(s); e.g. {a['date']}: “{a['remarks'][:160]}”", "confirmed",
                 "Remarks written by officials in the IFMS approval chain", [_ifms_ref(next(b for b in dbs if b['wbid'] == wb), "Approval chain")]))

    # ---- physical evidence
    ph_all = info["photos"]
    ph = [x for x in ph_all if not x.get("placeholder")]
    holders = [x for x in ph_all if x.get("placeholder")]
    if holders:
        put(fact("placeholder_photos", "evidence", f"{len(holders)} photo slot(s) filled with a blank / placeholder file", "confirmed",
                 "Photo attachments whose file name says empty/blank or whose file is a few KB",
                 [{"source_id": "IFMS-DOC", "label": x["file"], "url": x["url"]} for x in holders[:3]]))
    if ph:
        geo = [x for x in ph if x["gps"]]
        dl = [x for x in ph if x.get("downloaded")]
        dated = sorted(x["image_time"][:10] for x in dl if x.get("image_time") and not x.get("scanned_sheet"))
        val = f"{len(ph)} site photo(s) attached (before / during / after as labelled)" + (f"; {len(geo)} with GPS printed on the image" if geo else "; no GPS on any photo checked")
        if dated:
            val += f"; camera dates {dated[0]} – {dated[-1]}"
        put(fact("site_photos", "evidence", val, "confirmed",
                 "Photos attached to the bills in IFMS (type as labelled by the uploader)",
                 [{"source_id": "IFMS-DOC", "label": f"{x['type']}: {x['file']}", "url": x["url"]} for x in ph[:4]]))
        if geo:
            lat = sum(x["gps"]["lat"] for x in geo) / len(geo)
            lon = sum(x["gps"]["lon"] for x in geo) / len(geo)
            put(fact("photo_location", "evidence", f"{lat:.5f}, {lon:.5f}", "inferred",
                     f"Average of GPS coordinates printed on {len(geo)} site photo(s) (read by OCR; shows where the photos were taken)",
                     [{"source_id": "IFMS-DOC", "label": x["file"], "url": x["url"]} for x in geo[:3]]))
    p["direct"] = info


def defaultdict_list():
    from collections import defaultdict
    return defaultdict(list)


def lifecycle(p):
    """Six-stage lifecycle: planned -> contracted -> work performed -> payments -> completion -> physical evidence."""
    F = p["facts"]
    k = lambda f: F.get(f, {}).get("value") is not None
    p["stages"] = {
        "planned": k("estimated_cost") or k("tender_published") or k("sanctioned") or k("planned_amount"),
        "awarded": k("contractor") and (k("contract_value") or k("work_order_date") or k("awarded_date") or k("work_order_ref")),
        "work": k("bill_types") or k("work_documents") or k("final_bill"),
        "money": k("payments_gross"),
        "outcome": k("final_bill") or k("completion_date") or k("completed_on_certificate") or (not p["bills"] and k("status")),
        "evidence": k("site_photos") or F.get("imagery", {}).get("value", "").startswith("Change"),
    }
