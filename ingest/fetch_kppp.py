"""Fetch Koramangala works tenders from the Karnataka Public Procurement Portal (KPPP).

Uses the same public JSON endpoints the kppp.karnataka.gov.in web app calls:
  POST /portal-service/works/search-eproc-tenders      (search by title keyword)
  GET  /portal-service/{nitId}/works-tender-full-view   (estimate, dept, dates, award)
  GET  /portal-service/bids/{nitId}/tender-category/WORKS/get-selected-bid-for-lumpsum
                                                        (awarded bidder + bid/negotiated value)
Results are cached as JSON under data/raw/kppp/.

Usage: python -m ingest.fetch_kppp [--force]
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ingest.sources import KPPP_API

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "kppp"
from ingest.area import AREA                  # noqa: E402
KEYWORDS = AREA.kppp_keywords
STATUSES = ["PUBLISHED", "BIDDING_CLOSED", "UNDER_EVALUATION", "EVALUATED", "AWARDED",
            "FINALIZED", "CLOSED", "CANCELLED", "NO_BIDS_RECEIVED"]
TENDER_TYPES = ["OPEN", "RESERVED", "RESTRICTED"]
HDRS = {"User-Agent": "Mozilla/5.0 (bengaluru-public-works civic research)", "Content-Type": "application/json"}


def _req(url, body=None, retries=3):
    data = json.dumps(body).encode() if body is not None else None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=HDRS, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 500):  # some status filters are rejected by the server
                return None, {}
            time.sleep(2 * (i + 1))
        except Exception:
            time.sleep(2 * (i + 1))
    return None, {}


def search():
    found = {}
    for kw in KEYWORDS:
        for tt in TENDER_TYPES:
            for st in STATUSES:
                page = 0
                while True:
                    body = {"category": "WORKS", "status": st, "tenderType": tt, "title": kw}
                    rows, h = _req(f"{KPPP_API}/works/search-eproc-tenders?page={page}&size=50&order-by-tender-publish=true", body)
                    if not rows:
                        break
                    for r in rows:
                        found[r["nitId"]] = r
                    total = int(h.get("x-total-count") or h.get("X-Total-Count") or 0)
                    page += 1
                    if page * 50 >= total:
                        break
    return found


def main(force=False):
    OUT.mkdir(parents=True, exist_ok=True)
    found = search()
    print(f"KPPP search: {len(found)} works tenders mention Koramangala")
    (OUT / "search.json").write_text(json.dumps(list(found.values()), indent=1))
    for nit in found:
        dest = OUT / f"{nit}.json"
        if dest.exists() and not force and json.loads(dest.read_text()).get("selected_bid"):
            continue                 # awarded tenders do not change; tenders still in evaluation are re-checked
        full, _ = _req(f"{KPPP_API}/{nit}/works-tender-full-view")
        bid, _ = _req(f"{KPPP_API}/bids/{nit}/tender-category/WORKS/get-selected-bid-for-lumpsum")
        files, _ = _req(f"{KPPP_API}/{nit}/get-works-tender-files")
        if full:  # drop the bulky bill-of-quantities line items; keep the totals
            for se in full.get("tenderSubEstimateList") or []:
                se["itemCount"] = len(se.pop("itemList", None) or [])
            for k in ("generalCriterionList", "technicalCriterionList", "tenderCriterionDocumentList"):
                full.pop(k, None)
        dest.write_text(json.dumps({"search": found[nit], "full": full, "selected_bid": bid, "files": files}, indent=1))
        time.sleep(0.3)
    print("cached", len(list(OUT.glob("[0-9]*.json"))), "tender files")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
