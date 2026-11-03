import copy
import json

from lending_mcp import itr
from tests.fixtures_itr import ATTENTION, FLAGS, FORMULAS, LABELS, PAN, SUMMARY, columns


def doc_of():
    d = itr.normalise(columns(), FLAGS, SUMMARY, ATTENTION, FORMULAS, LABELS)
    d.update({"document_id": "itr-abc-12345678", "source_files": ["itr_fy2023.pdf", "itr_fy2024.pdf"]})
    return d


def year(out, y):
    return next(c for c in out["years"] if c["year"] == y)


def test_published_ratio_carries_formula_and_note():
    r = year(itr.financials(doc_of()), 2023)["ratios"]["Current Ratio"]
    assert r["value"] == 1.42
    assert r["formula"] == FORMULAS["Current Ratio"]
    assert r["note"] == "a standard ratio derived from this year's statements"


def test_ratio_withheld_when_pl_not_read():
    y = year(itr.financials(doc_of()), 2024)
    assert "Interest Coverage" not in y["ratios"] and "PAT / Income (%)" not in y["ratios"]
    assert y["ratios_withheld"]["Interest Coverage"] == (
        "needs the profit and loss statement, which was not read this year")
    assert y["ratios"]["Current Ratio"]["value"] == 1.5  # balance sheet only: still published


def test_ratio_withheld_when_bs_not_read():
    cols = columns()
    for k in ("total_assets", "debtors"):
        cols[0]["values"][k] = None
    d = itr.normalise(cols, FLAGS, SUMMARY, ATTENTION, FORMULAS, LABELS)
    y = year(itr.financials(d), 2023)
    assert "Current Ratio" not in y["ratios"]
    assert y["ratios_withheld"]["Current Ratio"] == "needs the balance sheet, which was not read this year"
    assert y["ratios"]["Interest Coverage"]["value"] == 3.1


def test_dscr_never_published():
    out = itr.financials(doc_of())
    for y in out["years"]:
        assert "DSCR" not in y["ratios"]
        assert y["ratios_withheld"]["DSCR"] == (
            "DSCR is not shown: the formula assumes asset financed loans are repaid over four years")
    outside_withheld = [{k: v for k, v in y.items() if k != "ratios_withheld"} for y in out["years"]]
    assert "DSCR" not in json.dumps(outside_withheld)


def test_unknown_ratio_withheld():
    y = year(itr.financials(doc_of()), 2023)
    assert y["ratios_withheld"]["Mystery Ratio"] == "not in the MCP's ratio table"


def test_none_ratio_withheld_not_zero():
    y = year(itr.financials(doc_of()), 2023)
    assert "Debtor Days" not in y["ratios"]
    assert y["ratios_withheld"]["Debtor Days"] == "the engine could not compute it this year"


def test_none_value_stated_as_unknown():
    y = year(itr.financials(doc_of()), 2024)
    sales = y["values"]["sales_other_income"]
    assert sales == {"label": "Sales and other Income", "value": None, "read": False}
    assert y["values"]["total_assets"]["value"] is not None and y["values"]["total_assets"]["read"] is True
    assert y["values"]["networth"]["label"] == "networth"  # no label known: the key itself


def test_statements_and_checks_per_year():
    y = year(itr.financials(doc_of()), 2024)
    assert y["statements_read"] == {"profit_and_loss": False, "balance_sheet": True}
    assert y["statements"] == [{"kind": "bs", "status": "verified", "page": 4}]
    assert y["checks"] == [{"name": "sheet_balances", "status": "pass", "detail": ""}]
    assert y["warnings"] == ["Profit and loss statement not read for this year."]


def test_flags_summary_attention_and_envelope():
    out = itr.financials(doc_of())
    assert out["document_type"] == "itr" and out["engine"] == "ITR Extractor"
    assert out["flags"] == FLAGS and out["revenue_cagr"] is None and out["attention"] == ATTENTION
    assert any("2024" in lim and "profit and loss" in lim for lim in out["limitations"])


def test_pan_masked_and_unmasked():
    masked = itr.financials(doc_of(), unmasked=False)
    assert PAN not in json.dumps(masked) and year(masked, 2023)["pan"] == "******234F"
    assert year(itr.financials(doc_of(), unmasked=True), 2023)["pan"] == PAN


def test_loaded_passes_a_reading_status_through():
    pending = {"status": "reading", "document_id": "itr-abc-12345678", "message": "still reading"}
    assert itr.loaded(pending) == pending


def test_load_answer_shows_each_years_form_and_engine_warnings():
    from tests.fixtures_itr import COLUMNS

    doc = {"columns": copy.deepcopy(COLUMNS), "document_id": "itr-x-1"}
    years = itr.loaded(doc)["years"]
    assert years[0]["form"] == "ITR-3" and years[1]["warnings"] == ["Profit and loss statement not read for this year."]
