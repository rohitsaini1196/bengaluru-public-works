"""Unit tests for parsing, scope rules, de-duplication and linking."""
from ingest import build, match, parse, scope, signals, tenders, truth


# ---------------------------------------------------------------- parse helpers
def test_parse_date_formats():
    assert parse.parse_date("26-Apr-2022") == "2022-04-26"
    assert parse.parse_date("24/01/2011") == "2011-01-24"
    assert parse.parse_date("08-07-2026 10:56:36") == "2026-07-08"
    assert parse.parse_date("22-Jun-18") == "2018-06-22"
    assert parse.parse_date("") is None
    assert parse.parse_date("not a date") is None


def test_money():
    assert parse.money("1,23,456") == 123456
    assert parse.money(" 99.5 ") == 99.5
    assert parse.money("") is None and parse.money("abc") is None


def test_contractor_strips_code_and_phone():
    assert parse.clean_contractor("004595 SADIAN9845583321") == "SADIAN"
    assert parse.clean_contractor("SATHYACONSTRUCTIONS9341705164") == "SATHYACONSTRUCTIONS"
    assert parse.clean_contractor("M/s Simplex Infrastructures Limited") == "M/s Simplex Infrastructures Limited"
    assert parse.clean_contractor("  ") is None


def test_job_number_normalisation():
    assert parse.norm_job(" 151-22-000004 ") == "151-22-000004"
    assert parse.norm_job("R-307-11-000029") == "R-307-11-000029"
    assert parse.norm_job("garbage") is None


def test_br_and_rtgs_regex_handles_glued_text():
    s = "BR - 000096 / 17-Oct-2019CBR - / Rtgs - 000003 / 05-Apr-2022"
    assert parse.BR_RE.search(s).groups() == ("000096", "17-Oct-2019")
    assert parse.RTGS_RE.search(s).groups() == ("000003", "05-Apr-2022")
    assert parse.BR_RE.search("BR - / CBR - / Rtgs - 000001 / 01-Jul-2025") is None


# ---------------------------------------------------------------- scope
def test_scope_by_ward_per_regime():
    assert scope.scope_reason("Asphalting", "198", "151")
    assert scope.scope_reason("Asphalting", "225", "174")
    assert scope.scope_reason("Asphalting", "243", "186")
    assert not scope.scope_reason("Asphalting", "243", "151")  # 151 is a different ward on the 243 map
    assert scope.scope_reason("Asphalting", "198", None, "151-19-000072")


def test_scope_by_description_and_exclusions():
    assert scope.scope_reason("Improvements to drains at 7th block, Koramangala in ward No.147 Adugodi")
    assert not scope.scope_reason("Remodelling of primary drains in Koramangala Valley package 3")
    assert not scope.scope_reason("Rejuvenation of Koramangala (K-100) valley from K.R Market")
    assert not scope.scope_reason("Koramangala Valley and Challaghatta Valley ... Koramangala and Challagatta Valley")
    assert not scope.scope_reason("Toilet at school in Koramangala Grama Panchayath, Devanahalli Taluk")
    assert scope.scope_reason("Elevated corridor along 100ft. Inner Ring Road, Koramangala, Bangalore")


def test_classify():
    assert scope.classify("Arranging temporary illumination to Ganesha immersion at Koramangala") is None
    assert scope.classify("Providing Project Management Consultancy services for flyover") is None
    assert scope.classify("Renovation of toilets, modular kitchen, wardrobe in Qtrs No.403") is None
    assert scope.classify("Improvements to roads, drains and footpath in 8th block") == "Roads, drains & footpaths (combined)"
    assert scope.classify("Construction of RCC box drain in ward 186") == "Drains & storm-water"
    assert scope.classify("Filling of potholes in Koramangala Sub Division") == "Roads"
    assert scope.classify("Construction of Elevated corridor ... flyover") == "Junctions, flyovers & grade separators"


def test_extract_location():
    locs = scope.extract_location("works in Kuduremukha Colony, 1st Block, 6th Block of Koramangala, 80 feet road")
    assert "Kuduremukha Colony" in locs and "1st Block" in locs and "80 Feet Road" in locs


# ---------------------------------------------------------------- dedupe & linking
SRC_A = {"id": "S01", "kind": "bbmp_publicview", "name": "A", "url": "u1"}
SRC_B = {"id": "S02", "kind": "oc_wodetails", "name": "B", "url": "u2"}
SRC_C = {"id": "S03", "kind": "bbmp_dept", "name": "C", "url": "u3"}


def bill(src, **kw):
    return dict(source=src, job_number="151-20-000001", **kw)


def test_dedupe_same_bill_across_exports():
    bills = [
        bill(SRC_A, br_no="000039", br_date="2022-06-18", gross=100.0, nett=90.0),
        bill(SRC_B, br_no="39", br_date="2022-06-18", gross=100.0, nett=90.0, extra={"ifms_id": "1"}),
        bill(SRC_B, br_no=None, br_date=None, gross=50.0, nett=45.0, extra={"ifms_id": "1"}),  # same IFMS id
        bill(SRC_C, wo_no="WO-1", gross=100.0, nett=90.0),          # register copy of the BR bill
        bill(SRC_C, wo_no="WO-2", gross=70.0, deduction=7.0, nett=63.0),
        bill(SRC_C, wo_no="WO-2o", gross=70.0, deduction=7.0, nett=63.0),  # typo'd repeat
    ]
    out = build.dedupe_bills(bills)
    assert sorted(b["gross"] for b in out) == [70.0, 100.0]


def _bill(**kw):
    s = {"id": "S01", "kind": "bbmp_publicview", "name": "A", "url": "https://x/a.csv"}
    b = dict(source=s, job_number="151-20-000001", description="Improvements to drains in 6th block", gross=10.0, nett=9.0,
             source_row=1, scope_reason="x", regime="198", ward="151", dedupe_key="BR", category="Roads")
    b.update(kw)
    return b


def test_truth_status_and_confidence():
    run = [_bill(bill_type="Running")]
    p = truth.assemble(build.job_skeleton("151-20-000001", run), [], {})
    F = p["facts"]
    assert F["status"]["value"].startswith("Running bill only") and F["status"]["confidence"] == "inferred"
    assert F["completion_date"]["confidence"] == "unknown"
    assert F["contract_value"]["confidence"] == "unknown"  # never invented
    assert F["payments_gross"]["confidence"] == "confirmed"
    fin = run + [_bill(bill_type="Second and Final", end_date="2020-01-01", source_row=2, br_no="7", br_date="2020-02-01")]
    F = truth.assemble(build.job_skeleton("151-20-000001", fin), [], {})["facts"]
    assert F["status"]["value"] == "Completed (final bill)"
    assert F["completion_date"]["value"] == "2020-01-01" and F["completion_date"]["confidence"] == "confirmed"
    assert F["payments_gross"]["value"] == 20.0
    # totals that needed de-duplication by amount are only inferred
    F = truth.assemble(build.job_skeleton("151-20-000001", [_bill(dedupe_key="ROW")]), [], {})["facts"]
    assert F["payments_gross"]["confidence"] == "inferred"


TENDER = dict(source="KPPP", tender_number="BBMP/2019-20/OW/WORK_INDENT1", base="BBMP/2019-20/OW/WORK_INDENT1", call=1,
              description="Improvements to drains in 6th block Koramangala ward no 151", dept="BBMP", office="BBMP BTM South",
              published_date="2019-06-01", status="AWARDED", status_text="Awarded", ecv=100.0, provisional_amount=None,
              contract_value=95.0, negotiated_value=None, contractor="RAVI K( RAVI CONSTRUCTIONS )", awarded_date="2019-07-01",
              period_days=180, period_text="6 months", period_doc="kw.doc", period_url="https://t/doc", url="https://t/1",
              bid_url="https://t/b", category="Roads", scope_reason="x")


def test_linked_tender_facts_are_inferred_and_exact_tender_confirmed():
    job = build.job_skeleton("151-20-000001", [_bill(contractor="RAVI K", wo_date="2019-08-01", payment_date="2019-10-01")])
    ev = {"confidence": "high", "score": 0.9, "text": 0.95, "support": ["contractor matches"], "conflict": []}
    F = truth.assemble(job, [([TENDER], ev)], {})["facts"]
    assert F["contract_value"]["value"] == 95.0 and F["contract_value"]["confidence"] == "inferred" and F["contract_value"]["via"]
    assert F["expected_completion"]["value"] == "2020-01-28" and F["expected_completion"]["confidence"] == "inferred"
    tonly = truth.assemble(build.tender_skeleton([TENDER]), [([TENDER], None)], {})["facts"]
    assert tonly["contract_value"]["confidence"] == "confirmed" and tonly["status"]["value"] == "Tender awarded"


def test_matcher_accepts_true_link_and_rejects_wrong_kind_or_place():
    jobs = [build.job_skeleton("151-20-000001", [_bill(contractor="RAVI K", payment_date="2019-10-01", gross=96.0)]),
            build.job_skeleton("151-20-000002", [_bill(job_number="151-20-000002", description="Providing RO plant in 6th block",
                                                       payment_date="2019-10-01")]),
            build.job_skeleton("151-20-000003", [_bill(job_number="151-20-000003", description="Improvements to drains in 2nd block",
                                                       payment_date="2019-10-01")])]
    acc, pos = match.link(jobs, {("KPPP", TENDER["base"]): [TENDER]})
    (p, ev), = acc.values()
    assert p["job_numbers"] == ["151-20-000001"] and ev["confidence"] in ("high", "medium")
    ro = {"description": "Providing RO Plant in Koramangala Village in Ward No:151"}
    corpus = match.Corpus([ro["description"], "Improvements to CC roads and Drains in Koramangala village in ward 151"])
    j = build.job_skeleton("151-18-000088", [_bill(description="Improvements to CC roads and Drains in Koramangala village in ward 151",
                                                   payment_date="2019-10-01")])
    ev = match.score_pair(corpus, [dict(TENDER, **ro)], j, match.job_info(j))
    assert ev["confidence"] in (None, "possible") and any("different kind" in c for c in ev["conflict"])


def test_matcher_rejects_bills_before_tender():
    j = build.job_skeleton("151-17-000001", [_bill(payment_date="2017-01-01", br_date="2016-12-01", br_no="1")])
    corpus = match.Corpus([TENDER["description"]])
    ev = match.score_pair(corpus, [dict(TENDER, published_date="2024-06-01")], j, match.job_info(j))
    assert ev["confidence"] in (None, "possible")


def test_ward_numbers_and_places():
    assert {"147", "148", "151", "173"} <= match.ward_numbers("roads in ward No 147 Adugodi, 148 Ejipura, 151 Koramangala and 173 Jakkasandra in BTM")
    assert match.place_keys("Kudremukh colony, 2nd block, Koramangala") == match.place_keys("Kuduremukha Colony 2nd Block Koramangala")


def test_period_from_tender_text():
    txt = "Name of the Work Approximate value EMD Period of Completion 1st Call 5 Comprehensive development ... 100.00 2,00,000/- 180 days Note:"
    assert tenders.period_from_text(txt)[0] == 180
    assert tenders.period_from_text("Period of Completion: 6 (Six) Months")[0] == 180
    assert tenders.period_from_text("Defects liability period 12 months")[0] is None


def test_signals():
    job = build.job_skeleton("151-20-000001", [_bill(contractor="RAVI K", wo_date="2019-08-01", payment_date="2019-09-01", gross=150.0)])
    ev = {"confidence": "high", "score": 0.9, "text": 0.95, "support": ["x"], "conflict": []}
    p = truth.assemble(job, [([dict(TENDER, call=3, bidders=[{"name": "A", "amount": 95.0, "rank": "L1"}], comparative_url="https://c")], ev)], {})
    codes = {s["code"]: s for s in signals.project_signals(p)}
    assert codes["paid_over_contract"]["confidence"] == "inferred"   # rests on a matched contract value
    assert "recalled_tender" in codes and "single_bidder" in codes


def test_base_tender_collapses_recalls():
    assert build.base_tender("BWSSB/2024-25/WS/WORK_INDENT1397/CALL-2") == "BWSSB/2024-25/WS/WORK_INDENT1397"


def test_similarity_ignores_boilerplate():
    c = match.Corpus(["x"])
    a = "Maintenance of Roads, drains and footpath work in Ward No-151 Koramangala for the year 2023-24"
    assert match.text_similarity(c, a, a + ".")[0] > 0.95
    assert match.text_similarity(c, "Providing Modren Dust Bin in ward No: 151 (Koramangala)",
                                 "Providing and Fixing Missing Slabs For Drain in Ward no 151 koramangala")[0] < 0.6


# ---------------------------------------------------------------- Phase 3: contractor identity
from ingest import contractors, plans  # noqa: E402


def test_contractor_ids_never_keep_phone_in_name():
    assert parse.contractor_ids("024046 SRI VINAYAKA ELECTR9000000002") == ("024046", "9000000002")
    assert parse.contractor_ids("000234 KRIDL BHUSIRI ACCOU0000000000") == ("000234", None)
    assert parse.clean_contractor("024046 SRI VINAYAKA ELECTR9000000002") == "SRI VINAYAKA ELECTR"


def _b(name, code=None, phone=None):
    return {"contractor": name, "contractor_code": code, "_phone": phone, "gross": 1.0}


def test_resolve_joins_on_identifiers_and_guards_names():
    bills = [_b("SRI VINAYAKA ELECTR", "024046", "9000000002"), _b("M/s Sri Vinayaka Electricals", None, "9000000002"),
             _b("Sri Durga Enterprises", None, "9000000001"), _b("A K Enterprises", None, "9000000001"),
             _b("SREEDHARA.K.C", "022360")]
    tenders = [{"contractor": "K C SREEDHARA", "supplier_id": 1384, "bidders": [{"name": "K C SREEDHARA"}]},
               {"contractor": "UMESH KUMAR K( BALAJI CONSTRUCTIONS )", "supplier_id": 1, "bidders": []},
               {"contractor": "Annavari Prakash( SRI BALAJI CONSTRUCTIONS )", "supplier_id": 2, "bidders": []}]
    ents, m2e, amb = contractors.resolve(bills + [_b("BALAJI CONSTRUCTIONS")], tenders)
    e = lambda kind, n: m2e[(kind, n)]
    assert e("ifms", "SRI VINAYAKA ELECTR") == e("ifms", "M/s Sri Vinayaka Electricals")       # mobile + similar name
    assert e("ifms", "Sri Durga Enterprises") != e("ifms", "A K Enterprises")                   # same phone, different firm
    assert e("ifms", "SREEDHARA.K.C") == e("kppp", "K C SREEDHARA")                              # token-order name match
    assert e("ifms", "BALAJI CONSTRUCTIONS") not in (e("kppp", "UMESH KUMAR K( BALAJI CONSTRUCTIONS )"),
                                                     e("kppp", "Annavari Prakash( SRI BALAJI CONSTRUCTIONS )"))  # ambiguous firm
    sree = next(x for x in ents if x["id"] == e("ifms", "SREEDHARA.K.C"))
    assert sree["basis"] == "inferred" and "IFMS:022360" in sree["ids"] and "KPPP:1384" in sree["ids"]


def test_plans_parse_archived_documents():
    recs = plans.load_all()
    kinds = {r["kind"] for r in recs}
    assert {"sanction", "pow"} <= kinds
    s = next(r for r in recs if r["kind"] == "sanction" and r["job_number"] == "151-17-000001")
    assert s["ecv"] == 2400000.0 and "web.archive.org" in s["url"]
    assert all(r["ocr"] for r in recs if r["kind"] in ("notice", "grant"))


# ---------------------------------------------------------------- Phase 4: direct IFMS evidence
from ingest import direct, documents, fetch_bbmp_direct  # noqa: E402

WO_TEXT = """No: EE (PC-9)/WO/ 03/2023-24 Date: 15.11.2023
Ref: 1. This office Tender Notification No.EE/PC-9/TEND/01/2022-23 dated:28.02.2023.
3. Letter of Acceptance No. EE/PC-9/PR/41/2023-24 Dtd: 22.09.2023
4. Contract Agreement No: EE/PC-9/AGG/03/2023-24 Dtd:15.11.2023
for a tender Price of Rupees. 176,11,00,000/- (Rupees One Hundred and Seventy Six Crores)
4. Time of Completion 15 Months
5 Date of Commencement of Worle 15.11.2023
6. Date of Completion 14.02.2025
Lat 12.93412 Long 77.62245"""


def test_document_patterns(tmp_path, monkeypatch):
    f = tmp_path / "WO-7--1-WORK ORDER.pdf"
    f.write_bytes(b"%PDF-1.4")
    (tmp_path / (f.name + ".ocr.json")).write_text(__import__("json").dumps([WO_TEXT]))
    got = {x["field"]: x["value"] for x in documents.extract(f)}
    assert got["contract_value"] == 1761100000.0
    assert got["period"] == 450 and got["commencement_date"] == "2023-11-15" and got["date_of_completion"] == "2025-02-14"
    assert got["loa_ref"] == {"ref": "EE/PC-9/PR/41/2023-24", "date": "2023-09-22"}
    assert got["agreement_ref"]["date"] == "2023-11-15" and got["work_order_ref"]["date"] == "2023-11-15"
    assert got["gps"] == {"lat": 12.93412, "lon": 77.62245}


def test_collector_redacts_contact_details_and_builds_file_urls():
    assert fetch_bbmp_direct.redact({"contractormobile1": "9999999999", "contractoremail": "a@b", "gross": "1"}) == \
        {"contractormobile1": None, "contractoremail": None, "gross": "1"}
    assert fetch_bbmp_direct.file_url("", "WB-MB--1-MB 2.jpg").endswith("/vssIFMS/Files/WB-MB--1-MB%202.jpg")
    assert fetch_bbmp_direct.file_url("1", "x.pdf").endswith("/vssIFMS/Files1/x.pdf")
    assert fetch_bbmp_direct.wanted("Photo - Before Work") and fetch_bbmp_direct.wanted("Work Order")
    assert not fetch_bbmp_direct.wanted("Tender Documents") and not fetch_bbmp_direct.wanted("DPR")


def test_direct_bill_parsing():
    rec = {"details": {"billtype": "Second and Final", "gross": "100.00", "deduction": "5.00", "nett": "95.00",
                       "dbrnumber": "000051", "dbrdate": "2024-01-24", "rtgs": "003252", "rtgsdate": "06-Mar-2024",
                       "releaseper": "75", "contractormobile1": None},
           "deductions": {"fine": "2.00", "others3": "1.00", "it": "2.00"},
           "approvals": [{"date": "12-Jun-2024<br/> 18:09:53", "name": "caoho<br/> scf", "remarks": "Approved<br/> balance 25% paid", "systemtype": "13"}],
           "wo_files": [{"rFileName": "WO-7--1-WORK ORDER.pdf", "rFileType": "Work Order", "raddl": ""}], "bill_files": [],
           "details_url": "https://accounts.bbmp.gov.in/vssWB/x"}
    b = direct.bill_from_direct("632783", rec, "304-17-000105")
    assert b["bill_type"] == "Second and Final" and b["br_date"] == "2024-01-24" and b["rtgs_date"] == "2024-03-06"
    assert b["release_pct"] == 75 and b["deductions"] == {"fine": 2.0, "withheld": 1.0, "it": 2.0}
    assert b["approvals"][0]["date"] == "2024-06-12" and "balance 25% paid" in b["approvals"][0]["remarks"]
    assert b["wo_files"][0]["_url"].endswith("/vssIFMS/Files/WO-7--1-WORK%20ORDER.pdf")
