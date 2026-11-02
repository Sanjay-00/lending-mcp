"""
The MCP server: four tools over one parsed bureau report. Each tool only
validates its arguments and calls lending_mcp.loader or lending_mcp.bureau;
there is no logic here.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from lending_mcp import bank, bureau, gst, itr, loader, workbook
from lending_mcp.config import load_settings

INSTRUCTIONS = """\
Tools over lending documents that the user's own engines read locally: bureau (credit)
reports, bank statements, ITRs and GST returns. The PDF never reaches you; answer only from what these tools
return.

Bureau: start with load_bureau_report(path). Use get_bureau_summary for headline facts,
find_bureau_accounts for any question about a set of accounts (it filters, sorts and
totals in code), and get_bureau_account to explain one account.

Bank statement: start with load_bank_statement(paths). Use get_bank_summary for monthly
figures, averages, balances, bounces and EMI debits, and find_bank_transactions for any
question about a set of transactions (filters and totals in code). Bank credits are not
income; say "credits". Averages cover complete months only.

Do not add figures yourself: use the totals the tools return. A value of null is unknown,
never zero; say so. A flag shown as "not provided by this report format" is unknown, never
false. Mention a tool's limitations when they bear on the question. Report facts only: never
state or imply a lending decision."""

mcp = MCPServer("lending-mcp", instructions=INSTRUCTIONS)

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_LOAD = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (loader.LoadError, bureau.QueryError, bank.QueryError, workbook.WorkbookError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_LOAD)
def load_bureau_report(path: str) -> dict:
    """Load a CRIF High Mark bureau report (retail or Commercial ACE; PDF or HTML) from a local
    path inside the allowed folders. Parses it once with AutoCAM (cached afterwards) and writes
    AutoCAM's CAM Excel workbook. Returns the report_id every other tool needs, the borrower,
    the score, account counts, whether the reading reconciles with the report's own totals,
    the workbook path, and the limitations."""
    settings = load_settings()
    report = _call(loader.load, path, settings)
    parsed = report["parsed"]
    return {
        **bureau.envelope(report, settings.unmasked),
        "source_file": report["source_file"],
        "from_cache": report["from_cache"],
        "workbook": report["workbook"],
        "borrower": {
            "name": parsed.get("name"),
            "score": bureau.score(report),
            "identity": bureau.identity(report, settings.unmasked),
        },
        "accounts": {
            "total": len(parsed.get("accounts", [])),
            "by_status": dict(Counter(a.get("status") for a in parsed.get("accounts", []))),
        },
    }


@mcp.tool(annotations=_READ_ONLY)
def get_bureau_summary(report_id: str) -> dict:
    """Headline facts of a loaded report, computed in code: account counts by status, known
    totals of sanction, outstanding, EMI and overdue over active accounts (with the accounts
    whose value is unknown), the worst DPD ever, in the last 12 months and last reported
    (with the accounts holding it and those unknown or doubtful), accounts written off,
    settled, suit filed or delinquent, AutoCAM's portfolio analysis and its validation
    against the report's printed totals."""
    settings = load_settings()
    return _call(bureau.summary, _call(loader.require, report_id, settings, "bureau"), settings.unmasked)


@mcp.tool(annotations=_READ_ONLY)
def find_bureau_accounts(
    report_id: str,
    status: Optional[Literal["active", "closed", "written_off", "settled", "any"]] = None,
    lender: Optional[str] = None,
    loan_type: Optional[str] = None,
    ownership: Optional[str] = None,
    min_dpd: Optional[int] = None,
    dpd_window: Literal["ever", "12m", "last_reported"] = "ever",
    min_outstanding: Optional[int] = None,
    min_overdue: Optional[int] = None,
    written_off: Optional[bool] = None,
    settled: Optional[bool] = None,
    suit_filed: Optional[bool] = None,
    delinquent: Optional[bool] = None,
    dpd_doubtful_only: Optional[bool] = None,
    sort_by: Literal[
        "sr_no", "date_of_sanction", "sanction_amount", "outstanding", "emi", "overdue",
        "max_dpd_ever", "max_dpd_12m", "last_reported_dpd",
    ] = "sr_no",
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Find accounts in a loaded report. All filters combine (AND). lender, loan_type and
    ownership match text case insensitively. min_dpd applies to dpd_window: ever, 12m (last
    12 reported months) or last_reported. Amounts are rupees. Returns total_matches, has_more,
    one page of accounts (limit 1 to 200, offset), and totals and worst DPD over ALL matches,
    not just the page. Accounts whose value is unknown for a filter are listed in
    excluded_because_value_unknown, never treated as zero. Filters on a flag the report format
    does not provide are refused."""
    settings = load_settings()
    report = _call(loader.require, report_id, settings, "bureau")
    return _call(
        bureau.find_accounts, report, settings.unmasked,
        status=status, lender=lender, loan_type=loan_type, ownership=ownership,
        min_dpd=min_dpd, dpd_window=dpd_window, min_outstanding=min_outstanding,
        min_overdue=min_overdue, written_off=written_off, settled=settled,
        suit_filed=suit_filed, delinquent=delinquent, dpd_doubtful_only=dpd_doubtful_only,
        sort_by=sort_by, descending=descending, limit=limit, offset=offset,
    )


@mcp.tool(annotations=_READ_ONLY)
def get_bureau_account(report_id: str, sr_no: int) -> dict:
    """Every extracted field of one account (by its sr_no in the report), with plain labels,
    the fields that are unknown, the flags this report format does not provide, and an
    explanation of any doubt AutoCAM has about its DPD reading."""
    settings = load_settings()
    report = _call(loader.require, report_id, settings, "bureau")
    return _call(bureau.account_detail, report, sr_no, settings.unmasked)


# --- bank statements ---------------------------------------------------------------------


@mcp.tool(annotations=_LOAD)
def load_bank_statement(paths: list[str], password: Optional[str] = None) -> dict:
    """Load a bank statement PDF from a local path inside the allowed folders, or several PDFs
    of the same account (merged in date order, overlaps counted once). Read once by Bank
    Statement Analyser and cached. password is for a protected PDF. Returns the document_id
    the other bank tools need, the bank, the masked account number, the dates covered, how
    many rows reconcile, which months are complete, and the limitations."""
    settings = load_settings()
    doc = _call(loader.load_bank, paths, settings, password)
    return bank.loaded(doc, settings.unmasked)


@mcp.tool(annotations=_READ_ONLY)
def get_bank_summary(document_id: str) -> dict:
    """Facts of a loaded bank statement, computed in code: opening and closing balance, total
    credits and debits, each month's credits and debits with whether the month is complete,
    average monthly credit and debit over complete months only, average balance windows,
    credits net of own transfers and loan disbursals, bounces, EMI debits seen, salary like
    credits, category totals, overdraft use, and the engine's attention signals with their
    evidence. Credits are not income."""
    settings = load_settings()
    return _call(bank.summary, _call(loader.require, document_id, settings, "bank"), settings.unmasked)


@mcp.tool(annotations=_READ_ONLY)
def find_bank_transactions(
    document_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    direction: Literal["any", "credit", "debit"] = "any",
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    narration_contains: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[Literal["verified", "failed", "unverified"]] = None,
    sort_by: Literal["date", "amount"] = "date",
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Find transactions in a loaded bank statement. All filters combine (AND). Dates are
    YYYY-MM-DD, amounts rupees; narration_contains and category match case insensitively;
    status is the engine's balance check of the row. Returns total_matches, has_more, one page
    (limit 1 to 200, offset), and credit and debit counts and sums over ALL matches. A row
    whose debit or credit could not be decided is never summed; it is counted in
    excluded_because_value_unknown when an amount or direction filter is set."""
    settings = load_settings()
    doc = _call(loader.require, document_id, settings, "bank")
    return _call(
        bank.find_transactions, doc, settings.unmasked,
        date_from=date_from, date_to=date_to, direction=direction, min_amount=min_amount,
        max_amount=max_amount, narration_contains=narration_contains, category=category,
        status=status, sort_by=sort_by, descending=descending, limit=limit, offset=offset,
    )


# --- ITR -----------------------------------------------------------------------------------


@mcp.tool(annotations=_LOAD)
def load_itr(paths: list[str]) -> dict:
    """Load an ITR bundle: one PDF per financial year (the return with its profit and loss
    statement and balance sheet), from local paths inside the allowed folders. Read once by ITR
    Extractor (local OCR for scans, no AI) and cached; also writes its ITR Validation workbook.
    Returns the document_id get_itr_financials needs, the entity, each year found and whether
    its statements were read, the time the reading took, and the limitations. A scanned
    bundle's first read can take minutes: then the answer is status "reading"; wait a minute
    and call load_itr again with the same paths."""
    settings = load_settings()
    return itr.loaded(_call(loader.load_itr, paths, settings), settings.unmasked)


@mcp.tool(annotations=_READ_ONLY)
def get_itr_financials(document_id: str) -> dict:
    """Year by year figures of a loaded ITR: sales, expenses, profit, depreciation, interest,
    assets, liabilities, net worth, debtors and creditors (rupees), whether each statement was
    read and reconciled, standard ratios with their formulas, and the engine's flags and
    attention rows. A null value was not read, never zero. A ratio is withheld, with the
    reason, when a statement it needs was not read; DSCR is never given."""
    settings = load_settings()
    return _call(itr.financials, _call(loader.require, document_id, settings, "itr"), settings.unmasked)


# --- GST -----------------------------------------------------------------------------------


@mcp.tool(annotations=_LOAD)
def load_gst_returns(paths: list[str]) -> dict:
    """Load GSTR-3B returns from local files or folders inside the allowed folders. Read once
    by GST Analyser and cached. Other documents in a folder (bank statements, bureau reports,
    ITRs) are recognised and listed as skipped. Returns the document_id get_gst_summary needs,
    each GSTIN found (masked, with an entity_id), the periods found, the files skipped, and
    the limitations. If the answer is status "reading", call again in a minute."""
    settings = load_settings()
    return gst.loaded(_call(loader.load_gst, paths, settings), settings.unmasked)


@mcp.tool(annotations=_READ_ONLY)
def get_gst_summary(document_id: str) -> dict:
    """Facts of loaded GSTR-3B returns, per GSTIN: each period's aggregate turnover, taxable
    outward supplies, output tax, net ITC, tax paid through ITC and in cash, interest and late
    fee, financial year totals with the months covered, trends, filing dates and delays, the
    engine's findings (attention signals, as worded), validation counts and confidence. A
    null figure was not read, never zero."""
    settings = load_settings()
    return _call(gst.summary, _call(loader.require, document_id, settings, "gst"), settings.unmasked)


# --- the combined workbook ---------------------------------------------------------------


@mcp.tool(annotations=_LOAD)
def export_excel(document_ids: list[str]) -> dict:
    """Write one Excel workbook with one sheet per loaded document, in the order given: the
    bureau sheet is AutoCAM's CIBIL Summary, the ITR sheet ITR Extractor's ITR Validation (DSCR
    not shown), and the bank and GST sheets give the same facts as their summary tools. Pass
    the ids the load tools returned. Returns the workbook's local path and the sheets.
    Combining documents does not establish that they belong to one borrower, and no
    reconciliation between them is made."""
    settings = load_settings()
    return _call(workbook.export, document_ids, settings)
