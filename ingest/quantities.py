"""Phase 5 quantity verification: planned (estimate / Schedule B) vs billed (bill abstract / MB) quantities.

Pipeline, conservative by design:
  1. pick the highest-value Koramangala projects that have IFMS quantity attachments
  2. fetch only those attachments (Schedule B, Supplementary Schedule B, Estimates, M.B., Bill Form),
     public URLs, 1 request/s, cached under data/raw/bbmp_direct/qty/
  3. text per page: native PDF text layer if it has real words, else OCR (page image upscaled 2x,
     tesseract --psm 6, which keeps table rows on one line). Cached as <file>.qty.json
  4. structured parsing: a line item is accepted only when a (quantity, rate, amount) triple on one
     row satisfies quantity x rate = amount (within 0.5 % or ₹2). That arithmetic is the
     cross-validation; OCR text that fails it is never used as a quantity.
       - "decimal restored": OCR dropped a decimal point but the token's digits equal the digits of
         quantity x rate exactly -> accepted, confidence medium
       - handwritten MB pages essentially never pass, so they stay "unreadable" (no quantity is read)
  5. normalise units (sqm/m², cum/m³, rmt/m, km->m …) and match planned to billed items by
     schedule-of-rates code, then by identical rate + unit, then by description similarity + unit
  6. compare quantities, percentages only; signals are observations, never findings of wrongdoing

Every quantity carries provenance: attachment URL, IFMS listing URL, page, raw OCR row, method,
confidence and cross_validated. Quantities never overwrite IFMS amounts (direct source wins); they
are stored as separate inferred facts.
"""
import json
import re
import subprocess
import time
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QTY = ROOT / "data" / "raw" / "bbmp_direct" / "qty"
OUT = ROOT / "data" / "quantities.json"
PLAN_TYPES = ("Schedule B", "Supplementary Schedule B", "Estimates")
BILL_TYPES = ("M.B.", "Bill Form")
MAX_PAGES = 25
MAX_BILL_FILES_PER_JOB = 20
TOP_N = 30

# ---------------------------------------------------------------- units

UNIT_MAP = [
    ("sqm", r"sq\.?\s*m(?:tr?s?|etres?|eters?)?|sqm(?:tr?s?)?|m2|m²|square\s*met(?:re|er)s?|sq\.?mt"),
    ("cum", r"cu\.?\s*m(?:tr?s?|etres?|eters?)?|cum(?:tr?s?)?|m3|m³|cubic\s*met(?:re|er)s?|cu\.?mt"),
    ("m", r"r\.?\s*m\.?t?r?s?|rmt?|running\s*met(?:re|er)s?|met(?:re|er)s?|mtrs?|mt\b|lm|m"),
    ("km", r"km|kms|kilomet(?:re|er)s?"),
    ("nos", r"nos?\.?|numbers?|each|ea"),
    ("kg", r"kgs?|kilograms?"),
    ("tonne", r"tonnes?|tons?|mt\.?\s*ton|quintal"),
    ("litre", r"lit(?:re|er)s?|ltrs?"),
    ("ls", r"l\.?\s*s\.?|lump\s*sum|ls"),
    ("sqft", r"sq\.?\s*f(?:ee)?t|sft"),
    ("rft", r"r\.?\s*ft|rft|running\s*f(?:ee|oo)t"),
    ("day", r"days?"),
]
UNIT_RX = [(u, re.compile(rf"^(?:{p})$", re.I)) for u, p in UNIT_MAP]
UNIT_TOKEN = re.compile(r"\b(sq\.?\s*m\w*|sqm\w*|cu\.?\s*m\w*|cum\w*|r\.?m\.?t?|rmt|m[23²³]|mtrs?|metres?|meters?|km|kms|"
                        r"nos?\.?|each|kgs?|tonnes?|tons?|ltrs?|litres?|l\.?s\.?|sft|rft|days?|m)\b", re.I)
FACTOR = {("km", "m"): 1000.0, ("sqft", "sqm"): 0.092903, ("rft", "m"): 0.3048}


def norm_unit(u):
    """'Sq.m' -> 'sqm', 'm³' -> 'cum', 'RMT' -> 'm', 'Kms' -> 'km'. None if not a unit."""
    s = re.sub(r"\s+", " ", (u or "").strip().strip(".:|)(][")).lower()
    if not s:
        return None
    for name, rx in UNIT_RX:
        if rx.match(s):
            return name
    return None


def convert(qty, unit, to):
    """Convert between compatible units (km->m, sqft->sqm, rft->m). None if incompatible."""
    if qty is None or unit is None or to is None:
        return None
    if unit == to:
        return qty
    f = FACTOR.get((unit, to))
    if f:
        return qty * f
    f = FACTOR.get((to, unit))
    return qty / f if f else None


# ---------------------------------------------------------------- amounts

def num(tok):
    """'1,23,456.78' -> 123456.78 ; '78348,6418' -> 78348.6418 (OCR comma for decimal point)."""
    t = (tok or "").strip().rstrip(".")
    if re.fullmatch(r"\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?", t):   # 1,23,456 / 123,456
        t = t.replace(",", "")
    elif re.fullmatch(r"\d+,\d{1,4}", t):                               # decimal comma
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


NUM_TOKEN = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


def _close(a, b):
    return b > 0 and abs(a - b) <= max(2.0, 0.005 * b)


def _plausible(q, r, a):
    """Reject trivial or tiny products (1 x 870 = 870, 2 x 3 = 4 …) that table rulings and MB
    chainage columns produce by accident. A real priced row has amount >= ₹50 and, unless the amount
    is large, neither factor equal to 1."""
    if a < 50 or q < 0.01 or r < 0.5:
        return False
    if (q <= 1 or r <= 1) and a < 1000:
        return False
    return True


def _digits(x, places):
    return re.sub(r"\D", "", f"{x:.{places}f}").lstrip("0")


def up_to_date(tokens, rate, amount):
    """IFMS bill forms read Previous (qty, amt) | Present (qty, rate, amt) | Total up to date (qty, amt).
    After the present-bill amount, a trailing (qty, amount) pair with qty x rate = amount is the
    cumulative executed quantity. Returns it, else None."""
    vals = [num(t) for t in tokens]
    k = next((i for i, v in enumerate(vals) if v is not None and abs(v - amount) <= max(2.0, 0.005 * amount)), None)
    if k is None:
        return None
    best = None
    for i in range(k + 1, len(vals)):
        for j in range(i + 1, len(vals)):
            if vals[i] and vals[j] and vals[i] > 0.01 and _close(vals[i] * rate, vals[j]):
                best = vals[i]
    return best


def find_triple(tokens):
    """tokens: number strings in row order. Return (q, r, a, method) where q*r == a, else None.
    method 'arithmetic' (all three read correctly) or 'decimal restored' (one token lost its decimal
    point, and its digits equal the digits of the product/quotient exactly)."""
    vals = [num(t) for t in tokens]
    n = len(vals)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                q, r, a = vals[i], vals[j], vals[k]
                if not q or not r or not a or a < 1:
                    continue
                # scanned schedules print all three with decimals; when two lose their decimal point the
                # product can still balance at 100x scale, so at least two must carry one
                n_dec = sum("." in t for t in (tokens[i], tokens[j], tokens[k]))
                if n_dec >= 2 and _close(q * r, a) and a >= q and a >= r and _plausible(q, r, a):
                    return q, r, a, "arithmetic"
    # one token without its decimal point
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                tq, tr, ta = tokens[i], tokens[j], tokens[k]
                q, r, a = vals[i], vals[j], vals[k]
                if not q or not r or not a:
                    continue
                if "." not in ta and "," not in ta and ("." in tq or "." in tr) and _plausible(q, r, q * r):
                    p = q * r
                    for places in (2, 3, 4):
                        if _digits(p, places) == ta.lstrip("0") and p >= 1:
                            return q, r, round(p, places), "decimal restored"
                if "." not in tq and "," not in tq and "." in tr and "." in ta and _plausible(a / r, r, a):
                    p = a / r
                    for places in (2, 3):
                        if _digits(p, places) == tq.lstrip("0") and abs(round(p, places) * r - a) <= max(2.0, 0.005 * a):
                            return round(p, places), r, a, "decimal restored"
    return None


# ---------------------------------------------------------------- text extraction

def _upscale(im):
    from PIL import Image
    g = im.convert("L")
    return g.resize((g.width * 2, g.height * 2), Image.LANCZOS) if g.width < 1800 else g


def remove_rulings(g):
    """Blank out long horizontal/vertical ink runs (table rulings) so tesseract reads the cells.
    Pure PIL: rows/columns as bytes, runs found with a regex."""
    from PIL import Image
    W, H = g.size
    ink = g.point(lambda v: 1 if v < 160 else 0).tobytes()
    out = bytearray(g.tobytes())
    rx = re.compile(rb"\x01{%d,}" % max(40, W // 12))
    for y in range(H):
        for m in rx.finditer(ink, y * W, (y + 1) * W):
            out[m.start():m.end()] = b"\xff" * (m.end() - m.start())
    inkt = g.transpose(Image.TRANSPOSE).point(lambda v: 1 if v < 160 else 0).tobytes()
    rx = re.compile(rb"\x01{%d,}" % max(40, H // 15))
    for x in range(W):
        for m in rx.finditer(inkt, x * H, (x + 1) * H):
            for y in range(m.start() - x * H, m.end() - x * H):
                out[y * W + x] = 255
    return Image.frombytes("L", (W, H), bytes(out))


def _tess(g, tmp):
    g.save(tmp)
    try:
        r = subprocess.run(["tesseract", str(tmp), "-", "--psm", "6"], capture_output=True, timeout=300)
        return r.stdout.decode("utf-8", "replace")
    finally:
        tmp.unlink(missing_ok=True)


def count_rows(text):
    return sum(1 for ln in (text or "").splitlines()
               if len(NUM_TOKEN.findall(ln)) >= 3 and find_triple(NUM_TOKEN.findall(ln)[-8:]))


def _ocr_image(im, tmp):
    """(text, method): 2x OCR; if fewer than 3 validated rows, retry with table rulings removed and
    keep whichever text yields more arithmetic-validated rows."""
    g = _upscale(im)
    t1 = _tess(g, tmp)
    rot = ""
    if count_rows(t1) < 3:               # landscape tables are often scanned sideways: ask tesseract's OSD
        deg = _osd_rotation(g, tmp)
        if deg:
            gr = g.rotate(deg, expand=True)
            tr = _tess(gr, tmp)
            if count_rows(tr) > count_rows(t1) or _english(tr) > max(5, 2 * _english(t1)):
                g, t1, rot = gr, tr, f", rotated {deg}°"
    n1 = count_rows(t1)
    if n1 >= 3 or len(NUM_TOKEN.findall(t1)) < 6:
        return t1, f"OCR (tesseract psm 6, 2x{rot})"
    t2 = _tess(remove_rulings(g), tmp)
    if count_rows(t2) > n1:
        return t2, f"OCR (tesseract psm 6, 2x{rot}, rulings removed)"
    return t1, f"OCR (tesseract psm 6, 2x{rot})"


def _osd_rotation(g, tmp):
    """Counter-clockwise degrees to make the page upright (tesseract --psm 0), or 0 if unsure."""
    g.save(tmp)
    try:
        r = subprocess.run(["tesseract", str(tmp), "-", "--psm", "0"], capture_output=True, timeout=120)
        out = r.stdout.decode("utf-8", "replace")
    except Exception:
        return 0
    finally:
        tmp.unlink(missing_ok=True)
    m, c = re.search(r"Rotate:\s*(\d+)", out), re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not m or not c or float(c.group(1)) < 2:
        return 0
    return (360 - int(m.group(1))) % 360


COMMON = {"the", "of", "and", "to", "work", "for", "in", "is", "by", "as", "per", "with", "rs", "amount", "no", "dated",
          "your", "above", "this", "be", "on", "at", "from", "total", "rate", "qty", "quantity", "bbmp", "road", "engineer"}


def _english(t):
    """Count of common English / BBMP words: gibberish from a sideways or upside-down scan scores ~0."""
    return sum(1 for w in re.findall(r"[A-Za-z]+", (t or "").lower()) if w in COMMON)


def _words(t):
    return len(re.findall(r"[A-Za-z]{4,}", t or ""))


def page_texts(path, max_pages=MAX_PAGES):
    """[{page, method, text}] — native text layer when it has real words, else 2x OCR. Cached."""
    cache = path.with_suffix(path.suffix + ".qty.json")
    if cache.exists():
        return json.loads(cache.read_text())
    out = []
    suf = path.suffix.lower()
    tmp = path.with_suffix(".qtytmp.png")
    try:
        if suf == ".pdf":
            import warnings
            from pypdf import PdfReader
            warnings.filterwarnings("ignore")
            rd = PdfReader(str(path))
            for i, p in enumerate(rd.pages[:max_pages]):
                t = p.extract_text() or ""
                if len(re.findall(r"[a-z]{4,}", t)) >= 30 and NUM_TOKEN.search(t):
                    out.append({"page": i + 1, "method": "native text layer", "text": t})
                    continue
                imgs = list(p.images)
                if not imgs:
                    out.append({"page": i + 1, "method": "no image", "text": t})
                    continue
                im = max(imgs, key=lambda x: len(x.data)).image
                t, how = _ocr_image(im, tmp)
                out.append({"page": i + 1, "method": how, "text": t})
        elif suf in (".jpg", ".jpeg", ".png"):
            from PIL import Image
            t, how = _ocr_image(Image.open(path), tmp)
            out.append({"page": 1, "method": how, "text": t})
    except Exception as e:
        out = [{"page": None, "method": "unreadable", "text": f"[unreadable: {e}]"}]
    cache.write_text(json.dumps(out))
    return out


# ---------------------------------------------------------------- parsing

CODE = re.compile(r"\b(KS?R{1,2}B|KBSR|KPWD|SR|MORTH|M\s*O\s*R\s*T\s*H|BBMP\s*SR)\s*[-:.]?\s*([A-Z]?\s?\d{1,4}(?:[.\-]\d{1,3}){0,3})", re.I)


def norm_code(m):
    if not m:
        return None
    book = re.sub(r"[^A-Z]", "", m.group(1).upper())
    book = {"KSRRB": "KSRRB", "KSRB": "KSRB", "KRRB": "KSRRB", "KRB": "KSRB"}.get(book, book)
    return f"{book} {re.sub(r'\s+', '', m.group(2)).upper()}"


DOC_KIND = [
    ("abstract", re.compile(r"abstract|bill\s*sheet|up\s*to\s*date|this\s+bill|part\s+bill|final\s+bill|r\.?a\.?\s*bill", re.I)),
    ("schedule_b", re.compile(r"schedule\s*[-‘'\"]?\s*b|bill\s+of\s+quantit|\bBOQ\b", re.I)),
    ("estimate", re.compile(r"detailed\s+estimate|estimate\s+amount|abstract\s+of\s+estimate|estimate", re.I)),
    ("measurement", re.compile(r"measurement\s+(?:sheet|book)|chainage|M\.?\s*A\.?\s*R\.?\s*57", re.I)),
]


def page_kind(text, ftype):
    """What a page is. The IFMS attachment type decides between plan and bill; text refines it."""
    t = text or ""
    if ftype in BILL_TYPES:
        # a priced abstract page says so, or carries the Rate / Amount column headers; a dimension page
        # carries chainage / length-breadth-depth columns or the "quantity paid" summary
        if (re.search(r"abstract|description\s+of\s+(?:the\s+)?item", t, re.I)
                or (re.search(r"\brate\b", t, re.I) and re.search(r"\bamount\b", t, re.I))):
            return "abstract"
        if re.search(r"chainage|\bch\s*:|breadth|quantity\s+paid|total\s+quantity|measurement|M\.?\s*A\.?\s*R\.?\s*57", t, re.I):
            return "measurement"
        return "abstract"
    if ftype in ("Schedule B", "Supplementary Schedule B"):
        return "schedule_b"
    return "estimate"


def parse_items(pages, ftype):
    """Line items from page texts. Returns ([item], page_stats)."""
    items, stats = [], []
    for pg in pages:
        text = pg.get("text") or ""
        kind = page_kind(text, ftype)
        lines = text.splitlines() if kind != "measurement" else []   # MB dimension pages: not priced rows
        block = []
        n_rows = 0
        for ln in lines:
            toks = NUM_TOKEN.findall(ln)
            hit = find_triple(toks[-8:]) if len(toks) >= 3 else None
            if not hit:
                block.append(ln)
                if len(block) > 14:
                    block = block[-14:]
                continue
            q, r, a, how = hit
            to_date = up_to_date(toks[-8:], r, a) if kind == "abstract" else None
            ctx = " ".join(block[-8:] + [ln])
            # the unit is the unit-looking word nearest before the quantity on this row
            qpos = ln.find(next((t for t in toks if num(t) == q), str(q)))
            before = ln[:qpos] if qpos > 0 else ln
            us = [norm_unit(m.group(1)) for m in UNIT_TOKEN.finditer(before)]
            us = [u for u in us if u]
            unit = us[-1] if us else None
            if unit == "km" and re.search(r"lead\D{0,20}\d*\s*kms?\b", before, re.I):
                unit = None                       # "lead upto 15 kms" is a haul distance, not the item unit
            code = norm_code(list(CODE.finditer(ctx))[-1]) if CODE.search(ctx) else None
            desc = re.sub(r"[|\[\]{}]+", " ", ctx)
            desc = re.sub(r"\s+", " ", NUM_TOKEN.sub(" ", desc)).strip()[-260:]
            items.append({
                "page": pg.get("page"), "page_kind": kind, "code": code, "unit": unit, "unit_raw": us and us[-1],
                "quantity": q, "rate": r, "amount": a, "description": desc, "raw": ln.strip()[:240],
                "method": pg.get("method"), "check": how, "to_date_quantity": to_date,
                "confidence": "high" if how == "arithmetic" and unit else "medium",
                "cross_validated": True,
            })
            n_rows += 1
            block = []
        stats.append({"page": pg.get("page"), "kind": kind, "method": pg.get("method"),
                      "rows": n_rows, "words": len(re.findall(r"[A-Za-z]{4,}", text))})
    return items, stats


def readable(stats):
    """A document is 'readable' if at least one page yielded an arithmetic-validated row."""
    return any(s["rows"] for s in stats)


# "Quantity Paid In First Bill = 202.80 Cum" / "Quantity Paid In This Bill = 233.56 Cum" / "Total Quantity 436.36 Cum"
TOTALS = re.compile(r"(?:quantity\s+paid\s+in\s+(?P<which>first|previous|this|\w+)\s+bill|(?P<total>total\s+quantity))"
                    r"\s*[=:\-]?\s*(?P<q>\d[\d,]*\.\d+|\d[\d,]*)\s*(?P<u>[A-Za-z.]{1,6})?", re.I)


def parse_totals(pages):
    """Measurement-sheet summaries: per page [{page, previous, this_bill, total, unit, consistent}].
    'consistent' = previous + this bill == total (the sheet checks itself)."""
    out = []
    for pg in pages:
        found = {}
        for m in TOTALS.finditer(pg.get("text") or ""):
            q = num(m.group("q"))
            key = "total" if m.group("total") else ("this_bill" if m.group("which").lower() == "this" else "previous")
            found.setdefault(key, (q, norm_unit(m.group("u") or ""), m.group(0).strip()))
        if "total" in found or "this_bill" in found:
            prev, this, tot = (found.get(k, (None,))[0] for k in ("previous", "this_bill", "total"))
            unit = next((v[1] for v in found.values() if v[1]), None)
            consistent = None
            if tot is not None and this is not None:
                consistent = _close((prev or 0) + this, tot) or abs((prev or 0) + this - tot) < 0.02
            text = pg.get("text") or ""
            cm = CODE.search(text)
            desc = re.sub(r"\s+", " ", text[cm.start(): cm.start() + 200]) if cm else None
            out.append({"page": pg.get("page"), "code": norm_code(cm), "description": desc,
                        "previous": prev, "this_bill": this, "total": tot, "unit": unit,
                        "consistent": consistent, "raw": " / ".join(v[2] for v in found.values())[:240],
                        "method": pg.get("method")})
    return out


# ---------------------------------------------------------------- categories

CATEGORY = [
    ("Bituminous surfacing", r"bitum|asphalt|tack\s*coat|prime\s*coat|\bDBM\b|\bBC\b|\bSDBC\b|mastic|hot\s*mix|bituminous\s*macadam|mix\s*seal"),
    ("Granular base / sub-base", r"wet\s*mix|\bWMM\b|granular\s*sub|\bGSB\b|aggregate\s*base|stone\s*aggregate|filling\s*(?:in|with)?\s*(?:sand|earth|murrum|dust)"),
    ("Earthwork & excavation", r"earth\s*work|excavat|desilt|cutting|embankment"),
    ("Dismantling & removal", r"dismantl|removing|removal|demoli|scarif|milling"),
    ("Road marking & safety", r"marking|thermoplastic|road\s*stud|cat'?s?\s*eye|sign\s*board|delineator|crash\s*barrier|bollard"),
    ("Paving, kerbs & footpath", r"kerb|curb|paver|cobble|tile|footpath|granite|slab\s*laying|interlock"),
    ("Drain & culvert works", r"drain|culvert|slab|chamber|weep|stone\s*masonry|size\s*stone|U\s*-?\s*drain"),
    ("Concrete", r"concrete|\bPCC\b|\bRCC\b|\bM-?\s*\d{2}\b|cement"),
    ("Reinforcement & steel", r"reinforc|\bTMT\b|steel|\bFe\s*\d{3}|structural"),
    ("Pipes & ducts", r"pipe|duct|hume|\bDWC\b|HDPE"),
    ("Electrical & lighting", r"cable|light|pole|luminaire|lamp|electri|LED"),
    ("Horticulture & landscaping", r"plant|lawn|tree|horticult|garden|grass|soil\s*for"),
]
CATEGORY = [(c, re.compile(p, re.I)) for c, p in CATEGORY]


def category(desc):
    for c, rx in CATEGORY:
        if rx.search(desc or ""):
            return c
    return "Other"


# ---------------------------------------------------------------- selection and fetching

def _load_listing():
    """{wbid: (job, bill-details, [attachments])} for every cached IFMS work bill whose job is known,
    minus bills set aside as job-number collisions (another ward's work)."""
    from ingest import direct
    cache, job_of = direct.load()
    coll = set()
    cp = ROOT / "data" / "raw" / "bbmp_direct" / "collisions.json"
    if cp.exists():
        coll = {str(c["wbid"]) for c in json.loads(cp.read_text())}
    out = {}
    for wbid, rec in cache.items():
        job = job_of.get(wbid, (None,))[0]
        if not job or wbid in coll:
            continue
        d = rec.get("details") if isinstance(rec.get("details"), dict) else {}
        files = []
        for kind, url_key in (("wo_files", "wo_files_url"), ("bill_files", "bill_files_url")):
            for f in rec.get(kind) or []:
                if isinstance(f, dict) and f.get("rFileName"):
                    files.append({**f, "listing": kind, "listing_url": rec.get(url_key)})
        out[wbid] = (job, d, files)
    return out


def _bill_meta(wbid, d):
    from ingest.direct import _d, _f
    return {"wbid": wbid, "bill_type": d.get("billtype"), "gross": _f(d.get("gross")),
            "br_date": _d(d.get("sbrdate")) or _d(d.get("commonbrdate")), "rtgs_date": _d(d.get("rtgsdate")),
            "paid": bool(d.get("rtgs")), "final": "final" in (d.get("billtype") or "").lower()}


def _is_placeholder(name, size=None):
    n = (name or "").lower()
    return bool(re.search(r"not\s*applicable|\bn\.?a\b|emty|empty|nil\b|blank", n)) or (size is not None and size < 15_000)


def job_documents(listing, job):
    """Quantity documents of a job: plan files (unique by name) and bill files per work bill."""
    bills, plan, per_bill = [], {}, defaultdict(list)
    for wbid, (j, d, files) in listing.items():
        if j != job:
            continue
        bills.append(_bill_meta(wbid, d))
        for f in files:
            t = (f.get("rFileType") or "").strip()
            name = f["rFileName"]
            if _is_placeholder(name):
                continue
            if t in PLAN_TYPES:
                plan.setdefault(name, {**f, "type": t, "wbid": wbid})
            elif t in BILL_TYPES and f["listing"] == "bill_files":
                if name not in {x["rFileName"] for x in per_bill[wbid]}:
                    per_bill[wbid].append({**f, "type": t, "wbid": wbid})
            elif t in BILL_TYPES:
                per_bill.setdefault(wbid, per_bill[wbid])
                if name not in {x["rFileName"] for x in per_bill[wbid]}:
                    per_bill[wbid].append({**f, "type": t, "wbid": wbid})
    bills.sort(key=lambda b: (b["br_date"] or "9999", b["wbid"]))
    return bills, list(plan.values()), per_bill


def select_projects(n=TOP_N, db=None):
    """Highest-value projects (max of paid, contract) whose IFMS listing has a plan document
    (Schedule B / Estimates) and a bill document (M.B. / Bill Form)."""
    import sqlite3
    con = sqlite3.connect(db or ROOT / "data" / "koramangala.db")
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT slug, title, job_numbers, payments_gross, contract_value, estimated_cost FROM projects "
                       "WHERE job_numbers IS NOT NULL ORDER BY MAX(COALESCE(payments_gross,0), COALESCE(contract_value,0)) DESC").fetchall()
    listing = _load_listing()
    jobs_with = defaultdict(set)
    for wbid, (job, d, files) in listing.items():
        for f in files:
            if not _is_placeholder(f["rFileName"]):
                jobs_with[job].add((f.get("rFileType") or "").strip())
    picked, skipped = [], []
    for r in rows:
        jobs = [j for j in (r["job_numbers"] or "").split(",") if j]
        types = set().union(*(jobs_with[j] for j in jobs)) if jobs else set()
        has_plan = bool(types & set(PLAN_TYPES))
        has_bill = bool(types & set(BILL_TYPES))
        entry = {"slug": r["slug"], "title": r["title"], "jobs": jobs, "paid": r["payments_gross"],
                 "contract": r["contract_value"], "estimate": r["estimated_cost"], "has_plan": has_plan, "has_bill": has_bill}
        if has_plan and has_bill:
            picked.append(entry)
        elif len(picked) < n:
            skipped.append(entry)
        if len(picked) >= n:
            break
    con.close()
    return picked, skipped, listing


def _abstract_like(f):
    n = (f["rFileName"] or "").lower()
    return 0 if re.search(r"abstract|bill\s*sheet|bill\b|abs\b", n) else 1 if f["type"] == "Bill Form" else 2


def bill_files_to_fetch(bills, per_bill, cap=MAX_BILL_FILES_PER_JOB):
    """Round-robin over the job's bills (final bill first), most abstract-like file first, so every
    bill gets a chance at a priced abstract before any bill gets a second file."""
    order = [b["wbid"] for b in sorted(bills, key=lambda b: (not b["final"], b["br_date"] or ""))]
    queues = [sorted(per_bill.get(w, []), key=_abstract_like) for w in order]
    out = []
    while len(out) < cap and any(queues):
        for q in queues:
            if q and len(out) < cap:
                out.append(q.pop(0))
    return out


def fetch(projects, listing):
    """Download the selected projects' quantity documents (public URLs, 1 request/s, cached)."""
    from ingest import fetch_bbmp_direct as F
    QTY.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in projects:
        for job in p["jobs"]:
            bills, plan, per_bill = job_documents(listing, job)
            bill_files = bill_files_to_fetch(bills, per_bill)
            for f in plan + bill_files:
                dest = QTY / f["rFileName"].replace("/", "_")
                if dest.exists():
                    continue
                body, ct = F._open(F.file_url(f.get("raddl") or f.get("rAddl"), f["rFileName"]), timeout=180)
                time.sleep(F.DELAY)
                n += 1
                if body and len(body) <= F.MAX_FILE:
                    dest.write_bytes(body)
                else:
                    (QTY / (dest.name + ".missing")).write_text(json.dumps({"status": ct, "size": len(body or b"")}))
    return n


# ---------------------------------------------------------------- analysis

# Signal thresholds (explained in PHASE5_REPORT.md). They sit outside routine measurement variation;
# the contracts' own variation clauses were not available, so they are not contractual limits.
BELOW = 0.75            # billed/measured < 75 % of the planned quantity
ABOVE = 1.25            # billed/measured > 125 % of the planned quantity
MAJOR_SHARE = 0.10      # a "major" planned item: >= 10 % of the parsed plan value
MIN_ITEM_AMOUNT = 1e5   # ignore items under ₹1 lakh for every quantity signal
HIGH_VALUE = 1e7        # ₹1 crore paid: "high-value" for the unreadable-MB observation
COVERAGE_OK = (0.6, 1.35)
IDENTICAL = 0.0005      # "identical": within 0.05 % (OCR rounding) of the tendered quantity
MIN_IDENTICAL = 3       # ... for at least three code/rate-matched items of one project   # a bill's parsed abstract value / its IFMS gross, to count the bill as fully read


def _url(f):
    from ingest import fetch_bbmp_direct as F
    return F.file_url(f.get("raddl") or f.get("rAddl"), f["rFileName"])


def _prov(f, item_or_total, kind):
    from ingest import fetch_bbmp_direct as F
    return {"document_id": f["rFileName"], "attachment_type": f["type"], "work_bill_id": f.get("wbid"),
            "attachment_url": F.file_url(f.get("raddl") or f.get("rAddl"), f["rFileName"]),
            "listing_url": f.get("listing_url"), "page": item_or_total.get("page"), "page_kind": kind,
            "raw": item_or_total.get("raw"), "method": item_or_total.get("method"),
            "check": item_or_total.get("check") or ("previous + this bill = total" if item_or_total.get("consistent") else "none"),
            "confidence": item_or_total.get("confidence") or ("medium" if item_or_total.get("consistent") else "low"),
            "cross_validated": bool(item_or_total.get("cross_validated") or item_or_total.get("consistent"))}


def _key(it):
    return (it.get("code"), round(it["quantity"], 2), round(it["rate"], 2))


def _sim(a, b):
    na = " ".join(re.findall(r"[a-z]{3,}", (a or "").lower()))
    nb = " ".join(re.findall(r"[a-z]{3,}", (b or "").lower()))
    return SequenceMatcher(None, na[-200:], nb[-200:]).ratio() if na and nb else 0.0


def _units_ok(a, b):
    return a is None or b is None or a == b or convert(1.0, a, b) is not None


RATE_BAND = 0.35   # a billed rate within ±35 % of the Schedule B rate (tender premium / revisions) can be the same item


def _rate_ok(plan, other):
    pr, orr = plan.get("rate"), other.get("rate")
    return bool(pr and orr) and abs(orr - pr) <= RATE_BAND * pr


def orient(plan, other):
    """OCR sometimes puts quantity and rate the other way round on a bill row. If the row's 'quantity'
    is the plan rate (and its 'rate' is not), swap them. Returns a (possibly swapped) copy."""
    pr = plan.get("rate")
    if pr and other.get("quantity") and abs(other["quantity"] - pr) <= 0.005 * pr and not _rate_ok(plan, other):
        return {**other, "quantity": other["rate"], "rate": other["quantity"], "swapped": True}
    return other


def match_score(plan, other, rate_unique=True):
    """3 = same schedule-of-rates code; 2 = same rate (contract rates carry into bills) with some
    description overlap and a rate no other planned item has; 1+sim = similar description.
    Every match needs compatible units and a billed rate within ±35 % of the planned rate. 0 = no match."""
    other = orient(plan, other)
    if not _units_ok(plan.get("unit"), other.get("unit")) or not _rate_ok(plan, other):
        return 0
    s = _sim(plan.get("description"), other.get("description"))
    if plan.get("code") and plan.get("code") == other.get("code"):
        return 3
    if abs(plan["rate"] - other["rate"]) <= 0.005 * plan["rate"] and rate_unique and s >= 0.3:
        return 2
    return 1 + s if s >= 0.55 else 0


def best_match(item, plan_items):
    best, score = None, 0
    rates = [p.get("rate") for p in plan_items]
    for i, p in enumerate(plan_items):
        uniq = sum(1 for r in rates if r and p.get("rate") and abs(r - p["rate"]) <= 0.005 * p["rate"]) == 1
        sc = match_score(p, item, uniq)
        if sc > score:
            best, score = i, sc
    return best, score


def _read(f, path):
    pages = page_texts(path)
    items, stats = parse_items(pages, f["type"])
    totals = parse_totals(pages) if f["type"] in BILL_TYPES else []
    return pages, items, stats, totals


def analyse_job(job, listing, project):
    bills, plan_docs, per_bill = job_documents(listing, job)
    bill_docs = bill_files_to_fetch(bills, per_bill)
    meta = {b["wbid"]: b for b in bills}
    docs, plan, supp = [], [], []
    seen = set()
    for f in plan_docs:
        path = QTY / f["rFileName"].replace("/", "_")
        if not path.exists():
            docs.append({"document_id": f["rFileName"], "url": _url(f), "type": f["type"], "status": "not downloaded"})
            continue
        pages, items, stats, _ = _read(f, path)
        docs.append({"document_id": f["rFileName"], "url": _url(f), "type": f["type"], "pages": len(pages),
                     "status": "readable" if readable(stats) else "unreadable", "rows": len(items),
                     "methods": sorted({s["method"] for s in stats if s.get("method")})})
        for it in items:
            k = _key(it)
            if k in seen:
                continue
            seen.add(k)
            rec = {**it, "source_type": f["type"], "provenance": [_prov(f, it, it["page_kind"])]}
            (supp if f["type"] == "Supplementary Schedule B" else plan).append(rec)
    # Schedule B (contract BOQ) is the plan; Estimates fill in only when no Schedule B row was read
    sched = [p for p in plan if p["source_type"] == "Schedule B"]
    plan = sched or plan
    plan_basis = "Schedule B (contract BOQ)" if sched else ("Estimate" if plan else None)

    by_bill, totals, seen_rows = defaultdict(list), [], set()
    for f in bill_docs:
        path = QTY / f["rFileName"].replace("/", "_")
        if not path.exists():
            docs.append({"document_id": f["rFileName"], "url": _url(f), "type": f["type"], "work_bill_id": f["wbid"], "status": "not downloaded"})
            continue
        pages, items, stats, tots = _read(f, path)
        meas_pages = sum(1 for s in stats if s["kind"] == "measurement")
        docs.append({"document_id": f["rFileName"], "url": _url(f), "type": f["type"], "work_bill_id": f["wbid"], "pages": len(pages),
                     "status": "readable" if (readable(stats) or tots) else "unreadable", "rows": len(items),
                     "measurement_pages": meas_pages, "totals": len(tots),
                     "methods": sorted({s["method"] for s in stats if s.get("method")})})
        for it in items:
            k = _key(it)
            if k in seen_rows:          # the same priced row re-uploaded (same file in two bills, or MB + bill form)
                continue
            seen_rows.add(k)
            by_bill[f["wbid"]].append({**it, "provenance": [_prov(f, it, "abstract")]})
        for t in tots:
            totals.append({**t, "wbid": f["wbid"], "provenance": [_prov(f, {**t, "confidence": None}, "measurement")]})

    # which bills were read completely enough to sum
    cum = 0.0
    bill_read = {}
    for b in bills:
        cum += b["gross"] or 0
        s = sum(i["amount"] for i in by_bill.get(b["wbid"], []))
        g = b["gross"] or 0
        scope_ = None
        if s and g:
            r_this, r_cum = s / g, s / cum if cum else 0
            if COVERAGE_OK[0] <= r_this <= COVERAGE_OK[1]:
                scope_ = "this bill"
            elif cum > g and COVERAGE_OK[0] <= r_cum <= COVERAGE_OK[1]:
                scope_ = "cumulative"
        bill_read[b["wbid"]] = {"wbid": b["wbid"], "bill_type": b["bill_type"], "gross": g, "paid": b["paid"],
                                "abstract_value": round(s, 2) if s else None,
                                "coverage": round(s / g, 3) if s and g else None, "scope": scope_}
    paid_bills = [b for b in bills if b["paid"]]
    all_read = bool(paid_bills) and all(bill_read[b["wbid"]]["scope"] for b in paid_bills)
    has_final = any(b["final"] and b["paid"] for b in bills)
    cumulative = [b for b in bills if bill_read[b["wbid"]]["scope"] == "cumulative"]

    # billed quantity per plan item
    work = [{"description": p["description"], "normalized_category": category(p["description"]), "code": p.get("code"),
             "unit": p.get("unit"), "estimated_quantity": p["quantity"], "estimated_rate": p["rate"],
             "estimated_amount": p["amount"], "plan_basis": p["source_type"], "billed_quantity": None,
             "billed_amount": None, "billed_rate": None, "measured_quantity": None, "bills": [],
             "provenance": list(p["provenance"])} for p in plan]
    unmatched = []
    last_cum = cumulative[-1]["wbid"] if cumulative else None
    cum_idx = next((i for i, b in enumerate(bills) if b["wbid"] == last_cum), 0)
    for b in bills:
        rd = bill_read[b["wbid"]]
        if last_cum and bills.index(b) < cum_idx:
            continue                      # included in a later up-to-date (cumulative) abstract
        for it in by_bill.get(b["wbid"], []):
            i, sc = best_match(it, work and [{"code": w["code"], "unit": w["unit"], "rate": w["estimated_rate"],
                                               "description": w["description"]} for w in work])
            if i is None:
                unmatched.append({**it, "wbid": b["wbid"], "normalized_category": category(it["description"])})
                continue
            w = work[i]
            it = orient({"rate": w["estimated_rate"]}, it)
            q = convert(it["quantity"], it.get("unit"), w["unit"]) if it.get("unit") and w["unit"] else it["quantity"]
            if q is None:
                unmatched.append({**it, "wbid": b["wbid"], "normalized_category": category(it["description"])})
                continue
            if any(x["wbid"] != b["wbid"] and abs(x["quantity"] - q) <= 0.005 * max(q, 1e-9) for x in w["bills"]):
                w.setdefault("repeated_rows", []).append({"wbid": b["wbid"], "quantity": q})   # same row re-uploaded
                continue
            if it.get("to_date_quantity") is not None:
                td = convert(it["to_date_quantity"], it.get("unit"), w["unit"]) if it.get("unit") and w["unit"] else it["to_date_quantity"]
                if td is not None and (w.get("to_date") is None or bills.index(b) >= w.get("to_date_idx", -1)):
                    w["to_date"], w["to_date_idx"] = round(td, 3), bills.index(b)
            w["billed_quantity"] = round((w["billed_quantity"] or 0) + q, 3)
            w["billed_amount"] = round((w["billed_amount"] or 0) + it["amount"], 2)
            w["billed_rate"] = it["rate"]
            w["bills"].append({"wbid": b["wbid"], "quantity": q, "match": {3: "code", 2: "rate"}.get(sc, "description"),
                              "swapped": bool(it.get("swapped"))})
            w["provenance"] += it["provenance"]
    # measured cumulative totals from typed measurement sheets (matched by schedule-of-rates code)
    for t in totals:
        if t["total"] is None or not t.get("code"):
            continue
        for w in work:
            if w["code"] == t["code"] and _units_ok(w["unit"], t["unit"]):
                q = convert(t["total"], t["unit"], w["unit"]) if t["unit"] and w["unit"] else t["total"]
                if q is not None and (w["measured_quantity"] is None or q > w["measured_quantity"]):
                    w["measured_quantity"] = round(q, 3)
                    w["provenance"] += t["provenance"]
    for w in work:
        if w.get("to_date") is not None and (w["billed_quantity"] is None or w["to_date"] >= w["billed_quantity"] - 0.01):
            w["billed_quantity"] = w["to_date"]           # bill form's "Total up to date" column (latest bill)
            w["billed_basis"] = "bill form total up to date"
        got = w["measured_quantity"] if w["measured_quantity"] is not None else w["billed_quantity"]
        w["compared_quantity"] = got
        w["compared_basis"] = ("MB measurement total" if w["measured_quantity"] is not None else
                               w.get("billed_basis") or ("bill abstracts (sum of read bills)" if got is not None else None))
        last = w["bills"][-1]["wbid"] if w["bills"] else None
        w["from_final_bill"] = bool(last and meta.get(last, {}).get("final")) if w["measured_quantity"] is None else None
        w["variance_pct"] = round((got - w["estimated_quantity"]) / w["estimated_quantity"] * 100, 1) if got is not None and w["estimated_quantity"] else None
    readable_mb = any(d["status"] == "readable" for d in docs if d["type"] in BILL_TYPES)
    return {
        "job": job, "plan_basis": plan_basis, "plan_items": len(plan), "supplementary_items": len(supp),
        "plan_value_parsed": round(sum(p["amount"] for p in plan), 2),
        "bills": list(bill_read.values()), "all_paid_bills_read": all_read, "has_final_bill": has_final,
        "paid_gross": round(sum(b["gross"] or 0 for b in paid_bills), 2),
        "billed_value_read": round(sum(bill_read[b["wbid"]]["abstract_value"] or 0 for i, b in enumerate(bills)
                                       if b["paid"] and bill_read[b["wbid"]]["scope"] == "this bill" and i > (cum_idx if last_cum else -1))
                                   + (bill_read[last_cum]["abstract_value"] if last_cum else 0), 2),
        "work_items": work, "billed_without_plan_item": unmatched, "measurement_totals": totals, "documents": docs,
        "readable_bill_documents": readable_mb,
        "variation_documents": sorted({f["rFileName"] for _, (j, d, fs) in listing.items() if j == job for f in fs
                                       if re.search(r"supplementary|variation|deviation|revised\s*estimate|work\s*slip",
                                                    f"{f.get('rFileType')} {f['rFileName']}", re.I) and not _is_placeholder(f["rFileName"])}),
    }


# ---------------------------------------------------------------- signals and facts

def _ev(prov, note=None):
    """A provenance entry as an evidence reference (attachment URL + page + method)."""
    lab = f"IFMS attachment {prov['document_id']} ({prov['attachment_type']}), p.{prov.get('page')}"
    return {"source_id": "IFMS-QTY", "label": lab, "url": prov["attachment_url"],
            "note": (note + " · " if note else "") + f"{prov['method']}; {prov['check']}; confidence {prov['confidence']}"
                    + (f' · row: "{prov["raw"]}"' if prov.get("raw") else "")}


def _fmt(q):
    return f"{q:,.2f}".rstrip("0").rstrip(".") if q is not None else "?"


def signals_for(res, paid=None, contract=None):
    """Conservative quantity observations for one analysed project (dict from analyse_job()).
    One signal per kind per project; the detail lists up to five items, the evidence their pages."""
    from ingest.signals import sig
    plan_value = res["plan_value_parsed"] or 0
    complete = res["has_final_bill"] and res["all_paid_bills_read"]
    no_variation = not res["variation_documents"] and not res["supplementary_items"]
    hits = defaultdict(list)            # code -> [(line, evidence)]
    for w in res["work_items"]:
        got, est = w["compared_quantity"], w["estimated_quantity"]
        if got is None or not est or (w["estimated_amount"] or 0) < MIN_ITEM_AMOUNT:
            continue
        ratio = got / est
        line = (f'{w["description"][-80:]} — planned {_fmt(est)} {w["unit"] or ""} ({w["plan_basis"]}), '
                f'{w["compared_basis"]} {_fmt(got)} ({ratio - 1:+.0%})')
        ev = [_ev(x) for x in w["provenance"][:3]]
        firm = w["compared_basis"] == "MB measurement total" or complete
        if ratio < BELOW and firm:
            hits["qty_measured_below_plan"].append((line, ev))
        scoped = w["compared_basis"] in ("MB measurement total", "bill form total up to date") or all(
            next((b["scope"] for b in res["bills"] if b["wbid"] == x["wbid"]), None) for x in w["bills"])
        exact = w["compared_basis"] == "MB measurement total" or all(x["match"] in ("code", "rate") for x in w["bills"])
        if ratio > ABOVE and scoped and exact:   # description-only matches can sum several different items
            hits["qty_billed_above_plan"].append((line, ev))
            if no_variation:
                hits["qty_above_plan_no_variation"].append((line, ev))
    same = [w for w in res["work_items"] if w["compared_quantity"] is not None and w["estimated_quantity"]
            and w["bills"] and all(x["match"] in ("code", "rate") for x in w["bills"])
            and abs(w["compared_quantity"] - w["estimated_quantity"]) <= IDENTICAL * w["estimated_quantity"] + 0.011]
    if len(same) >= MIN_IDENTICAL:
        for w in same:
            hits["qty_identical_to_plan"].append((f'{w["description"][-60:]} — {_fmt(w["estimated_quantity"])} {w["unit"] or ""}',
                                                  [_ev(x) for x in w["provenance"][:2]]))
    plan_ok = res["plan_basis"] == "Schedule B (contract BOQ)" and contract and 0.8 * contract <= plan_value <= 1.5 * contract
    if plan_ok:
        for u in sorted(res["billed_without_plan_item"], key=lambda u: -u["amount"]):
            if u["amount"] >= MIN_ITEM_AMOUNT:
                hits["qty_billed_item_not_in_plan"].append((
                    f'{u["description"][-80:]} — {_fmt(u["quantity"])} {u["unit"] or ""} @ ₹{u["rate"]:,.2f} = ₹{u["amount"]:,.0f} '
                    f'(bill {u["wbid"]})', [_ev(x) for x in u["provenance"][:1]]))
    if complete and plan_value:
        for w in res["work_items"]:
            if w["compared_quantity"] is None and (w["estimated_amount"] or 0) >= max(MIN_ITEM_AMOUNT, MAJOR_SHARE * plan_value):
                hits["qty_major_item_not_billed"].append((
                    f'{w["description"][-80:]} — planned {_fmt(w["estimated_quantity"])} {w["unit"] or ""}, '
                    f'₹{w["estimated_amount"]:,.0f} ({w["estimated_amount"] / plan_value:.0%} of the parsed plan)',
                    [_ev(x) for x in w["provenance"][:1]]))
    TEXT = {
        "qty_measured_below_plan": ("Measured / billed quantity well below plan",
                                    "Possible explanations: work reduced by a variation, items shifted to another job, "
                                    "measurement recorded in an MB we could not read."),
        "qty_billed_above_plan": ("Billed quantity above plan",
                                  "Possible explanations: approved deviation or supplementary schedule, site conditions, "
                                  "an OCR misread on the plan side (each row passed quantity × rate = amount)."),
        "qty_above_plan_no_variation": ("Quantity above plan with no variation document visible",
                                        "No supplementary Schedule B, deviation statement, work slip or revised-estimate attachment is "
                                        "listed for this job in IFMS (it may exist on paper)."),
        "qty_billed_item_not_in_plan": ("Billed item with no matching Schedule B item",
                                        f"Schedule B was read to {plan_value / contract:.0%} of the contract value. "
                                        "Possible explanations: extra item approved separately, a Schedule B row we could not read, "
                                        "different item wording." if plan_ok else ""),
        "qty_identical_to_plan": ("Billed quantities identical to Schedule B",
                                  "Executed quantity up to date equals the tendered quantity to the decimal. Auditors commonly "
                                  "check such items to confirm measurements were taken on site; it can also be legitimate "
                                  "(quantities fixed by drawings, items measured once)."),
        "qty_major_item_not_billed": ("Major planned item with no billed / measured quantity",
                                      "Every paid bill's abstract was read and no matching row was found."),
    }
    out = []
    for code, lst in hits.items():
        title, why = TEXT[code]
        detail = "; ".join(l for l, _ in lst[:5]) + (f"; and {len(lst) - 5} more" if len(lst) > 5 else "") + ". " + why
        out.append(sig(code, "info" if code == "qty_identical_to_plan" else "notice", f"{title} ({len(lst)} item{'s' if len(lst) > 1 else ''})", detail, "inferred",
                       [e for _, ev in lst[:5] for e in ev][:8]))
    bill_docs = [d for d in res["documents"] if d["type"] in BILL_TYPES and d["status"] != "not downloaded"]
    if (paid or 0) >= HIGH_VALUE and bill_docs and not res["readable_bill_documents"]:
        out.append(sig("qty_unreadable_mb", "info", "High-value work whose measurement records are unreadable",
                       f"{len(bill_docs)} MB / bill-form attachments downloaded; none yielded an arithmetic-checked quantity "
                       "(handwritten or low-quality scans). Quantities for this work cannot be verified from public data.",
                       "confirmed", []))
    return out


def mb_presence(listing):
    """{job: {...}} for every job in the IFMS cache: final bill paid, and whether any MB / bill form is attached."""
    out = {}
    jobs = defaultdict(list)
    for wbid, (job, d, files) in listing.items():
        jobs[job].append((wbid, d, files))
    for job, rows in jobs.items():
        final = [w for w, d, _ in rows if "final" in (d.get("billtype") or "").lower() and d.get("rtgs")]
        mb = [f["rFileName"] for _, _, fs in rows for f in fs if (f.get("rFileType") or "").strip() in BILL_TYPES]
        real = [n for n in mb if not _is_placeholder(n)]
        # IFMS returns no bill-level attachments for bills from mid-2024 to public requests: absence of an MB there is not evidence
        bill_level = any(f.get("listing") == "bill_files" for _, _, fs in rows for f in fs)
        elsewhere = sorted({f["rFileName"] for _, _, fs in rows for f in fs if (f.get("rFileType") or "").strip() not in BILL_TYPES
                            and re.search(r"\bm\.?\s*b\b|measurement", f["rFileName"], re.I) and not _is_placeholder(f["rFileName"])})
        out[job] = {"final_bills": final, "mb_files": len(set(mb)), "mb_real": len(set(real)),
                    "mb_placeholders": sorted(set(mb) - set(real))[:5], "bill_level_files": bill_level,
                    "mb_filed_elsewhere": elsewhere}
    return out


def run(fetch_files=True, n=TOP_N):
    picked, skipped, listing = select_projects(n)
    fetched = fetch(picked, listing) if fetch_files else 0
    todo = [QTY / f["rFileName"].replace("/", "_") for p in picked for j in p["jobs"]
            for f in (job_documents(listing, j)[1] + bill_files_to_fetch(*[job_documents(listing, j)[i] for i in (0, 2)]))]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as ex:                     # OCR in parallel (tesseract subprocesses)
        list(ex.map(lambda p: p.exists() and page_texts(p), todo))
    projects = []
    for p in picked:
        for job in p["jobs"]:
            res = analyse_job(job, listing, p)
            res.update(slug=p["slug"], title=p["title"], paid=p["paid"], contract=p["contract"], estimate=p["estimate"])
            res["signals"] = signals_for(res, p["paid"], p["contract"])
            projects.append(res)
    data = {"thresholds": {"below": BELOW, "above": ABOVE, "major_share": MAJOR_SHARE, "min_item_amount": MIN_ITEM_AMOUNT,
                           "high_value": HIGH_VALUE, "coverage_ok": COVERAGE_OK},
            "requests": fetched, "selected": len(picked), "skipped_no_docs": skipped,
            "projects": projects, "mb_presence": mb_presence(listing)}
    OUT.write_text(json.dumps(data, indent=1, default=str))
    return data


def load():
    return json.loads(OUT.read_text()) if OUT.exists() else None


if __name__ == "__main__":
    import sys
    d = run(fetch_files="--no-fetch" not in sys.argv)
    print(json.dumps({"selected": d["selected"], "requests": d["requests"],
                      "compared": sum(1 for p in d["projects"] if any(w["compared_quantity"] is not None for w in p["work_items"]))}))
