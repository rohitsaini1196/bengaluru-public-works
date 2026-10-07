"""Phase 7: the pipeline is configured per area (config/areas/<slug>.json), not hard-coded to Koramangala."""
import json

import pytest

from ingest import area as A, discover_bbmp as D, scope


def test_configs_load_and_compile():
    assert {"koramangala", "hsr_layout"} <= set(A.available())
    for slug in A.available():
        cfg = A.load(slug)
        assert cfg.area_name and cfg.ward_maps and cfg.locality.pattern
        assert set(cfg.ward_maps) <= {"198", "225", "243"}


def test_default_area_is_koramangala_and_unchanged():
    k = A.load("koramangala")
    assert scope.AREA == k and scope.KORAMANGALA_WARDS == {"198": {"151"}, "225": {"174"}, "243": {"186"}}
    assert scope.scope_reason("Asphalting in Koramanagala 6th block") and scope.scope_reason("SWD in Koramangala Valley") is None


def test_same_ward_number_means_different_areas_on_different_maps():
    hsr, kora = A.load("hsr_layout"), A.load("koramangala")
    # ward 174 is HSR Layout on the 198-ward map but Koramangala on the 225-ward map
    assert scope.is_area_ward("198", "174", area=hsr) and not scope.is_area_ward("198", "174", area=kora)
    assert scope.is_area_ward("225", "174", area=kora) and not scope.is_area_ward("225", "174", area=hsr)


def test_scope_rules_follow_the_area():
    hsr = A.load("hsr_layout")
    assert scope.scope_reason("Asphalting of roads in HSR Layout 2nd sector", area=hsr).startswith("Work description names HSR Layout")
    assert scope.scope_reason("Asphalting of roads in HSR Layout 2nd sector") is None          # not Koramangala
    assert scope.scope_reason("Footpath in Koramangala 4th block", area=hsr) is None


def test_unverified_config_is_marked():
    assert A.load("koramangala").verified is True and A.load("hsr_layout").verified is False


@pytest.mark.skipif(not D.OUT.exists() or not any(D.OUT.glob("*.json")), reason="payment grid not cached")
def test_second_area_discovers_from_cached_grid_without_code_changes():
    hsr = D.in_area(area=A.load("hsr_layout"))
    kora = D.in_area(area=A.load("koramangala"))
    assert len(hsr) > 100 and len(kora) == len(D.in_area())
    overlap = {r["bill_id"] for r in hsr} & {r["bill_id"] for r in kora}
    assert len(overlap) < 0.05 * len(hsr)              # the areas are largely disjoint
