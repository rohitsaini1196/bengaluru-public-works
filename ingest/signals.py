"""Signals: patterns in the public record that are worth a closer look.

A signal is NOT an allegation. Each one states what the data shows, how it was computed and the
evidence, and carries the confidence of the facts it rests on (a signal built on an inferred
tender<->bill link is itself inferred).

severity: info (context) | notice (unusual) | attention (strongly unusual against the record)
"""
import re
from collections import Counter, defaultdict
from datetime import date

TODAY = date(2026, 10, 5)
DATA_END = date(2026, 3, 31)  # latest BBMP bill export


def _d(s):
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


def sig(code, severity, title, detail, confidence="confirmed", evidence=None, related=None):
    return {"code": code, "severity": severity, "title": title, "detail": detail, "confidence": confidence,
            "evidence": evidence or [], "related": related or []}


def worst(*confs):
    order = {"confirmed": 0, "inferred": 1, "unknown": 2}
    return max(confs, key=lambda c: order.get(c, 2))


def project_signals(p):
    F, out = p["facts"], []
    v = lambda k: F.get(k, {}).get("value")
    c = lambda k: F.get(k, {}).get("confidence", "unknown")
    ev = lambda *ks: [e for k in ks for e in F.get(k, {}).get("evidence", [])][:6]

    # 1. paid more than contract / estimate
    if v("paid_vs_contract") and v("paid_vs_contract") > 1.25:   # up to ~118% is GST on a contract value quoted excluding GST
        out.append(sig("paid_over_contract", "attention" if v("paid_vs_contract") > 1.6 else "notice",
                       f"Paid {v('paid_vs_contract'):.0%} of contract value",
                       f"Gross payments ₹{v('payments_gross'):,.0f} exceed the contract value ₹{v('contract_value'):,.0f} by more than "
                       "GST would explain (work-order values are often quoted excluding 12–18% GST). Variations, work slips, "
                       "escalation, further work orders under the same job or a longer service period can explain the rest.",
                       worst(c("contract_value"), c("payments_gross")), ev("contract_value", "payments_gross")))
    elif v("estimated_cost") and v("payments_gross") and not v("contract_value") and v("payments_gross") > 1.25 * v("estimated_cost"):
        r = v("payments_gross") / v("estimated_cost")
        out.append(sig("paid_over_estimate", "notice", f"Paid {r:.0%} of the estimate",
                       f"Gross payments ₹{v('payments_gross'):,.0f} vs estimate ₹{v('estimated_cost'):,.0f}.",
                       worst(c("estimated_cost"), c("payments_gross")), ev("estimated_cost", "payments_gross")))

    # 1b. front-loaded payment: most of the contract paid early in the time allowed
    award = _d(v("work_order_date") or v("awarded_date"))
    first_pay = _d(v("first_payment"))
    if v("paid_vs_contract") and v("paid_vs_contract") >= 0.75 and award and first_pay and v("period_days"):
        lag = (_d(v("last_payment")) - award).days
        if 0 <= lag < 0.4 * v("period_days"):
            out.append(sig("front_loaded_payment", "notice",
                           f"{v('paid_vs_contract'):.0%} paid within {lag} days of a {v('period_days')}-day contract",
                           "Most of the contract value was paid early in the time allowed. This can be legitimate (fast "
                           "execution, mobilisation advance) but is worth checking against measurement records.",
                           worst(c("paid_vs_contract"), c("period_days")), ev("paid_vs_contract", "period_days")))

    # 2. duration
    # the stipulated period belongs to the contract it was written in: count from that contract's commencement
    start = _d(v("commencement_date") or v("work_order_date") or v("start_date") or v("awarded_date"))
    end = _d(v("completion_date") or v("last_payment"))
    pdays = v("period_days")
    ext = _d(v("extended_completion"))
    if pdays and start and ext and (ext - start).days > pdays:
        pdays = (ext - start).days            # an extension of time lengthens the allowed period
    if start and end and (end - start).days > 0:
        span = (end - start).days
        if pdays and span > 2 * pdays and span - pdays > 180:
            out.append(sig("over_stipulated_period", "notice",
                           f"Activity spans {span // 30} months vs {pdays // 30 or 1}-month stipulated period",
                           f"From {start} to {end} ({'completion' if v('completion_date') else 'last payment'}) against a "
                           f"stipulated {pdays} days. Late final payments also stretch this span.",
                           worst(c("period_days"), "confirmed"), ev("period_days", "completion_date", "last_payment")))
        elif span > 3 * 365:
            out.append(sig("long_running", "notice", f"Activity spread over {span / 365:.1f} years",
                           f"First record {start}, last {end}. Long gaps can mean phased work, delays or late bill settlement.",
                           "confirmed", ev("work_order_date", "start_date", "last_payment")))

    # 3. overdue / stale without completion
    exp = _d(v("expected_completion"))
    has_final = v("final_bill") is not None
    bill_types_known = any(b.get("bill_type") for b in p["bills"])
    if exp and not has_final and bill_types_known and (DATA_END - exp).days > 90:
        out.append(sig("past_expected_completion", "notice",
                       f"No final bill though expected completion ({exp}) has passed",
                       "Expected completion = work-order/award date + stipulated period. Bills for this work carry bill "
                       "types, and none is a final bill.", "inferred", ev("expected_completion", "status")))
    elif exp and not p["bills"] and v("awarded_date") and (DATA_END - exp).days > 180:
        out.append(sig("no_bills_after_expected_completion", "info",
                       f"No bill found though expected completion ({exp}) is 6+ months before the data ends",
                       "BBMP bill exports run to about Mar 2026; a finished work would normally show a bill by then. "
                       "The bill may be booked under a job number we could not link.", "inferred", ev("expected_completion")))
    last = _d(v("last_payment") or p.get("first_date"))
    if p["bills"] and not has_final and last and (TODAY - last).days > 3 * 365 and \
            v("status") == "Running bill only (no final bill found)":
        out.append(sig("stale_running", "notice", "Running bill but no final bill in 3+ years",
                       f"Last bill/payment {last}. The work may be finished with the final bill outside these exports, or stalled.",
                       "inferred", ev("status", "last_payment")))

    # 4. tender re-calls
    if (v("tender_calls") or 1) >= 3:
        out.append(sig("recalled_tender", "notice" if v("tender_calls") < 5 else "attention",
                       f"Tender called {v('tender_calls')} times",
                       "Re-calls usually mean no or too few valid bids on earlier calls, which can delay the work and "
                       "reduce competition.", c("tender_calls"), ev("tender_calls")))
    elif v("tender_calls") == 2:
        out.append(sig("recalled_tender", "info", "Tender re-called once (CALL-2)", "First call did not result in an award.",
                       c("tender_calls"), ev("tender_calls")))

    # 5. competition
    if v("bidders") == 1:
        out.append(sig("single_bidder", "notice", "Only one bidder", "The commercial comparative statement lists a single bidder.",
                       c("bidders"), ev("bidders")))
    same_tender = lambda: (F.get("contract_value", {}).get("evidence") or [{}])[0].get("source_id") in ("KPPP",) and \
        (F.get("estimated_cost", {}).get("evidence") or [{}])[0].get("source_id") in ("KPPP",)
    if v("contract_value") and v("estimated_cost") and same_tender():   # only compare award and estimate of the same tender
        r = v("contract_value") / v("estimated_cost") - 1
        if r > 0.10:
            out.append(sig("award_above_estimate", "notice", f"Awarded {r:+.0%} vs estimate",
                           f"Contract ₹{v('contract_value'):,.0f} vs estimate ₹{v('estimated_cost'):,.0f}.",
                           worst(c("contract_value"), c("estimated_cost")), ev("contract_value", "estimated_cost")))
        elif r < -0.25:
            out.append(sig("deep_discount", "info", f"Awarded {r:+.0%} below estimate",
                           "Very low bids can strain execution or lead to variations later.",
                           worst(c("contract_value"), c("estimated_cost")), ev("contract_value", "estimated_cost")))

    # 5b. bidders on the same tender whose personal names overlap
    for t in p.get("tenders", []):
        bs = t.get("bidders") or []
        persons = []
        for b in bs:
            person = re.sub(r"\(.*", "", b["name"]).lower()
            toks = {w for w in re.findall(r"[a-z]{5,}", person)}
            persons.append((b, toks))
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                (a, ta), (b, tb) = persons[i], persons[j]
                if ta and tb and (ta <= tb or tb <= ta):
                    out.append(sig("bidder_name_overlap", "info",
                                   f"Two bidders with overlapping names on {t['tender_number']}",
                                   f"“{a['name']}” ({a['rank']}) and “{b['name']}” ({b['rank']}) both bid. Shared names are "
                                   "common and do not mean the bidders are related.",
                                   "confirmed" if not p["bills"] or not p["links"] else "inferred",
                                   [{"source_id": "KPPP", "label": "Comparative statement", "url": t.get("comparative_url")}]))
                    break

    # 5c. payment timing against the award (also a consistency check on matched links)
    aw = _d(v("awarded_date") or v("work_order_date"))
    early = [b for b in p["bills"] if b.get("payment_date") and aw and (_d(b["payment_date"]) - aw).days < -30]
    if early and v("awarded_date"):
        out.append(sig("paid_before_award", "notice", f"{len(early)} payment(s) dated before the tender award",
                       f"Earliest payment {min(b['payment_date'] for b in early)} vs award {aw}. Either the tender↔bill link "
                       "is wrong, or bills for this job pre-date the tender (re-tendered balance work?).",
                       worst(c("awarded_date"), "confirmed"), ev("awarded_date", "first_payment")))
    # 5d. financial-year-end payments
    gross = sum(b.get("gross") or 0 for b in p["bills"] if b.get("payment_date"))
    march = sum(b.get("gross") or 0 for b in p["bills"] if b.get("payment_date") and b["payment_date"][5:7] == "03"
                and int(b["payment_date"][8:10]) >= 15)
    if gross >= 1e6 and march >= 0.5 * gross:
        out.append(sig("year_end_payment", "info", f"{march / gross:.0%} of payments made in the last fortnight of March",
                       "Payments bunched at financial-year end (budget lapse pressure) are common; worth checking that "
                       "measurement of the work preceded payment.", "confirmed", ev("payments_gross")[:2]))

    # 5e. direct IFMS evidence
    due = _d(v("completion_due"))
    if due and v("final_bill") is None:
        last_bill = max((_d(b.get("br_date") or b.get("payment_date")) for b in p["bills"] if (b.get("br_date") or b.get("payment_date"))), default=None)
        ext = _d(v("extended_completion"))
        eff = max(due, ext) if ext else due
        if ext and ext >= due:
            eot_note = f" An extension of time in IFMS moves completion to {ext}."
        elif ext:
            eot_note = (f" Extension-of-time orders in IFMS cover an earlier period (latest to {ext}); none is visible for "
                        f"the {due} date.")
        else:
            eot_note = " No extension-of-time order is attached in IFMS (one may exist on paper)."
        if (DATA_END - eff).days > 90:
            out.append(sig("past_stipulated_completion", "notice",
                           f"{'Extended' if eff != due else 'Stipulated'} completion {eff} has passed; no final bill in IFMS",
                           f"The work order fixes completion on {due}. IFMS shows no final bill"
                           + (f"; latest bill {last_bill}." if last_bill else ".") + eot_note,
                           worst(c("completion_due"), "confirmed"), ev("completion_due", "time_extensions", "bill_types")))
    if v("penalties_deductions") and "fine" in str(v("penalties_deductions")):
        out.append(sig("fine_levied", "info", "Fine / penalty deducted from a bill", f"IFMS deductions: {v('penalties_deductions')}. "
                       "Penalties usually relate to delay or quality; their reason is not in the public record.", "confirmed",
                       ev("penalties_deductions")))
    if v("placeholder_photos"):
        out.append(sig("placeholder_photos", "notice", "Blank / placeholder file used as a site photo",
                       f"{v('placeholder_photos')}. Bills require before/during/after photos; a placeholder means the "
                       "photo record for this work is missing.", "confirmed", ev("placeholder_photos")))
    if v("partial_release"):
        out.append(sig("partial_release", "info", "Bill paid in part (release % below 100)", f"{v('partial_release')}. BBMP releases "
                       "bills in tranches when funds are short; the balance may follow later.", "confirmed", ev("partial_release")))
    if v("bill_to_payment_days") and v("bill_to_payment_days") > 365:
        out.append(sig("slow_payment", "info", f"Median {v('bill_to_payment_days')} days from bill registration to payment",
                       "Long payment delays are common (seniority-based bill clearance) and raise contractors' financing costs.",
                       "confirmed", ev("bill_to_payment_days")))
    for x in (p.get("direct") or {}).get("photos", []):
        g = x.get("gps")
        if g and not (12.905 <= g["lat"] <= 12.965 and 77.595 <= g["lon"] <= 77.655):
            out.append(sig("photo_gps_outside_area", "notice", "Site photo GPS is outside Koramangala",
                           f"{x['file']} ({x['type']}) carries coordinates {g['lat']}, {g['lon']}. Could be an OCR misread, a photo "
                           "of a related site, or a wrongly attached photo.", "inferred",
                           [{"source_id": "IFMS-DOC", "label": x["file"], "url": x["url"]}]))
            break

    # 6. payment patterns
    bills = p["bills"]
    days = Counter(b["payment_date"] for b in bills if b.get("payment_date"))
    burst = [(d, n) for d, n in days.items() if n >= 3]
    if burst:
        d, n = max(burst, key=lambda x: x[1])
        out.append(sig("payment_burst", "info", f"{n} bills paid on the same day ({d})",
                       "Several bills settled together, often at financial-year end or when funds are released.", "confirmed",
                       [e for e in F.get("n_bills", {}).get("evidence", [])][:3]))
    finals = [b for b in bills if "final" in (b.get("bill_type") or "").lower()]
    if finals:
        fdate = min(b.get("br_date") or b.get("payment_date") or "9999" for b in finals)
        after = [b for b in bills if (b.get("br_date") or "") > fdate and "final" not in (b.get("bill_type") or "").lower()]
        if after:
            out.append(sig("bills_after_final", "notice", f"{len(after)} bill(s) registered after the final bill",
                           f"Final bill registered {fdate}; later non-final bills exist.", "confirmed", ev("final_bill")))
    big_ded = [b for b in bills if b.get("gross") and b.get("deduction") and b["deduction"] > 0.3 * b["gross"]]
    if big_ded:
        b = max(big_ded, key=lambda x: x["deduction"])
        out.append(sig("large_deduction", "info", f"Deduction of {b['deduction'] / b['gross']:.0%} on a bill",
                       f"₹{b['deduction']:,.0f} withheld from a ₹{b['gross']:,.0f} bill. Usually recoveries, penalties, "
                       "escrow or security deposit.", "confirmed", ev("payments_gross")[:2]))
    if v("payments_gross") and v("payments_gross") >= 1e5:
        late = [b for b in bills if b.get("end_date") and b.get("payment_date") and
                (_d(b["payment_date"]) - _d(b["end_date"])).days > 2 * 365]
        if late:
            b = max(late, key=lambda x: (_d(x["payment_date"]) - _d(x["end_date"])).days)
            out.append(sig("late_payment", "info",
                           f"Bill paid {(_d(b['payment_date']) - _d(b['end_date'])).days // 365} years after work end date",
                           "Long payment lag; common with BBMP bill backlogs (seniority-based payment).", "confirmed",
                           ev("payments_gross")[:2]))
    return out


def dataset_signals(projects, related, ents=None, kppp=(), m2e=None):
    """Signals that need the whole Koramangala dataset, computed on resolved contractor entities."""
    ents = ents or {}
    by_ent = defaultdict(list)
    for p in projects:
        if p.get("contractor_entity"):
            by_ent[p["contractor_entity"]].append(p)
    money = lambda p: (p["facts"].get("payments_gross", {}).get("value") or 0) or (p["facts"].get("contract_value", {}).get("value") or 0)
    total = sum(money(p) for p in projects) or 1
    summary = []
    # bidding behaviour across every Koramangala KPPP tender with a comparative statement
    bids = defaultdict(lambda: {"bids": 0, "wins": 0, "single_wins": 0, "tenders": []})
    pairs = Counter()
    pair_tenders = defaultdict(list)
    for t in kppp:
        bs = [b for b in (t.get("bidders") or []) if b.get("entity")]
        ids = sorted({b["entity"] for b in bs})
        for b in bs:
            r = bids[b["entity"]]
            r["bids"] += 1
            r["tenders"].append(t["tender_number"])
            if b.get("rank") == "L1":
                r["wins"] += 1
                if len(bs) == 1:
                    r["single_wins"] += 1
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs[(ids[i], ids[j])] += 1
                pair_tenders[(ids[i], ids[j])].append(t["tender_number"])
    for eid, ps in by_ent.items():
        e = ents.get(eid, {"name": eid, "aliases": [], "ids": [], "basis": "unknown"})
        amt = sum(money(p) for p in ps)
        share = amt / total
        b = bids.get(eid, {"bids": 0, "wins": 0, "single_wins": 0})
        summary.append({"contractor": e["name"], "entity": eid, "aliases": e["aliases"], "ids": e["ids"], "basis": e["basis"],
                        "projects": len(ps), "amount": amt, "share": share, "bids": b["bids"], "wins": b["wins"],
                        "single_wins": b["single_wins"], "slugs": [p["slug"] for p in ps]})
        if len(ps) >= 2 and (share >= 0.05 or len(ps) >= 6):
            for p in ps:
                p["signals"].append(sig("contractor_concentration", "info",
                                        f"Contractor holds {share:.0%} of Koramangala money ({len(ps)} projects)",
                                        f"{eid} {e['name']} (identity {e['basis']}; ids: {', '.join(e['ids']) or 'none'}): "
                                        f"₹{amt:,.0f} across {len(ps)} projects in this dataset. Concentration is common for ward "
                                        "maintenance contracts; it is context, not a finding.",
                                        e["basis"] if e["basis"] != "unknown" else "inferred",
                                        related=[q["slug"] for q in ps if q is not p][:10]))
    # frequent co-bidders: the same two contractors meet on 3+ tenders
    slug_by_tender = {t["tender_number"]: p["slug"] for p in projects for t in p["tenders"]}
    co_bidding = []
    for (a, b2), n in pairs.items():
        if n < 3:
            continue
        ts = pair_tenders[(a, b2)]
        wins_a = sum(1 for t in kppp if t["tender_number"] in ts for x in t.get("bidders") or [] if x.get("entity") == a and x.get("rank") == "L1")
        wins_b = sum(1 for t in kppp if t["tender_number"] in ts for x in t.get("bidders") or [] if x.get("entity") == b2 and x.get("rank") == "L1")
        na, nb = ents.get(a, {}).get("name", a), ents.get(b2, {}).get("name", b2)
        co_bidding.append({"a": na, "b": nb, "a_id": a, "b_id": b2, "tenders": n, "a_wins": wins_a, "b_wins": wins_b, "list": ts})
        for tn in ts:
            p = next((q for q in projects if q["slug"] == slug_by_tender.get(tn)), None)
            if p:
                p["signals"].append(sig("frequent_co_bidders", "notice" if n >= 4 else "info",
                                        f"Same two bidders met on {n} Koramangala tenders",
                                        f"{na} and {nb} both bid on {n} tenders ({na} won {wins_a}, {nb} won {wins_b}). Repeated "
                                        "head-to-head bidding between the same firms limits competition and is worth checking "
                                        "against the full bidder list; it does not by itself indicate collusion.",
                                        "confirmed" if all(ents.get(x, {}).get("basis") == "confirmed" for x in (a, b2)) else "inferred",
                                        [{"source_id": "KPPP", "label": "Comparative statement", "url": t.get("comparative_url")}
                                         for t in kppp if t["tender_number"] == tn and t.get("comparative_url")][:1]))
    # serial single-bid winners
    for eid, r in bids.items():
        if r["single_wins"] >= 2:
            e = ents.get(eid, {"name": eid})
            for p in projects:
                if any(t.get("entity") == eid and len(t.get("bidders") or []) == 1 for t in p["tenders"]):
                    p["signals"].append(sig("repeat_single_bid_winner", "notice",
                                            f"Contractor won {r['single_wins']} Koramangala tenders as the only bidder",
                                            f"{e['name']} bid on {r['bids']} tenders and won {r['wins']}; {r['single_wins']} of the wins "
                                            "had no competing bid.", "confirmed"))
    for a, b, s in related:
        for x, y in ((a, b), (b, a)):
            x["signals"].append(sig("related_work", "info",
                                    f"Near-identical work under another number ({y['job_numbers'][0] if y['job_numbers'] else y['tender_numbers'][0]})",
                                    f"Descriptions are {s:.0%} similar. Could be a recurring annual work, a re-tender, a "
                                    "split of one work, or a duplicate entry.", "inferred", related=[y["slug"]]))
    # the same attachment (photo / work order / agreement / completion report) under different job numbers
    seen = defaultdict(lambda: {"slugs": set(), "items": []})
    for p in projects:
        for md5, items in ((p.get("direct") or {}).get("file_hashes") or {}).items():
            seen[md5]["slugs"].add(p["slug"])
            seen[md5]["items"] += [(p["slug"],) + tuple(i) for i in items]
    PLACEHOLDER = re.compile(r"\b(e?mp?ty|emty|blank|dummy|na)\b", re.I)
    for md5, d in seen.items():
        if len(d["slugs"]) < 2:
            continue
        types = sorted({t for _, t, *_ in d["items"]})
        files = [f for _, _, f, *_ in d["items"]]
        tiny = all(b < 8000 for *_, b in d["items"])
        placeholder = tiny or all(PLACEHOLDER.search(f) for f in files)
        is_photo = all("photo" in t.lower() for t in types)
        if placeholder:
            code, sev, title = "placeholder_reused", "info", "Same placeholder file uploaded as evidence on several works"
        elif is_photo:
            code, sev, title = "photo_reused", "notice", "Identical site photo attached to another work"
        else:
            code, sev, title = "document_reused", "notice", f"Identical {' / '.join(types)} attached to another job number"
        for p in projects:
            if p["slug"] in d["slugs"]:
                others = sorted(d["slugs"] - {p["slug"]})
                url = next((u for sl, _, _, u, _ in d["items"] if sl == p["slug"]), None)
                p["signals"].append(sig(code, sev, title,
                                        f"The same file (md5 {md5[:10]}…; {', '.join(types)}) is attached to bills of {len(d['slugs'])} "
                                        f"works: {', '.join(others[:4])}. "
                                        + ("A placeholder means the real record is missing." if placeholder else
                                           "Could be one contract booked under two job numbers, or a wrong upload — worth checking."),
                                        "confirmed", [{"source_id": "IFMS-DOC", "label": "Attachment", "url": url}] if url else [],
                                        related=others[:5]))
    summary.sort(key=lambda r: -r["amount"])
    return {"contractors": summary, "co_bidding": sorted(co_bidding, key=lambda r: -r["tenders"])}


def _canon(name):
    import re
    n = re.sub(r"[^a-z ]", " ", name.lower())
    n = re.sub(r"\b(m s|ms|sri|shri|the|pvt|ltd|limited)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()
