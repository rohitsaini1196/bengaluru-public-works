"""Contractor entity resolution across BBMP bills (IFMS) and KPPP tenders.

Mentions of a contractor (a payee on a bill, an awarded bidder, a bidder in a comparative
statement) are joined into entities with union-find. Every join records the rule that made it:

  rule               confidence  meaning
  ifms_code          confirmed   same 5-6 digit IFMS contractor code on BBMP bills
  kppp_supplier      confirmed   same KPPP supplierId on awarded bids
  kppp_display       confirmed   identical KPPP display name "PERSON( FIRM )" (KPPP suffixes
                                 duplicate person names with "(1)", so the string identifies an account)
  ifms_mobile        inferred    same registered mobile on BBMP bills (mobile used in memory only —
                                 never stored or shown); skipped when one mobile spans 3+ codes
  name               inferred    same normalised name (IFMS truncates names at 20 characters, so a
                                 truncated name is matched as a prefix)
  cross_system_name  inferred    KPPP person or firm name equals an IFMS payee name; strengthened
                                 (and only allowed for short names) when they co-occur on a linked project

An entity's identity basis is "confirmed" only if every join inside it is confirmed.
"""
import re
from collections import Counter, defaultdict

GENERIC = {"technical manager", "executive engineer", "assistant executive engineer", "chief engineer",
           "contractor", "na", "nil", "escrow", "the technical manager"}
BUSINESS_WORDS = {"enterprises", "enterprise", "constructions", "construction", "associates", "electricals", "electrical",
                  "engineering", "engineers", "works", "infra", "infrastructure", "infrastructures", "contractor",
                  "contractors", "builders", "traders", "services", "solutions", "technologies", "consultants",
                  "management", "industries", "agencies", "developers", "projects", "systems", "electric", "civil"}
STRIP = re.compile(r"\b(m\s*/?\s*s|messrs|mr|mrs|ms|sri|shri|smt|the|and|&)\b")


def norm_name(s):
    s = (s or "").lower()
    s = re.sub(r"\(\s*\d+\s*\)", " ", s)          # KPPP "(1)" disambiguator
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = STRIP.sub(" ", s)
    s = re.sub(r"\b(pvt|private|ltd|limited|llp|co|company)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def kppp_parts(display):
    """'Annavari Prakash( SRI BALAJI CONSTRUCTIONS )' -> ('annavari prakash', 'balaji constructions')."""
    m = re.match(r"\s*(.*?)\s*\(\s*(.*?)\s*\)\s*$", display or "")
    if m and not m.group(2).strip().isdigit():
        person, firm = m.group(1), m.group(2)
    else:
        person, firm = display, None
    return norm_name(person), (norm_name(firm) if firm else None)


class DSU:
    def __init__(self):
        self.p, self.edges = {}, defaultdict(list)

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b, rule, conf, detail):
        ra, rb = self.find(a), self.find(b)
        self.edges[(a, b)].append((rule, conf, detail))
        if ra != rb:
            self.p[rb] = ra


def _similar_names(a, b):
    """Loose similarity used only together with a shared mobile: share a 4+ letter token, or
    token-sorted strings are >= 0.6 similar ('k c sridhar' ~ 'sreedhara k c')."""
    from difflib import SequenceMatcher
    ta, tb = set(a.split()) - BUSINESS_WORDS, set(b.split()) - BUSINESS_WORDS
    if not ta or not tb:
        return False
    if any(len(t) >= 4 for t in ta & tb):
        return True
    return SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio() >= 0.6


def _name_compatible(a, b):
    """IFMS names are cut at 20 characters: 'vijayalakshmi associ' ~ 'vijayalakshmi associates'."""
    if not a or not b or a in GENERIC or b in GENERIC:
        return False
    if a == b:
        return len(a) >= 5
    ta, tb = a.split(), b.split()
    if sorted(ta) == sorted(tb) and len(ta) >= 3 and len(a) >= 8:   # 'sreedhara k c' == 'k c sreedhara'
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 14 and long_.startswith(short)


def resolve(bills, tenders, linked_pairs=()):
    """bills: bill records (contractor, contractor_code, _phone, source...);
    tenders: KPPP tender records (contractor, supplier_id, bidders);
    linked_pairs: iterable of (tender_number, set_of_bill_payee_names) for accepted tender<->job links.
    Returns (entities, mention_to_entity) where a mention key is ('ifms', name) or ('kppp', display)."""
    d = DSU()
    mentions = Counter()
    # --- IFMS mentions: key by cleaned name; codes/mobiles join them
    by_code, by_phone, phone_codes = defaultdict(set), defaultdict(set), defaultdict(set)
    for b in bills:
        name = b.get("contractor")
        if not name:
            continue
        m = ("ifms", name)
        mentions[m] += 1
        d.find(m)
        if b.get("contractor_code"):
            by_code[b["contractor_code"]].add(m)
        if b.get("_phone"):
            by_phone[b["_phone"]].add(m)
            if b.get("contractor_code"):
                phone_codes[b["_phone"]].add(b["contractor_code"])
    for code, ms in by_code.items():
        ms = sorted(ms)
        for x in ms[1:]:
            d.union(ms[0], x, "ifms_code", "confirmed", f"IFMS contractor code {code}")
    for ph, ms in by_phone.items():
        if len(phone_codes[ph]) >= 3:
            continue  # an agent's or office phone shared by several contractors
        ms = sorted(ms)
        for i, a in enumerate(ms):
            for x in ms[i + 1:]:
                if _similar_names(norm_name(a[1]), norm_name(x[1])):  # a phone alone never merges different names
                    d.union(a, x, "ifms_mobile", "inferred", "same registered mobile on BBMP bills (not published) and similar name")
    # --- KPPP mentions
    by_supplier = defaultdict(set)
    for t in tenders:
        if t.get("contractor"):
            m = ("kppp", t["contractor"])
            mentions[m] += 1
            d.find(m)
            if t.get("supplier_id"):
                by_supplier[t["supplier_id"]].add(m)
        for bd in t.get("bidders") or []:
            m = ("kppp", bd["name"])
            mentions[m] += 1
            d.find(m)
    for sid, ms in by_supplier.items():
        ms = sorted(ms)
        for x in ms[1:]:
            d.union(ms[0], x, "kppp_supplier", "confirmed", f"KPPP supplierId {sid}")
    # identical KPPP display strings are already one mention key ("kppp_display")

    # --- name joins
    ifms = [m for m in mentions if m[0] == "ifms"]
    norm = {m: norm_name(m[1]) for m in ifms}
    by_norm = defaultdict(list)
    for m, n in norm.items():
        by_norm[n].append(m)
    for n, ms in by_norm.items():
        for x in ms[1:]:
            if _name_compatible(n, n):
                d.union(ms[0], x, "name", "inferred", f"same normalised name “{n}”")
    keys = sorted(by_norm)
    for i, a in enumerate(keys):            # truncated-name prefix joins
        for b in keys[i + 1:]:
            if not b.startswith(a[:14]):
                if b[:1] > a[:1]:
                    break
                continue
            if a != b and _name_compatible(a, b):
                d.union(by_norm[a][0], by_norm[b][0], "name", "inferred", f"“{a}” is a truncation of “{b}”")
    # --- cross-system joins (KPPP person/firm name == IFMS payee name)
    linked_names = defaultdict(set)
    for tn_contractor, payees in linked_pairs:
        for pn in payees:
            linked_names[tn_contractor].add(norm_name(pn))
    kppp = [m for m in mentions if m[0] == "kppp"]
    firm_accounts = defaultdict(set)
    for m in kppp:
        firm = kppp_parts(m[1])[1]
        if firm:
            firm_accounts[firm].add(m[1])
    ambiguous = []
    for m in kppp:
        person, firm = kppp_parts(m[1])
        for kind, cand in (("person", person), ("firm", firm)):
            if not cand:
                continue
            for n in by_norm:
                if not _name_compatible(cand, n):
                    continue
                together = n in linked_names.get(m[1], set())
                if kind == "firm" and len(firm_accounts[cand]) > 1 and not together:
                    ambiguous.append((m[1], by_norm[n][0][1]))   # several KPPP accounts trade under this firm name
                    continue
                if kind == "person" and len(cand.split()) < 3 and not together:
                    continue                                       # short person names are too common
                d.union(m, by_norm[n][0], "cross_system_name", "inferred",
                        f"KPPP “{m[1]}” ~ IFMS “{by_norm[n][0][1]}”" + (" — also on the same linked project" if together else ""))

    # --- build entities
    groups = defaultdict(list)
    for m in mentions:
        groups[d.find(m)].append(m)
    entities, m2e = [], {}
    for i, (root, ms) in enumerate(sorted(groups.items(), key=lambda kv: -sum(mentions[m] for m in kv[1]))):
        eid = f"C{i + 1:04d}"
        edges = [(a, b, r) for (a, b), rs in d.edges.items() if d.find(a) == root for r in rs]
        rules = Counter(r[0] for _, _, r in edges)
        basis = "confirmed" if all(r[1] == "confirmed" for _, _, r in edges) else "inferred"
        names = Counter()
        for m in ms:
            names[m[1]] += mentions[m]
        display = max(names, key=lambda n: (names[n], len(n)))
        ids = sorted({f"IFMS:{c}" for c, s in by_code.items() if set(s) & set(ms)} |
                     {f"KPPP:{sid}" for sid, s in by_supplier.items() if set(s) & set(ms)})
        entities.append({"id": eid, "name": display, "aliases": sorted(n for n in names if n != display),
                         "ids": ids, "basis": basis, "rules": dict(rules),
                         "evidence": sorted({r[2] for _, _, r in edges})[:12],
                         "systems": sorted({m[0] for m in ms})})
        for m in ms:
            m2e[m] = eid
    return entities, m2e, ambiguous
