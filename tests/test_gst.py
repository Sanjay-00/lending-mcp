import json

from lending_mcp import gst
from tests.fixtures_gst import GSTIN_1, GSTIN_2, PAN, report


def doc_of():
    d = gst.normalise(report())
    d.update({"document_id": "gst-abc-12345678", "source_files": ["3b_2025-04.pdf", "bank.pdf"]})
    return d


def test_entities_are_records_with_opaque_ids():
    d = doc_of()
    assert "canonical" not in d
    assert [(e["entity_id"], e["gstin"], e["pan"]) for e in d["entities"]] == [
        ("gstin-1", GSTIN_1, PAN), ("gstin-2", GSTIN_2, PAN)]
    assert d["confidence"]["data_coverage"] == {"gstin-1": 100.0, "gstin-2": 100.0}
    assert d["files"][0]["entity_id"] == "gstin-1" and "gstin" not in d["files"][0]
    assert "vendor_compat" not in d and "vendor_summary" not in d


def test_two_gstins_one_pan_both_kept():
    out = gst.summary(doc_of(), unmasked=False)
    assert [e["entity_id"] for e in out["entities"]] == ["gstin-1", "gstin-2"]
    assert [e["gstin_masked"] for e in out["entities"]] == ["29**********1Z5", "29**********2Z5"]
    assert out["entities"][0]["fy_totals"]["2025-26"]["aggregate_turnover_3b"] == 2000000.0
    assert out["entities"][1]["fy_totals"]["2025-26"]["aggregate_turnover_3b"] == 400000.0


def test_gst_full_identifiers_never_appear_masked():
    text = json.dumps(gst.summary(doc_of(), unmasked=False))
    for full in (GSTIN_1, GSTIN_2, PAN):
        assert full not in text
    assert "29**********1Z5" in text  # masked forms are expected
    assert "filed May 2025 four days late" in text  # the finding's text survives, masked


def test_unmasked_adds_fields_not_keys():
    out = gst.summary(doc_of(), unmasked=True)
    e = out["entities"][0]
    assert e["gstin"] == GSTIN_1 and e["pan"] == PAN and e["gstin_masked"] == "29**********1Z5"
    assert all(isinstance(e, dict) for e in out["entities"])
    assert out["masking"] == "off"


def test_periods_carry_the_figures_and_unknown_stays_none():
    p = gst.summary(doc_of(), unmasked=False)["entities"][0]["periods"][0]
    assert p["period"] == "April 2025" and p["key"] == "2025-04" and p["extraction_confidence"] == "HIGH"
    assert p["figures"]["aggregate_turnover_3b"] == 1000000.0
    assert p["figures"]["late_fee_paid"] is None
    assert p["ratios"]["cash_tax_ratio"] == 0.44


def test_filing_per_return():
    filing = gst.summary(doc_of(), unmasked=False)["entities"][0]["filing"]
    assert filing == [
        {"period": "April 2025", "due_date": "2025-04-20", "arn_date": "2025-04-22", "delay_days": 0, "late": False},
        {"period": "May 2025", "due_date": "2025-05-20", "arn_date": "2025-05-22", "delay_days": 4, "late": True},
    ]


def test_findings_validation_and_confidence():
    out = gst.summary(doc_of(), unmasked=False)
    assert out["findings"][0]["interpretation_scope"] == "attention_signal_only"
    assert out["validation"]["counts"]["WARNING"] == 1
    assert [c["check_id"] for c in out["validation"]["not_passed"]] == ["V2"]
    assert out["confidence"]["extraction_summary"] == {"HIGH": 4, "MEDIUM": 0, "LOW": 0}


def test_skipped_files_listed():
    out = gst.summary(doc_of(), unmasked=False)
    assert {"file_name": "bank.pdf", "doc_type": "BANK_STATEMENT", "status": "SKIPPED",
            "reason": "not a GST return", "entity_id": None, "period": None} in out["files"]
    assert any("1 file(s) were not GST returns" in lim for lim in out["limitations"])


def test_envelope():
    out = gst.summary(doc_of(), unmasked=False)
    assert out["document_type"] == "gst" and out["engine"] == "GST Analyser"
    assert out["window"]["label"] == "Apr to May 2025"


def test_duplicates_are_not_called_non_gst_files():
    r = report()
    r["files"].append({"file_name": "3b_copy.pdf", "sha256": "a", "doc_type": "UNKNOWN", "status": "DUPLICATE_FILE",
                       "reason": "byte-identical to 3b_2025-04.pdf", "doc_id": None, "gstin": None, "period_label": None})
    lims = gst.summary(gst.normalise(r), unmasked=False)["limitations"]
    assert "1 file(s) were not GST returns and were skipped (listed in files)" in lims
    assert "1 file(s) were byte-identical copies of another file and were counted once" in lims
