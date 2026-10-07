"""Phase 7: the public read model and the public dataset.

    python -m ingest.publish        # data/koramangala.db -> data/public/koramangala_public.db + data/public/dataset/

The public database is a sanitised copy of the build:
  * every free-text field (fact values and explanations, evidence notes / labels, OCR rows, signal details,
    case JSON, record descriptions and extras) is scrubbed of e-mail addresses and phone numbers;
    URLs and identifiers (OSM node ids, MD5s, UUIDs inside URLs) are left alone
  * the search index column keeps only scrubbed text
  * coverage metrics (data/phase5_metrics.json) and the case summary are copied into `meta`
  * after writing, the whole file is re-scanned; any surviving phone / e-mail pattern aborts the publish
  * the file is made read-only (chmod 444); the web app opens it with mode=ro&immutable=1

The dataset (CSV + JSON) is the redistributable part: project summaries, facts with source URLs, signals,
cases, work items, sources and the discovery table. Raw caches and downloaded attachments are never copied.
"""
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "koramangala.db"
OUT_DIR = ROOT / "data" / "public"
PUBLIC_DB = OUT_DIR / "koramangala_public.db"
DATASET = OUT_DIR / "dataset"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# a mobile number standing alone (not inside a URL path, id, hash or longer digit run)
MOBILE = re.compile(r"(?<![\w/#.=-])(?<!md5 )(?:\+?91[\s-]?)?[6-9]\d{9}(?![\w/.-])")
LANDLINE = re.compile(r"(?<![\w/])0\d{2,4}[\s-]\d{6,8}(?![\w/])")
URL = re.compile(r"https?://\S+")


def scrub_text(s):
    """Remove e-mails and phone numbers from free text; URLs are kept intact."""
    if not isinstance(s, str) or not s:
        return s
    parts, last = [], 0
    for m in URL.finditer(s):
        parts.append(_scrub_plain(s[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_scrub_plain(s[last:]))
    return "".join(parts)


def _scrub_plain(s):
    s = EMAIL.sub("[e-mail removed]", s)
    s = MOBILE.sub("[phone removed]", s)
    return LANDLINE.sub("[phone removed]", s)


URL_KEYS = {"url", "attachment_url", "listing_url", "source_url", "comparative_url", "bid_url", "period_url", "tender_url", "document"}


def scrub_json(obj):
    """Scrub every string value in a JSON structure, except URL-valued keys."""
    if isinstance(obj, dict):
        return {k: (v if k in URL_KEYS else scrub_json(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_json(v) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def _scrub_cell(v):
    if not isinstance(v, str) or not v:
        return v
    st = v.lstrip()
    if st[:1] in "[{":
        try:
            return json.dumps(scrub_json(json.loads(v)), ensure_ascii=False)
        except ValueError:
            pass
    return scrub_text(v)


# columns that may carry free text from documents or sources
TEXT_COLUMNS = {
    "projects": ["title", "location", "scope_reason", "search_text", "office"],
    "facts": ["value", "explanation", "evidence", "via"],
    "signals": ["title", "detail", "evidence"],
    "records": ["description", "extra", "contractor"],
    "links": ["text", "support", "conflict", "tender_title"],
    "work_items": ["description", "provenance"],
    "cases": ["why", "data"],
    "discovery": ["description", "contractor", "scope_reason"],
    "contractors": None,          # every TEXT column
}


def leaks(con):
    """Every phone / e-mail pattern still present outside URLs: [(table, column, match)]."""
    found = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        for row in con.execute(f"SELECT * FROM {t}"):
            for col, v in zip(cols, row):
                if not isinstance(v, str):
                    continue
                text = URL.sub(" ", v)
                for rx in (EMAIL, MOBILE, LANDLINE):
                    m = rx.search(text)
                    if m:
                        found.append((t, col, m.group(0)))
    return found


def build_public_db(src=SRC, dst=PUBLIC_DB):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        os.chmod(dst, stat.S_IWUSR | stat.S_IRUSR)
        dst.unlink()
    tmp = dst.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(tmp) as d:
        s.backup(d)
    con = sqlite3.connect(tmp)
    n = len(leaks(con))                  # patterns found before scrubbing (all are removed below)
    for table, cols in TEXT_COLUMNS.items():
        info = [(r[1], r[2]) for r in con.execute(f"PRAGMA table_info({table})")]
        if not info:
            continue
        cols = cols or [c for c, ty in info if ty.upper() == "TEXT"]
        cols = [c for c in cols if c in {c2 for c2, _ in info}]
        rows = con.execute(f"SELECT rowid, {', '.join(cols)} FROM {table}").fetchall()
        for row in rows:
            new = [_scrub_cell(v) for v in row[1:]]
            if new != list(row[1:]):
                con.execute(f"UPDATE {table} SET {', '.join(c + ' = ?' for c in cols)} WHERE rowid = ?", (*new, row[0]))
    # coverage metrics and case summary for the public pages
    metrics = ROOT / "data" / "phase5_metrics.json"
    if metrics.exists():
        con.execute("INSERT OR REPLACE INTO meta VALUES ('coverage', ?)", (metrics.read_text(),))
    cases = ROOT / "data" / "investigation_cases.json"
    if cases.exists():
        d = json.loads(cases.read_text())
        con.execute("INSERT OR REPLACE INTO meta VALUES ('cases_summary', ?)", (json.dumps(
            {"reviewed": d["reviewed"], "retained": d["retained"], "as_of": d["as_of"], "weights": d["weights"]}),))
    from ingest.area import AREA
    con.execute("INSERT OR REPLACE INTO meta VALUES ('area', ?)", (json.dumps(
        {"area_name": AREA.area_name, "city": AREA.city, "slug": AREA.slug}),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('published_at', ?)", (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    con.commit()
    bad = leaks(con)
    if bad:
        con.close()
        tmp.unlink()
        raise SystemExit(f"publish aborted: {len(bad)} phone/e-mail patterns survived, e.g. {bad[:5]}")
    con.execute("VACUUM")
    con.close()
    tmp.rename(dst)
    os.chmod(dst, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)       # 444: the web process never needs to write
    return n


def export_dataset(db=PUBLIC_DB, out=DATASET):
    """CSV / JSON files for the public dataset (generated metadata only — no downloaded documents)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    queries = {
        "projects.csv": "SELECT id, slug, title, category, location, wards, department, job_numbers, tender_numbers, status, "
                        "estimated_cost, contract_value, contractor, payments_gross, n_bills, first_date, last_activity, lifecycle, "
                        "link_confidence, n_signals, signal_level FROM projects ORDER BY id",
        "facts.csv": "SELECT project_id, field, stage, value, confidence, explanation, evidence FROM facts ORDER BY project_id, field",
        "signals.csv": "SELECT project_id, code, severity, title, detail, confidence, evidence FROM signals ORDER BY project_id",
        "work_items.csv": "SELECT project_id, description, normalized_category, code, unit, plan_basis, estimated_quantity, "
                          "estimated_rate, estimated_amount, billed_quantity, measured_quantity, compared_quantity, compared_basis, "
                          "variance_pct, min_confidence, cross_validated, provenance FROM work_items",
        "bills.csv": "SELECT project_id, job_number, bill_type, gross, deduction, nett, br_no, br_date, payment_ref, payment_date, "
                     "payment_status, contractor, source_id, source_url FROM records WHERE record_type = 'bill' ORDER BY project_id",
        "sources.csv": "SELECT * FROM sources",
        "discovery.csv": "SELECT * FROM discovery",
    }
    manifest = {}
    for name, sql in queries.items():
        rows = con.execute(sql).fetchall()
        with open(out / name, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(rows[0].keys() if rows else [])
            w.writerows([tuple(r) for r in rows])
        manifest[name] = len(rows)
    cases = [json.loads(r["data"]) for r in con.execute("SELECT data FROM cases ORDER BY retained DESC, rank")]
    (out / "cases.json").write_text(json.dumps(cases, indent=1, ensure_ascii=False))
    manifest["cases.json"] = len(cases)
    con.close()
    for f in sorted(out.iterdir()):
        manifest[f.name] = {"rows": manifest.get(f.name), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1))
    return manifest


if __name__ == "__main__":
    n = build_public_db()
    m = export_dataset()
    print(json.dumps({"contact_patterns_removed": n, "public_db": str(PUBLIC_DB.relative_to(ROOT)),
                      "dataset": {k: v["rows"] for k, v in m.items()}}, indent=1))
