"""Merge direct BBMP IFMS evidence (ingest.fetch_bbmp_direct cache) into bills and projects.

For every cached work bill (OpenCity id or discovered through LoadTypeCombo by job number):
  * match it to a bill we already hold: same job + BR number/date, else same job + gross amount;
    otherwise add it as a new bill (source: BBMP IFMS direct)
  * copy official fields: bill type (running/final), SBR/BR/common-BR, RTGS no./date, release %,
    category, current approval level, deductions (fine, mobilisation advance, withheld, audit recovery…)
  * keep the approval chain (officials' remarks with timestamps)
  * list attached documents and photos; mine work orders / agreements / LoAs / completion
    certificates with ingest.documents (OCR); read GPS printed on site photos.
Every value keeps its IFMS URL (LoadDetails…&pWorkBillID=<id>) or document URL as provenance.
"""
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ingest import documents, fetch_bbmp_direct as F

ROOT = Path(__file__).resolve().parent.parent
DIRECT = ROOT / "data" / "raw" / "bbmp_direct"
DOC_TYPES_MINE = ("work order", "agreement", "job certificate", "completion", "loa", "letter of acceptance", "pmc work order")
PHOTO_TYPES = ("photo",)


def _d(s):
    """'24-Jan-2024' / '2024-01-24' / '24-Jan-2024<br/> 13:52:05' -> ISO date."""
    s = re.sub(r"<br\s*/?>.*", "", str(s or "")).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(s[:11].strip(), fmt).date()
            return d.isoformat() if d.year > 1950 else None
        except ValueError:
            pass
    return None


def _f(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def load():
    cache = F.load_cache()
    job_of = {}
    for p in (DIRECT / "jobs").glob("*.json") if (DIRECT / "jobs").exists() else []:
        for r in json.loads(p.read_text()):
            if r.get("wbid"):
                job_of[str(r["wbid"])] = (p.stem, r)
    return cache, job_of


def file_url(f):
    return F.file_url(f.get("raddl") or f.get("rAddl"), f["rFileName"])


def bill_from_direct(wbid, rec, job):
    d = rec.get("details") if isinstance(rec.get("details"), dict) else {}
    ded = rec.get("deductions") if isinstance(rec.get("deductions"), dict) else {}
    url = rec.get("details_url")
    extra_ded = {k: _f(ded.get(k)) for k in ("fine", "mobilizationadvance", "it", "fsd", "royalty", "labourcess", "md",
                                            "otheradvances", "others2", "others3", "others4", "addlsd", "addlauditrec")
                 if _f(ded.get(k))}
    cap = {"others2": "reserve", "others3": "withheld", "others4": "audit_recovery"}
    extra_ded = {cap.get(k, k): v for k, v in extra_ded.items()}
    approvals = []
    for a in rec.get("approvals") if isinstance(rec.get("approvals"), list) else []:
        approvals.append({"date": _d(a.get("date")), "by": re.sub(r"<br\s*/?>", " / ", a.get("name") or ""),
                          "remarks": re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", " — ", a.get("remarks") or "")).strip(" —"),
                          "stage": a.get("systemtype")})
    return {
        "wbid": wbid, "job_number": job, "description": d.get("wcdescription"), "bill_type": d.get("billtype"),
        "gross": _f(d.get("gross")), "deduction": _f(d.get("deduction")), "nett": _f(d.get("nett")),
        "sbr_no": d.get("sbrnumber"), "sbr_date": _d(d.get("sbrdate")), "br_no": d.get("dbrnumber"), "br_date": _d(d.get("dbrdate")),
        "common_br": d.get("commonbrnumber"), "rtgs": d.get("rtgs"), "rtgs_date": _d(d.get("rtgsdate")),
        "release_pct": _f(d.get("releaseper")), "release_gross": _f(d.get("releasegross")),
        "category": d.get("category"), "level": d.get("currentlevelname"), "budget": d.get("budget"), "ddo": d.get("ddoname"),
        "contractor_raw": d.get("contractorname"), "deductions": extra_ded, "approvals": approvals,
        "wo_files": [dict(f, _url=file_url(f)) for f in rec.get("wo_files") or [] if isinstance(f, dict) and f.get("rFileName")],
        "bill_files": [dict(f, _url=file_url(f)) for f in rec.get("bill_files") or [] if isinstance(f, dict) and f.get("rFileName")],
        "url": url, "fetched_at": rec.get("fetched_at"),
    }


def _exif(path):
    """(EXIF DateTimeOriginal as ISO, looks-like-a-scanned-A4-sheet). For scans the EXIF time is the scan time."""
    if path.suffix.lower() not in (".jpg", ".jpeg"):
        return None, path.suffix.lower() == ".pdf"
    try:
        from PIL import Image
        im = Image.open(path)
        ex = im._getexif() or {}
        raw = ex.get(36867) or ex.get(306)
        dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").isoformat() if raw else None
        w, h = im.size
        scan = max(w, h) >= 3000 and abs(max(w, h) / min(w, h) - 1.414) < 0.05
        return dt, scan
    except Exception:
        return None, None


def mine_documents(files):
    """Run the document miner over downloaded work-order level files. Returns facts with provenance."""
    out = []
    for f in files:
        t = (f.get("rFileType") or "").lower()
        if not any(k in t for k in DOC_TYPES_MINE):
            continue
        path = documents.FILES / f["rFileName"].replace("/", "_")
        if not path.exists():
            continue
        is_cc = "completion" in t
        for x in documents.extract(path):
            if x["field"] == "date_of_completion":
                # in a completion report/certificate a bare "Date of completion" is the actual date;
                # in a work order it is the stipulated one
                x = dict(x, field="completed_on" if is_cc else "completion_due")
            out.append({**x, "doc": f["rFileName"], "doc_type": f.get("rFileType"), "url": file_url(f)})
    return out


def photos(files, bill):
    """Site photos attached to a bill, with GPS printed on the image (OCR) where present."""
    out = []
    for f in files:
        t = (f.get("rFileType") or "")
        if not any(k in t.lower() for k in PHOTO_TYPES) and not re.search(r"\.(jpe?g|png)$", f["rFileName"], re.I):
            continue
        path = documents.FILES / f["rFileName"].replace("/", "_")
        gps, size, digest, exif_dt, scan = None, None, None, None, None
        if path.exists():
            size = path.stat().st_size
            if path.suffix.lower() in (".jpg", ".jpeg", ".png") and size >= 8000:   # camera images only (GPS overlays)
                for x in documents.extract(path):
                    if x["field"] == "gps":
                        gps = x["value"]
                        break
            import hashlib
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            exif_dt, scan = _exif(path)
        placeholder = bool(re.search(r"\b(e?mp?ty|emty|blank|dummy|nil|not\s*applicable|na)\b", f["rFileName"], re.I)) or \
            (size is not None and size < 8000)
        out.append({"wbid": bill["wbid"], "file": f["rFileName"], "type": t, "url": file_url(f), "placeholder": placeholder,
                    "bill_date": bill.get("br_date") or bill.get("rtgs_date"), "gps": gps, "bytes": size, "md5": digest,
                    "image_time": exif_dt, "scanned_sheet": scan,
                    "downloaded": path.exists()})
    return out


def _hashes(files):
    """md5 of every downloaded attachment: {md5: [(type, file, url, bytes)]}."""
    import hashlib
    out = defaultdict(list)
    for f in files:
        path = documents.FILES / f["rFileName"].replace("/", "_")
        if path.exists():
            out[hashlib.md5(path.read_bytes()).hexdigest()].append((f.get("rFileType") or "", f["rFileName"], file_url(f), path.stat().st_size))
    return dict(out)


def _dedupe_photos(ps):
    out, seen = [], set()
    for x in ps:
        if x["file"] not in seen:
            seen.add(x["file"])
            out.append(x)
    return out


def merge(bills_by_job):
    """bills_by_job: {job: [bill records]} from the exports. Enrich in place; return per-job direct info."""
    cache, job_of = load()
    if not cache:
        return {}, {"direct_bills": 0}
    stats = defaultdict(int)
    by_job = defaultdict(list)
    for wbid, rec in cache.items():
        job = job_of.get(wbid, (None,))[0]
        if not job:
            # OpenCity ids we fetched directly: find the job from our own bills
            job = next((j for j, bs in bills_by_job.items() for b in bs if str((b.get("extra") or {}).get("ifms_id")) == wbid), None)
        if not job:
            stats["direct_unplaced"] += 1
            continue
        by_job[job].append(bill_from_direct(wbid, rec, job))
    info = {}
    collisions = []
    from difflib import SequenceMatcher
    from ingest import scope as _scope
    norm = lambda t: re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())
    for job, dbs in list(by_job.items()):
        ours0 = bills_by_job.get(job, [])
        known_ids = {str((b.get("extra") or {}).get("ifms_id")) for b in ours0}
        known_desc = [norm(b.get("description")) for b in ours0 if b.get("description")]
        keep = []
        for db_ in dbs:
            d = norm(db_["description"])
            same = (db_["wbid"] in known_ids or not d.strip()
                    or any(SequenceMatcher(None, d[:200], k[:200]).ratio() >= 0.6 for k in known_desc)
                    or (_scope.scope_reason(db_["description"]) is not None))
            if same:
                keep.append(db_)
            else:
                # job numbers repeat across ward maps (e.g. 186-23-000001 is both a Koramangala and a
                # Jaraganahalli job): a bill whose own description is another work is not ours
                collisions.append({"job": job, "wbid": db_["wbid"], "description": db_["description"], "url": db_["url"]})
        by_job[job] = keep
    stats["direct_job_number_collisions"] = len(collisions)
    for job, dbs in by_job.items():
        ours = bills_by_job.get(job, [])
        pending = []
        for db_ in dbs:
            stats["direct_bills"] += 1
            m = next((b for b in ours if str((b.get("extra") or {}).get("ifms_id")) == db_["wbid"]), None)
            if not m and db_["br_no"]:
                m = next((b for b in ours if (b.get("br_no") or "").lstrip("0") == db_["br_no"].lstrip("0")
                          and (not b.get("br_date") or b["br_date"] == db_["br_date"])), None)
            if not m and db_["gross"]:
                m = next((b for b in ours if b.get("gross") and abs(b["gross"] - db_["gross"]) < 1 and not b.get("direct")), None)
            paid = bool((db_["rtgs"] or "").strip()) or (db_["level"] or "").upper() == "RTGS"
            db_["paid"] = paid
            if m:
                stats["direct_matched_existing"] += 1
            elif not paid:
                # registered-but-unpaid or draft bill (no RTGS): evidence of work claimed, not money paid
                pending.append(db_)
                stats["direct_unpaid_bills"] += 1
                continue
            else:
                m = {"job_number": job, "description": db_["description"], "gross": db_["gross"], "deduction": db_["deduction"],
                     "nett": db_["nett"], "br_no": db_["br_no"], "br_date": db_["br_date"],
                     "payment_ref": f"RTGS {db_['rtgs']}" if db_["rtgs"] else None, "payment_date": db_["rtgs_date"],
                     "contractor": None, "source": {"id": "IFMS", "name": "BBMP IFMS Works Bill (direct)", "url": db_["url"],
                                                    "kind": "ifms_direct"},
                     "source_row": None, "dedupe_key": "IFMS", "regime": None, "ward": None, "added_from_direct": True,
                     "extra": {"ifms_id": db_["wbid"]}}
                ours.append(m)
                stats["direct_new_bills"] += 1
            m["direct"] = db_
            db_["in_records"] = True
            if db_["bill_type"] and not m.get("bill_type"):
                m["bill_type"] = db_["bill_type"]
                m["bill_type_source"] = "ifms_direct"
            if not m.get("payment_date") and db_["rtgs_date"]:
                m["payment_date"] = db_["rtgs_date"]
            m.setdefault("extra", {})["ifms_id"] = db_["wbid"]
        bills_by_job[job] = ours
        wo_files = {f["rFileName"]: f for b in dbs for f in b["wo_files"]}
        info[job] = {"docs": mine_documents(wo_files.values()) + mine_documents({f["rFileName"]: f for b in dbs for f in b["bill_files"]}.values()),
                     "photos": _dedupe_photos([ph for b in dbs for ph in photos(b["bill_files"] + b["wo_files"], b)
                                               if "photo" in (ph["type"] or "").lower()]),
                     "file_hashes": _hashes({f["rFileName"]: f for b in dbs for f in b["bill_files"] + b["wo_files"]}.values()),
                     "wo_files": list(wo_files.values()),
                     "approvals": {b["wbid"]: b["approvals"] for b in dbs},
                     "bills": dbs, "pending": pending}
        stats["jobs_with_direct"] += 1
    (DIRECT / "collisions.json").write_text(json.dumps(collisions, indent=1))
    return info, dict(stats)
