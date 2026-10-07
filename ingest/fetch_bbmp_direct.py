"""Direct collector for BBMP IFMS "Works Bill" public endpoints.

Phase 3 found the old host (account.bbmpgov.in, 117.236.190.54) unreachable. Phase 4 found the
service has moved: the public, login-free pages now live at

  https://accounts.bbmp.gov.in/PublicView/   (Works Bill Public View)
  https://accounts.bbmp.gov.in/vssWB/        (Works Bill viewer)  <- used here
  https://accounts.bbmp.gov.in/vssIFMS/Files<raddl>/<file>        (attached documents)

JSON actions used (POST, no authentication — exactly what the public page's own JavaScript calls):
  LoadDetails            &pWorkBillID=<id>  bill type, BR/commonBR, RTGS, release %, category,
                                            current approval level, gross/deduction/nett
  LoadDeductions         &pWorkBillID=<id>  IT, fine, mobilisation advance, withheld, audit recovery …
  LoadGridApprovalLevels &pWorkBillID=<id>  approval chain with officials' remarks and timestamps
  LoadWBFiles            &pWorkBillID=<id>  work-order level documents (estimate, AS, TS, agreement,
                                            work order, job certificate, DPR, tender documents …)
  LoadFilesDetails       &pMainID=<id>&pCheck=1  bill level documents (bill form, completion
                                            certificate, quality certificate, MB, before/after photos …)
NOT used: LoadAllPhotos&pMainID=<id> — its id is a DC-bill main id, not the work-bill id; called with a
work-bill id it returns other works' photos (coordinates far outside Koramangala).

`<id>` is the OpenCity `id` column we already hold. Contractor mobile/e-mail returned by LoadDetails
are dropped before anything is written. Requests are rate-limited (1/s) and every response is cached.

Usage: python -m ingest.fetch_bbmp_direct [--check] [--no-files]
Output: data/raw/bbmp_direct/bills/<id>.json, data/raw/bbmp_direct/files/<name>, ACCESS_REPORT.json
"""
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "bbmp_direct"
HOST = "accounts.bbmp.gov.in"
PAGE = f"https://{HOST}/vssWB/"
API = f"https://{HOST}/vssWB/vss00CvStatusData.php"
FILES = f"https://{HOST}/vssIFMS/Files{{addl}}/{{name}}"
OLD_HOST = "account.bbmpgov.in"
DELAY = 1.0
REDACT = {"contractormobile1", "contractoremail", "mobile", "email"}
# documents worth downloading (others — DPR, full tender documents, estimates — are listed but not fetched)
WANT_TYPES = ("photo", "completion", "work order", "agreement", "acceptance", "loa", "extension", "eot", "job certificate")
MAX_PHOTOS_PER_BILL = 3
MAX_FILE = 12_000_000

def _tls_context():
    """accounts.bbmp.gov.in serves only its leaf certificate (no GoDaddy G2 intermediate), so normal
    verification fails. Keep verification ON and add the public intermediate (fetched from
    http://certificates.godaddy.com/repository/gdig2.crt, stored in ingest/certs/)."""
    import ssl
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    pem = Path(__file__).parent / "certs" / "godaddy_g2_intermediate.pem"
    if pem.exists():
        ctx.load_verify_locations(cafile=str(pem))
    return ctx


_cj = CookieJar()
_op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj), urllib.request.HTTPSHandler(context=_tls_context()))
_op.addheaders = [("User-Agent", "Mozilla/5.0 (bengaluru-public-works civic research; cached, 1 req/s)")]


def reachable(host, timeout=8):
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        return {"host": host, "dns": None, "tcp443": False, "error": f"DNS: {e}"}
    try:
        with socket.create_connection((ip, 443), timeout=timeout):
            return {"host": host, "dns": ip, "tcp443": True}
    except OSError as e:
        return {"host": host, "dns": ip, "tcp443": False, "error": f"{e.__class__.__name__}: {e}"}


def _open(url, data=None, timeout=90, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"X-Requested-With": "XMLHttpRequest", "Referer": PAGE})
            with _op.open(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404):
                return None, f"HTTP {e.code}"
            last = e
        except Exception as e:  # network hiccup: back off and retry
            last = e
        time.sleep(3 * (i + 1))
    return None, f"error: {last}"


def call(action, **params):
    url = API + "?" + urllib.parse.urlencode({"pAction": action, **params})
    body, ct = _open(url, data=b"")
    time.sleep(DELAY)
    if body is None:
        return {"error": ct}, url
    try:
        data = json.loads(body)
    except ValueError:
        data = {"raw": body[:500].decode("utf-8", "replace")} if body.strip() else None
    return redact(data), url


def redact(d):
    if isinstance(d, dict):
        return {k: (None if k in REDACT else redact(v)) for k, v in d.items()}
    if isinstance(d, list):
        return [redact(x) for x in d]
    return d


def file_url(addl, name):
    """Files live under /vssIFMS/Files<raddl>/ — raddl is often empty (-> /Files/), sometimes "1"."""
    return FILES.format(addl=addl or "", name=urllib.parse.quote(name))


def wanted(ftype):
    t = (ftype or "").lower()
    return any(w in t for w in WANT_TYPES) and "dpr" not in t and "tender doc" not in t and "pmc" not in t


def job_numbers():
    """BBMP job numbers of the projects in the current database (Koramangala only)."""
    import sqlite3
    db = ROOT / "data" / "koramangala.db"
    if not db.exists():
        return []
    con = sqlite3.connect(db)
    jobs = sorted({j for (js,) in con.execute("SELECT job_numbers FROM projects WHERE job_numbers IS NOT NULL")
                   for j in js.split(",") if j})
    con.close()
    try:   # plus every Koramangala job found in the public payment grid (Phase 5)
        from ingest import discover_bbmp
        jobs = sorted(set(jobs) | {r["job_number"] for r in discover_bbmp.koramangala() if r["job_number"]})
    except Exception:
        pass
    return jobs


def discover_job_bills(jobs):
    """LoadTypeCombo&pJobNumber=<job>&pSelection=1|2|3 lists the work bills of a job number
    (selection 1 = bills with payment details; 2/3 = other statuses). Cached per job."""
    (OUT / "jobs").mkdir(parents=True, exist_ok=True)
    found = {}
    for job in jobs:
        dest = OUT / "jobs" / f"{job}.json"
        if dest.exists():
            rows = json.loads(dest.read_text())
        else:
            rows = []
            for sel in (1, 2, 3):
                d, url = call("LoadTypeCombo", pJobNumber=job, pSelection=sel)
                for r in d if isinstance(d, list) else []:
                    rows.append({**r, "selection": sel, "url": url})
            dest.write_text(json.dumps(rows, indent=1))
        for r in rows:
            if r.get("wbid"):
                found[str(r["wbid"])] = job
    return found


def bill_ids():
    """Work-bill ids (OpenCity `id`) of every Koramangala bill in the current build."""
    from ingest import bills as B
    br, _, _ = B.load_bills(B.load_sources())
    return sorted({(b.get("extra") or {}).get("ifms_id") for b in B.dedupe_bills(br)} - {None}, key=int)


def main(check_only=False, files=True):
    (OUT / "bills").mkdir(parents=True, exist_ok=True)
    (OUT / "files").mkdir(parents=True, exist_ok=True)
    rep = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "old_host": reachable(OLD_HOST), "host": reachable(HOST)}
    ids = bill_ids()
    rep["work_bill_ids_known"] = len(ids)
    if not rep["host"]["tcp443"]:
        rep["status"] = f"blocked: {HOST} not reachable from this network"
    else:
        page, _ = _open(PAGE)
        rep["status"] = "reachable; public page loads without login" if page and b"vssWB" in page else "reachable; page did not load"
    (OUT / "ACCESS_REPORT.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    if check_only or not rep["host"]["tcp443"]:
        return
    discovered = discover_job_bills(job_numbers())
    rep["work_bill_ids_discovered_by_job"] = len(discovered)
    ids = sorted(set(ids) | set(discovered), key=int)
    rep["work_bill_ids_total"] = len(ids)
    print("work bills to fetch:", len(ids))
    n_new = n_files = 0
    have_photo = set()        # (job, photo type): one before / during / after photo per job is enough
    for wbid in ids:
        dest = OUT / "bills" / f"{wbid}.json"
        if dest.exists():
            rec = json.loads(dest.read_text())
        else:
            rec = {"work_bill_id": wbid, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            for key, action, params in (("details", "LoadDetails", {"pWorkBillID": wbid}),
                                        ("deductions", "LoadDeductions", {"pWorkBillID": wbid}),
                                        ("approvals", "LoadGridApprovalLevels", {"pWorkBillID": wbid}),
                                        ("wo_files", "LoadWBFiles", {"pWorkBillID": wbid}),
                                        ("bill_files", "LoadFilesDetails", {"pMainID": wbid, "pCheck": 1})):
                rec[key], rec[key + "_url"] = call(action, **params)
            dest.write_text(json.dumps(rec, indent=1))
            n_new += 1
            print("bill", wbid, (rec.get("details") or {}).get("billtype") if isinstance(rec.get("details"), dict) else "?")
        if not files:
            continue
        for kind in ("wo_files", "bill_files"):
            n_photo = 0
            for f in rec.get(kind) or []:
                if not isinstance(f, dict):
                    continue
                name, ftype = f.get("rFileName"), f.get("rFileType")
                if not name or not wanted(ftype):
                    continue
                if "photo" in (ftype or "").lower():
                    n_photo += 1
                    key = (discovered.get(str(wbid), wbid), ftype)
                    if n_photo > MAX_PHOTOS_PER_BILL or key in have_photo:
                        continue
                    have_photo.add(key)
                fdest = OUT / "files" / name.replace("/", "_")
                if fdest.exists():
                    continue
                body, ct = _open(file_url(f.get("raddl") or f.get("rAddl"), name), timeout=180)
                time.sleep(DELAY)
                if body and len(body) <= MAX_FILE:
                    fdest.write_bytes(body)
                    n_files += 1
    rep.update(bills_fetched_now=n_new, files_fetched_now=n_files,
               bills_cached=len(list((OUT / "bills").glob("*.json"))), files_cached=len(list((OUT / "files").iterdir())))
    (OUT / "ACCESS_REPORT.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep[k] for k in ("bills_fetched_now", "files_fetched_now", "bills_cached", "files_cached")}))


def load_cache():
    """{work_bill_id: record} for whatever has been collected."""
    out = {}
    for p in (OUT / "bills").glob("*.json") if (OUT / "bills").exists() else []:
        try:
            r = json.loads(p.read_text())
            out[str(r["work_bill_id"])] = r
        except (ValueError, KeyError):
            pass
    return out


def invalidate_active(days=730, today=None):
    """Make the next run re-check what can still change: the bill list of every active job (no final bill paid,
    or a bill registered / paid within `days`) and every bill not yet paid. Closed jobs and paid bills stay cached
    (a paid bill's amounts, dates and documents do not change). Returns (jobs, bills) invalidated."""
    from datetime import date, timedelta
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    cache = load_cache()
    jobs_dir = OUT / "jobs"
    n_jobs = n_bills = 0
    for jf in sorted(jobs_dir.glob("*.json")) if jobs_dir.exists() else []:
        rows = json.loads(jf.read_text())
        bills = [cache.get(str(r.get("wbid"))) for r in rows if r.get("wbid")]
        dets = [b.get("details") for b in bills if b and isinstance(b.get("details"), dict)]
        final_paid = any("final" in (d.get("billtype") or "").lower() and (d.get("rtgs") or "").strip() for d in dets)
        recent = any(max(_iso(d.get("sbrdate")), _iso(d.get("rtgsdate"))) >= cutoff for d in dets)
        if not final_paid or recent or not dets:
            jf.unlink()
            n_jobs += 1
            for b in bills:
                d = (b or {}).get("details")
                if b and isinstance(d, dict) and not (d.get("rtgs") or "").strip():
                    (OUT / "bills" / f"{b['work_bill_id']}.json").unlink(missing_ok=True)
                    n_bills += 1
    return n_jobs, n_bills


def _iso(s):
    """'2025-07-01' / '01-Jul-2025' -> '2025-07-01'; '' when unreadable."""
    from datetime import datetime
    s = (s or "").strip()[:11]
    for fmt in ("%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


if __name__ == "__main__":
    if "--refresh-active" in sys.argv:
        print("invalidated (jobs, unpaid bills):", invalidate_active())
    main(check_only="--check" in sys.argv, files="--no-files" not in sys.argv)
