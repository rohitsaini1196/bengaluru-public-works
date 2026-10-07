"""Download the BBMP work-order / bill CSVs (mirrored on data.opencity.in) into data/raw/.

Usage: python -m ingest.fetch_opencity [--force]
"""
import json
import sys
import urllib.request
from pathlib import Path

from ingest.sources import SOURCES, LEGACY_DATASET, LEGACY_NAME_FILTER

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
UA = {"User-Agent": "bengaluru-public-works/1.0 (civic data research)"}


def _get(url, timeout=300):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def legacy_sources():
    """Resolve legacy 2010-19 register files from the CKAN API."""
    meta = json.loads(_get(f"https://data.opencity.in/api/3/action/package_show?id={LEGACY_DATASET}"))
    out = []
    for r in meta["result"]["resources"]:
        if any(k in r["name"] for k in LEGACY_NAME_FILTER):
            fname = r["name"].replace(" ", "_").replace("(", "").replace(")", "") + ".csv"
            kind = ("bbmp_billreg" if "Bill Register" in r["name"]
                    else "bbmp_jobcodes" if "Job Codes" in r["name"]
                    else "bbmp_nagarothana" if "Nagarothana" in r["name"]
                    else "bbmp_dept")
            out.append(dict(file="legacy/" + fname, kind=kind, regime="198" if "Zone" in r["name"] else "dept",
                            name=r["name"], page=f"https://data.opencity.in/dataset/{LEGACY_DATASET}", url=r["url"]))
    return out


def fetch_tender_lists(force=False):
    """BBMP tender lists 2013-18 (dataset bbmp-tenders) -> data/raw/tenders_oc/ + list.tsv."""
    out = RAW / "tenders_oc"
    out.mkdir(exist_ok=True)
    meta = json.loads(_get("https://data.opencity.in/api/3/action/package_show?id=bbmp-tenders"))
    lines = []
    for r in meta["result"]["resources"]:
        fname = r["name"].replace(" ", "_").replace("(", "").replace(")", "") + ".csv"
        lines.append(f"{fname}\t{r['url']}")
        dest = out / fname
        if force or not dest.exists():
            print("fetch", fname)
            dest.write_bytes(_get(r["url"]))
    (out / "list.tsv").write_text("\n".join(lines) + "\n")


def main(force=False):
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "legacy").mkdir(exist_ok=True)
    srcs = SOURCES + legacy_sources()
    (RAW / "manifest.json").write_text(json.dumps(srcs, indent=1))
    for s in srcs:
        dest = RAW / s["file"]
        if dest.exists() and dest.stat().st_size > 0 and not force:
            continue
        print("fetch", s["file"])
        dest.write_bytes(_get(s["url"]))
    fetch_tender_lists(force)
    print(f"{len(srcs)} bill source files + BBMP tender lists present in {RAW}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
