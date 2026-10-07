"""Build data/koramangala.db — Phase 2 "Project Truth" pipeline.

  1. bills      parse every BBMP bill export, keep Koramangala public works, de-duplicate (ingest.bills)
  2. projects   group bills by BBMP job number
  3. tenders    KPPP tenders (+ tender documents, comparative statements) and BBMP tender lists 2013-18
                (ingest.tenders); re-calls of one tender number form one group
  4. match      score every tender group against every job project and accept confident links (ingest.match)
  5. truth      assemble facts per project with confidence + evidence (ingest.truth)
  6. select     keep up to 200 projects (value / recency rule)
  7. signals    per-project and dataset-wide signals (ingest.signals)
  8. write      SQLite

Usage: python -m ingest.build
"""
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from ingest import quantities as QTY
from ingest import eot as EOT
from ingest import fetch_bbmp_direct
from ingest import direct as DIR
from ingest import bills as B, contractors as CTR, match, plans as PL, scope, signals, tenders as T, truth
from ingest.sources import KPPP_API, KPPP_PORTAL

ROOT = Path(__file__).resolve().parent.parent
import os
DB = Path(os.environ.get("KORA_DB_OUT", ROOT / "data" / "koramangala.db"))
NO_DIRECT = os.environ.get("KORA_NO_DIRECT") == "1"   # rebuild without IFMS direct evidence (for comparison)
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

MAX_PROJECTS = None   # Phase 5: no cap — every in-scope project worth >= MIN_VALUE
MIN_VALUE = 500_000          # ₹5 lakh: below this ward petty works dominate
RECENT_FROM = "2022-01-01"

# re-exported for tests / callers
base_tender = T.base_tender
dedupe_bills = B.dedupe_bills


def most_common(values):
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else None


def job_skeleton(job, bills):
    bills = sorted(bills, key=lambda b: (b.get("br_date") or b.get("wo_date") or b.get("payment_date") or "", b.get("source_row") or 0))
    descs = [b["description"] for b in bills if b.get("description")]
    title = max(Counter(descs).most_common(3), key=lambda kv: (kv[1], len(kv[0])))[0] if descs else job
    wards = sorted({f'{b["ward"].lstrip("0")} ({b["regime"]}-ward map)' for b in bills
                    if b["regime"] in ("198", "225", "243") and (b.get("ward") or "").isdigit() and int(b["ward"]) <= 243})
    dates = [d for b in bills for d in (b.get("wo_date"), b.get("start_date"), b.get("br_date"), b.get("payment_date")) if d]
    return dict(key=f"job:{job}", job_numbers=[job], title=title, descriptions=sorted(set(descs)),
                category=scope.classify(title) and most_common([b.get("category") for b in bills]),
                bills=bills, wards=wards, office=most_common([b.get("office") for b in bills]),
                department="Bruhat Bengaluru Mahanagara Palike", scope_reason=next((b["scope_reason"] for b in bills if b.get("scope_reason")), None),
                first_date=min(dates) if dates else None, signals=[])


def tender_skeleton(group):
    t = group[-1]
    return dict(key=f"tender:{t['base']}", job_numbers=[], title=t["description"],
                descriptions=sorted({x["description"] for x in group}), category=t["category"], bills=[], wards=[],
                office=t.get("office"), department=t["dept"], scope_reason=t["scope_reason"],
                first_date=group[0]["published_date"], signals=[])


def fv(p, k):
    return p["facts"].get(k, {}).get("value")


def project_value(p):
    vals = [fv(p, k) for k in ("payments_gross", "contract_value", "estimated_cost")]
    return max([v for v in vals if isinstance(v, (int, float))] or [0])


def last_activity(p):
    ds = [fv(p, "last_payment"), p["first_date"], fv(p, "tender_published"), fv(p, "awarded_date"),
          max((b.get("br_date") or "" for b in p["bills"]), default=None)]
    return max([d for d in ds if d] or [""])


def select(projects):
    """1. drop projects worth < MIN_VALUE (largest of paid / contract / estimate);
       2. keep every project whose tender and bills are both known (full lifecycle);
       3. keep every project with activity since RECENT_FROM;
       4. fill up to MAX_PROJECTS with older projects, largest value first."""
    eligible = [p for p in projects if project_value(p) >= MIN_VALUE]
    # depth first: projects whose tender AND bills are both known tell the whole story
    deep = [p for p in eligible if p["bills"] and p["tenders"]]
    rest = [p for p in eligible if p not in deep]
    recent = sorted((p for p in rest if last_activity(p) >= RECENT_FROM), key=last_activity, reverse=True)
    older = sorted((p for p in rest if last_activity(p) < RECENT_FROM), key=project_value, reverse=True)
    chosen = (deep + recent + older)[:MAX_PROJECTS] if MAX_PROJECTS else (deep + recent + older)
    chosen.sort(key=last_activity, reverse=True)
    return chosen, len(eligible)


REALITY = ROOT / "data" / "reality"
VERDICT_TEXT = {"visible_change": "Change consistent with the work is visible", "no_visible_change": "No change visible",
                "inconclusive": "Inconclusive", "not_geocoded": "Location could not be pinned down"}


def attach_imagery(projects):
    """Experimental: manual before/after review of Esri World Imagery Wayback captures (data/reality)."""
    summ = REALITY / "summary.json"
    ass = REALITY / "assessments.json"
    if not summ.exists():
        return
    summary = json.loads(summ.read_text())
    assessments = json.loads(ass.read_text()) if ass.exists() else {}
    for p in projects:
        r, a = summary.get(p["slug"]), assessments.get(p["slug"])
        if not r or not a:
            continue
        g = r.get("geocode") or {}
        ev = []
        if g.get("osm_url"):
            ev.append({"source_id": "OSM", "label": f"OpenStreetMap {g['osm']} (geocode of “{g['query']}”)", "url": g["osm_url"]})
        ev.append({"source_id": "ESRI-WAYBACK", "label": "Esri World Imagery Wayback (archived satellite releases)",
                   "url": "https://livingatlas.arcgis.com/wayback/"})
        caps = {c["date"]: c for c in r.get("captures", [])}
        desc = lambda d: f"{d} ({caps[d].get('sensor') or '?'}, {caps[d].get('res_m') or '?'} m)" if d in caps else d
        p["facts"]["imagery"] = truth.fact(
            "imagery", "outcome", VERDICT_TEXT[a["verdict"]], "inferred",
            f"Manual comparison of satellite captures {desc(r.get('before'))} → {desc(r.get('after'))} around the geocoded point: "
            f"{a['observation']} Experimental: location is geocoded from place names in the description, captures are not "
            "perfectly co-registered, and small works (drains, footpaths) are below what 0.3–0.6 m imagery can show.",
            ev)
        p["imagery"] = {"slug": p["slug"], "before": r.get("before"), "after": r.get("after"), "verdict": a["verdict"]}


def slugify(p):
    base = (p["job_numbers"] or [T.base_tender(p["tender_numbers"][0])])[0]
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def build():
    stats = Counter()
    sources = B.load_sources()
    bill_recs, estimates, st = B.load_bills(sources)
    stats.update(st)
    bill_recs = B.dedupe_bills(bill_recs)
    stats["after_dedupe"] = len(bill_recs)
    by_job = defaultdict(list)
    for b in bill_recs:
        by_job[b["job_number"]].append(b)
    direct_info, dstats = ({}, {}) if NO_DIRECT else DIR.merge(by_job)   # BBMP IFMS direct evidence
    stats.update(dstats)
    jobs = []
    for job, bs in by_job.items():
        p = job_skeleton(job, bs)
        p["direct_info"] = direct_info.get(job)
        if p["category"] is None:
            stats["excluded_not_public_work"] += 1
            continue
        jobs.append(p)

    kppp, oc = T.load_kppp(), T.load_oc_tenders()
    stats["kppp_notices"], stats["oc_tender_notices"] = len(kppp), len(oc)
    groups = {k: g for k, g in T.group_by_base(kppp + oc).items() if any(t["category"] for t in g)}
    stats["tender_groups_public_works"] = len(groups)
    # archived BBMP planning documents: job-coded sanction lists attach exactly; the rest go to the matcher
    plan_recs = PL.load_all()
    by_job_key = {p["job_numbers"][0]: p for p in jobs}
    for r in plan_recs:
        stats[f"wayback_{r['kind']}_rows"] += 1
        if r["kind"] == "sanction":
            if r["job_number"] in by_job_key:
                by_job_key[r["job_number"]]["sanction"] = r
                stats["sanction_rows_matched_to_jobs"] += 1
        else:
            groups[(r["source"], r["base"])] = [r]
    stats["kppp_groups"] = sum(1 for k in groups if k[0] == "KPPP")
    stats["oc_groups"] = sum(1 for k in groups if k[0] == "OC-TENDERS")

    # exact links: a work order / LoA in IFMS that cites the tender number
    doc_links = {}
    norm_t = lambda x: re.sub(r"[^A-Z0-9]", "", (x or "").upper().replace("/CALL-", "CALL"))
    base_index = {norm_t(gk[1]): gk for gk in groups if gk[0] in ("KPPP", "OC-TENDERS")}
    for p in jobs:
        for x in (p.get("direct_info") or {}).get("docs", []):
            if x["field"] == "tender_ref":
                gk = base_index.get(norm_t(re.sub(r"/CALL-\d+$", "", x["value"], flags=re.I)))
                if gk and gk not in doc_links:
                    doc_links[gk] = (p, {"confidence": "document", "score": 1.0, "text": 1.0, "conflict": [],
                                         "support": [f"{x['doc_type']} {x['doc']} (p{x['page']}) cites tender {x['value']}"]})
    stats["tender_links_from_documents"] = len(doc_links)
    accepted, possible = match.link([p for p in jobs if p["key"] not in {q["key"] for q, _ in doc_links.values()}],
                                    {k: g for k, g in groups.items() if k not in doc_links})
    accepted.update(doc_links)
    by_proj = defaultdict(list)
    for gk, (p, ev) in accepted.items():
        by_proj[p["key"]].append((groups[gk], ev))
        stats[f"linked_{gk[0]}_{ev['confidence']}"] += 1
    projects = []
    for p in jobs:
        projects.append(truth.assemble(p, by_proj.get(p["key"], []), estimates))
        truth.add_direct(p, p.get("direct_info"))
        truth.lifecycle(p)
        p["possible"] = [(gk, ev) for gk, q, ev in possible if q is p and gk not in accepted]
    for gk, g in groups.items():
        if gk in accepted or gk[0] != "KPPP":
            continue  # old BBMP tender lists are used only to enrich job projects, not to add projects
        p = truth.assemble(tender_skeleton(g), [(g, None)], estimates)
        truth.lifecycle(p)
        p["possible"] = []
        projects.append(p)
    stats["candidate_projects"] = len(projects)

    chosen, stats["above_value_threshold"] = select(projects)
    for p in chosen:
        p["slug"] = slugify(p)
    # contractor entities: identifiers first, names second; linked projects corroborate cross-system joins
    linked_pairs = [(t["contractor"], {b["contractor"] for b in p["bills"] if b.get("contractor")})
                    for p in projects for t in p["tenders"] if t.get("contractor") and p["bills"]]
    all_bills = [b for p in projects for b in p["bills"]]
    entities, m2e, ambiguous = CTR.resolve(all_bills, kppp, linked_pairs)
    stats["contractor_name_variants"] = len({("i", b["contractor"]) for b in all_bills if b.get("contractor")} |
                                            {("k", t["contractor"]) for t in kppp if t.get("contractor")} |
                                            {("k", bd["name"]) for t in kppp for bd in t.get("bidders") or []})
    stats["contractor_entities"] = len(entities)
    stats["contractor_entities_cross_system"] = sum(1 for e in entities if len(e["systems"]) == 2)
    stats["contractor_ambiguous_firm_names"] = len(ambiguous)
    ent_by_id = {e["id"]: e for e in entities}
    for p in projects:
        for b in p["bills"]:
            b["entity"] = m2e.get(("ifms", b["contractor"])) if b.get("contractor") else None
        for t in p["tenders"]:
            t["entity"] = m2e.get(("kppp", t["contractor"])) if t.get("contractor") and t["source"] == "KPPP" else None
            for bd in t.get("bidders") or []:
                bd["entity"] = m2e.get(("kppp", bd["name"]))
        truth.attach_entities(p, ent_by_id)
    attach_imagery(chosen)
    qty = QTY.load()
    eots = EOT.load()
    for p in chosen:
        add_eot(p, eots)
        p["signals"] = signals.project_signals(p)
        add_quantities(p, qty)
    related = match.related_works(chosen)
    contractors = signals.dataset_signals(chosen, related, ent_by_id, kppp, m2e)

    stats["projects"] = len(chosen)
    stats["with_bills"] = sum(1 for p in chosen if p["bills"])
    stats["with_tender"] = sum(1 for p in chosen if p["tenders"])
    stats["bills_and_tender"] = sum(1 for p in chosen if p["bills"] and p["tenders"])
    stats["related_pairs"] = len(related)
    write_db(sources, chosen, groups, contractors, dict(stats), entities)
    return chosen, stats


SEV = {"attention": 3, "notice": 2, "info": 1}


def add_eot(p, eots):
    """Extension-of-time orders attached in IFMS (ingest.eot): an outcome-stage fact, and the latest
    extended date as 'extended_completion' (used by the time signals instead of the stipulated date
    when it is later). OCR-read, so inferred."""
    orders = [o for j in p["job_numbers"] for o in eots.get(j, []) if o.get("status") == "parsed"]
    listed = [o for j in p["job_numbers"] for o in eots.get(j, [])]
    if not listed:
        return
    ev = [{"source_id": "IFMS-DOC", "label": f"IFMS attachment {o['document_id']}" + (f", p.{o['page']}" if o.get("page") else ""),
           "url": o["url"], "note": (o.get("raw") or o["status"])[:300]} for o in (orders or listed)[:6]]
    if orders:
        orders.sort(key=lambda o: o["to"])
        parts = [f"to {o['to']}" + (f" ({o['days']} days)" if o.get("days") else "")
                 + (f", fine ₹{o['fine']:,.0f}" if o.get("fine") else "") for o in orders]
        value = f"{len(orders)} extension-of-time order(s): " + "; ".join(parts)
    else:
        value = f"{len(listed)} extension-of-time document(s) attached; no extended date could be read"
    p["facts"]["time_extensions"] = truth.fact(
        "time_extensions", "outcome", value, "inferred",
        "Extension-of-time orders attached to this job's bills in IFMS, read by OCR. A later order supersedes an earlier one; "
        "an order may belong to an earlier contract under the same job.", ev, via="IFMS attachments (OCR)")
    if orders:
        p["facts"]["extended_completion"] = truth.fact(
            "extended_completion", "outcome", orders[-1]["to"], "inferred",
            "Latest completion date granted by an extension-of-time order found in IFMS.", ev[-1:], via="IFMS attachments (OCR)")


def add_quantities(p, qty):
    """Phase 5 quantity verification (ingest.quantities): a work-stage fact plus quantity signals.
    These are separate inferred facts; they never overwrite IFMS amounts or dates."""
    if not qty:
        return
    for job in p["job_numbers"]:
        mp = qty["mb_presence"].get(job)
        if mp and mp["final_bills"] and not mp["mb_real"] and mp.get("bill_level_files", True) and not mp.get("mb_filed_elsewhere"):
            p["signals"].append(signals.sig(
                "final_bill_without_mb", "notice", "Final bill paid with no measurement-book attachment",
                f"Final bill(s) {', '.join(mp['final_bills'])} paid, but no M.B. / bill-form file is attached to any bill of job "
                f"{job} in IFMS" + (f" (only placeholders: {', '.join(mp['mb_placeholders'])})" if mp["mb_placeholders"] else "")
                + ". The MB may exist on paper; it is not public.", "confirmed",
                [{"source_id": "IFMS", "label": f"BBMP IFMS work bill {w}",
                  "url": f"{DIR.F.API}?pAction=LoadFilesDetails&pMainID={w}&pCheck=1"} for w in mp["final_bills"][:3]]))
        res = next((r for r in qty["projects"] if r["job"] == job), None)
        if not res:
            continue
        items = res["work_items"]
        compared = [w for w in items if w["compared_quantity"] is not None]
        docs = res["documents"]
        got = [d for d in docs if d["status"] != "not downloaded"]
        if compared:
            var = sorted(w["variance_pct"] for w in compared if w["variance_pct"] is not None)
            med = var[len(var) // 2] if var else None
            value = (f"{len(compared)} of {len(items)} planned items compared ({res['plan_basis']} vs "
                     f"{'MB totals / ' if any(w['compared_basis'] == 'MB measurement total' for w in compared) else ''}bill abstracts)"
                     + (f"; median quantity difference {med:+.0f}%" if med is not None else ""))
        elif items:
            value = f"{len(items)} planned items read ({res['plan_basis']}); no billed / measured quantity could be read"
        elif got:
            value = "Quantity documents downloaded but no arithmetic-checked line item could be read"
        else:
            value = None
        ev = [QTY._ev(x) for w in compared[:3] for x in w["provenance"][:2]] or \
             [{"source_id": "IFMS-QTY", "label": f"IFMS attachment {d['document_id']} ({d['type']})", "url": d["url"],
               "note": d["status"]} for d in got[:4]]
        p["facts"]["quantity_check"] = truth.fact(
            "quantity_check", "work", value, "inferred",
            "Planned quantities (Schedule B / estimate) against billed (bill abstracts) and measured (typed MB sheets) "
            "quantities. Only rows where quantity × rate = amount on the scanned page are used; handwritten MB pages "
            "are left unread. Never overrides IFMS amounts.", ev, via="IFMS attachments (OCR, arithmetic-checked)")
        p["quantity_items"] = items
        p["signals"].extend(res["signals"])


def write_flags(con):
    """Explorer filters: which kinds of evidence each project has (derived from facts / work items / signals / cases)."""
    con.execute("""INSERT INTO project_flags
        SELECT p.id,
          EXISTS(SELECT 1 FROM facts f WHERE f.project_id = p.id AND f.field = 'contract_value' AND f.value IS NOT NULL),
          COALESCE(p.n_bills, 0) > 0,
          EXISTS(SELECT 1 FROM facts f WHERE f.project_id = p.id AND f.field IN ('final_bill', 'completed_on_certificate', 'completion_date')
                 AND f.value IS NOT NULL),
          EXISTS(SELECT 1 FROM facts f WHERE f.project_id = p.id AND f.field = 'site_photos' AND f.value IS NOT NULL
                 AND f.value NOT LIKE '0 %'),
          EXISTS(SELECT 1 FROM work_items w WHERE w.project_id = p.id),
          EXISTS(SELECT 1 FROM signals s WHERE s.project_id = p.id AND s.severity IN ('notice', 'attention')),
          EXISTS(SELECT 1 FROM cases c WHERE c.project_id = p.id AND c.retained = 1)
        FROM projects p""")


def write_cases(con, projects):
    """Phase 6 cases (data/investigation_cases.json), re-pointed at this build's project ids by job number."""
    path = ROOT / "data" / "investigation_cases.json"
    if not path.exists():
        return
    pid = {}
    for i, js in con.execute("SELECT id, job_numbers FROM projects WHERE job_numbers IS NOT NULL"):
        for j in js.split(","):
            pid[j] = i
    for c in json.loads(path.read_text())["cases"]:
        con.execute("INSERT OR REPLACE INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (c["case_id"], pid.get(c["job_number"]), c["job_number"], c.get("rank"), int(bool(c.get("retained"))),
                     c.get("status"), c.get("confidence"), c.get("case_score"), c.get("primary_issue"),
                     c.get("why_selected") or c.get("why_not_retained"), json.dumps(c, default=str)))


def write_discovery(con, projects):
    """Canonical discovery table + CSV. 'previously known' = present in the Phase 4 bill sources
    (OpenCity exports + direct IFMS cache built from known job numbers)."""
    from ingest import discover_bbmp
    import csv
    if not discover_bbmp.OUT.exists():
        return
    srcs = B.load_sources()
    prior, _, _ = B.load_bills(srcs)
    prior = [b for b in prior if b["source"].get("kind") != "ifms_grid"]
    known_jobs = {b["job_number"] for b in prior}
    known_ids = {str((b.get("extra") or {}).get("ifms_id")) for b in prior if (b.get("extra") or {}).get("ifms_id")}
    base = ROOT / "data" / "phase4_baseline.json"      # IFMS bills fetched by the end of Phase 4 (fixed snapshot)
    known_ids |= set(json.loads(base.read_text())["ifms_bills"]) if base.exists() else set()
    direct = {p.stem: json.loads(p.read_text()) for p in (ROOT / "data" / "raw" / "bbmp_direct" / "bills").glob("*.json")}
    slug_of = {j: p["slug"] for p in projects for j in p["job_numbers"]}
    rows = []
    for r in discover_bbmp.koramangala():
        det = (direct.get(r["bill_id"]) or {}).get("details") or {}
        det = det if isinstance(det, dict) else {}
        row = (r["bill_id"], r["job_number"], r["br_no"], r["br_date"], r["description"], r["ward_prefix"], r["regime"],
               det.get("ddoname"), r["contractor"], det.get("billtype"), r["gross"], r["nett"], r["deduction"], r["rtgs_no"],
               ",".join(sorted(r["payment_dates"])), r["cbr_no"], r["cbr_date"], r["scope_basis"], r["scope_reason"],
               int(r["job_number"] in known_jobs), int(r["bill_id"] in known_ids), slug_of.get(r["job_number"]), r["source_url"])
        rows.append(row)
        con.execute("INSERT OR REPLACE INTO discovery VALUES (" + ",".join("?" * len(row)) + ")", row)
    cols = [d[1] for d in con.execute("PRAGMA table_info(discovery)")]
    with open(ROOT / "data" / "discovery_koramangala.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)


def write_db(sources, projects, groups, contractors, stats, entities=()):
    DB.parent.mkdir(exist_ok=True)
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    for s in sources:
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                    (s["id"], s["name"], s["kind"], s["publisher"], s["page"], s["url"], s["original_portal"], s["file"]))
    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                ("KPPP", "Karnataka Public Procurement Portal — works tenders, tender documents, comparative statements",
                 "kppp_tender", "Government of Karnataka (e-Procurement)", KPPP_PORTAL, KPPP_API, KPPP_PORTAL, "kppp/, kppp_docs/"))
    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                ("OC-TENDERS", "BBMP tender lists 2013-2018", "oc_tenders", "BBMP (via OpenCity Data Portal mirror)",
                 T.OC_TENDER_PAGE, T.OC_TENDER_PAGE, "https://eproc.karnataka.gov.in/", "tenders_oc/"))
    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                ("IFMS-GRID", "BBMP IFMS payment grid (all paid work bills, mid-2015 onward)", "ifms_grid", "BBMP / GBA",
                 "https://accounts.bbmp.gov.in/vssWB/", "https://accounts.bbmp.gov.in/vssWB/vss00CvStatusData.php?pAction=LoadPaymentGridData",
                 "https://accounts.bbmp.gov.in/vssWB/", "bbmp_discovery/"))
    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                ("IFMS", "BBMP IFMS Works Bill (direct, public vssWB service)", "ifms_direct", "BBMP / GBA",
                 "https://accounts.bbmp.gov.in/vssWB/", "https://accounts.bbmp.gov.in/vssWB/vss00CvStatusData.php",
                 "https://accounts.bbmp.gov.in/vssWB/", "bbmp_direct/"))
    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                ("IFMS-DOC", "Documents and photos attached to BBMP IFMS work bills", "ifms_docs", "BBMP / GBA",
                 "https://accounts.bbmp.gov.in/vssWB/", "https://accounts.bbmp.gov.in/vssIFMS/", "https://accounts.bbmp.gov.in/vssWB/",
                 "bbmp_direct/files/"))
    con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                ("WAYBACK", "Archived BBMP planning documents and tender notices (Internet Archive copies of site.bbmp.gov.in / bbmp.gov.in)",
                 "wayback_docs", "BBMP (via Internet Archive Wayback Machine)", "https://web.archive.org/", "https://web.archive.org/",
                 "https://site.bbmp.gov.in/", "wayback/"))
    for sid in ("WB-SANCTION", "WB-POW", "WB-NOTICE", "WB-GRANT", "WB-HORT"):
        con.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?)",
                    (sid, {"WB-SANCTION": "BBMP ward works lists FY 2015-16 / 2016-17", "WB-POW": "BBMP Programme of Works 2022-23 (South)",
                           "WB-NOTICE": "BBMP BTM division tender notifications 2018-19 (OCR)", "WB-GRANT": "Amrutha Nagarothana grants, BTM Layout (OCR)",
                           "WB-HORT": "BBMP Horticulture South park-maintenance tenders 2022"}[sid],
                     "wayback_docs", "BBMP (via Internet Archive Wayback Machine)", "https://web.archive.org/", "https://web.archive.org/",
                     "https://site.bbmp.gov.in/", "wayback/"))
    for p in projects:
        F = p["facts"]
        v = lambda k: F.get(k, {}).get("value")
        conf = "exact" if (p["tenders"] and not p["bills"]) else \
            ("high" if any(l["confidence"] == "high" for l in p["links"]) else
             "medium" if p["links"] else ("none" if p["bills"] else "exact"))
        sev = max((SEV[s["severity"]] for s in p["signals"]), default=0)
        cur = con.execute(
            """INSERT INTO projects (slug,title,category,location,wards,department,office,job_numbers,tender_numbers,
               status,estimated_cost,contract_value,contractor,payments_gross,tender_published,awarded_date,work_order_date,
               completion_date,expected_completion,last_payment,first_date,last_activity,n_bills,lifecycle,stages,
               link_confidence,n_signals,signal_level,scope_reason,search_text,contractor_entity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["slug"], p["title"], p["category"], v("location"), ", ".join(p["wards"]) or None, p["department"], p["office"],
             ",".join(p["job_numbers"]) or None, ",".join(p["tender_numbers"]) or None, v("status"), v("estimated_cost"),
             v("contract_value"), v("contractor"), v("payments_gross"), v("tender_published"), v("awarded_date"),
             v("work_order_date"), v("completion_date"), v("expected_completion"), v("last_payment"), p["first_date"],
             last_activity(p), len(p["bills"]), sum(p["stages"].values()), json.dumps(p["stages"]), conf,
             len(p["signals"]), {3: "attention", 2: "notice", 1: "info", 0: None}[sev], p["scope_reason"],
             " ".join([p["title"], *p["descriptions"], v("contractor") or "", " ".join(p["job_numbers"]),
                       " ".join(p["tender_numbers"]), v("location") or "", p["category"] or "",
                       " ".join(s["code"] for s in p["signals"])]).lower(), p.get("contractor_entity")))
        pid = cur.lastrowid
        for f in F.values():
            con.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)",
                        (pid, f["field"], f["stage"], None if f["value"] is None else str(f["value"]), f["confidence"],
                         f["explanation"], f.get("via"), json.dumps(f["evidence"])))
        for l in p["links"]:
            g = groups[(l["source"], l["group"])]
            con.execute("INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, l["group"], l["source"], "accepted", l["confidence"], l["score"], l["text"],
                         json.dumps(l["support"]), json.dumps(l["conflict"]), g[-1]["description"], g[-1]["url"]))
        for gk, ev in p.get("possible", [])[:5]:
            g = groups[gk]
            con.execute("INSERT INTO links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (pid, gk[1], gk[0], "possible", ev["confidence"], ev["score"], ev["text"],
                         json.dumps(ev["support"]), json.dumps(ev["conflict"]), g[-1]["description"], g[-1]["url"]))
        for s in p["signals"]:
            con.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?)",
                        (pid, s["code"], s["severity"], s["title"], s["detail"], s["confidence"],
                         json.dumps(s["evidence"]), json.dumps(s["related"])))
        for w in p.get("quantity_items") or []:
            con.execute("INSERT INTO work_items VALUES (" + ",".join("?" * 20) + ")",
                        (pid, w["description"], w["normalized_category"], w.get("code"), w["unit"], w["plan_basis"],
                         w["estimated_quantity"], w["estimated_rate"], w["estimated_amount"], w["measured_quantity"],
                         w["billed_quantity"], w["billed_rate"], w["billed_amount"], w["compared_quantity"],
                         w["compared_basis"], w["variance_pct"], len(w["bills"]),
                         min((x["confidence"] for x in w["provenance"]), key=lambda c: {"high": 2, "medium": 1}.get(c, 0)),
                         all(x["cross_validated"] for x in w["provenance"]), json.dumps(w["provenance"])))
        link_of = {t["tender_number"]: ("exact" if not p["bills"] else next(
            (l["confidence"] for l in p["links"] if l["group"] == t["base"]), "medium")) for t in p["tenders"]}
        for b in p["bills"]:
            con.execute(
                """INSERT INTO records (project_id,record_type,link,source_id,source_row,job_number,description,ward,contractor,
                   bill_type,gross,deduction,nett,br_no,br_date,payment_ref,payment_date,payment_status,wo_no,wo_date,
                   start_date,end_date,dedupe_key,source_url,contractor_entity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, "bill", "exact", b["source"]["id"], b.get("source_row"), b["job_number"], b.get("description"),
                 b.get("ward"), b.get("contractor"), b.get("bill_type"), b.get("gross"), b.get("deduction"), b.get("nett"),
                 b.get("br_no"), b.get("br_date"), b.get("payment_ref"), b.get("payment_date"), b.get("payment_status"),
                 b.get("wo_no"), b.get("wo_date"), b.get("start_date"), b.get("end_date"), b.get("dedupe_key"), b["source"]["url"],
                 b.get("entity")))
        for t in p["tenders"]:
            extra = {k: t.get(k) for k in ("source", "dept", "office", "ecv", "provisional_amount", "bid_value", "negotiated_value",
                                           "awarded_date", "closing_date", "file_number", "period_days", "period_text",
                                           "period_url", "bidders", "comparative_url", "bid_url", "nit_id", "row", "call",
                                           "doc_title", "ocr", "plan_status", "kind")}
            extra["documents"] = t.get("documents")
            con.execute(
                """INSERT INTO records (project_id,record_type,link,source_id,tender_number,description,contractor,gross,
                   payment_status,start_date,source_url,extra,contractor_entity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, "tender", link_of[t["tender_number"]], t["source"], t["tender_number"], t["description"], t.get("contractor"),
                 t.get("contract_value"), t.get("status_text"), t.get("published_date"), t["url"], json.dumps(extra), t.get("entity")))
        for t in p.get("plan_entries", []) + ([p["sanction"]] if p.get("sanction") else []):
            con.execute(
                """INSERT INTO records (project_id,record_type,link,source_id,tender_number,description,gross,payment_status,
                   start_date,source_url,extra) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, "plan", "exact" if t["kind"] == "sanction" else next((l["confidence"] for l in p["links"] if l["group"] == t["base"]), "medium"),
                 t["source"], t.get("job_number") or t["tender_number"], t["description"], t.get("ecv"),
                 t.get("status_text") or t.get("plan_status"), t.get("published_date"), t["url"],
                 json.dumps({k: t.get(k) for k in ("doc_title", "ocr", "plan_status", "kind", "row", "fy", "budget_head", "office")})))
    summary = {c["entity"]: c for c in contractors["contractors"]}
    for e in entities:
        c = summary.get(e["id"], {})
        con.execute("INSERT INTO contractors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e["id"], e["name"], json.dumps(e["aliases"]), json.dumps(e["ids"]), e["basis"], json.dumps(e["rules"]),
                     json.dumps(e["evidence"]), json.dumps(e["systems"]), c.get("projects", 0), c.get("amount", 0),
                     c.get("share", 0), c.get("bids", 0), c.get("wins", 0), c.get("single_wins", 0), json.dumps(c.get("slugs", []))))
    for c in contractors["co_bidding"]:
        con.execute("INSERT INTO co_bidding VALUES (?,?,?,?,?,?,?,?)",
                    (c["a_id"], c["b_id"], c["a"], c["b"], c["tenders"], c["a_wins"], c["b_wins"], json.dumps(c["list"])))
    write_discovery(con, projects)
    write_cases(con, projects)
    write_flags(con)
    con.execute("INSERT INTO meta VALUES ('stats', ?)", (json.dumps(stats),))
    con.commit()
    con.close()


def main():
    _, stats = build()
    print(json.dumps(dict(stats), indent=1))


if __name__ == "__main__":
    main()
