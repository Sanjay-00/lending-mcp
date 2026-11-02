"""
The combined Excel workbook: one sheet per loaded document.

The bureau and ITR sheets are the engines' own first sheets ("CIBIL Summary", "ITR
Validation"), copied cell by cell, so every improvement made to AutoCAM or ITR Extractor
reaches this workbook unchanged. openpyxl cannot move a sheet between workbooks, so the copy
carries exactly what those two sheets use (checked 2026-10-31): values and formulas, cell
styles, comments, column widths, row heights, merged cells, freeze panes and print
settings. A sheet using anything else fails loudly instead of being copied half right.

On the ITR sheet the DSCR rows are replaced by a note: the engine's formula assumes term
debt is repaid over four years.
"""

from __future__ import annotations

import hashlib
import io
import re
from copy import copy
from typing import Any, Dict, List, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from lending_mcp import bank, gst, loader
from lending_mcp.config import Settings

DSCR_NOTE = ("DSCR is not shown: the formula assumes asset financed loans are repaid over four years "
             "(interest + loans / 4), which the ITR does not state.")
NOT_SHOWN = "Not shown"
NOTE_AUTHOR = "Lending MCP"
DISCLAIMER = ("These documents were combined as requested. Combining them does not establish that they belong "
              "to the same borrower, and no reconciliation between them has been made.")
NOT_READ = "Not read"

# The house style of AutoCAM's and ITR Extractor's first sheets (their excel_generator.py).
NAVY, WHITE, BORDER, ALT, TOTAL, UNREAD = "1F3864", "FFFFFF", "BFBFBF", "F2F2F2", "FFF2CC", "FFC7CE"
# Indian grouping: 1,23,45,678.
INR = r"[>=10000000]##\,##\,##\,##0;[>=100000]##\,##\,##0;##,##0"
_TITLES = {"bureau": "Bureau", "bank": "Bank", "itr": "ITR", "gst": "GST"}


class WorkbookError(ValueError):
    """A workbook that cannot be built as asked; the message says why."""


def check_supported(ws: Worksheet) -> None:
    def fail(feature):
        raise WorkbookError(f"the {ws.title} sheet uses {feature}, which the Excel export does not copy yet")

    if len(ws.conditional_formatting):
        fail("conditional formatting")
    if ws.data_validations.dataValidation:
        fail("data validation")
    if getattr(ws, "_images", None):
        fail("images")
    if getattr(ws, "_charts", None):
        fail("charts")
    if len(getattr(ws, "defined_names", {}) or {}):
        fail("named ranges")
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("=") and "!" in c.value:
                fail(f"a formula referring to another sheet (cell {c.coordinate})")


def copy_sheet(src: Worksheet, dst_wb, title: str) -> Worksheet:
    check_supported(src)
    dst = dst_wb.create_sheet(title)
    for row in src.iter_rows():
        for c in row:
            if c.__class__.__name__ == "MergedCell" and not c.has_style:
                continue
            d = dst.cell(row=c.row, column=c.column)
            if c.__class__.__name__ != "MergedCell":
                d.value = c.value
            if c.has_style:
                d.font, d.fill, d.border = copy(c.font), copy(c.fill), copy(c.border)
                d.alignment, d.protection = copy(c.alignment), copy(c.protection)
                d.number_format = c.number_format
            if c.comment is not None:
                d.comment = Comment(c.comment.text, c.comment.author, width=c.comment.width,
                                    height=c.comment.height)
    for rng in src.merged_cells.ranges:
        dst.merge_cells(str(rng))
        # merging resets the covered cells; put their styles back
        for row in src[str(rng)]:
            for c in row:
                if c.has_style:
                    d = dst[c.coordinate]
                    d.font, d.fill, d.border = copy(c.font), copy(c.fill), copy(c.border)
                    d.alignment, d.protection = copy(c.alignment), copy(c.protection)
                    d.number_format = c.number_format
    for key, dim in src.column_dimensions.items():
        dst.column_dimensions[key].width = dim.width
        dst.column_dimensions[key].hidden = dim.hidden
    for key, dim in src.row_dimensions.items():
        dst.row_dimensions[key].height = dim.height
        dst.row_dimensions[key].hidden = dim.hidden
    dst.freeze_panes = src.freeze_panes
    if src.print_title_rows:
        dst.print_title_rows = src.print_title_rows.replace("$", "")
    if src.print_title_cols:
        dst.print_title_cols = src.print_title_cols.replace("$", "")
    for attr in ("orientation", "fitToWidth", "fitToHeight", "paperSize", "scale"):
        setattr(dst.page_setup, attr, getattr(src.page_setup, attr))
    dst.sheet_properties.pageSetUpPr.fitToPage = src.sheet_properties.pageSetUpPr.fitToPage
    return dst


def _refers_to(formula: str, coordinate: str) -> bool:
    col, row = re.match(r"([A-Z]+)(\d+)", coordinate).groups()
    return re.search(rf"(?<![A-Z$])\$?{col}\$?{row}(?!\d)", formula) is not None


def replace_dscr(ws: Worksheet) -> None:
    """Both DSCR rows (Ratios and Financial Snap) hold "Not shown" with the reason, in every
    year cell; nothing else on the sheet changes."""
    rows = [r for r in range(1, ws.max_row + 1) if str(ws.cell(r, 1).value or "").strip() == "DSCR"]
    if len(rows) != 2:
        raise WorkbookError(f"expected 2 DSCR rows (Ratios and Financial Snap) on the {ws.title} sheet, "
                            f"found {len(rows)}")
    replaced = []
    for r in rows:
        for c in range(2, ws.max_column + 1):
            cell = ws.cell(r, c)
            if cell.value in (None, ""):
                continue
            cell.value = NOT_SHOWN
            cell.comment = Comment(DSCR_NOTE, NOTE_AUTHOR)
            replaced.append(cell.coordinate)
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                for coord in replaced:
                    if _refers_to(c.value, coord):
                        raise WorkbookError(f"the {ws.title} sheet's cell {c.coordinate} reads the DSCR in {coord}; "
                                            "it cannot be hidden safely")


# --- the bank and GST sheets ----------------------------------------------------------------


def _font(size=10, bold=False, color="000000"):
    return Font(name="Arial", size=size, bold=bold, color=color)


def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)


def _border():
    side = Side(style="thin", color=BORDER)
    return Border(left=side, right=side, top=side, bottom=side)


def _put(ws, row, col, value, bold=False, fill=None, money=False):
    """One cell. An unknown value is written as "Not read" on red, never as 0."""
    cell = ws.cell(row=row, column=col)
    if value is None:
        cell.value, cell.fill = NOT_READ, _fill(UNREAD)
    else:
        cell.value = value
        if fill:
            cell.fill = _fill(fill)
        if money and isinstance(value, (int, float)) and not isinstance(value, bool):
            cell.number_format = INR
    cell.font = _font(bold=bold)
    cell.border = _border()
    cell.alignment = Alignment(horizontal="left" if isinstance(cell.value, str) else "right", vertical="center")
    return cell


def _title(ws, text, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws["A1"] = text
    ws["A1"].font = _font(size=14, bold=True, color=NAVY)
    ws.row_dimensions[1].height = 24
    return 3


def _pairs(ws, row, pairs):
    for label, value in pairs:
        ws.cell(row=row, column=1, value=label).font = _font(bold=True, color=NAVY)
        _put(ws, row, 2, value, money=True)
        row += 1
    return row + 1


def _header(ws, row, headers):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font, c.fill, c.border = _font(bold=True, color=WHITE), _fill(NAVY), _border()
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30
    return row + 1


def _known_sum(values):
    """A total only when every value is known: unknown is never counted as zero."""
    return None if any(v is None for v in values) else round(sum(values), 2)


def _notes(ws, row, title, lines):
    lines = [line for line in lines if line]
    if not lines:
        return row
    ws.cell(row=row, column=1, value=title).font = _font(bold=True, color=NAVY)
    for line in lines:
        row += 1
        ws.cell(row=row, column=1, value=line).font = _font()
    return row + 2


def bank_sheet(wb, summary: Dict[str, Any], title: str) -> Worksheet:
    ws = wb.create_sheet(title)
    row = _title(ws, "Bank statement", [34, 12, 16, 10, 16, 10, 16, 10])
    st, totals, reading = summary["statement"], summary["totals"], summary["reading"]
    counts = reading.get("rows_by_status") or {}
    rows = sum(v for k, v in counts.items() if k != "OPENING")
    row = _pairs(ws, row, [
        ("Bank", st.get("bank")), ("Account type", st.get("account_type")),
        ("Account number", st.get("account_number")), ("Account holder", st.get("account_holder")),
        ("Transactions from", st.get("first_transaction")), ("Transactions to", st.get("last_transaction")),
        ("Printed period", " to ".join(st["printed_period"]) if st.get("printed_period") else "not printed"),
        ("Opening balance", totals.get("opening_balance")), ("Closing balance", totals.get("closing_balance")),
        ("Rows reconciled", f"{counts.get('VERIFIED', 0)} of {rows}"),
        ("Scanned pages not read", st.get("scanned_pages") or 0),
    ])
    row = _header(ws, row, ["Month", "Status", "Credits", "Credit count", "Debits", "Debit count", "Net", "Bounces"])
    bounces: Dict[str, int] = {}
    for b in (summary.get("bounces") or {}).get("instances") or []:
        if b.get("date"):
            bounces[str(b["date"])[:7]] = bounces.get(str(b["date"])[:7], 0) + 1
    months = summary.get("months") or []
    for i, m in enumerate(months):
        fill = ALT if i % 2 else None
        for col, value, money in ((1, m["month"], False), (2, m["status"], False), (3, m["credits"], True),
                                  (4, m["credit_count"], False), (5, m["debits"], True), (6, m["debit_count"], False),
                                  (7, m["net"], True), (8, bounces.get(m["month"], 0), False)):
            _put(ws, row, col, value, fill=fill, money=money)
        row += 1
    _put(ws, row, 1, "Total, all months", bold=True, fill=TOTAL)
    _put(ws, row, 2, "", fill=TOTAL)
    for col, key, money in ((3, "credits", True), (4, "credit_count", False), (5, "debits", True),
                            (6, "debit_count", False), (7, "net", True)):
        _put(ws, row, col, _known_sum([m[key] for m in months]), bold=True, fill=TOTAL, money=money)
    _put(ws, row, 8, sum(bounces.values()), bold=True, fill=TOTAL)
    row += 1
    avg = summary.get("averages") or {}
    if avg.get("months_used"):
        _put(ws, row, 1, "Average over complete months (" + ", ".join(avg["months_used"]) + ")", bold=True, fill=TOTAL)
        _put(ws, row, 3, avg.get("monthly_credit"), bold=True, fill=TOTAL, money=True)
        _put(ws, row, 5, avg.get("monthly_debit"), bold=True, fill=TOTAL, money=True)
    else:
        _put(ws, row, 1, "Average over complete months: none, no month is complete", bold=True, fill=TOTAL)
    row += 3
    emi = summary.get("emi_debits_seen") or []
    if emi:
        row = _header(ws, row, ["EMI debits seen (lender)", "Months seen", "Monthly amount", "First", "Last"])
        for e in emi:
            for col, value, money in ((1, e.get("identity"), False), (2, e.get("months_seen"), False),
                                      (3, e.get("monthly_amount"), True), (4, e.get("first_date"), False),
                                      (5, e.get("last_date"), False)):
                _put(ws, row, col, value, money=money)
            row += 1
        row += 2
    row = _notes(ws, row, "Credits", [(summary.get("credits") or {}).get("note")])
    _notes(ws, row, "Limitations", summary.get("limitations") or [])
    ws.freeze_panes = "A3"
    return ws


def gst_sheet(wb, summary: Dict[str, Any], title: str) -> Worksheet:
    ws = wb.create_sheet(title)
    row = _title(ws, "GST returns (GSTR-3B)", [30, 12, 18, 16, 16, 16, 16, 14])
    window = summary.get("window") or {}
    for e in summary.get("entities") or []:
        cov = e.get("coverage") or {}
        row = _pairs(ws, row, [
            ("GSTIN", e.get("gstin") or e.get("gstin_masked")),
            ("Legal name", ", ".join(e.get("legal_names") or []) or None),
            ("Filing pattern", e.get("filing_pattern")),
            ("Window", window.get("label")),
            ("Periods covered", f"{len(cov.get('available') or [])} of {len(cov.get('expected_periods') or [])}"),
        ])
        row = _header(ws, row, ["Period", "Status", "Aggregate turnover", "Output tax", "Net ITC",
                                "Tax paid in cash", "Tax paid by ITC", "Filing delay (days)"])
        delay = {f.get("period"): f.get("delay_days") for f in e.get("filing") or []}
        for i, p in enumerate(e.get("periods") or []):
            f, fill = p.get("figures") or {}, (ALT if i % 2 else None)
            for col, value, money in ((1, p.get("period"), False), (2, p.get("status"), False),
                                      (3, f.get("aggregate_turnover_3b"), True), (4, f.get("output_tax"), True),
                                      (5, f.get("net_itc"), True), (6, f.get("tax_paid_cash"), True),
                                      (7, f.get("tax_paid_itc"), True), (8, delay.get(p.get("period")), False)):
                _put(ws, row, col, value, fill=fill, money=money)
            row += 1
        for fy, t in (e.get("fy_totals") or {}).items():
            _put(ws, row, 1, f"FY {fy} total ({t.get('months_covered')} of {t.get('months_expected')} months)",
                 bold=True, fill=TOTAL)
            _put(ws, row, 2, "", fill=TOTAL)
            _put(ws, row, 3, t.get("aggregate_turnover_3b"), bold=True, fill=TOTAL, money=True)
            row += 1
        row += 2
    findings = [f"{f.get('severity')}: {f.get('title')}. {f.get('explanation') or ''}".strip()
                for f in summary.get("findings") or []]
    row = _notes(ws, row, "Findings (attention signals, not conclusions)", findings)
    _notes(ws, row, "Limitations", summary.get("limitations") or [])
    ws.freeze_panes = "A3"
    return ws


# --- the export -----------------------------------------------------------------------------


def _engine_sheet(doc: dict, document_id: str) -> Worksheet:
    try:
        return load_workbook(doc.get("workbook")).worksheets[0]
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise WorkbookError(f"The engine workbook for {document_id!r} is missing; load the document again.") from exc


def _entity(kind: str, doc: dict) -> Optional[str]:
    if kind == "bureau":
        return (doc.get("parsed") or {}).get("name")
    if kind == "bank":
        return doc.get("account_holder")
    if kind == "itr":
        return next((c.get("entity") for c in doc.get("columns") or [] if c.get("entity")), None)
    names = [n for e in doc.get("entities") or [] for n in e.get("legal_names") or []]
    return ", ".join(dict.fromkeys(names)) or None


def export(document_ids: List[str], settings: Settings) -> Dict[str, Any]:
    """One workbook, one sheet per document id in the order given."""
    if not document_ids:
        raise loader.LoadError("Give at least one document id.")
    docs = [(i, loader.kind_of(i), loader.require(i, settings)) for i in document_ids]
    wb = Workbook()
    wb.remove(wb.active)
    seen: Dict[str, int] = {}
    sheets = []
    for doc_id, kind, doc in docs:
        seen[kind] = seen.get(kind, 0) + 1
        title = _TITLES[kind] + (f" ({seen[kind]})" if seen[kind] > 1 else "")
        if kind == "bureau":
            copy_sheet(_engine_sheet(doc, doc_id), wb, title)
        elif kind == "itr":
            replace_dscr(copy_sheet(_engine_sheet(doc, doc_id), wb, title))
        elif kind == "bank":
            bank_sheet(wb, bank.summary(doc, settings.unmasked), title)
        else:
            gst_sheet(wb, gst.summary(doc, settings.unmasked), title)
        sheets.append({
            "title": title, "document_id": doc_id, "document_type": kind,
            "source_files": doc.get("source_files") or ([doc["source_file"]] if doc.get("source_file") else []),
            "entity": _entity(kind, doc),
        })

    mode = "unmasked" if settings.unmasked else "masked"
    wb.properties.title = "Lending MCP export"
    wb.properties.description = DISCLAIMER + "\n" + "\n".join(
        f"{s['title']}: {s['document_id']} ({', '.join(s['source_files'])})" for s in sheets)
    name = hashlib.sha256((",".join(document_ids) + "|" + mode).encode()).hexdigest()[:8]
    path = settings.workbooks_dir / f"export-{name}.xlsx"
    buf = io.BytesIO()
    wb.save(buf)
    loader._atomic_write(path, buf.getvalue())
    return {"workbook": str(path), "sheets": sheets, "note": DISCLAIMER,
            "masking": "off" if settings.unmasked else "on"}
