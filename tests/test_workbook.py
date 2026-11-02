import io
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side

from lending_mcp import workbook
from lending_mcp.workbook import WorkbookError

AUTOCAM = Path(__file__).resolve().parents[2] / "CIBIL_EXCEL"


def _autocam_sheet():
    from tests.fixtures import COMMERCIAL

    if str(AUTOCAM) not in sys.path:
        sys.path.insert(0, str(AUTOCAM))
    import excel_generator

    return load_workbook(io.BytesIO(excel_generator.generate_excel(COMMERCIAL))).worksheets[0]


def _itr_sheet():
    from itr_engine.excel_generator import generate_excel

    from tests.fixtures_itr import columns

    return load_workbook(io.BytesIO(generate_excel(columns()))).worksheets[0]


def _cell_state(c):
    comment = (c.comment.text, c.comment.author) if c.comment else None
    return (c.value, c.number_format, repr(c.font), repr(c.fill), repr(c.border), repr(c.alignment),
            repr(c.protection), comment)


def assert_same_sheet(src, dst, skip=()):
    """Every attribute the two engine sheets use, compared one by one."""
    assert dst.max_row == src.max_row and dst.max_column == src.max_column
    for row in src.iter_rows():
        for c in row:
            if c.coordinate in skip:
                continue
            assert _cell_state(dst[c.coordinate]) == _cell_state(c), c.coordinate
    assert {str(r) for r in dst.merged_cells.ranges} == {str(r) for r in src.merged_cells.ranges}
    for key, dim in src.column_dimensions.items():
        assert dst.column_dimensions[key].width == dim.width, key
    for key, dim in src.row_dimensions.items():
        assert dst.row_dimensions[key].height == dim.height, key
    assert dst.freeze_panes == src.freeze_panes
    assert dst.print_title_rows == src.print_title_rows and dst.print_title_cols == src.print_title_cols
    for attr in ("orientation", "fitToWidth", "fitToHeight", "paperSize"):
        assert getattr(dst.page_setup, attr) == getattr(src.page_setup, attr), attr
    assert dst.sheet_properties.pageSetUpPr.fitToPage == src.sheet_properties.pageSetUpPr.fitToPage


def _rich_sheet():
    wb = Workbook()
    ws = wb.active
    ws.title = "Source"
    ws["A1"] = "Borrower"
    ws["A1"].font = Font(name="Arial", size=12, bold=True, color="1F3864")
    ws["A1"].fill = PatternFill("solid", fgColor="F2F2F2")
    ws["A1"].border = Border(left=Side(style="thin", color="BFBFBF"))
    ws["A1"].alignment = Alignment(horizontal="center", wrap_text=True)
    ws["A1"].protection = Protection(locked=False)
    ws["B2"] = 1234.5
    ws["B2"].number_format = "#,##0.00"
    ws["B3"] = "=B2*2"
    ws["B3"].comment = Comment("Not read", "ITR Extractor")
    ws.merge_cells("A5:C5")
    ws.column_dimensions["A"].width = 30
    ws.row_dimensions[2].height = 22
    ws.freeze_panes = "A2"
    ws.print_title_rows = "1:1"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    return ws


def _copy(src):
    dst_wb = Workbook()
    dst_wb.remove(dst_wb.active)
    return workbook.copy_sheet(src, dst_wb, "Copy")


def test_copy_preserves_everything_used():
    src = _rich_sheet()
    dst = _copy(src)
    assert dst.title == "Copy"
    assert_same_sheet(src, dst)


def test_copy_matches_real_autocam_sheet():
    src = _autocam_sheet()
    assert_same_sheet(src, _copy(src))


def test_copy_matches_real_itr_sheet():
    src = _itr_sheet()
    dst = _copy(src)
    assert_same_sheet(src, dst)
    assert dst["B58"].value == src["B58"].value and str(src["B58"].value).startswith("=")


def test_copy_survives_a_save_and_reload():
    src = _itr_sheet()
    dst = _copy(src)
    buf = io.BytesIO()
    dst.parent.save(buf)
    assert_same_sheet(src, load_workbook(io.BytesIO(buf.getvalue())).worksheets[0])


def test_unsupported_feature_fails_loudly():
    src = _rich_sheet()
    src.conditional_formatting.add("B2", CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", fgColor="FF0000")))
    with pytest.raises(WorkbookError, match="the Source sheet uses conditional formatting"):
        _copy(src)


def test_cross_sheet_formula_fails_loudly():
    src = _rich_sheet()
    src["C9"] = "='Other sheet'!A1"
    with pytest.raises(WorkbookError, match="a formula referring to another sheet"):
        _copy(src)


def test_replace_dscr_both_rows_only():
    src = _itr_sheet()
    dst = _copy(src)
    workbook.replace_dscr(dst)
    dscr_rows = [r for r in range(1, src.max_row + 1) if str(src.cell(r, 1).value or "").strip() == "DSCR"]
    assert len(dscr_rows) == 2
    replaced = set()
    for r in dscr_rows:
        for c in range(2, src.max_column + 1):
            if src.cell(r, c).value not in (None, ""):
                cell = dst.cell(r, c)
                assert cell.value == "Not shown"
                assert cell.comment.text == workbook.DSCR_NOTE and cell.comment.author == "Lending MCP"
                replaced.add(cell.coordinate)
    assert replaced
    assert_same_sheet(src, dst, skip=replaced)


def test_replace_dscr_fails_when_rows_missing():
    src = _rich_sheet()
    src["A7"] = "DSCR"
    with pytest.raises(WorkbookError, match="expected 2 DSCR rows"):
        workbook.replace_dscr(_copy(src))


def test_replace_dscr_fails_if_another_formula_reads_it():
    src = _itr_sheet()
    dst = _copy(src)
    dst["D2"] = "=B58*2"
    with pytest.raises(WorkbookError, match="D2 reads the DSCR"):
        workbook.replace_dscr(dst)


# --- the combined export (Task 9) --------------------------------------------------------------

import copy as copymod  # noqa: E402

from lending_mcp import loader  # noqa: E402
from lending_mcp.config import load_settings  # noqa: E402
from tests.test_loader import FakeBank, FakeGst, FakeItr, _bank_files, env  # noqa: E402,F401


def _bureau(env):
    from tests.fixtures import COMMERCIAL

    if str(AUTOCAM) not in sys.path:
        sys.path.insert(0, str(AUTOCAM))
    import excel_generator

    return loader.load(str(env["allowed"] / "report.pdf"), load_settings(),
                       parse=lambda p: copymod.deepcopy(COMMERCIAL), make_workbook=excel_generator.generate_excel)["report_id"]


def _itr(env):
    from itr_engine.excel_generator import generate_excel

    engine = FakeItr()
    engine.workbook = generate_excel
    return loader.load_itr(_bank_files(env, "fy23.pdf"), load_settings(), engine=engine)["document_id"]


def _bank(env, name="jan.pdf"):
    return loader.load_bank(_bank_files(env, name), load_settings(), engine=FakeBank())["document_id"]


def _gst(env):
    return loader.load_gst(_bank_files(env, "3b.pdf"), load_settings(), engine=FakeGst())["document_id"]


def _open(result):
    return load_workbook(result["workbook"])


def _texts(ws):
    return [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]


def test_export_one_two_and_four_documents(env):
    ids = {"bureau": _bureau(env), "bank": _bank(env), "itr": _itr(env), "gst": _gst(env)}
    one = workbook.export([ids["bureau"]], load_settings())
    assert _open(one).sheetnames == ["Bureau"]
    two = workbook.export([ids["bureau"], ids["bank"]], load_settings())
    assert _open(two).sheetnames == ["Bureau", "Bank"]
    four = workbook.export([ids["bank"], ids["itr"], ids["gst"], ids["bureau"]], load_settings())
    assert _open(four).sheetnames == ["Bank", "ITR", "GST", "Bureau"]
    assert [s["document_type"] for s in four["sheets"]] == ["bank", "itr", "gst", "bureau"]
    assert four["sheets"][0]["source_files"] == ["jan.pdf"]


def test_export_refuses_unknown_or_empty_ids(env):
    with pytest.raises(loader.LoadError, match="at least one document id"):
        workbook.export([], load_settings())
    with pytest.raises(loader.LoadError, match="bank-nope-00000000"):
        workbook.export(["bank-nope-00000000"], load_settings())


def test_repeated_type_titles(env):
    ids = [_bank(env, "jan.pdf"), _bank(env, "feb.pdf")]
    assert ids[0] != ids[1]
    assert _open(workbook.export(ids, load_settings())).sheetnames == ["Bank", "Bank (2)"]


def test_bureau_sheet_is_autocams_first_sheet(env):
    ws = _open(workbook.export([_bureau(env)], load_settings()))["Bureau"]
    assert_same_sheet(_autocam_sheet(), ws)


def test_itr_sheet_dscr_replaced_in_export(env):
    ws = _open(workbook.export([_itr(env)], load_settings()))["ITR"]
    src = _itr_sheet()
    dscr = [r for r in range(1, src.max_row + 1) if str(src.cell(r, 1).value or "").strip() == "DSCR"]
    assert all(ws.cell(r, 2).value == "Not shown" for r in dscr)
    assert ws.cell(3, 1).value == src.cell(3, 1).value


def test_bank_sheet_has_no_scoring_text(env):
    ws = _open(workbook.export([_bank(env)], load_settings()))["Bank"]
    text = " ".join(_texts(ws)).lower()
    assert "credits" in text
    for word in ("foir", "dscr", "score"):
        assert word not in text


def test_bank_sheet_months_totals_and_average(env):
    ws = _open(workbook.export([_bank(env)], load_settings()))["Bank"]
    texts = _texts(ws)
    assert "2025-01" in texts and "partial" in texts
    assert any(t.startswith("Average over complete months") for t in texts)
    assert 11500 in [c.value for row in ws.iter_rows() for c in row]  # the average monthly debit


def test_new_sheets_show_not_read_for_unknown(env):
    from tests import fixtures_gst

    r = fixtures_gst.report()
    point = r["canonical"]["borrowers"][fixtures_gst.PAN]["gstins"][fixtures_gst.GSTIN_1]["series"]["points"][0]
    point["base"]["output_tax"] = None
    doc_id = loader.load_gst(_bank_files(env, "3b.pdf"), load_settings(), engine=FakeGst(r))["document_id"]
    gst_ws = _open(workbook.export([doc_id], load_settings()))["GST"]
    cells = [c for row in gst_ws.iter_rows() for c in row if c.value == "Not read"]
    assert cells and all(c.fill.fgColor.rgb.endswith(workbook.UNREAD) for c in cells)


def test_new_sheets_follow_masking(env, monkeypatch):
    from tests.fixtures_bank import ACCOUNT
    from tests.fixtures_gst import GSTIN_1

    ids = [_bank(env), _gst(env)]
    masked = " ".join(t for ws in _open(workbook.export(ids, load_settings())).worksheets for t in _texts(ws))
    assert ACCOUNT not in masked and GSTIN_1 not in masked and "29**********1Z5" in masked
    monkeypatch.setenv("LENDING_MCP_UNMASKED", "1")
    shown = " ".join(t for ws in _open(workbook.export(ids, load_settings())).worksheets for t in _texts(ws))
    assert ACCOUNT in shown and GSTIN_1 in shown


def test_disclaimer_in_result_and_properties(env):
    result = workbook.export([_bank(env), _gst(env)], load_settings())
    assert result["note"] == workbook.DISCLAIMER
    props = _open(result).properties
    assert props.title == "Lending MCP export"
    assert props.description.startswith(workbook.DISCLAIMER)
    assert "Bank:" in props.description and "jan.pdf" in props.description
