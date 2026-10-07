"""Download KPPP tender documents and commercial comparative statements, extract text.

For every cached Koramangala tender (data/raw/kppp/<nitId>.json):
  * tender files (KW-1..KW-4 tender documents, notifications) via
      GET /portal-service/{nitId}/works-tender-file/{uuid}/download-file
    -> period of completion, approximate value, IFT number
  * commercial comparative statement (all qualified bidders and their quoted totals) via
      GET /portal-service/tender-eval/{nitId}/commercial-evaluation/tender-category/WORKS/commercial-comparison/download-detailed
    -> number of bidders and each bidder's quoted amount
Files go to data/raw/kppp_docs/<nitId>/; extracted text is cached next to them as .txt.

Usage: python -m ingest.fetch_kppp_docs [--force]
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ingest.sources import KPPP_API

ROOT = Path(__file__).resolve().parent.parent
KPPP = ROOT / "data" / "raw" / "kppp"
OUT = ROOT / "data" / "raw" / "kppp_docs"
HDRS = {"User-Agent": "Mozilla/5.0 (bengaluru-public-works civic research)"}
TEXT_EXT = {".doc", ".docx", ".rtf", ".pdf"}


def _get(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 500):
                return None
            time.sleep(2 * (i + 1))
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def to_text(path):
    """Extract text with macOS textutil (doc/docx/rtf) or pypdf (pdf). Returns '' if unavailable."""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        if shutil.which("textutil"):
            res = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True, timeout=120)
            return res.stdout.decode("utf-8", "replace")
        if ext == ".docx":
            import zipfile, re
            xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "replace")
            return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))
    except Exception as e:  # corrupt/unsupported document: keep going, record nothing
        print("  text extraction failed", path.name, e)
    return ""


def main(force=False):
    OUT.mkdir(parents=True, exist_ok=True)
    tenders = sorted(KPPP.glob("[0-9]*.json"))
    for p in tenders:
        nit = p.stem
        j = json.loads(p.read_text())
        d = OUT / nit
        d.mkdir(exist_ok=True)
        manifest = d / "manifest.json"
        if manifest.exists() and not force and json.loads(manifest.read_text()).get("comparative"):
            continue                 # the comparative statement appears after evaluation: re-check until it exists
        files = []
        for f in j.get("files") or []:
            name, uuid = f.get("fileName"), f.get("uuid")
            if not uuid or not name:
                continue
            url = f"{KPPP_API}/{nit}/works-tender-file/{uuid}/download-file"
            dest = d / name.replace("/", "_")
            fetched = False
            if not dest.exists():            # documents already on disk are reused (only re-checks for the comparative)
                blob = _get(url)
                if not blob:
                    continue
                dest.write_bytes(blob)
                fetched = True
            txt_file = dest.with_suffix(dest.suffix + ".txt")
            txt = txt_file.read_text() if txt_file.exists() else (to_text(dest) if dest.suffix.lower() in TEXT_EXT else "")
            if txt and not txt_file.exists():
                txt_file.write_text(txt)
            files.append({"name": dest.name, "url": url, "documentType": f.get("documentType"), "has_text": bool(txt)})
            if fetched:
                time.sleep(0.2)
        cmp_url = f"{KPPP_API}/tender-eval/{nit}/commercial-evaluation/tender-category/WORKS/commercial-comparison/download-detailed"
        blob = _get(cmp_url)
        cmp = None
        if blob and blob[:2] == b"PK":  # xlsx
            (d / "comparative.xlsx").write_bytes(blob)
            cmp = {"name": "comparative.xlsx", "url": cmp_url}
        manifest.write_text(json.dumps({"nit": nit, "files": files, "comparative": cmp}, indent=1))
        print(nit, len(files), "files", "+ comparative" if cmp else "")
    print("done:", len(tenders), "tenders")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
