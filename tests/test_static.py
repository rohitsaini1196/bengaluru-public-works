"""Static export (GitHub Pages): every page written, links prefixed and resolvable, no leaks, and the
browser-side filters select exactly what the server-side filters select."""
import html
import json
import re
import shutil
import sqlite3
import subprocess
from html.parser import HTMLParser

import pytest

from app import PUBLIC_DB, create_app
from app import freeze as FZ
from app.core import query_projects

pytestmark = pytest.mark.skipif(not PUBLIC_DB.exists(), reason="public database not built (python -m ingest.publish)")
BASE = "/bengaluru-public-works/"
EMAIL = re.compile(rb"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
MOBILE = re.compile(rb"(?<![\w/#.=%-])(?<!md5 )(?:\+?91[\s-]?)?[6-9]\d{9}(?![\w/.%-])")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    res = FZ.freeze(out, BASE)
    return out, res


def test_every_page_written_and_links_resolve(site):
    out, res = site
    con = sqlite3.connect(f"file:{PUBLIC_DB}?mode=ro", uri=True)
    n_proj = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    n_cases = con.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    con.close()
    assert res["broken_links"] == []
    assert res["pages"] >= len(FZ.PAGES) + n_proj + n_cases
    for f in ("index.html", "404.html", ".nojekyll", "projects/index.html", "cases/index.html", "methodology/index.html",
              "coverage/index.html", "api/projects.json", "api/cases.json", "static/style.css", "static/explorer.js"):
        assert (out / f).exists(), f
    assert len(json.loads((out / "api/projects.json").read_text())) == n_proj


def test_links_carry_the_base_path_and_csp(site):
    out, _ = site
    home = (out / "index.html").read_bytes()
    assert f'href="{BASE}projects"'.encode() in home and f'href="{BASE}static/style.css"'.encode() in home
    assert b'http-equiv="Content-Security-Policy"' in home and b"script-src 'self'" in home
    about = (out / "about/index.html").read_bytes()
    assert f'{BASE}api/projects.json'.encode() in about and b'href="/api/' not in about
    assert f'src="{BASE}static/explorer.js"'.encode() in (out / "projects/index.html").read_bytes()


def test_static_projects_page_lists_everything(site):
    out, _ = site
    page = (out / "projects/index.html").read_bytes()
    con = sqlite3.connect(f"file:{PUBLIC_DB}?mode=ro", uri=True)
    assert page.count(b'class="pcard"') == con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert b'class="pager"' not in page
    con.close()


def test_no_contact_details_anywhere_in_export(site):
    out, _ = site
    for f in list(out.rglob("*.html")) + list(out.rglob("*.json")):
        body = re.sub(rb'(href|src)="[^"]*"', b"", f.read_bytes())
        body = re.sub(rb"https?://[^\s\"'<]+", b"", body)
        assert not EMAIL.search(body), (f, EMAIL.search(body).group(0))
        assert not MOBILE.search(body), (f, MOBILE.search(body).group(0))


class Cards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "li" and a.get("class") == "pcard":
            d = {k[5:]: html.unescape(v or "") for k, v in a.items() if k.startswith("data-")}
            self.cards.append(d)
        if tag == "a" and a.get("class") == "ptitle" and self.cards:
            self.cards[-1]["slug"] = a["href"].rstrip("/").rsplit("/", 1)[-1]


STATES = [
    {"q": "6th block"}, {"q": "drain road"}, {"signal": "paid_over_contract"}, {"depth": "5"},
    {"has": ["has_case"]}, {"has": ["has_photos", "has_contract"]}, {"category": "Roads"}, {"link": "high"},
    {"q": "park", "has": ["has_bills"], "depth": "3"},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_filters_match_server_filters(site, tmp_path):
    out, _ = site
    p = Cards()
    p.feed((out / "projects/index.html").read_text())
    assert p.cards and all("slug" in c for c in p.cards)
    (tmp_path / "cards.json").write_text(json.dumps(p.cards))
    (tmp_path / "states.json").write_text(json.dumps(STATES))
    js = f"""
const m = require({json.dumps(str(FZ.ROOT / 'app/static/explorer.js'))});
const cards = require({json.dumps(str(tmp_path / 'cards.json'))});
const states = require({json.dumps(str(tmp_path / 'states.json'))});
console.log(JSON.stringify(states.map(s => cards.filter(c => m.matches(c, s)).map(c => c.slug).sort())));
"""
    got = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    app = create_app("public", RATE_LIMIT=0)
    with app.app_context():
        for s, js_slugs in zip(STATES, got):
            kw = {k: v for k, v in s.items() if k != "has"}
            server = sorted(r["slug"] for r in query_projects(flags=s.get("has", []), **kw))
            assert js_slugs == server, (s, len(js_slugs), len(server))
