"""Entity resolution: link tender groups (KPPP / BBMP tender lists) to BBMP job-number projects.

There is no shared identifier between tenders and bills, so every link is an inference.
Each candidate pair is scored on independent pieces of evidence:

  text       TF-IDF cosine of content words + character-sequence ratio of the descriptions
  place      overlap of extracted places (blocks, roads, localities)
  ward       ward numbers in the tender text agree / disagree with the job's ward
  contractor awarded bidder's name overlaps the payee on the bills
  timing     bills start after the tender was published (and not years later)
  amount     total paid is plausible against the tender's contract value / estimate
  office     same engineering division

A link is accepted at confidence
  high    text >= 0.90 with no contradiction, plus corroboration (contractor, amount or timing)
  medium  overall score >= 0.62, text >= 0.60, no hard contradiction, >= 2 corroborating signals
Lower-scoring candidates are kept as "possible" (shown as evidence, never merged).
"""
import math
import re
from collections import Counter
from datetime import date
from difflib import SequenceMatcher

from ingest import scope

STOP = set("""a an and the of in to for at on by from with near opp opposite other works work various
including incl etc no nos ward wards sub division bbmp bscc gba koramangala kormangala bengaluru bangalore
area areas surrounding surroundings road roads improvements improvement development developments developmental
providing construction comprehensive and allied as per year tender call package pkg constituency layout assembly
old new main cross""".split())
# keep 'main'/'cross' out of content words (they're in every ward work); places capture them with numbers

NAME_STOP = {"m", "s", "sri", "shri", "the", "and", "pvt", "ltd", "limited", "private", "co", "company",
             "constructions", "construction", "enterprises", "enterprise", "engineering", "engineers", "works",
             "infra", "infrastructure", "infrastructures", "associates", "contractor", "contractors", "india",
             "escrow", "a", "c", "account", "electricals", "electrical", "consultants", "consultant", "bhusiri", "accou"}

SPELL = {"blcok": "block", "drian": "drain", "drians": "drains", "korumangala": "koramangala", "improvments": "improvements",
         "comprohensive": "comprehensive", "comprehesive": "comprehensive", "jakksandra": "jakkasandra",
         "maintainance": "maintenance", "kudremukh": "kuduremukha", "kudremukha": "kuduremukha", "korumangla": "koramangala",
         "sidartha": "siddartha", "siddhartha": "siddartha", "ejipuara": "ejipura", "ejipua": "ejipura", "maintanance": "maintenance", "footpaths": "footpath", "drains": "drain"}

# Ward-number equivalences across delimitations (198-ward map -> 225-ward map -> 243-ward map),
# read off tender titles such as "ward No. 174 (Old No.151) Koramangala".
from ingest.area import AREA as _AREA        # noqa: E402
WARD_EQUIV = [set(x) for x in _AREA.ward_equivalents]


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(SPELL.get(w, w) for w in s.split())


def content_tokens(s):
    return [w for w in norm(s).split() if w not in STOP and not (w.isdigit() and len(w) > 3) and len(w) > 1]


def name_tokens(s):
    return {t for t in norm(s).split() if len(t) > 2 and t not in NAME_STOP and not t.isdigit()}


WORK_TYPES = {
    "road": r"asphalt|road|cc road|cement concrete|cobble|paver|pothole|white ?topping",
    "drain": r"drain|culvert|swd|desilt|slab",
    "footpath": r"footpath|foot path",
    "water": r"borewell|bore well|water supply|drinking water|\bro plant|pipeline|pipe line|sump|rain ?water",
    "light": r"street ?light|lighting|\bled\b|high mast|electri",
    "park": r"\bpark|garden|playground|play ground|gazebo|badminton",
    "building": r"building|bhavan|school|office|toilet|hall|canteen|nursery|reading room",
    "burial": r"burial|cremat",
    "waste": r"waste|garbage|dust ?bin|shredder|compost",
    "signage": r"name ?board|sign ?board|signage",
}
WORK_TYPES = {k: re.compile(v, re.I) for k, v in WORK_TYPES.items()}


def work_types(s):
    s = norm(s)
    return {k for k, rx in WORK_TYPES.items() if rx.search(s)}


LOCALITIES = {
    "kuduremukha": r"kuduremukh", "jakkasandra": r"jakk?a?sandra", "siddartha colony": r"siddart?h?a colony",
    "kathalipalya": r"kathali ?pa?l?a?ya|kathali playa", "mestripalya": r"mest(?:h)?r[iy] ?pa?l?a?ya",
    "rajendranagar": r"rajendra ?nagar", "ngv": r"\bngv\b|national games? village", "st johns": r"st johns?",
    "ejipura": r"ejipura", "adugodi": r"adugodi", "venkatapura": r"venkatapura", "khb colony": r"khb colony",
    "krupanidhi": r"krupanidhi", "sony world": r"sony", "inner ring road": r"inner ring road", "sarjapur road": r"sarjapura? road",
    "hosur road": r"hosur road", "80 feet road": r"80 ?(?:feet|ft) road", "100 feet road": r"100 ?(?:feet|ft) road",
    "koramangala village": r"koramangala village", "ews": r"\bews\b", "lakkasandra": r"lakkasandra", "tavarekere": r"tavarekere",
    "industrial area": r"industrial (?:area|layout)", "nanjappa reddy": r"nanjappa reddy", "ak colony": r"\ba ?k colony",
}
LOCALITIES = {k: re.compile(v) for k, v in LOCALITIES.items()}


def place_keys(s):
    """Canonical place keys: block5a, main17g, cross3, plus named localities."""
    t = norm(s)
    keys = set()
    for n, letter in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*([a-d])?\s*block\b", t):
        keys.add(f"block{n}")
    for n, letter, kind in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)\s*([a-h])?\s*(main|cross)\b", t):
        keys.add(f"{kind}{n}{letter}")
    for k, rx in LOCALITIES.items():
        if rx.search(t):
            keys.add(k)
    return keys


def ward_numbers(s):
    """All ward numbers in a 'ward No 147 Adugodi, 148 Ejipura, 151 Koramangala and 173 …' list."""
    nums = set()
    for m in re.finditer(r"wards?\s*(?:no|nos|number)?s?\.?\s*[:\-]?\s*(\d{1,3})", s or "", re.I):
        nums.add(m.group(1))
        tail = (s or "")[m.end(): m.end() + 90]
        tail = re.split(r"\b(?:in|of|under|for|package|pkg|bbmp)\b", tail, flags=re.I)[0]
        nums |= set(re.findall(r"\b(\d{2,3})\b", tail))
    nums |= set(re.findall(r"old\s*no\.?\s*(\d{1,3})", s or "", re.I))
    out = set(nums)
    for n in nums:
        for g in WARD_EQUIV:
            if n in g:
                out |= g
    return out


def to_date(s):
    try:
        return date.fromisoformat(s[:10])
    except (TypeError, ValueError):
        return None


class Corpus:
    """Tiny TF-IDF over all descriptions so that rare words (road names, block numbers) weigh more."""

    def __init__(self, docs):
        self.df = Counter()
        for d in docs:
            self.df.update(set(content_tokens(d)))
        self.n = max(1, len(docs))

    def vec(self, s):
        tf = Counter(content_tokens(s))
        return {t: c * math.log((1 + self.n) / (1 + self.df[t])) for t, c in tf.items()}

    @staticmethod
    def cosine(a, b):
        if not a or not b:
            return 0.0
        dot = sum(v * b.get(k, 0) for k, v in a.items())
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0


def text_similarity(corpus, a, b):
    """Max of TF-IDF cosine and sequence ratio — both on content words only, so shared
    boiler-plate ("in ward no 151 Koramangala") cannot make two different works look alike."""
    cos = Corpus.cosine(corpus.vec(a), corpus.vec(b))
    ca, cb = " ".join(content_tokens(a)), " ".join(content_tokens(b))
    seq = SequenceMatcher(None, ca, cb).ratio() if ca and cb else 0.0
    return max(cos, seq), cos, seq


def job_info(p):
    """Facts about a job-number project used for matching."""
    bills = p["bills"]
    dates = sorted(d for b in bills for d in (b.get("wo_date"), b.get("start_date"), b.get("br_date"), b.get("payment_date")) if d)
    wards = set()
    for b in bills:
        w = (b.get("ward") or "").lstrip("0")
        if b["regime"] in ("198", "225", "243") and w.isdigit():
            wards.add(w)
    for j in p["job_numbers"]:
        wards.add(j.replace("R-", "").split("-")[0].lstrip("0"))
    eq = set(wards)
    for w in wards:
        for g in WARD_EQUIV:
            if w in g:
                eq |= g
    payees = {b.get("contractor") for b in bills if b.get("contractor")}
    gross = sum(b.get("gross") or 0 for b in bills)
    yy = [int(j.replace("R-", "").split("-")[1]) for j in p["job_numbers"]]
    return dict(first=to_date(dates[0]) if dates else None, last=to_date(dates[-1]) if dates else None,
                wards=eq, payees=payees, gross=gross, job_years=yy,
                places=place_keys(" ".join(p["descriptions"])),
                office=norm(p.get("office") or ""))


def score_pair(corpus, group, p, ji):
    """Score one tender group against one job project. Returns a dict of evidence."""
    t0 = group[0]
    tl = group[-1]
    awarded = next((t for t in reversed(group) if t.get("contractor")), tl)
    texts = {t["description"] for t in group}
    best = max((text_similarity(corpus, a, b) for a in texts for b in p["descriptions"]), key=lambda x: x[0])
    text = best[0]
    ev = {"text": round(text, 3), "text_cosine": round(best[1], 3), "text_sequence": round(best[2], 3)}
    support, conflict = [], []

    # kind of work (a borewell tender cannot be a road job)
    tt = set().union(*(work_types(t["description"]) for t in group))
    jt = set().union(*(work_types(d) for d in p["descriptions"]))
    if tt and jt:
        if tt & jt:
            support.append(f"same kind of work ({', '.join(sorted(tt & jt))})")
        else:
            conflict.append(f"different kind of work ({', '.join(sorted(tt))} vs {', '.join(sorted(jt))})")

    # place overlap
    tplaces = place_keys(" ".join(texts))
    if tplaces and ji["places"]:
        ov = tplaces & ji["places"]
        ev["place"] = sorted(ov)
        if ov:
            support.append(f"same place(s): {', '.join(sorted(ov)[:3])}")
        else:
            conflict.append(f"different places named ({', '.join(sorted(tplaces)[:3])} vs {', '.join(sorted(ji['places'])[:3])})")

    # ward
    tw = set()
    for t in group:
        tw |= ward_numbers(t["description"])
    if tw and ji["wards"]:
        if tw & ji["wards"]:
            support.append("ward numbers agree")
            ev["ward"] = "agree"
        else:
            conflict.append(f"ward numbers differ (tender {', '.join(sorted(tw)[:3])})")
            ev["ward"] = "differ"

    # contractor
    if awarded.get("contractor") and ji["payees"]:
        tn = name_tokens(awarded["contractor"])
        hit = [pname for pname in ji["payees"] if tn & name_tokens(pname)]
        if hit:
            support.append(f"contractor matches bills ({hit[0]})")
            ev["contractor"] = "match"
        else:
            ev["contractor"] = "differ"
            conflict.append("awarded bidder not among payees")

    # timing
    pub = to_date(t0.get("published_date"))
    if pub and ji["first"]:
        lag = (ji["first"] - pub).days
        ev["lag_days"] = lag
        if -45 <= lag <= 3 * 365:
            support.append(f"bills start {lag} days after tender")
        elif lag < -45:
            conflict.append(f"bills start {-lag} days before the tender")
        else:
            conflict.append(f"bills start {lag // 365} years after tender")
    if pub and ji["job_years"]:
        fy_end = pub.year + (1 if pub.month >= 4 else 0)  # FY 2024-25 -> 25
        dy = min(abs((2000 + y) - fy_end) for y in ji["job_years"])
        ev["job_year_gap"] = dy
        if dy > 2:
            conflict.append("job-number year far from tender year")

    # amount
    ref_amt = next((t.get("contract_value") for t in reversed(group) if t.get("contract_value")), None) or \
        next((t.get("ecv") for t in reversed(group) if t.get("ecv")), None)
    if ref_amt and ji["gross"]:
        ratio = ji["gross"] / ref_amt
        ev["paid_ratio"] = round(ratio, 2)
        if 0.5 <= ratio <= 1.3:
            support.append(f"paid is {ratio:.0%} of tender value")
        elif ratio > 2.5 or ratio < 0.1:
            conflict.append(f"paid is {ratio:.0%} of tender value")

    # office
    to = norm(" ".join(filter(None, [t.get("office") or "" for t in group])))
    if to and ji["office"]:
        keys = [k for k in ("btm", "koramangala", "electrical", "swd", "project", "horticult", "major road", "mped")
                if k in to.replace(" ", "") or k in to]
        if any(k.replace(" ", "") in ji["office"].replace(" ", "") for k in keys):
            support.append("same engineering division")

    hard = [c for c in conflict if c.startswith(("bills start", "ward numbers differ", "job-number year", "different places", "different kind"))]
    lag = ev.get("lag_days")
    recency = 0.1 * max(0.0, 1 - lag / 730) if lag is not None and lag >= -45 else 0.0
    score = 0.6 * text + 0.08 * min(len(support), 4) + recency - 0.12 * len(conflict)
    ev.update(score=round(score, 3), support=support, conflict=conflict)
    late = lag is not None and lag > 2 * 365
    if late:  # bills 2-3 years after the tender: plausible but weak (re-tender? different contract?)
        support = [x for x in support if not x.startswith("bills start")]
        conflict = conflict + [f"bills begin {lag // 30} months after the tender (re-tender or a different contract?)"]
        ev.update(support=support, conflict=conflict)
    if text >= 0.90 and not hard and len(support) >= 1 and not late:
        ev["confidence"] = "high"
    elif score >= 0.62 and text >= 0.72 and not hard and len(support) >= 2 and any(
            x.startswith(("same kind", "same place", "contractor matches")) or
            (x.startswith("paid is") and 0.8 <= ev.get("paid_ratio", 0) <= 1.2) for x in support):
        ev["confidence"] = "medium"
    elif text >= 0.45 and score >= 0.35:
        ev["confidence"] = "possible"
    else:
        ev["confidence"] = None
    return ev


PLAN_SOURCES = {"WB-POW", "WB-GRANT"}


def link(job_projects, tender_groups):
    """Return (accepted, possible): accepted maps group key -> (project, evidence) using a
    greedy best-first one-tender-to-one-job assignment; possible lists weaker candidates."""
    corpus = Corpus([d for p in job_projects for d in p["descriptions"]] +
                    [t["description"] for g in tender_groups.values() for t in g])
    infos = {p["key"]: job_info(p) for p in job_projects}
    cands = []
    for gk, g in tender_groups.items():
        gvec = corpus.vec(" ".join(t["description"] for t in g))
        for p in job_projects:
            # cheap prefilter on TF-IDF before the expensive scoring
            if Corpus.cosine(gvec, corpus.vec(" ".join(p["descriptions"]))) < 0.25 and \
                    SequenceMatcher(None, norm(g[0]["description"]), norm(p["title"])).quick_ratio() < 0.6:
                continue
            ev = score_pair(corpus, g, p, infos[p["key"]])
            if ev["confidence"]:
                cands.append((gk, p, ev))
    order = {"high": 0, "medium": 1, "possible": 2}
    cands.sort(key=lambda c: (order[c[2]["confidence"]], -c[2]["score"]))
    accepted, used_groups, used_jobs, possible = {}, set(), set(), []
    for gk, p, ev in cands:  # one tender group <-> one job, best evidence first
        fam = "plan" if gk[0] in PLAN_SOURCES else "tender"   # a job may have one tender AND one plan entry
        if ev["confidence"] in ("high", "medium") and gk not in used_groups and (fam, p["key"]) not in used_jobs:
            accepted[gk] = (p, ev)
            used_groups.add(gk)
            used_jobs.add((fam, p["key"]))
        else:
            possible.append((gk, p, dict(ev, confidence="possible")))
    return accepted, possible


def related_works(projects, corpus=None, threshold=0.82):
    """Pairs of distinct projects whose descriptions are near-identical (possible repeat/duplicate works)."""
    corpus = corpus or Corpus([d for p in projects for d in p["descriptions"]])
    vecs = [(p, corpus.vec(p["title"])) for p in projects]
    out = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            a, va = vecs[i]
            b, vb = vecs[j]
            c = Corpus.cosine(va, vb)
            if c < 0.6:
                continue
            s = max(c, SequenceMatcher(None, norm(a["title"]), norm(b["title"])).ratio())
            if s >= threshold:
                out.append((a, b, round(s, 3)))
    return out
