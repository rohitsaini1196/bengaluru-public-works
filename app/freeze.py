"""Static export of the public site (for GitHub Pages or any static host).

    python -m app.freeze --base /<repo>/ --out site/        # project site: https://<user>.github.io/<repo>/
    python -m app.freeze --base / --out site/               # user site or custom domain

Every page is rendered by the public app itself (create_app("public", STATIC=True)), with the base path as the
WSGI script root, so all links carry the right prefix. Pages are written as <path>/index.html; JSON data as
api/projects.json and api/cases.json; plus 404.html, .nojekyll and the static assets. The project list is a single
page filtered in the browser (app/static/explorer.js). Finally every internal link and asset is checked to exist.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app import create_app

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["/", "/projects", "/cases", "/methodology", "/coverage", "/about", "/signals", "/sources"]


def routes(app):
    """Every public page the site links to."""
    from app.core import db
    with app.app_context():
        d = db()
        slugs = [r[0] for r in d.execute("SELECT slug FROM projects ORDER BY id")]
        cases = [r[0] for r in d.execute("SELECT case_id FROM cases")]
        entities = [r[0] for r in d.execute("SELECT entity FROM contractors")]
    return PAGES + [f"/p/{s}" for s in slugs] + [f"/cases/{c}" for c in cases] + [f"/c/{e}" for e in entities]


def target(out, path):
    rel = path.strip("/")
    return out / rel / "index.html" if rel else out / "index.html"


def freeze(out, base="/", db_path=None):
    base = "/" + base.strip("/") + "/" if base.strip("/") else "/"
    overrides = {"STATIC": True, "RATE_LIMIT": 0, "TRUST_PROXY": False}
    if db_path:
        overrides["DB_PATH"] = str(db_path)
    app = create_app("public", **overrides)
    client = app.test_client()
    base_url = "http://localhost" + base.rstrip("/")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written = 0
    for path in routes(app):
        r = client.get(path, base_url=base_url)
        if r.status_code != 200:
            raise SystemExit(f"{path}: HTTP {r.status_code}")
        f = target(out, path)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(r.data)
        written += 1
    (out / "404.html").write_bytes(client.get("/__not_found__", base_url=base_url).data)
    api = out / "api"
    api.mkdir()
    for name, path in (("projects.json", "/api/projects"), ("cases.json", "/api/cases")):
        (api / name).write_bytes(client.get(path, base_url=base_url).data)
    shutil.copytree(ROOT / "app" / "static", out / "static")
    (out / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    (out / ".nojekyll").write_text("")          # serve files as-is (no Jekyll processing)
    broken = check_links(out, base)
    return {"pages": written, "base": base, "broken_links": broken}


LINK = re.compile(rb'(?:href|src)="([^"#?]*)')


def check_links(out, base):
    """Internal links (starting with the base path) that do not resolve to a written file."""
    broken = set()
    for f in out.rglob("*.html"):
        for m in LINK.finditer(f.read_bytes()):
            url = m.group(1).decode()
            if not url or url.startswith(("http://", "https://", "mailto:", "//")):
                continue
            if not url.startswith(base):
                broken.add((str(f.relative_to(out)), url, "outside base path"))
                continue
            rel = unquote(urlsplit(url).path)[len(base):].strip("/")
            p = out / rel
            if not (p.is_file() or (p / "index.html").is_file() or (rel == "" and (out / "index.html").is_file())):
                broken.add((str(f.relative_to(out)), url, "missing"))
    return sorted(broken)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--base", default="/", help="URL path the site is served under, e.g. /bengaluru-public-works/ for a GitHub project site")
    ap.add_argument("--db", default=None, help="public database (default: data/public/koramangala_public.db)")
    a = ap.parse_args(argv)
    res = freeze(Path(a.out), a.base, a.db)
    print(json.dumps({**res, "broken_links": res["broken_links"][:20], "n_broken": len(res["broken_links"])}, indent=1))
    if res["broken_links"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
