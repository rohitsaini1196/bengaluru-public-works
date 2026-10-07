"""Integrity checks on the built database and smoke tests of the web app.

Requires data/koramangala.db (run `python -m ingest.build` first).
"""
import json
import re
import sqlite3

import pytest

from app.app import DB_PATH, app

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="database not built")


@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_project_count_in_range(con):
    # MVP capped the build at 200 projects; Phase 5 removed the cap (every discovered Koramangala job >= ₹5 L)
    n = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert 50 <= n <= 1000


def test_every_known_fact_has_evidence(con):
    rows = con.execute("SELECT project_id, field, value, confidence, evidence, explanation FROM facts").fetchall()
    assert rows
    for r in rows:
        ev = json.loads(r["evidence"])
        assert r["explanation"], (r["project_id"], r["field"])
        if r["confidence"] == "unknown":
            assert r["value"] is None
        elif r["field"] != "location":  # place names are read from the description, which is itself evidenced
            assert r["value"] is not None
            assert ev and all(x["url"].startswith("https://") for x in ev), (r["project_id"], r["field"])


def test_matched_facts_are_never_confirmed(con):
    bad = con.execute("""SELECT f.project_id, f.field FROM facts f JOIN projects p ON p.id = f.project_id
                         WHERE f.via IS NOT NULL AND f.confidence = 'confirmed'""").fetchall()
    assert not bad
    # every project with both bills and a tender got there through an accepted link
    for (pid,) in con.execute("SELECT id FROM projects WHERE n_bills > 0 AND tender_numbers IS NOT NULL"):
        assert con.execute("SELECT COUNT(*) FROM links WHERE project_id=? AND status='accepted'", (pid,)).fetchone()[0] >= 1


def test_signals_have_explanations(con):
    for r in con.execute("SELECT * FROM signals"):
        assert r["title"] and r["detail"] and r["severity"] in ("info", "notice", "attention")
        assert r["confidence"] in ("confirmed", "inferred")


def test_every_project_has_records_with_urls(con):
    bad = con.execute("""SELECT p.id FROM projects p WHERE NOT EXISTS
                         (SELECT 1 FROM records r WHERE r.project_id = p.id AND r.source_url LIKE 'https://%')""").fetchall()
    assert not bad


def test_money_fields_consistent_with_records(con):
    for p in con.execute("SELECT id, payments_gross FROM projects WHERE payments_gross IS NOT NULL"):  # noqa
        s = con.execute("SELECT SUM(gross) FROM records WHERE project_id=? AND record_type='bill'", (p["id"],)).fetchone()[0]
        assert abs(s - p["payments_gross"]) < 1


def test_no_phone_numbers_published(con):
    for (c,) in con.execute("SELECT contractor FROM records WHERE contractor IS NOT NULL"):
        assert not re.search(r"[6-9]\d{9}", c), c


def test_completed_only_with_final_bill(con):
    for p in con.execute("SELECT id FROM projects WHERE status LIKE 'Completed%'"):
        n = con.execute("SELECT COUNT(*) FROM records WHERE project_id=? AND bill_type LIKE '%final%'", (p["id"],)).fetchone()[0]
        assert n >= 1


def test_index_and_search(client, con):
    # Phase 7: "/" is the landing page; the project list moved to /projects (old /?q= links redirect there)
    r = client.get("/")
    assert r.status_code == 200 and b"Bengaluru Public Works Explorer" in r.data
    r = client.get("/?q=6th+block", follow_redirects=True)
    assert r.status_code == 200
    n = len(re.findall(rb'class="pcard"', r.data))
    assert 0 < n < con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    r = client.get("/projects?q=zzzznotathing")
    assert b"No projects match" in r.data


def test_filters_and_sorts(client):
    for qs in ("?sort=value", "?sort=paid", "?sort=oldest", "?sort=depth", "?sort=signals", "?status=Tender+awarded",
               "?category=Roads", "?signal=single_bidder", "?depth=4", "?link=high", "?sort=bogus"):
        assert client.get("/projects" + qs).status_code == 200


def test_detail_pages(client, con):
    for (slug,) in con.execute("SELECT slug FROM projects"):
        r = client.get(f"/p/{slug}")
        assert r.status_code == 200, slug
        assert b"What was planned" in r.data and b"Completion" in r.data and b"Physical evidence" in r.data
    assert client.get("/p/does-not-exist").status_code == 404


def test_unknown_marked(client, con):
    slug = con.execute("SELECT slug FROM projects WHERE contract_value IS NULL LIMIT 1").fetchone()[0]
    assert b'class="unk"' in client.get(f"/p/{slug}").data


def test_sources_and_api(client):
    assert client.get("/sources").status_code == 200
    assert client.get("/signals").status_code == 200
    data = client.get("/api/projects?q=koramangala").get_json()
    assert data and "search_text" not in data[0]
    one = client.get(f"/api/projects/{data[0]['slug']}").get_json()
    assert one["project"]["slug"] == data[0]["slug"] and one["records"]


def test_contractor_entities_and_pages(client, con):
    rows = con.execute("SELECT * FROM contractors WHERE projects > 0").fetchall()
    assert rows and all(r["basis"] in ("confirmed", "inferred") for r in rows)
    for r in rows[:20]:
        assert client.get(f"/c/{r['entity']}").status_code == 200
    # no phone numbers anywhere in the contractor tables
    for r in con.execute("SELECT name, aliases, evidence FROM contractors"):
        assert not re.search(r"[6-9]\d{9}", " ".join(str(x) for x in r))


def test_identity_fact_confidence_matches_entity(con):
    for r in con.execute("""SELECT f.confidence, c.basis FROM facts f JOIN projects p ON p.id=f.project_id
                            JOIN contractors c ON c.entity=p.contractor_entity WHERE f.field='contractor_identity'"""):
        assert r["confidence"] == r["basis"]
