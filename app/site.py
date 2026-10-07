"""Public-safe routes: every page and API here only reads the database (read-only connection) and renders it.
No route writes, crawls, refreshes, browses files, runs commands or exposes configuration.
Registered in both modes; dev-only extras live in app/devtools.py."""
import json
import re

from flask import abort, current_app, jsonify, redirect, render_template, request, url_for

from app.core import (FLAGS, db, facets, load_project, query_projects, stats, summarize, timeline, bill_chart)

PAGE_SIZE = 50
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")
CASE_ID = re.compile(r"^KP6-[0-9R-]{6,20}$")


def _meta(key):
    row = db().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(row[0]) if row else {}
    except ValueError:
        return row[0] if row else None


def headline():
    """Home-page metrics, computed from the current data (nothing hard-coded)."""
    d = db()
    one = lambda sql: d.execute(sql).fetchone()[0]
    span = d.execute("SELECT MIN(payment_date), MAX(payment_date) FROM records WHERE record_type='bill' AND payment_date IS NOT NULL").fetchone()
    has_cases = d.execute("SELECT name FROM sqlite_master WHERE name='cases'").fetchone()
    return {
        "projects": one("SELECT COUNT(*) FROM projects"),
        "with_bills": one("SELECT COUNT(*) FROM projects WHERE n_bills > 0"),
        "paid_bills": one("SELECT COUNT(*) FROM records WHERE record_type='bill' AND payment_date IS NOT NULL"),
        "paid_total": one("SELECT SUM(payments_gross) FROM projects"),
        "cases": one("SELECT COUNT(*) FROM cases WHERE retained = 1") if has_cases else 0,
        "reviewed": one("SELECT COUNT(*) FROM cases") if has_cases else 0,
        "sources": one("SELECT COUNT(*) FROM sources"),
        "first": span[0], "last": span[1],
        "published_at": _meta("published_at"),
    }


STAGE_LABELS = [("planned", "Planned", "an estimate, sanction or plan entry"),
                ("awarded", "Contracted", "a tender award or work order"),
                ("work", "Work documented", "bill types, MB, bill forms or completion papers"),
                ("money", "Paid", "at least one BBMP bill"),
                ("outcome", "Completed", "a final bill or completion record"),
                ("evidence", "Physical evidence", "site photos (not GPS-located)")]


def story():
    """Everything the home page shows, computed from the data (captions included)."""
    from app import charts
    d = db()
    years = d.execute("""SELECT substr(payment_date, 1, 4) y, SUM(gross), COUNT(*) FROM records
                         WHERE record_type = 'bill' AND payment_date IS NOT NULL GROUP BY y ORDER BY y""").fetchall()
    peak = max(years, key=lambda r: r[1]) if years else None
    projects = d.execute("SELECT slug, title, payments_gross, category FROM projects WHERE payments_gross > 0 ORDER BY payments_gross DESC").fetchall()
    total = sum(r[2] for r in projects) or 1
    top = projects[0] if projects else None
    next9 = sum(r[2] for r in projects[1:10])
    rest = total - (top[2] if top else 0) - next9
    cats = d.execute("SELECT category, SUM(payments_gross), COUNT(*) FROM projects WHERE payments_gross > 0 GROUP BY category ORDER BY 2 DESC").fetchall()
    payees = d.execute("""SELECT COALESCE(k.name, r.contractor) name, SUM(r.gross) paid, COUNT(*) bills, COUNT(DISTINCT r.project_id) works
                          FROM records r LEFT JOIN contractors k ON k.entity = r.contractor_entity
                          WHERE r.record_type = 'bill' AND r.payment_date IS NOT NULL AND COALESCE(r.contractor, '') != ''
                          GROUP BY COALESCE(r.contractor_entity, r.contractor) ORDER BY paid DESC""").fetchall()
    busiest = max(payees, key=lambda r: r["works"]) if payees else None
    n_proj = d.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    stage = {k: 0 for k, _, _ in STAGE_LABELS}
    for (st,) in d.execute("SELECT stages FROM projects"):
        for k, v in json.loads(st or "{}").items():
            if v and k in stage:
                stage[k] += 1
    reviewed = load_cases(retained_only=False)
    return {
        "years": charts.columns([(y, v, f"{n} bills") for y, v, n in years], "Gross payments by year", highlight=peak[0] if peak else None),
        "years_list": charts.hbars([(y, v, None, f"{n} bills") for y, v, n in years], "Gross payments by year",
                                   highlight=peak[0] if peak else 0),
        "peak": {"year": peak[0], "amount": peak[1], "bills": peak[2]} if peak else None,
        "share": charts.share([(f"{top[1][:60]}…" if top else "", top[2] if top else 0, "s1"),
                               ("The next 9 largest works", next9, "s2"), (f"The other {max(0, len(projects) - 10)} works", rest, "s3")],
                              "Share of all payments"),
        "top": top, "top_share": (top[2] / total) if top else 0, "next9": next9, "rest": rest, "n_paid_projects": len(projects), "total": total,
        "cats": charts.hbars([(c or "Unclassified", v, None, f"{n} works") for c, v, n in cats], "Payments by kind of work"),
        "payees": charts.hbars([(r["name"], r["paid"], None, f'{r["bills"]} bills · {r["works"]} work{"s" if r["works"] != 1 else ""}')
                                for r in payees[:10]], "Payments by payee"),
        "n_payees": len(payees), "busiest": dict(busiest) if busiest else None,
        "funnel": charts.funnel([(label, stage[k], expl) for k, label, expl in STAGE_LABELS], n_proj, "Projects with evidence at each stage"),
        "biggest": charts.hbars([(r["title"][:80], r["payments_gross"], url_for("project", slug=r["slug"]), r["category"]) for r in projects[:8]],
                                "Largest works by payments"),
        "reviewed": reviewed, "open": [c for c in reviewed if c.get("retained")],
        "set_aside": [c for c in reviewed if not c.get("retained")],
    }


def load_cases(retained_only=True):
    if not db().execute("SELECT name FROM sqlite_master WHERE name='cases'").fetchone():
        return []
    sql = "SELECT c.data, p.slug FROM cases c LEFT JOIN projects p ON p.id = c.project_id"
    sql += " WHERE c.retained = 1 ORDER BY c.rank" if retained_only else " ORDER BY c.retained DESC, c.rank, c.case_score DESC"
    out = []
    for data, slug in db().execute(sql):
        c = json.loads(data)
        c["slug"] = slug or c.get("slug")
        out.append(c)
    return out


def register(app):
    @app.route("/")
    def home():
        if request.args:                 # old links (/?q=…) pointed at the project list
            return redirect(url_for("index", **request.args.to_dict(flat=False)))
        return render_template("home.html", h=headline(), cases=load_cases(), s=story())

    @app.route("/projects")
    def index():
        keys = ("q", "category", "status", "dept", "signal", "depth", "link")
        args = {k: request.args.get(k, "")[:200] for k in keys}
        flags = [f for f in request.args.getlist("has") if f in FLAGS]
        sort = request.args.get("sort", "recent")
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        static = bool(current_app.config.get("STATIC"))
        if static:                       # static export: every project on one page, filtered in the browser
            args, flags, page = {k: "" for k in keys}, [], 1
        rows = query_projects(sort=sort, flags=flags, **args)
        shown = rows if static else rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        sig_by_proj = {}
        for r in db().execute("SELECT project_id, code, severity, title FROM signals WHERE severity != 'info'"):
            sig_by_proj.setdefault(r["project_id"], []).append(r)
        case_by_proj = {r[0]: r[1] for r in db().execute("SELECT project_id, case_id FROM cases WHERE retained = 1")}
        pages = 1 if static else max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        codes = {}
        for pid, code in db().execute("SELECT DISTINCT project_id, code FROM signals"):
            codes.setdefault(pid, []).append(code)
        pflags = {r["project_id"]: [f for f in FLAGS if r[f]] for r in db().execute("SELECT * FROM project_flags")}
        return render_template("index.html", codes=codes, pflags=pflags, rows=shown, args=args, sort=sort, facets=facets(), summary=summarize(rows),
                               total=summarize(query_projects()), stats=stats(), sigs=sig_by_proj, flags=flags, FLAGS=FLAGS,
                               page=page, pages=pages, cases_by_proj=case_by_proj)

    @app.route("/p/<slug>")
    def project(slug):
        if not SLUG.match(slug):
            abort(404)
        p, facts, recs, links, sigs = load_project(slug)
        d = db()
        items = [dict(r, provenance=json.loads(r["provenance"] or "[]"))
                 for r in d.execute("SELECT * FROM work_items WHERE project_id = ? ORDER BY estimated_amount DESC", (p["id"],))]
        case = d.execute("SELECT case_id, status, retained, why FROM cases WHERE project_id = ?", (p["id"],)).fetchone()
        stages = json.loads(p["stages"] or "{}")
        img = bool(current_app.config.get("ENABLE_IMAGERY")) and "imagery" in facts
        from app.charts import lifecycle as _lc
        return render_template("project.html", lifecycle_strip=_lc(json.loads(p["stages"] or "{}"), [(k, l) for k, l, _ in STAGE_LABELS]), img=img, p=p, facts=facts, bills=[r for r in recs if r["record_type"] == "bill"],
                               tenders=[r for r in recs if r["record_type"] == "tender"], plans=[r for r in recs if r["record_type"] == "plan"],
                               links=links, sigs=sigs, stages=stages, events=timeline(facts, recs), chart=bill_chart(recs),
                               items=items, case=case)

    @app.route("/cases")
    def cases_page():
        return render_template("cases.html", cases=load_cases(), reviewed=load_cases(retained_only=False), summary=_meta("cases_summary"))

    @app.route("/cases/<case_id>")
    def case_page(case_id):
        if not CASE_ID.match(case_id):
            abort(404)
        row = db().execute("SELECT c.data, p.slug FROM cases c LEFT JOIN projects p ON p.id = c.project_id WHERE c.case_id = ?",
                           (case_id,)).fetchone()
        if not row:
            abort(404)
        c = json.loads(row[0])
        c["slug"] = row[1] or c.get("slug")
        return render_template("case.html", c=c)

    @app.route("/methodology")
    def methodology():
        return render_template("methodology.html", h=headline(), cov=_meta("coverage"), summary=_meta("cases_summary"))

    @app.route("/coverage")
    def coverage():
        return render_template("coverage.html", h=headline(), cov=_meta("coverage"), stats=stats())

    @app.route("/about")
    def about():
        return render_template("about.html", h=headline())

    @app.route("/signals")
    def signals_page():
        d = db()
        rows = d.execute("""SELECT s.*, p.slug, p.title AS project_title, p.payments_gross, p.contract_value FROM signals s
                            JOIN projects p ON p.id = s.project_id
                            WHERE s.code NOT IN ('contractor_concentration')
                            ORDER BY CASE s.severity WHEN 'attention' THEN 0 WHEN 'notice' THEN 1 ELSE 2 END, s.code""").fetchall()
        by_code = {}
        for r in rows:
            by_code.setdefault(r["code"], []).append(r)
        ctr = d.execute("SELECT * FROM contractors WHERE projects > 0 OR bids > 0 ORDER BY amount DESC, bids DESC LIMIT 30").fetchall()
        cob = d.execute("SELECT * FROM co_bidding ORDER BY tenders DESC").fetchall()
        return render_template("signals.html", by_code=by_code, contractors=ctr, co_bidding=cob, stats=stats())

    @app.route("/c/<entity>")
    def contractor(entity):
        if len(entity) > 80:
            abort(404)
        d = db()
        c = d.execute("SELECT * FROM contractors WHERE entity = ?", (entity,)).fetchone()
        if not c:
            abort(404)
        projects = d.execute("SELECT * FROM projects WHERE contractor_entity = ? ORDER BY last_activity DESC", (entity,)).fetchall()
        bids = []
        for r in d.execute("""SELECT r.tender_number, r.extra, p.slug, p.title FROM records r JOIN projects p ON p.id = r.project_id
                              WHERE r.record_type = 'tender'"""):
            x = json.loads(r["extra"] or "{}")
            for b in x.get("bidders") or []:
                if b.get("entity") == entity:
                    bids.append({"tender": r["tender_number"], "slug": r["slug"], "title": r["title"], "rank": b.get("rank"),
                                 "amount": b.get("amount"), "n": len(x["bidders"]),
                                 "others": [o["name"] for o in x["bidders"] if o.get("entity") != entity]})
        cob = d.execute("SELECT * FROM co_bidding WHERE a_entity = ? OR b_entity = ?", (entity, entity)).fetchall()
        return render_template("contractor.html", c=c, projects=projects, bids=bids, cob=cob)

    @app.route("/sources")
    def sources():
        rows = db().execute(
            """SELECT s.*, (SELECT COUNT(DISTINCT project_id) FROM records r WHERE r.source_id = s.id) AS n_projects,
                      (SELECT COUNT(*) FROM records r WHERE r.source_id = s.id) AS n_records
               FROM sources s ORDER BY n_projects DESC, s.id""").fetchall()
        return render_template("sources.html", sources=rows, stats=stats())

    # ---------------------------------------------------------------- JSON (read-only, same data as the pages)
    def _row(r):
        return {k: r[k] for k in r.keys() if k != "search_text"}

    @app.route("/api/projects")
    def api_projects():
        keys = ("q", "category", "status", "dept", "signal", "depth", "link")
        rows = query_projects(sort=request.args.get("sort", "recent"), flags=[f for f in request.args.getlist("has") if f in FLAGS],
                              **{k: request.args.get(k, "")[:200] for k in keys})
        return jsonify([_row(r) for r in rows])

    @app.route("/api/projects/<slug>")
    def api_project(slug):
        if not SLUG.match(slug):
            abort(404)
        p, facts, recs, links, sigs = load_project(slug)
        return jsonify({"project": _row(p), "facts": facts, "links": links, "signals": sigs, "records": [dict(r) for r in recs]})

    @app.route("/api/cases")
    def api_cases():
        return jsonify(load_cases())

    @app.route("/robots.txt")
    def robots():
        body = "User-agent: *\nDisallow: /api/\nCrawl-delay: 5\n"
        return current_app.response_class(body, mimetype="text/plain")

    @app.route("/healthz")
    def healthz():
        db().execute("SELECT 1").fetchone()
        return jsonify({"ok": True})

    def api_href(endpoint):
        """Link to the JSON data: a live endpoint on the server, a .json file in the static export."""
        if current_app.config.get("STATIC"):
            return request.script_root + {"api_projects": "/api/projects.json", "api_cases": "/api/cases.json"}[endpoint]
        return url_for(endpoint)

    app.jinja_env.globals.update(repo_url=current_app_config(app, "PUBLIC_REPO_URL"), contact=current_app_config(app, "PUBLIC_CONTACT"),
                                 api_href=api_href)


def current_app_config(app, key):
    return app.config.get(key) or None
