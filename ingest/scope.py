"""Koramangala scope test, work-type classification and location extraction.

All rules are keyword based and deliberately conservative: a record is in scope only if
(a) it is booked against the Koramangala ward number for the ward-numbering regime of its
source file, or (b) its work description names Koramangala (or its sub-localities) — and
it is a road / civic public work rather than an event, service contract or purchase.
"""
import re

from ingest import area as _area

AREA = _area.AREA
# The active area's rules (config/areas/<slug>.json). Names kept from the Koramangala-only version.
KORA = AREA.locality                 # description names the area (all spellings found in BBMP records)
VALLEY_ONLY = AREA.exclude           # names to ignore (e.g. "Koramangala Valley" storm-water system)
NOT_CITY = AREA.not_city             # same-named places outside the city
KORAMANGALA_WARDS = AREA.wards

# (category, pattern) — first match wins. None category = out of scope (not a public work).
EXCLUDE = re.compile(
    r"ganesh|immersion|illumination to|temporary illumination|festival|rajyotsava|grant in aid|grant-in-aid|"
    r"hiring of|hire of|manpower|man power|operator|data entry|housekeeping|fire extinguisher|"
    r"consultan|\bPMC\b|DPR\b|survey work|control room|"
    r"modular kitchen|wardrobe|mosquito|masquito|curtain rod|qtrs|quarters no|judicial block|"
    r"c ?& ?d (type|group) quarters|krishna block|sharavathi block|varada block|arkavathi", re.I)

CATEGORIES = [
    ("Drains & storm-water", r"\bswd\b|storm ?water|primary drain|secondary drain|tertiary drain|box drain|rcc drain|desilt|culvert|retaining wall"),
    ("Water supply & sewerage", r"water supply|sewer|\bugd\b|pipe ?line|borewell|bore well|\bstp\b|\bDI\b|fhtc"),
    ("Buildings & civic facilities", r"building|school|hospital|bhavan|bhavana|toilet|canteen|burial|cremat|crematorium|"
                                     r"compound wall|shed|market|library|anganwadi|community hall|quarters|nursery|multipurpose"),
    ("Street lighting & electrical", r"street ?light|lighting|high mast|electri|\bLT\b|\bHT\b|transformer|substation|C&R panel"),
    ("Footpaths & pedestrian", r"footpath|foot path|pedestrian|skywalk|sky walk|subway"),
    ("Junctions, flyovers & grade separators", r"flyover|fly over|grade separator|underpass|junction|signal"),
    ("Roads", r"road|asphalt|pothole|pot hole|white ?topping|tender ?sure|cobble|paver|street name|name board|carriage ?way|resurfac"),
    ("Drains & storm-water", r"drain"),
    ("Parks & playgrounds", r"park|playground|play ground|garden|horticult|lake|tree|landscap"),
    ("Solid waste infrastructure", r"waste|garbage|\bswm\b|dry waste|recycling"),
    ("General civic works", r"improvement|development|maintenance|repair|construction|providing"),
]
CATEGORIES = [(c, re.compile(p, re.I)) for c, p in CATEGORIES]
# Ward works usually bundle roads + drains + footpaths in one job.
COMBINED_PARTS = [re.compile(p, re.I) for p in (r"\broads?\b|asphalt|cobble", r"drain", r"footpath|foot path")]
SWD_MAJOR = re.compile(r"\bswd\b|storm ?water|primary drain|secondary drain", re.I)


def is_area_ward(regime, ward, job_number=None, area=None):
    wards = (area or AREA).wards.get(regime or "", set())
    if ward and ward.strip().lstrip("0") in wards:
        return True
    if job_number and regime == "198":
        prefix = job_number.replace("R-", "").split("-")[0].lstrip("0")
        return prefix in wards
    return False


is_koramangala_ward = is_area_ward


def scope_reason(text, regime=None, ward=None, job_number=None, area=None):
    """Return a human-readable reason the record is in the area's scope, or None."""
    a = area or AREA
    text = text or ""
    if a.not_city.search(text):
        return None
    if is_area_ward(regime, ward, job_number, a):
        num = sorted(a.wards[regime])[0]
        return f"Booked to {a.area_name} ward ({num} under the {regime}-ward map)"
    if a.locality.search(text):
        stripped = a.exclude.sub("", text)
        m = a.locality.search(stripped)
        if not m:
            return None  # only an excluded name (e.g. "Koramangala Valley", the city-wide K-100 drain)
        snippet = stripped[max(0, m.start() - 40): m.end() + 30].strip()
        return f'Work description names {a.area_name}: "…{snippet}…"'
    return None


def classify(text):
    """Return a work category, or None if the record isn't a road/public work."""
    text = text or ""
    if EXCLUDE.search(text):
        return None
    parts = [rx.search(text) for rx in COMBINED_PARTS]
    if sum(bool(m) for m in parts) >= 2 and not SWD_MAJOR.search(text):
        return "Roads, drains & footpaths (combined)"
    for cat, rx in CATEGORIES:
        if rx.search(text):
            return cat
    return None


LOC_RX = AREA.locations              # sub-localities / roads of the active area


def extract_location(text):
    seen, out = set(), []
    for m in LOC_RX.finditer(text or ""):
        s = re.sub(r"\s+", " ", m.group(0)).strip(" ,").title()
        s = re.sub(r"(\d)(St|Nd|Rd|Th)\b", lambda x: x.group(1) + x.group(2).lower(), s)
        s = re.sub(r"\b(Ngv|Ews|St Bed|S T Bed)\b", lambda x: x.group(0).upper(), s)
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:6]
