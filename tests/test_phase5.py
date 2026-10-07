"""Phase 5: discovery (IFMS payment grid), unit / amount normalisation, BOQ and MB parsing,
quantity comparison, signal thresholds and provenance."""
import json
import sqlite3
from datetime import date

import pytest

from ingest import bills as B, build, discover_bbmp as D, quantities as Q

URL = "https://accounts.bbmp.gov.in/vssWB/vss00CvStatusData.php?pAction=LoadPaymentGridData&pDateFrom=01-Apr-2020"


def grid_row(bill_id, job, desc, rtgs="Rtgs - 000021 / 21-May-2020", amount="1444028", contractor="C Pushparaj", br="000094"):
    return {"slno": "1", "id": bill_id,
            "wodetails": f"<a href=\"#\" onclick=\"vssFillData({bill_id},'{job}');\">{job}</a><br/>{desc}",
            "contractor": contractor, "brnumber": f"BR - {br} / 09-Aug-2018<br/>CBR - 009072 / 17-Mar-2020<br/>{rtgs}",
            "amount": amount, "nett": "1403704", "deduction": "40324"}


@pytest.fixture()
def grid(tmp_path, monkeypatch):
    """A fake cache of two quarterly windows."""
    monkeypatch.setattr(D, "OUT", tmp_path)
    w1 = [grid_row("1", "151-17-000082", "Improvements to Burial ground in Jakkasandra Ext and Koramangala in ward no 151"),
          grid_row("2", "151-18-000004", "Asphalting of roads in ward no 151"),                      # ward prefix only
          grid_row("3", "186-23-000001", "Development of roads in ward No.186 Jaraganahalli"),        # prefix collides
          grid_row("4", "160-19-000010", "Asphalting of roads in Jayanagar 4th block"),             # out of scope
          grid_row("5", "186-23-000002", "Footpath works in Koramanagala 6th block")]               # misspelt name
    w2 = [grid_row("1", "151-17-000082", "Improvements to Burial ground in Jakkasandra Ext and Koramangala in ward no 151",
                   rtgs="Rtgs - 000099 / 02-Jul-2020"),                                              # same bill, 2nd release
          grid_row("6", "151-17-000082", "Improvements to Burial ground in Jakkasandra Ext and Koramangala in ward no 151",
                   amount="500000", br="000120")]                                                    # same job, new bill
    (tmp_path / "2020-04-01_2020-06-30.json").write_text(json.dumps({"from": "2020-04-01", "to": "2020-06-30", "url": URL, "rows": w1}))
    (tmp_path / "2020-07-01_2020-09-30.json").write_text(json.dumps({"from": "2020-07-01", "to": "2020-09-30", "url": URL, "rows": w2}))
    return tmp_path


# ---------------------------------------------------------------- discovery

def test_quarters_cover_range_without_gaps_or_overlap():
    qs = list(D.quarters(date(2015, 4, 1), date(2016, 5, 15)))
    assert qs[0][0] == date(2015, 4, 1) and qs[-1][1] == date(2016, 5, 15)
    for (a1, b1), (a2, b2) in zip(qs, qs[1:]):
        assert (a2 - b1).days == 1          # contiguous: every day in exactly one window
    assert len(qs) == 5


def test_parse_grid_row():
    r = D.parse_row(grid_row("183920", "151-17-000082", "Improvements to Burial ground in Koramangala"), URL)
    assert r["bill_id"] == "183920" and r["job_number"] == "151-17-000082"
    assert r["description"] == "Improvements to Burial ground in Koramangala"
    assert (r["br_no"], r["br_date"]) == ("000094", "2018-08-09")
    assert (r["rtgs_no"], r["rtgs_date"]) == ("000021", "2020-05-21")
    assert r["gross"] == 1444028 and r["ward_prefix"] == "151" and r["job_year"] == "17"


def test_phone_numbers_stripped_before_caching():
    rows = D.sanitize([{"contractor": "C Pushparaj<br/>9000000003"}, {"contractor": "ABC Constructions 919000000005"}])
    assert rows[0]["contractor"] == "C Pushparaj" and rows[1]["contractor"] == "ABC Constructions"


def test_ward_map_by_job_year():
    assert D.regime_for("151", "22") == "198"
    assert D.regime_for("186", "23") == "243"
    assert D.regime_for("174", "25") == "225"
    assert D.regime_for("304", "17") == "dept"


def test_ward_and_text_filtering(grid):
    rows = {r["bill_id"]: r for r in D.load()}
    assert rows["1"]["scope_basis"] == "description"
    assert rows["2"]["scope_basis"] == "ward_prefix"                     # 151 under the 198-ward map
    assert rows["3"]["scope_reason"] is None and rows["3"]["scope_basis"] == "ward_conflict"   # names Jaraganahalli
    assert rows["4"]["scope_reason"] is None and rows["4"]["scope_basis"] is None
    assert rows["5"]["scope_basis"] == "description"                     # "Koramanagala" spelling


def test_duplicate_bills_across_windows_collapse(grid):
    k = {r["bill_id"]: r for r in D.koramangala()}
    assert set(k) == {"1", "2", "5", "6"}
    assert sorted(k["1"]["payment_dates"]) == ["2020-05-21", "2020-07-02"]   # one bill, two releases


def test_duplicate_job_discovery_counts_job_once(grid):
    k = D.koramangala()
    jobs = [r["job_number"] for r in k]
    assert jobs.count("151-17-000082") == 2 and len(set(jobs)) == 3


def test_grid_bill_and_export_bill_dedupe_on_ifms_id(grid):
    src = {"id": "IFMS-GRID", "kind": "ifms_grid", "name": "grid", "url": URL}
    grid_bills = D.as_bills(src)
    export = dict(grid_bills[0], source={"id": "OC", "kind": "opencity", "name": "x", "url": "u"})
    out = B.dedupe_bills(grid_bills + [export])
    assert len(out) == len(grid_bills)


# ---------------------------------------------------------------- normalisation

@pytest.mark.parametrize("raw,unit", [("Sqm", "sqm"), ("Sq.m", "sqm"), ("m²", "sqm"), ("M2", "sqm"), ("Cum.", "cum"),
                                      ("cu.m", "cum"), ("m³", "cum"), ("RMT", "m"), ("Rm", "m"), ("metre", "m"),
                                      ("Running metre", "m"), ("Kms", "km"), ("Nos", "nos"), ("No.", "nos"), ("each", "nos"),
                                      ("L.S", "ls"), ("Kg", "kg"), ("banana", None), ("", None)])
def test_unit_normalisation(raw, unit):
    assert Q.norm_unit(raw) == unit


def test_unit_conversion():
    assert Q.convert(1.2, "km", "m") == pytest.approx(1200)
    assert Q.convert(500, "m", "km") == pytest.approx(0.5)
    assert Q.convert(10, "sqm", "sqm") == 10
    assert Q.convert(10, "sqm", "cum") is None                       # incompatible units never convert


def test_amount_normalisation():
    assert Q.num("1,23,456.78") == pytest.approx(123456.78)        # Indian grouping
    assert Q.num("123,456.78") == pytest.approx(123456.78)
    assert Q.num("78348,6418") == pytest.approx(78348.6418)        # OCR decimal comma
    assert Q.num("2832.00") == 2832.0 and Q.num("x") is None


# ---------------------------------------------------------------- BOQ / abstract parsing

BOQ = """SCHEDULE - B
Name of Work: Improvement and Asphalting to 7th cross from 100ft road to 80 ft road.
1 Removing BS Slab of drain and Stacking Sqm 2832.00 50.11 Fifty and Eleven 141911.52
2 Refixing Stone Slabs of drains and Pointing in C.M(1:3) Sqm 2548.80 | 70.04 Seventy 178517.95 |
KSRRB 300-1. Earthwork excavation for desilting of drains
3 upto 0.5 m, excavated surface leveled and sides neatly Cum. 805.20 | 189.22 Eighty Nine 152359.94 |
4 a row the scanner mangled Cum 193.0 — 151987.50
"""


def test_boq_parsing_accepts_only_arithmetic_rows():
    items, stats = Q.parse_items([{"page": 1, "method": "OCR", "text": BOQ}], "Schedule B")
    got = [(i["unit"], i["quantity"], i["rate"], i["amount"]) for i in items]
    assert ("sqm", 2832.0, 50.11, 141911.52) in got
    assert ("sqm", 2548.8, 70.04, 178517.95) in got
    assert ("cum", 805.2, 189.22, 152359.94) in got
    assert len(items) == 3                                         # the mangled row (no valid triple) is not read
    assert all(i["cross_validated"] and i["check"] == "arithmetic" for i in items)
    assert next(i for i in items if i["quantity"] == 805.2)["code"] == "KSRRB 300-1"
    assert stats[0]["kind"] == "schedule_b" and Q.readable(stats)


def test_decimal_restored_only_on_exact_digit_match():
    q, r, a, how = Q.find_triple(["5838.89", "17.93", "1046912977"])
    assert how == "decimal restored" and a == pytest.approx(104691.2977)
    assert Q.find_triple(["5838.89", "17.93", "1046912000"]) is None   # digits differ -> not read


def test_trivial_products_rejected():
    assert Q.find_triple(["1", "870", "870"]) is None              # MB chainage column noise
    assert Q.find_triple(["2", "3", "6"]) is None
    assert Q.find_triple(["57.00", "9370.17", "534099.69"])[3] == "arithmetic"


def test_confidence_levels():
    text = ("Description of the Item Unit Qty Rate Amount\n"
            "KSRRB M500-19 Bituminous concrete Cum 233.56 11722.59 2737928.12\n"
            "KSRRB 500-7 tack coat including cleaning 5838.89 17.93 1046912977\n")
    items, _ = Q.parse_items([{"page": 1, "method": "OCR", "text": text}], "M.B.")
    by_code = {i["code"]: i for i in items}
    assert by_code["KSRRB M500-19"]["confidence"] == "high"        # arithmetic + unit
    assert by_code["KSRRB 500-7"]["confidence"] == "medium"        # decimal restored, no unit
    assert by_code["KSRRB 500-7"]["page_kind"] == "abstract"


# ---------------------------------------------------------------- MB parsing

MB_TYPED = """BRUHAT BANGALORE MAHANAGARA PALIKE
MEASUREMENT SHEET
KSRRB 500-7. Providing and applying tack coat on the prepared black topped surfaces
Chainage Breadth Average Quantity
0 30 1 30 9.25 9.70 9.48 284.25
Quantity Paid In First Bill = 5070.10 Sqm
Quantity Paid In This Bill = 5838.89 Sqm
Total Quantity 10908.99 Sqm
"""
MB_HANDWRITTEN = """Measurement Book No. B 165749
Dimensions Reference Number Length Breadth Depth Contents
Stasw PLY Sean ES | aad reel ee |RSS Mp YSWs MAT sh LEY a S6GD 1 2 3 4 5 6 7
"""


def test_mb_totals_parsed_and_self_checked():
    pages = [{"page": 4, "method": "OCR", "text": MB_TYPED}]
    items, stats = Q.parse_items(pages, "M.B.")
    assert items == [] and stats[0]["kind"] == "measurement"     # dimension rows are not priced rows
    t = Q.parse_totals(pages)[0]
    assert (t["previous"], t["this_bill"], t["total"], t["unit"]) == (5070.10, 5838.89, 10908.99, "sqm")
    assert t["consistent"] is True and t["code"] == "KSRRB 500-7"


def test_handwritten_mb_stays_unreadable():
    pages = [{"page": 1, "method": "OCR", "text": MB_HANDWRITTEN}]
    items, stats = Q.parse_items(pages, "M.B.")
    assert items == [] and not Q.readable(stats) and Q.parse_totals(pages) == []


# ---------------------------------------------------------------- matching, comparison, signals

def test_matching_by_code_rate_and_unit():
    plan = {"code": "KSRRB 500-7", "unit": "sqm", "rate": 17.93, "description": "Providing and applying tack coat on black topped surface"}
    assert Q.match_score(plan, {"code": "KSRRB 500-7", "unit": "sqm", "rate": 18.5, "description": "x"}) == 3
    assert Q.match_score(plan, {"code": None, "unit": "sqm", "rate": 17.93,
                                "description": "applying tack coat on the prepared black topped surfaces"}) == 2
    assert Q.match_score(plan, {"code": "KSRRB 500-7", "unit": "cum", "rate": 17.93, "description": "x"}) == 0   # unit
    assert Q.match_score(plan, {"code": "KSRRB 500-7", "unit": "sqm", "rate": 40.0, "description": "x"}) == 0    # rate band


def test_same_rate_alone_is_not_enough():
    plan = {"code": None, "unit": "cum", "rate": 250.0, "description": "Earthwork excavation in ordinary soil"}
    other = {"code": None, "unit": "cum", "rate": 250.0, "description": "Earthwork excavation in ordinary soil for drains"}
    assert Q.match_score(plan, other, rate_unique=True) == 2
    assert Q.match_score(plan, other, rate_unique=False) >= 1      # shared rate: falls back to description similarity
    assert Q.match_score(plan, dict(other, description="Supplying sand filling"), rate_unique=True) == 0


def test_swapped_quantity_and_rate_are_reoriented():
    plan = {"code": None, "unit": "cum", "rate": 6843.0, "description": "M20 concrete"}
    row = {"quantity": 6843.0, "rate": 32.77, "unit": "cum", "description": "M20 concrete"}
    fixed = Q.orient(plan, row)
    assert fixed["quantity"] == 32.77 and fixed["rate"] == 6843.0 and fixed["swapped"]
    assert Q.orient(plan, {"quantity": 40.0, "rate": 6843.0})["quantity"] == 40.0


def work_item(est, got, basis="bill abstracts", amount=5e5, unit="sqm"):
    prov = {"document_id": "WO-4--1-SB.pdf", "attachment_type": "Schedule B", "work_bill_id": "1",
            "attachment_url": "https://accounts.bbmp.gov.in/vssIFMS/Files/WO-4--1-SB.pdf", "listing_url": "L", "page": 2,
            "page_kind": "schedule_b", "raw": "Sqm 100.00 50.00 5000.00", "method": "OCR", "check": "arithmetic",
            "confidence": "high", "cross_validated": True}
    return {"description": "Bituminous concrete 40mm", "normalized_category": "Bituminous surfacing", "code": "KSRRB M500-19",
            "unit": unit, "estimated_quantity": est, "estimated_rate": amount / est, "estimated_amount": amount,
            "plan_basis": "Schedule B", "billed_quantity": got, "measured_quantity": None, "billed_amount": None,
            "billed_rate": None, "bills": [{"wbid": "1", "quantity": got, "match": "code"}] if got is not None else [],
            "compared_quantity": got, "compared_basis": basis if got is not None else None,
            "variance_pct": round((got - est) / est * 100, 1) if got is not None else None, "provenance": [prov]}


def result(items, final=True, all_read=True, variation=(), supp=0, unmatched=(), plan_value=None):
    return {"job": "151-20-000096", "plan_basis": "Schedule B (contract BOQ)", "plan_items": len(items),
            "supplementary_items": supp, "plan_value_parsed": plan_value or sum(w["estimated_amount"] for w in items),
            "bills": [{"wbid": "1", "scope": "this bill"}], "all_paid_bills_read": all_read, "has_final_bill": final,
            "work_items": items, "billed_without_plan_item": list(unmatched), "measurement_totals": [],
            "documents": [], "readable_bill_documents": True, "variation_documents": list(variation)}


def codes(sigs):
    return sorted(s["code"] for s in sigs)


def test_below_plan_threshold_and_completeness():
    assert codes(Q.signals_for(result([work_item(100, 74)]))) == ["qty_measured_below_plan"]
    assert codes(Q.signals_for(result([work_item(100, 76)]))) == []                  # inside the 25 % band
    assert codes(Q.signals_for(result([work_item(100, 50)], final=False))) == []     # running bill: not yet complete
    assert codes(Q.signals_for(result([work_item(100, 50)], all_read=False))) == []  # a bill we could not read


def test_above_plan_and_variation_visibility():
    assert codes(Q.signals_for(result([work_item(100, 126)]))) == ["qty_above_plan_no_variation", "qty_billed_above_plan"]
    assert codes(Q.signals_for(result([work_item(100, 126)], variation=["WO-5--1-Supplementary Schedule B.pdf"]))) == ["qty_billed_above_plan"]
    assert codes(Q.signals_for(result([work_item(100, 124)]))) == []


def test_above_plan_needs_code_or_rate_matches():
    w = work_item(100, 200)
    w["bills"] = [{"wbid": "1", "quantity": 120, "match": "description"}, {"wbid": "1", "quantity": 80, "match": "rate"}]
    assert Q.signals_for(result([w])) == []


def test_bill_form_total_up_to_date_column():
    toks = Q.NUM_TOKEN.findall("Present 30.00 50.00 1500.00 | Total up to date 40.00 2000.00")
    q, r, a, _ = Q.find_triple(toks)
    assert (q, r, a) == (30.0, 50.0, 1500.0) and Q.up_to_date(toks, r, a) == 40.0
    assert Q.up_to_date(Q.NUM_TOKEN.findall("100.00 50.00 5000.00"), 50.0, 5000.0) is None


def test_identical_to_plan_needs_three_items():
    three = [work_item(100 + i, 100 + i) for i in range(3)]
    assert codes(Q.signals_for(result(three))) == ["qty_identical_to_plan"]
    assert Q.signals_for(result(three))[0]["severity"] == "info"
    assert Q.signals_for(result(three[:2])) == []


def test_small_items_never_signal():
    assert Q.signals_for(result([work_item(100, 10, amount=50_000)])) == []


def test_major_item_not_billed_needs_complete_bills():
    items = [work_item(100, None, amount=8e5), work_item(50, 50, amount=2e5)]
    assert codes(Q.signals_for(result(items))) == ["qty_major_item_not_billed"]
    assert codes(Q.signals_for(result(items, all_read=False))) == []


def test_billed_item_not_in_plan_needs_plan_coverage():
    u = {"description": "Extra item: kerb painting", "quantity": 900, "unit": "m", "rate": 150.0, "amount": 135000.0,
         "wbid": "1", "provenance": [work_item(1, 1)["provenance"][0]]}
    r = result([work_item(100, 100, amount=9e5)], unmatched=[u])
    assert "qty_billed_item_not_in_plan" in codes(Q.signals_for(r, contract=1e6))     # plan read to 90 %
    assert "qty_billed_item_not_in_plan" not in codes(Q.signals_for(r, contract=5e6))  # plan read to 18 %: absence means nothing


def test_missing_source_gives_no_signal_and_no_fact():
    assert Q.signals_for(result([work_item(100, None)], final=False)) == []
    p = {"job_numbers": ["999-99-000001"], "facts": {}, "signals": []}
    build.add_quantities(p, None)
    assert p["facts"] == {} and p["signals"] == []
    build.add_quantities(p, {"projects": [], "mb_presence": {}})
    assert "quantity_check" not in p["facts"]


def test_signals_never_use_fraud_language():
    sigs = Q.signals_for(result([work_item(100, 10), work_item(100, 200)]), paid=5e7)
    text = " ".join(s["title"] + s["detail"] for s in sigs).lower()
    assert sigs and not any(w in text for w in ("fraud", "corrupt", "scam", "theft", "suspicious"))
    assert all(s["confidence"] == "inferred" for s in sigs)


def test_final_bill_without_mb_signal():
    p = {"job_numbers": ["151-20-000001"], "facts": {}, "signals": []}
    qty = {"projects": [], "mb_presence": {"151-20-000001": {"final_bills": ["777"], "mb_files": 1, "mb_real": 0,
                                                           "mb_placeholders": ["WB-MB--1-Not Applicable.PDF"]}}}
    build.add_quantities(p, qty)
    assert codes(p["signals"]) == ["final_bill_without_mb"]
    assert "pMainID=777" in p["signals"][0]["evidence"][0]["url"]


# ---------------------------------------------------------------- provenance

def test_evidence_carries_url_page_method_confidence():
    ev = Q._ev(work_item(100, 100)["provenance"][0])
    assert ev["url"].startswith("https://accounts.bbmp.gov.in/vssIFMS/Files/")
    assert "p.2" in ev["label"] and "arithmetic" in ev["note"] and "confidence high" in ev["note"]
    assert "Sqm 100.00 50.00 5000.00" in ev["note"]


def test_quantity_fact_is_inferred_and_does_not_touch_amounts():
    p = {"job_numbers": ["151-20-000096"], "signals": [],
         "facts": {"payments_gross": {"value": 1e7, "confidence": "confirmed"}}}
    qty = {"projects": [dict(result([work_item(100, 90)]), signals=[])], "mb_presence": {}}
    build.add_quantities(p, qty)
    f = p["facts"]["quantity_check"]
    assert f["confidence"] == "inferred" and f["stage"] == "work" and "1 of 1 planned items compared" in f["value"]
    assert p["facts"]["payments_gross"] == {"value": 1e7, "confidence": "confirmed"}   # direct source untouched
    assert f["evidence"] and all(e["url"] for e in f["evidence"])


DB = build.ROOT / "data" / "koramangala.db"


@pytest.mark.skipif(not DB.exists(), reason="database not built")
def test_db_work_items_have_full_provenance():
    con = sqlite3.connect(DB)
    if not con.execute("SELECT name FROM sqlite_master WHERE name='work_items'").fetchone():
        pytest.skip("built before Phase 5")
    rows = con.execute("SELECT provenance, cross_validated, min_confidence FROM work_items").fetchall()
    for prov, xv, conf in rows:
        prov = json.loads(prov)
        assert prov and xv == 1 and conf in ("high", "medium")
        for x in prov:
            assert x["attachment_url"].startswith("https://accounts.bbmp.gov.in/") and x["page"] and x["method"]
    con.close()


# ---------------------------------------------------------------- extension of time (Phase 4 signal re-evaluation)

from ingest import eot as EOT, signals as S  # noqa: E402


def test_eot_parse_dates_days_fine():
    t = ("With reference to above subject, the extension of time was verified. The extension of time is accorded\n"
         "| from 31/12/2020 to 31/12/2021 (365 days) with a nominal fine of Rs 1.09 crore considering the delay")
    (o,) = EOT.parse(t)
    assert (o["from"], o["to"], o["days"]) == ("2020-12-31", "2021-12-31", 365)
    assert o["fine"] == pytest.approx(1.09e7)
    assert EOT.parse("extension of time is accorded from 31/02/2020 to 99/99/2021") == []   # invalid dates never read


def _proj(due, ext=None, last="2025-07-01"):
    facts = {"completion_due": {"value": due, "confidence": "inferred", "evidence": []}}
    if ext:
        facts["extended_completion"] = {"value": ext, "confidence": "inferred", "evidence": []}
    return {"facts": facts, "bills": [{"br_date": last, "payment_date": last, "gross": 1e6}], "tenders": [], "signals": []}


def test_eot_later_than_due_resolves_time_signal():
    assert any(s["code"] == "past_stipulated_completion" for s in S.project_signals(_proj("2021-01-01")))
    assert not any(s["code"] == "past_stipulated_completion" for s in S.project_signals(_proj("2021-01-01", ext="2026-12-31")))


def test_eot_for_earlier_period_keeps_signal_with_note():
    sig = next(s for s in S.project_signals(_proj("2025-02-14", ext="2021-12-31")) if s["code"] == "past_stipulated_completion")
    assert "earlier period" in sig["detail"] and "2021-12-31" in sig["detail"]
