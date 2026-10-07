"""Phase 7: the public surface — read-only, hardened, no sensitive data, every page renders.

Runs against the public build (data/public/koramangala_public.db; `python -m ingest.publish`)."""
import os
import re
import sqlite3
import stat

import pytest

from app import PUBLIC_DB, create_app
from app.security import RateLimiter
from ingest import publish as P

pytestmark = pytest.mark.skipif(not PUBLIC_DB.exists(), reason="public database not built (python -m ingest.publish)")

EMAIL = re.compile(rb"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
MOBILE = re.compile(rb"(?<![\w/#.=%-])(?<!md5 )(?:\+?91[\s-]?)?[6-9]\d{9}(?![\w/.%-])")
LEAKS = [rb"Traceback", rb"/Users/", rb"/home/", rb"site-packages", rb"sqlite3.", rb"koramangala_public.db", rb"SECRET", rb"Werkzeug"]


@pytest.fixture(scope="module")
def app():
    a = create_app("public", RATE_LIMIT=0)       # rate limit off for bulk page checks (tested separately)
    return a


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(f"file:{PUBLIC_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _clean(body):
    text = re.sub(rb'(href|src)="[^"]*"', b"", body)            # URLs carry ids that look like numbers
    text = re.sub(rb"https?://\S+", b"", text)
    return text


# ---------------------------------------------------------------- configuration and routes

def test_production_config_disables_debug(app):
    assert app.config["DEBUG"] is False and app.debug is False
    assert app.config["PROPAGATE_EXCEPTIONS"] is False and app.config["PUBLIC"] is True
    assert app.config["MAX_CONTENT_LENGTH"] <= 64 * 1024
    assert str(app.config["DB_PATH"]).endswith("koramangala_public.db")


def test_dev_only_routes_absent_in_public_mode(app, client):
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert not any(r.startswith("/reality") for r in rules)
    assert client.get("/reality/x/before_after.jpg").status_code == 404
    dev = {r.rule for r in create_app("dev").url_map.iter_rules()}
    assert "/reality/<slug>/<path:fname>" in dev


def test_every_public_route_is_get_only(app):
    for r in app.url_map.iter_rules():
        assert r.methods <= {"GET", "HEAD", "OPTIONS"}, r.rule


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", ["/", "/projects", "/api/projects", "/cases", "/p/anything"])
def test_writes_are_refused(client, method, path):
    r = getattr(client, method)(path, data={"x": "1"})
    assert r.status_code == 405 and b"read-only" in r.data


def test_public_db_cannot_be_mutated(con):
    with pytest.raises(sqlite3.OperationalError):
        con.execute("UPDATE projects SET title = 'x'")
    if not os.environ.get("CI"):                                           # git checkouts do not keep 444
        mode = stat.S_IMODE(os.stat(PUBLIC_DB).st_mode)
        assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)      # 444 as written by ingest.publish


def test_security_headers(client):
    r = client.get("/")
    h = r.headers
    assert "default-src 'self'" in h["Content-Security-Policy"] and "script-src 'none'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in h and "Permissions-Policy" in h


def test_hsts_only_over_https(client):
    assert "Strict-Transport-Security" not in client.get("/").headers
    assert "Strict-Transport-Security" in client.get("/", base_url="https://example.org").headers


# ---------------------------------------------------------------- errors never leak internals

def test_404_page_is_generic(client):
    r = client.get("/p/does-not-exist")
    assert r.status_code == 404 and b"Page not found" in r.data
    assert not any(x in r.data for x in LEAKS)


def test_500_does_not_expose_stack_trace():
    a = create_app("public", RATE_LIMIT=0)

    @a.route("/boom")
    def boom():
        raise RuntimeError("secret internal detail /Users/someone/db.sqlite")
    r = a.test_client().get("/boom")
    assert r.status_code == 500
    assert b"secret internal detail" not in r.data and not any(x in r.data for x in LEAKS)


def test_missing_database_does_not_reveal_path(tmp_path):
    a = create_app("public", RATE_LIMIT=0, DB_PATH=str(tmp_path / "nowhere" / "x.db"))
    r = a.test_client().get("/projects")
    assert r.status_code == 503 and str(tmp_path).encode() not in r.data and b"x.db" not in r.data


def test_rate_limit():
    lim = RateLimiter(limit=3, api_limit=1, window=60)
    assert all(lim.allow("1.2.3.4", "/", now=t) for t in (0, 1, 2)) and not lim.allow("1.2.3.4", "/", now=3)
    assert lim.allow("1.2.3.4", "/", now=70)                     # window slides
    assert lim.allow("5.6.7.8", "/api/projects", now=0) and not lim.allow("5.6.7.8", "/api/projects", now=1)
    a = create_app("public", RATE_LIMIT=2)
    c = a.test_client()
    codes = [c.get("/about").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_oversized_query_refused(client):
    assert client.get("/projects?q=" + "a" * 3000).status_code == 414


# ---------------------------------------------------------------- sensitive data

def test_scrubber_removes_contacts_but_keeps_urls_and_ids():
    s = ("Telephone: 080-12345678, e-mail: division.office@example.gov.in, Mob 9000000004, see "
         "https://www.openstreetmap.org/node/9790284747 and md5 8638511742…")
    out = P.scrub_text(s)
    assert "division.office" not in out and "12345678" not in out and "9000000004" not in out
    assert "https://www.openstreetmap.org/node/9790284747" in out and "8638511742" in out


def test_scrub_json_leaves_url_keys():
    out = P.scrub_json({"url": "https://x/a@b.com", "note": "call 9876543210", "nested": [{"raw": "a@b.com"}]})
    assert out["url"] == "https://x/a@b.com" and "9876543210" not in out["note"] and "a@b.com" not in out["nested"][0]["raw"]


def test_public_db_has_no_contact_patterns(con):
    assert P.leaks(con) == []


def _pages(con, n=60):
    paths = ["/", "/projects", "/cases", "/methodology", "/coverage", "/about", "/sources", "/signals", "/api/cases"]
    paths += [f"/p/{s}" for (s,) in con.execute("SELECT slug FROM projects ORDER BY payments_gross DESC LIMIT ?", (n,))]
    paths += [f"/cases/{c}" for (c,) in con.execute("SELECT case_id FROM cases")]
    paths += [f"/api/projects/{s}" for (s,) in con.execute("SELECT slug FROM projects ORDER BY payments_gross DESC LIMIT 20")]
    return paths


def test_no_contact_details_or_internals_rendered(client, con):
    for path in _pages(con):
        r = client.get(path)
        assert r.status_code == 200, path
        body = _clean(r.data)
        assert not EMAIL.search(body), (path, EMAIL.search(body).group(0))
        assert not MOBILE.search(body), (path, MOBILE.search(body).group(0))
        assert not any(x in r.data for x in LEAKS), path


def test_api_omits_internal_columns(client):
    rows = client.get("/api/projects?q=park").get_json()
    assert rows and all("search_text" not in r for r in rows)


# ---------------------------------------------------------------- pages

def test_home_metrics_come_from_data(client, con):
    r = client.get("/")
    n = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    cases = con.execute("SELECT COUNT(*) FROM cases WHERE retained = 1").fetchone()[0]
    assert str(n).encode() in r.data and f">{cases}<".encode() in r.data
    assert b"not findings of wrongdoing" in r.data
    for link in (b'href="/projects"', b'href="/cases"', b'href="/methodology"', b'href="/coverage"', b'href="/about"'):
        assert link in r.data


def test_case_pages(client, con):
    r = client.get("/cases")
    assert r.status_code == 200 and b"not accusations" in r.data
    for (cid, retained) in con.execute("SELECT case_id, retained FROM cases"):
        page = client.get(f"/cases/{cid}")
        assert page.status_code == 200
        if retained:
            for h in (b"What was promised", b"What happened", b"Why this case deserves follow-up", b"Possible legitimate explanations",
                      b"What would resolve it", b"Evidence"):
                assert h in page.data, (cid, h)
            assert not re.search(rb"\b(fraud|corruption|scam|fake)\b", page.data, re.I)
    assert client.get("/cases/KP6-000").status_code == 404 and client.get("/cases/../etc").status_code == 404


def test_project_page_links_sources(client, con):
    (slug,) = con.execute("SELECT slug FROM projects p JOIN work_items w ON w.project_id = p.id LIMIT 1").fetchone()
    r = client.get(f"/p/{slug}")
    assert r.status_code == 200 and b"Quantities: Schedule B vs billed" in r.data
    assert b"https://accounts.bbmp.gov.in/" in r.data or b"opencity" in r.data.lower()


def test_methodology_and_limitations(client):
    m = client.get("/methodology").data
    assert b"A signal can disappear when better evidence is found" in m and b"quantity \xc3\x97 rate = amount" in m
    c = client.get("/coverage").data
    for phrase in (b"Unpaid bills may be incomplete", b"require a login", b"Handwritten measurement books", b"No GPS",
                   b"Absence from public attachments does not prove"):
        assert phrase in c


def test_search_filters_and_pagination(client, con):
    allp = client.get("/projects").data
    with_case = client.get("/projects?has=has_case").data
    n_case = con.execute("SELECT COUNT(*) FROM project_flags WHERE has_case = 1").fetchone()[0]
    assert len(re.findall(rb'class="pcard"', with_case)) == n_case
    assert client.get("/projects?has=; DROP TABLE projects").status_code == 200        # unknown flag ignored
    assert client.get("/projects?page=2").status_code == 200 and client.get("/projects?page=abc").status_code == 200
    assert len(re.findall(rb'class="pcard"', allp)) <= 50
    assert client.get("/?q=road").status_code == 302                                    # old links redirect
