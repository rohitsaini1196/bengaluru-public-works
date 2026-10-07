"""Area configuration: everything that makes the pipeline about one locality.

    AREA=hsr_layout python -m ingest.discover_bbmp ...      (default AREA=koramangala)

An area is a JSON file in config/areas/:
  area_name, slug        display name and file-safe id
  locality_regex         description text that names the area (spellings, sub-areas)
  exclude_regex          names to ignore even when the locality regex matches (e.g. a city-wide drain named after it)
  not_city_regex         same-named places outside the city
  ward_maps              {ward-map regime: [ward numbers]}; regimes are "198" (to FY22-23), "243" (FY23-24), "225" (FY24-25 on)
  ward_equivalents       ward numbers that denote the same area across maps (used when matching tender titles)
  sub_localities         regexes for place names to extract as locations
  kppp_keywords          search terms for the procurement portal
  known_divisions, payments_from, verified, source_of_ward_numbers   documentation and coverage metadata
"""
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AREAS = ROOT / "config" / "areas"
NEVER = re.compile(r"(?!x)x")            # matches nothing


@dataclass(frozen=True)
class AreaConfig:
    area_name: str
    slug: str
    locality_regex: str
    ward_maps: dict
    exclude_regex: str = None
    not_city_regex: str = None
    ward_equivalents: list = field(default_factory=list)
    sub_localities: list = field(default_factory=list)
    kppp_keywords: list = field(default_factory=list)
    known_divisions: list = field(default_factory=list)
    payments_from: str = "2015-04-01"
    verified: bool = False
    city: str = "Bengaluru"
    source_of_ward_numbers: str = ""

    @property
    def locality(self):
        return re.compile(self.locality_regex, re.I)

    @property
    def exclude(self):
        return re.compile(self.exclude_regex, re.I) if self.exclude_regex else NEVER

    @property
    def not_city(self):
        return re.compile(self.not_city_regex, re.I) if self.not_city_regex else NEVER

    @property
    def wards(self):
        """{regime: set(ward numbers)}"""
        return {k: set(v) for k, v in self.ward_maps.items()}

    @property
    def locations(self):
        return re.compile("|".join(self.sub_localities), re.I) if self.sub_localities else NEVER


@lru_cache(maxsize=None)
def load(slug=None):
    slug = slug or os.environ.get("AREA", "koramangala")
    path = AREAS / f"{slug}.json"
    if not path.exists():
        raise SystemExit(f"unknown area {slug!r}: no {path.relative_to(ROOT)}")
    raw = json.loads(path.read_text())
    keys = AreaConfig.__dataclass_fields__
    cfg = AreaConfig(**{k: v for k, v in raw.items() if k in keys})
    cfg.locality, cfg.exclude, cfg.not_city, cfg.locations      # compile once: bad regexes fail at load time
    return cfg


def available():
    return sorted(p.stem for p in AREAS.glob("*.json"))


AREA = load()
