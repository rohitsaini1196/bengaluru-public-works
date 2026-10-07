"""Phase 6: investigation cases — work-slip parsing, variation reconciliation, signal removal,
scoring, confidence, rendering, traceability and RTI generation."""
import json
import re

import pytest

from ingest import case_evidence as CE, cases as C, quantities as Q

URL = "https://accounts.bbmp.gov.in/vssIFMS/Files/WB-Ot--1-WS.pdf"


# ---------------------------------------------------------------- work slip parsing

def test_work_slip_row_with_savings_check():
    r = CE.parse_work_slip_row("Sqm | 5354.95 157.30 842333.64 730.20 730.20 157.30 114860.46 114860.46 727473.18")
    assert (r["tender_qty"], r["rate"], r["executed_qty"], r["savings"]) == (5354.95, 157.3, 730.2, 727473.18)
    assert r["confidence"] == "high" and "tender - actual = savings" in r["checks"]


def test_work_slip_takes_total_executed_not_partial_columns():
    # tender | up to 125 % | above 125 % | TOTAL EXECUTED | excess
    r = CE.parse_work_slip_row("each 2,00 235000,00 470000.00 3.00 235000.00 705000.00 2.00 235000.00 470000.00 "
                               "5.00 1175000.00 3.00 705000.00")
    assert (r["tender_qty"], r["executed_qty"], r["excess"]) == (2.0, 5.0, 705000.0)
    assert r["confidence"] == "high"


def test_work_slip_rejects_rows_without_arithmetic_and_implausible_rows():
    assert CE.parse_work_slip_row("Rupees two lakh only 12 34 56") is None
    assert CE.parse_work_slip_row("each 3.00 258000.00 774000.00 300.00 258000.00 77400000.00") is None   # 100x tender


def test_decimal_comma_is_not_thousands_grouping():
    assert Q.num("2,00") == 2.0 and Q.num("1,23,456.78") == 123456.78 and Q.num("12,345") == 12345.0


def test_work_slip_header_totals():
    rows, hdr = CE.parse_work_slip([{"page": 12, "method": "OCR", "text":
                                     "Agreement Amount = 138213434.00\nWorkslip Cost (Excess) [= __|28755136.98\n"
                                     "Sanctioned Estimate Amount: Rs. 10,00,000.00\nTender Premium: Above 4.99%"}])
    assert hdr["agreement_amount"]["value"] == 138213434.0 and hdr["work_slip_excess"]["value"] == 28755136.98
    assert hdr["sanctioned_estimate"]["value"] == 1000000.0 and hdr["tender_premium"]["value"] == "Above 4.99%"


# ---------------------------------------------------------------- variation reconciliation

def ws_row(executed, confidence="high", rate=235000.0, unit="nos"):
    return {"tender_qty": 2.0, "rate": rate, "executed_qty": executed, "confidence": confidence, "unit": unit,
            "checks": ["a", "b", "c"], "document_id": "WS.pdf", "url": URL, "page": 11, "raw": "row", "method": "OCR"}


@pytest.mark.parametrize("rows,expected", [
    ([ws_row(5.0)], "explained"),                       # statement covers the billed 4
    ([ws_row(3.0)], "partially_explained"),             # raises the plan, not to the billed level
    ([ws_row(2.0)], "contradicted"),                    # fully checked row records no increase
    ([ws_row(2.0, confidence="medium")], None),         # a medium row may not contradict a bill
    ([], None),
])
def test_reconcile(rows, expected):
    out = C.reconcile(2.0, 4.0, rows)
    assert (out[0] if out else None) == expected


def work_item(plan=2.0, billed=4.0, rate=235000.0, unit="nos", basis="bill form total up to date", match="rate"):
    prov = {"document_id": "SB.pdf", "attachment_type": "Schedule B", "attachment_url": URL.replace("WS", "SB"), "page": 9,
            "raw": "each 2 235000.00 470000.00", "method": "OCR", "confidence": "high", "cross_validated": True}
    return {"description": "Self-weighted Rower (Rowing Machine)", "unit": unit, "estimated_quantity": plan, "estimated_rate": rate,
            "estimated_amount": plan * rate, "compared_quantity": billed, "compared_basis": basis, "from_final_bill": False,
            "bills": [{"wbid": "655880", "quantity": billed, "match": match}], "provenance": [prov]}


def docs(rows=(), var_docs=()):
    return {"work_slip_rows": list(rows), "variation_documents": list(var_docs), "work_slip_header": {}, "final_bill_rows": [],
            "other_documents": [], "final_wbids": []}


def test_explained_signal_is_removed_from_live_issues():
    (i,) = C.issue_quantity_above_plan({}, {"work_items": [work_item()]}, docs([ws_row(5.0)], [{"document_id": "WS.pdf", "status": "read", "rows": 1, "url": URL}]))
    assert i["status"] == "explained" and i["work_slip_executed_qty"] == 5.0
    assert "covered by a variation document" in C.explained_why(i)


def test_no_variation_document_is_unexplained_in_public_record_not_unapproved():
    (i,) = C.issue_quantity_above_plan({}, {"work_items": [work_item()]}, docs())
    assert i["status"] == "unexplained_in_public_record"
    text = C.issue_text(i)
    assert "No supplementary Schedule B, work slip, deviation statement or revised estimate explaining this quantity was found in the public records examined." in text
    assert not re.search(r"fraud|corrupt|scam|fake|unapproved", text, re.I)


def test_unreadable_variation_document_gives_insufficient_evidence():
    (i,) = C.issue_quantity_above_plan({}, {"work_items": [work_item()]}, docs([], [{"document_id": "WS.pdf", "status": "read", "rows": 0, "url": URL}]))
    assert i["status"] == "insufficient_evidence"


def test_description_only_matches_and_small_items_are_not_cases():
    assert C.issue_quantity_above_plan({}, {"work_items": [work_item(match="description")]}, docs()) == []
    assert C.issue_quantity_above_plan({}, {"work_items": [work_item(rate=1000.0)]}, docs()) == []   # ₹2,000 item
    assert C.issue_quantity_above_plan({}, None, docs()) == []                                        # missing evidence


def test_rate_matching_respects_units():
    rows = [ws_row(5.0, unit="nos"), ws_row(5.0, unit="cum")]
    assert len(C.match_rate(235000.0, rows, unit="nos")) == 1


def test_duplicate_evidence_rows_are_deduplicated():
    a = ws_row(5.0)
    assert len(C.dedupe_rows([a, dict(a, document_id="WS-copy.pdf"), ws_row(3.0)])) == 2


# ---------------------------------------------------------------- final bill quantity selection

def test_final_quantity_uses_total_up_to_date_column():
    items, _ = Q.parse_items([{"page": 3, "method": "OCR", "text":
                               "Description of the Item Unit Qty Rate Amount\nKSRRB 500-7 tack coat Sqm 10.00 500.00 50.00 50.00 2500.00 60.00 3000.00"}], "Bill Form")
    (it,) = items
    assert it["to_date_quantity"] == 60.0       # Previous 10 | Present 50 x 50 = 2500 | Up to date 60 = 3000


# ---------------------------------------------------------------- scoring and confidence

def test_score_components_are_transparent_and_bounded():
    issue = {"status": "unexplained_in_public_record", "confidence": "high", "value_at_issue": 1e8, "documentation_gap": 10, "timeline_days": 900}
    comp, total = C.score([issue], n_sources=4)
    assert set(comp) == set(C.WEIGHTS) and total == round(sum(comp.values()), 1)
    assert all(0 <= comp[k] <= C.WEIGHTS[k] for k in comp) and total <= 100
    explained = dict(issue, status="explained")
    assert C.score([explained], 4)[1] < total


def test_materiality_scale():
    assert C.materiality(1e5) == 0 and C.materiality(1e8) == 25 and 0 < C.materiality(1e6) < C.materiality(1e7) < 25


@pytest.mark.parametrize("sources,label", [
    ([{"kind": "api"}, {"kind": "document"}], "high"),
    ([{"kind": "document", "cross_validated": True}, {"kind": "bill_form", "cross_validated": True}], "high"),
    ([{"kind": "document", "cross_validated": True}], "medium"),
    ([{"kind": "derived"}], "low"),
])
def test_confidence_classification(sources, label):
    assert C.classify_confidence(sources) == label


# ---------------------------------------------------------------- rendering, traceability, RTI

def sample_case(status="unexplained_in_public_record"):
    i = C.issue_quantity_above_plan({}, {"work_items": [work_item()]}, docs())[0]
    i["status"] = status
    c = {"case_id": "KP6-186-23-000001", "job_number": "186-23-000001", "title": "Outdoor gym", "slug": "x", "status": "needs RTI",
         "confidence": "high", "case_score": 61.0, "primary_issue": i["type"], "issues": [i], "resolved_issues": [],
         "project_summary": {"category": "Parks", "location": "Koramangala", "ward": "186", "contractor": "ABC", "contractor_confidence": "confirmed",
                             "agency": "BBMP", "office": None},
         "contract": {"contract_value": 1.38e8, "contract_value_confidence": "inferred", "estimated_cost": None, "work_order_ref": "WO/1",
                      "work_order_date": "2023-05-01", "commencement_date": None, "stipulated_completion": "2024-05-01", "work_slip_header": {}},
         "execution": {"final_bill": None, "status": "Running", "completion_certificate": None, "time_extensions": None, "quantity_check": None, "site_photos": None},
         "payments": {"gross_paid": 1.56e8, "net_paid": 1.4e8, "n_bills": 5, "first_payment": "2023-08-01", "last_payment": "2025-01-01",
                      "pending_bills": None, "deductions": None},
         "source_links": {"project_page": "/p/x", "ifms_bills": ["655880"]}}
    c.update(explanations=C.benign(i, c), resolves=C.resolvers(i), questions=C.questions(i, c), rti=C.rti(i, c),
             score_components=C.score([i], 2)[0])
    return c


def test_render_has_required_sections_and_preserves_source_links():
    md = C.render(sample_case())
    for h in ("## Project", "## What was promised", "## What happened", "## Why this case deserves follow-up",
              "## Possible legitimate explanations", "## What would resolve it", "## Questions an investigator could ask", "## Evidence"):
        assert h in md
    assert URL.replace("WS", "SB") in md and "p.9" in md
    assert not re.search(r"\b(fraud|corruption|scam|fake)\b", md, re.I)


def test_every_number_in_why_section_comes_from_case_data():
    c = sample_case()
    i = c["issues"][0]
    text = C.issue_text(i)
    assert "2" in text and "4" in text and C.inr(i["value_at_issue"]) in text


def test_questions_and_rti_are_specific():
    c = sample_case()
    assert 3 <= len(c["questions"]) <= 8
    assert all("186-23-000001" == r["job"] for r in c["rti"]) and all(r["why"] and r["record"] for r in c["rti"])
    assert any("235,000.00" in r["detail"] for r in c["rti"])


def test_no_rti_for_explained_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C, "CASES_DIR", tmp_path / "cases")
    c = sample_case(status="explained")
    c["retained"], c["rank"] = True, 1
    data = {"as_of": "2026-10-07", "reviewed": 1, "retained": 1, "cases": [c]}
    C.write_markdown(data)
    assert data["rti_count"] == 0
    assert (tmp_path / "cases" / "KP6-186-23-000001.md").exists() and (tmp_path / "CASE_INDEX.md").exists()


DATA = C.OUT


@pytest.mark.skipif(not DATA.exists(), reason="cases not built")
def test_built_cases_are_traceable():
    d = json.loads(DATA.read_text())
    assert d["reviewed"] >= d["retained"] >= 1
    for c in d["cases"]:
        if not c.get("retained"):
            continue
        assert c["confidence"] in ("high", "medium") and c["why_selected"]
        for i in c["issues"]:
            assert i["evidence"], (c["case_id"], i["type"])
            assert all(e.get("url") or e.get("label") for e in i["evidence"])
        assert set(c["score_components"]) == set(C.WEIGHTS)


# ---------------------------------------------------------------- signals killed in Phase 6

def _proj(paid=7.49e7, signals=(), facts=None):
    f = {"payments_gross": {"value": paid, "confidence": "confirmed", "evidence": []}}
    f.update(facts or {})
    return {"id": 1, "job_numbers": "151-16-000019", "facts": f, "signals": list(signals)}


def test_placeholder_document_reuse_is_an_artefact():
    sig = {"code": "document_reused", "related": ["152-20-000049"], "detail": "same file",
           "evidence": [{"url": "https://accounts.bbmp.gov.in/vssIFMS/Files/WB-CC--21141644-NOT%20APPLICABLE.pdf"}]}
    (i,) = C.issue_document_reuse(_proj(signals=[sig]), {}, None, docs())
    assert i["status"] == "artefact" and "placeholder" in i["artefact_reason"]


def test_package_contract_explains_document_reuse(monkeypatch):
    url = "https://accounts.bbmp.gov.in/vssIFMS/Files/WO-6--25649455-Agre%2001.jpg"
    sig = {"code": "document_reused", "related": ["151-16-000025"], "detail": "same agreement", "evidence": [{"url": url}]}
    monkeypatch.setattr(C, "work_order_values", lambda d, kinds=("work order",): [
        {"value": 75039723.0, "document_id": "WO-6--25649455-Agre 01.jpg", "url": url.replace("%20", " "), "page": 1,
         "raw": "contract price of Rs. 7,50,39,723", "method": "OCR"}])

    class Con:
        row_factory = None

        def execute(self, *a):
            class R:
                def fetchone(self):
                    return [1.69e7]
            return R()
    (i,) = C.issue_document_reuse(_proj(paid=5.79e7, signals=[sig]), {}, Con(), docs())
    assert i["status"] == "explained" and i["contract_price"] == 75039723.0
    assert "one contract booked under several job codes" in C.explained_why(i)


def test_final_bill_without_mb_is_artefact_when_no_bill_level_files(monkeypatch):
    monkeypatch.setattr(C, "bill_level_files", lambda w: False)
    listing = {"701476": ("174-24-000004", {"billtype": "Second and Final"}, [])}
    qty = {"mb_presence": {"174-24-000004": {"final_bills": ["701476"], "mb_real": 0, "mb_files": 0, "mb_placeholders": []}}}
    (i,) = C.issue_final_bill_without_mb(_proj(), "174-24-000004", qty, listing)
    assert i["status"] == "artefact" and i["confidence"] == "low"
    monkeypatch.setattr(C, "bill_level_files", lambda w: True)
    (i,) = C.issue_final_bill_without_mb(_proj(), "174-24-000004", qty, listing)
    assert i["status"] == "unexplained_in_public_record"


def test_rti_asks_for_extension_order_when_billing_outlasts_contract():
    i = {"type": "paid_over_contract", "paid": 3.49e6, "contract_value": 1.54e6, "period_days": 240, "billing_span_days": 478,
         "first_payment": "2025-05-27", "last_payment": "2026-09-17"}
    reqs = C.rti(i, {"job_number": "174-25-000010"})
    assert reqs[0]["record"].startswith("Order extending or renewing") and "8-month" in reqs[0]["detail"]
