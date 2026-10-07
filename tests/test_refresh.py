"""Monthly refresh: open-quarter logic, what-changed report, and which cached IFMS records get re-checked."""
import json
import sqlite3
from datetime import date

from ingest import changes as CH, discover_bbmp as D, fetch_bbmp_direct as F


def test_quarter_end_and_open_quarters():
    assert D.quarter_end(date(2026, 10, 7)) == date(2026, 12, 31)
    assert D.quarter_end(date(2026, 2, 1)) == date(2026, 3, 31)
    assert D.quarter_end(date(2025, 12, 31)) == date(2025, 12, 31)
    qs = list(D.quarters(date(2026, 4, 1), date(2026, 10, 7)))
    assert qs[-1] == (date(2026, 10, 1), date(2026, 10, 7))          # current quarter fetched only up to today


def _db(path, projects, bills, signals=(), finals=(), cases=()):
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE projects (id INTEGER, slug TEXT, title TEXT, status TEXT, payments_gross REAL, n_bills INT, job_numbers TEXT);
                       CREATE TABLE records (project_id INT, record_type TEXT, job_number TEXT, bill_type TEXT, gross REAL, br_no TEXT,
                                             br_date TEXT, payment_date TEXT, contractor TEXT);
                       CREATE TABLE signals (project_id INT, code TEXT);
                       CREATE TABLE facts (project_id INT, field TEXT, value TEXT);
                       CREATE TABLE cases (case_id TEXT, job_number TEXT, retained INT, status TEXT, primary_issue TEXT);
                       CREATE TABLE meta (key TEXT, value TEXT);""")
    c.executemany("INSERT INTO projects VALUES (?,?,?,?,?,?,?)", projects)
    c.executemany("INSERT INTO records VALUES (?, 'bill', ?,?,?,?,?,?,?)", bills)
    c.executemany("INSERT INTO signals VALUES (?,?)", signals)
    c.executemany("INSERT INTO facts VALUES (?, 'final_bill', ?)", finals)
    c.executemany("INSERT INTO cases VALUES (?,?,?,?,?)", cases)
    c.commit()
    c.close()


def test_changes_report(tmp_path):
    old, new = tmp_path / "old.db", tmp_path / "new.db"
    p1 = (1, "151-20-000001", "Roads in 6th block", "Running bill only", 1e7, 1, "151-20-000001")
    _db(old, [p1], [(1, "151-20-000001", "Running", 1e7, "1", "2025-01-01", None, "A")],
        signals=[(1, "slow_payment")], cases=[("KP6-151-20-000001", "151-20-000001", 1, "needs RTI", "x")])
    p1n = p1[:3] + ("Completed (final bill)", 3e7) + p1[5:]
    p2 = (2, "174-26-000001", "New drain", "Running bill only", 5e6, 1, "174-26-000001")
    _db(new, [p1n, p2], [(1, "151-20-000001", "Running", 1e7, "1", "2025-01-01", "2025-02-01", "A"),
                         (1, "151-20-000001", "Final", 2e7, "2", "2026-09-01", "2026-09-20", "A"),
                         (2, "174-26-000001", "Running", 5e6, "1", "2026-09-10", None, "B")],
        signals=[(2, "slow_payment")], finals=[(1, "2026-09-01")],
        cases=[("KP6-151-20-000001", "151-20-000001", 0, "set aside", "x")])
    d = CH.diff(CH.snapshot(old), CH.snapshot(new))
    assert d["added"] == ["174-26-000001"] and len(d["new_bills"]) == 2 and len(d["newly_paid"]) == 1
    assert d["finals"] == ["151-20-000001"] and d["status"][0][2] == "Completed (final bill)"
    assert ("151-20-000001", "slow_payment") in d["sig_gone"] and ("174-26-000001", "slow_payment") in d["sig_new"]
    assert d["cases"][0][1] == "open → set aside"
    text = CH.render(d, CH.snapshot(new), when="2026-11-01")
    assert "## Data refresh 2026-11-01" in text and "Final bills recorded" in text and "open → set aside" in text
    assert "fraud" not in text.lower()


def test_unchanged_builds_say_so(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for p in (a, b):
        _db(p, [(1, "x", "t", "s", 1.0, 1, "j")], [(1, "j", "Final", 1.0, "1", "2020-01-01", "2020-02-01", "A")])
    assert "No changes." in CH.render(CH.diff(CH.snapshot(a), CH.snapshot(b)), CH.snapshot(b))


def test_refresh_rechecks_only_active_jobs_and_unpaid_bills(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "OUT", tmp_path)
    (tmp_path / "jobs").mkdir()
    (tmp_path / "bills").mkdir()
    def bill(wbid, billtype, rtgs, date_):
        (tmp_path / "bills" / f"{wbid}.json").write_text(json.dumps(
            {"work_bill_id": wbid, "details": {"billtype": billtype, "rtgs": rtgs, "sbrdate": date_, "rtgsdate": ""}}))
    bill("1", "First and Final", "000123", "2018-01-01")           # closed, old job -> kept
    bill("2", "Running", "000124", "2026-08-01")                  # recent running bill -> job re-checked
    bill("3", "Running", "", "2026-09-01")                        # unpaid -> bill re-fetched
    (tmp_path / "jobs" / "151-17-000001.json").write_text(json.dumps([{"wbid": 1}]))
    (tmp_path / "jobs" / "174-25-000001.json").write_text(json.dumps([{"wbid": 2}, {"wbid": 3}]))
    jobs, bills = F.invalidate_active(today=date(2026, 10, 7))
    assert (jobs, bills) == (1, 1)
    assert (tmp_path / "jobs" / "151-17-000001.json").exists() and not (tmp_path / "jobs" / "174-25-000001.json").exists()
    assert (tmp_path / "bills" / "2.json").exists() and not (tmp_path / "bills" / "3.json").exists()
