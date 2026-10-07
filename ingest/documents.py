"""Mine BBMP IFMS attachments (work orders, agreements, LoAs, completion certificates, photos).

Documents come from https://accounts.bbmp.gov.in/vssIFMS/Files<n>/<name> (listed by LoadWBFiles /
LoadFilesDetails) and are mostly scanned. Text is obtained from the PDF text layer when present,
otherwise by OCR (tesseract) of the page images; OCR text is cached next to the file as .ocr.txt.

Only high-confidence patterns are extracted, each with document + page provenance:
  contract_value       "tender price / agreement amount / contract value of Rs. X"
  period               "Time of Completion  N Months|Days"
  commencement_date    "Date of Commencement of Work dd.mm.yyyy"
  completion_due       "Date of Completion dd.mm.yyyy" (stipulated, in a work order)
  completed_on         "completed on dd.mm.yyyy" (completion certificate)
  tender_ref           tender notification number (BBMP/…/WORK_INDENTnnn on KPPP, or office numbers)
  loa_ref, agreement_ref, work_order_ref, go_ref   reference numbers with dates
  gps                  latitude/longitude printed on geotagged photos ("Lat 12.93…  Long 77.62…")
All OCR-derived facts are inferred (OCR can misread); the document link is always kept.
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "data" / "raw" / "bbmp_direct" / "files"

DATE = r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"


def _iso(d):
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$", d or "")
    if not m:
        return None
    dd, mm, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    yy = yy + 2000 if yy < 100 else yy
    if not 2005 <= yy <= 2030:
        return None
    try:
        from datetime import date
        return date(yy, mm, dd).isoformat()      # rejects impossible dates such as 31-09-2015 (OCR / typing errors)
    except ValueError:
        return None


def _money(s):
    try:
        return float(s.replace(",", ""))
    except (AttributeError, ValueError):
        return None


MAX_PAGES = 6


def pages_text(path, max_pages=None):
    """List of page texts (text layer if useful, else OCR). Cached as <file>.ocr.json."""
    cache = path.with_suffix(path.suffix + ".ocr.json")
    if cache.exists():
        return json.loads(cache.read_text())
    pages = []
    suf = path.suffix.lower()
    try:
        if suf == ".pdf":
            from pypdf import PdfReader
            import warnings
            warnings.filterwarnings("ignore")
            r = PdfReader(str(path))
            for i, p in enumerate(r.pages[:max_pages or MAX_PAGES]):
                t = p.extract_text() or ""
                if len(re.findall(r"[a-z]{4,}", t)) < 30:      # scanned page (or watermark noise) -> OCR
                    imgs = list(p.images)
                    if imgs:
                        im = max(imgs, key=lambda x: len(x.data)).image.convert("L")
                        tmp = path.with_suffix(f".p{i}.png")
                        im.save(tmp)
                        t = _ocr(tmp)
                        tmp.unlink()
                pages.append(t)
        elif suf in (".jpg", ".jpeg", ".png"):
            pages.append(_ocr(path))
    except Exception as e:  # corrupt or unsupported document: record and move on
        pages = [f"[unreadable: {e}]"]
    cache.write_text(json.dumps(pages))
    return pages


def _ocr(img):
    r = subprocess.run(["tesseract", str(img), "-", "--psm", "4"], capture_output=True, timeout=180)
    return r.stdout.decode("utf-8", "replace")


PATTERNS = [
    ("contract_value", re.compile(r"(?:tender\s+price|agreement\s+amount|contract\s+(?:value|amount)|accepted\s+tender\s+(?:value|amount))"
                                  r"[^0-9\n]{0,40}(?:Rs\.?|Rupees\.?|₹)?\s*" + NUM, re.I), "money"),
    ("period", re.compile(r"(?:time\s+(?:of|for)\s+completion|period\s+of\s+completion|stipulated\s+period)[^0-9\n]{0,25}(\d{1,3})\s*(months?|days?)", re.I), "period"),
    ("commencement_date", re.compile(r"date\s+of\s+commencement(?:\s+of)?\s*(?:the\s+)?(?:work|worle|wor)?\W{0,6}" + DATE, re.I), "date"),
    ("completion_due", re.compile(r"(?:scheduled|stipulated|intended)\s+date\s+of\s+completion\W{0,12}(?:\d{1,3}\s*(?:months?|days?)\W{0,6})?" + DATE, re.I), "date"),
    ("completed_on", re.compile(r"actual\s+date\s+of\s+completion\W{0,12}" + DATE, re.I), "date"),
    ("date_of_completion", re.compile(r"(?<!actual )(?<!scheduled )(?<!stipulated )(?<!intended )date\s+of\s+completion\W{0,12}" + DATE, re.I), "date"),
    ("completed_on", re.compile(r"(?:work\s+(?:has\s+been|was|is)\s+)?completed\s+(?:on|during)\s+" + DATE, re.I), "date"),
    ("sanctioned_estimate", re.compile(r"sanctioned\s+estimate(?:\s+amount)?\W{0,12}(?:Rs\.?|₹)?\s*" + NUM + r"\s*(lakhs?|lacs?|crores?)?", re.I), "money_unit"),
    ("agreement_amount", re.compile(r"amount\s+as\s+per\s+tender\s*/?\s*(?:agreement)?\W{0,25}(?:Rs\.?|₹)?\s*" + NUM, re.I), "money"),
    ("amount_spent", re.compile(r"(?:amount\s+spent|total\s+expenditure|actual\s+expenditure)\W{0,12}(?:Rs\.?|₹)?\s*" + NUM, re.I), "money"),
    ("work_slip", re.compile(r"amount\s+of\s+work\s*slip\W{0,12}(?:Rs\.?|₹)?\s*" + NUM, re.I), "money"),
    ("tender_premium", re.compile(r"tender\s+premium\W{0,12}(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(above|below|less|excess)", re.I), "premium"),
    ("time_extension", re.compile(r"time\s+extension(?:\s+if\s*any)?\W{0,12}(yes|no|granted|not\s+granted)", re.I), "yesno"),
    ("delay", re.compile(r"delay\s+in\s+execution(?:\s+of\s+work)?\W{0,12}(yes|no)", re.I), "yesno"),
    ("job_ref", re.compile(r"\b(\d{3}-\d{2}-\d{6})\b"), "ref"),
    ("tender_ref", re.compile(r"(BBMP/\d{4}-\d{2}/[A-Z]{2}/WORK_INDENT\d+(?:/CALL-\d)?|Tender\s+Notification\s+No\.?\s*:?\s*([A-Z][A-Za-z()/.\-0-9 ]{6,40}?\d{4}-\d{2}))", re.I), "ref"),
    ("loa_ref", re.compile(r"Letter\s+of\s+Acceptance\s+No\.?\s*:?\s*([A-Z][A-Za-z()/.\-0-9 ]{4,40}?)\s*(?:Dtd|dated|Dt)\W{0,3}" + DATE, re.I), "refdate"),
    ("agreement_ref", re.compile(r"(?:Contract\s+)?Agreement\s+No\.?\s*:?\s*([A-Z][A-Za-z()/.\-0-9 ]{4,40}?)\s*(?:Dtd|dated|Dt)\W{0,3}" + DATE, re.I), "refdate"),
    ("work_order_ref", re.compile(r"No\s*:\s*([A-Z][A-Za-z()/.\-0-9 ]{2,30}/WO/[A-Za-z()/.\-0-9 ]{2,20}?)\s+Date\s*:?\s*" + DATE, re.I), "refdate"),
    ("go_ref", re.compile(r"Government\s+Order\s+No\.?\s*([A-Z]{2,4}\s*\d+\s*[A-Z]{2,4}\s*\d{4}(?:\s*\([A-Z]\))?)", re.I), "ref"),
    ("gps", re.compile(r"Lat(?:itude)?\s*[:=]?\s*(1[23]\.\d{3,8})\D{1,25}Long(?:itude)?\s*[:=]?\s*(77\.\d{3,8})", re.I), "gps"),
]


def extract(path, max_pages=None):
    """Facts found in one document: [{field, value, page, snippet}]."""
    out = []
    for pi, t in enumerate(pages_text(path, max_pages), start=1):
        flat = re.sub(r"[ \t]+", " ", t)
        for field, rx, kind in PATTERNS:
            for m in rx.finditer(flat):
                snippet = flat[max(0, m.start() - 60): m.end() + 40].replace("\n", " ").strip()
                if kind == "money":
                    v = _money(m.group(1))
                    if not v or v < 1e4:
                        continue
                    # crude unit check: "176,11,00,000" style figures are rupees; tiny values are probably lakh/crore words
                elif kind == "money_unit":
                    v = _money(m.group(1))
                    unit = (m.group(2) or "").lower()
                    if v is None:
                        continue
                    v = v * 1e5 if unit.startswith(("lakh", "lac")) else v * 1e7 if unit.startswith("crore") else v
                    if v < 1e4:
                        continue
                elif kind == "premium":
                    pct = float(m.group(1))
                    v = pct if m.group(2).lower() in ("above", "excess") else -pct
                elif kind == "yesno":
                    v = m.group(1).lower().startswith(("yes", "granted"))
                elif kind == "period":
                    n, unit = int(m.group(1)), m.group(2).lower()
                    v = n * 30 if unit.startswith("month") else n
                    if not 7 <= v <= 1460:
                        continue
                elif kind == "date":
                    v = _iso(m.group(1))
                    if not v:
                        continue
                elif kind == "refdate":
                    v = {"ref": re.sub(r"\s+", " ", m.group(1)).strip(" .:"), "date": _iso(m.group(2))}
                elif kind == "gps":
                    v = {"lat": float(m.group(1)), "lon": float(m.group(2))}
                    if not (12.70 <= v["lat"] <= 13.25 and 77.35 <= v["lon"] <= 77.85):   # anywhere in Bengaluru; outliers are flagged later
                        continue
                else:
                    v = re.sub(r"\s+", " ", m.group(1)).strip(" .:")
                out.append({"field": field, "value": v, "page": pi, "snippet": snippet[:220]})
    return out


def ocr_all(paths, workers=6):
    """Parallel OCR pre-pass (fills the .ocr.json caches)."""
    from concurrent.futures import ThreadPoolExecutor  # tesseract runs as a subprocess, so threads parallelise fine
    todo = [p for p in paths if not p.with_suffix(p.suffix + ".ocr.json").exists()]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(pages_text, todo, chunksize=4))
    return len(todo)
