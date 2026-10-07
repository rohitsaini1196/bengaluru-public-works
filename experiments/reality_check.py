"""Phase 3 experiment: can public imagery independently show before/after for Koramangala projects?

For each selected project:
  1. geocode a named place from the work description with OpenStreetMap Nominatim (bounded to
     Koramangala); the OSM object is the location evidence (inferred: a place named in the text)
  2. ask Esri World Imagery Wayback metadata, for a sample of the 196 archived releases (2014-2026),
     which satellite capture (date, sensor, resolution) covers the point
  3. download a 3x3 z18 tile mosaic (~0.6 m/px, ~460 m across) for each distinct capture
  4. pick the latest capture before the project's first date and the first capture after its last
     date, and write a side-by-side before/after image for manual review

Output: data/reality/<slug>/... and data/reality/summary.json. Nothing here is written into the
project database automatically; findings are recorded by hand in data/reality/assessments.json.

Usage: python -m experiments.reality_check
"""
import hashlib
import io
import json
import math
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reality"
DB = ROOT / "data" / "koramangala.db"
UA = {"User-Agent": "bengaluru-public-works/1.0 (civic research)"}
WAYBACK_CFG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
VIEWBOX = "77.59,12.97,77.66,12.90"
Z = 18

# slug -> place queries tried in order (named places taken from the work description) + what would be visible
PROJECTS = {
    "304-17-000105": (["Sony World Signal, Koramangala", "Ejipura Signal, Bengaluru", "Ejipura Main Road, Bengaluru"],
                      "elevated corridor deck / piers along Inner Ring Road"),
    "151-17-000080": (["Siddharth Colony, Bengaluru", "Siddhartha Colony, Koramangala", "Koramangala 3rd Block, Bengaluru"],
                      "new Ambedkar Bhavana building"),
    "ksphidcl-2023-24-bd-work-indent69": (["KSRP, Koramangala", "Karnataka State Reserve Police, Koramangala", "KSRP Quarters Koramangala"],
                                          "new police quarters blocks (72 units)"),
    "bscc-2025-26-ow-work-indent20": (["ESI Hospital, Koramangala", "Koramangala 5th Block, Bengaluru"],
                                      "ESI hospital / BBMP office construction, 5th Block"),
    "147-17-000088": (["Rajendra Nagar, Koramangala", "Koramangala 8th Block, Bengaluru"], "school building extension"),
    "151-16-000018": (["Lakshmi Devi Park, Koramangala", "Koramangala 6th Block, Bengaluru"], "reading room / gazebo in park"),
    "151-17-000005": (["Wipro Park, Koramangala"], "toilet and reading-room building in Wipro Park"),
    "186-23-000001": (["Tank Bund Road, Koramangala", "Tavarekere, Bengaluru"], "multipurpose building and kitchen"),
    "147-17-000038": (["Adugodi Main Road, Bengaluru", "Adugodi, Bengaluru"], "fresh asphalt on Adugodi Main Road"),
    "174-26-000008": (["Kudremukh Colony, Koramangala", "Koramangala 2nd Block, Bengaluru"], "road / cobble-stone works"),
    "151-19-000056": (["Koramangala Indoor Stadium", "BBMP Playground, Koramangala"], "playground fencing / gallery steps"),
    "bwssb-2025-26-wt-work-indent2527": (["Koramangala Sewage Treatment Plant", "STP Koramangala", "Koramangala Valley STP"],
                                          "20 MLD STP construction"),
    "151-15-000031": (["Koramangala 4th Block, Bengaluru"], "badminton court / shelter in rectangular park, 4th C block"),
    "kptcl-2025-26-ow-work-indent3242": (["Koramangala Substation", "KPTCL Koramangala"], "tower protection works"),
    "151-17-000100": (["Subramanya Park, Koramangala", "Koramangala 6th Block, Bengaluru"], "road/drain works on 17th F–G Main"),
}


def get(url, timeout=60, binary=False):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                b = r.read()
                return b if binary else json.loads(b)
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def geocode(queries):
    for q in queries:
        url = ("https://nominatim.openstreetmap.org/search?" +
               urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "viewbox": VIEWBOX, "bounded": 1}))
        res = get(url)
        time.sleep(1.1)  # Nominatim usage policy
        if res:
            r = res[0]
            return {"query": q, "lat": float(r["lat"]), "lon": float(r["lon"]), "osm": f"{r['osm_type']}/{r['osm_id']}",
                    "osm_url": f"https://www.openstreetmap.org/{r['osm_type']}/{r['osm_id']}", "name": r["display_name"]}
    return None


def tile_xy(lat, lon, z=Z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def releases(step=2):
    cfg = get(WAYBACK_CFG)
    rel = sorted(({"id": k, "date": v["itemTitle"].split("Wayback ")[1].rstrip(")"), "tile": v["itemURL"],
                   "meta": v["metadataLayerUrl"]} for k, v in cfg.items()), key=lambda r: r["date"])
    return rel[::step] + ([rel[-1]] if rel[-1] not in rel[::step] else [])


def capture(meta_url, lat, lon):
    for layer in (4, 5, 6):  # 30cm / 60cm / 1.2m metadata layers
        q = (f"{meta_url}/{layer}/query?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326&"
             "spatialRel=esriSpatialRelIntersects&outFields=SRC_DATE,SRC_RES,SRC_DESC&returnGeometry=false&f=json")
        d = get(q)
        feats = (d or {}).get("features") or []
        if feats:
            a = feats[0]["attributes"]
            s = str(a.get("SRC_DATE") or "")
            return {"date": f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s, "res_m": a.get("SRC_RES"), "sensor": a.get("SRC_DESC")}
    return None


def mosaic(tile_url, x, y, path):
    from PIL import Image
    img = Image.new("RGB", (768, 768))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            b = get(tile_url.replace("{level}", str(Z)).replace("{row}", str(y + dy)).replace("{col}", str(x + dx)), binary=True)
            if b:
                img.paste(Image.open(io.BytesIO(b)).convert("RGB"), ((dx + 1) * 256, (dy + 1) * 256))
    img.save(path, quality=85)
    return hashlib.md5(img.tobytes()).hexdigest()


def project_window(slug):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    p = con.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
    con.close()
    if not p:
        return None
    start = p["work_order_date"] or p["awarded_date"] or p["tender_published"] or p["first_date"]
    end = p["completion_date"] or p["last_payment"] or p["expected_completion"] or start
    return {"title": p["title"], "status": p["status"], "start": start, "end": end}


def main():
    from PIL import Image
    OUT.mkdir(parents=True, exist_ok=True)
    rel = releases()
    summary = {}
    for slug, (queries, look_for) in PROJECTS.items():
        win = project_window(slug)
        if not win:
            print("not in db", slug)
            continue
        g = geocode(queries)
        d = OUT / slug
        d.mkdir(exist_ok=True)
        rec = {"slug": slug, **win, "look_for": look_for, "geocode": g, "captures": []}
        if not g:
            rec["result"] = "not geocoded: no OSM object for the places named in the description"
            summary[slug] = rec
            print(slug, "no geocode")
            continue
        x, y = tile_xy(g["lat"], g["lon"])
        seen = {}
        for r in rel:
            c = capture(r["meta"], g["lat"], g["lon"])
            if c and c["date"] not in seen:
                seen[c["date"]] = {**c, "release": r["date"], "release_id": r["id"], "tile": r["tile"]}
        caps = sorted(seen.values(), key=lambda c: c["date"])
        before = [c for c in caps if c["date"] < (win["start"] or "")]
        after = [c for c in caps if c["date"] > (win["end"] or "9999")]
        pick = ([before[-1]] if before else caps[:1]) + ([after[0]] if after else caps[-1:])
        for c in pick:
            c["file"] = f"{c['date']}.jpg"
            c["hash"] = mosaic(c["tile"], x, y, d / c["file"])
        if len(pick) == 2:
            a, b = (Image.open(d / c["file"]) for c in pick)
            pair = Image.new("RGB", (1546, 768), "white")
            pair.paste(a, (0, 0))
            pair.paste(b, (778, 0))
            pair.save(d / "before_after.jpg", quality=85)
        rec.update(captures=[{k: v for k, v in c.items() if k != "tile"} for c in caps],
                   before=pick[0]["date"] if before else None, after=pick[-1]["date"] if after else None,
                   tile_xy=[Z, x, y], identical_pair=len(pick) == 2 and pick[0]["hash"] == pick[1]["hash"])
        summary[slug] = rec
        print(slug, g["query"], len(caps), "captures; before", rec["before"], "after", rec["after"])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
