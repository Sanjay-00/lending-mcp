"""Synthetic ITR Extractor output in the shape lending_mcp.loader stores it: spread()'s year
columns after to_jsonable, plus flags, summary and attention. No real data.

FY2023 has both statements read. FY2024 has only the balance sheet read, so every P&L
value is None, the way taxonomy.compute leaves an unread section."""

import copy

PAN = "ABCDE1234F"

_PL = ["sales_other_income", "gross_receipts", "gross_expenses", "profit_before_tax", "profit_after_tax",
       "depreciation", "interest_finance", "cash_profit"]
_BS = ["equity_capital", "reserves", "secured_loan_asset_financed", "unsecured_loans", "sundry_creditors",
       "current_liabilities", "fixed_assets", "debtors", "current_assets", "total_assets", "total_liabilities",
       "networth"]


def _values(pl: bool, bs: bool):
    v = {}
    for i, k in enumerate(_PL):
        v[k] = (1000000 + 10000 * i) if pl else None
    for i, k in enumerate(_BS):
        v[k] = (500000 + 5000 * i) if bs else None
    return v


def _column(year, pl, bs, ratios):
    return {
        "year": year, "provisional": False, "entity": "TEST TRADERS",
        "identity": {"ay": f"{year + 1}-{(year + 2) % 100:02d}", "pan": PAN, "name": "TEST TRADERS",
                     "form": "ITR-3", "ack_no": "123456789012345", "filing_date": f"{year + 1}-10-20",
                     "assessee_status": "individual", "regime": None},
        "scanned": False, "pages_total": 12, "pages_used": 4,
        "blocks_used": [b for b in (
            {"kind": "pl", "status": "verified", "page": 3, "entity": "TEST TRADERS"} if pl else None,
            {"kind": "bs", "status": "verified", "page": 4, "entity": "TEST TRADERS"} if bs else None,
        ) if b],
        "checks": [{"name": "sheet_balances", "status": "pass" if bs else "skip", "gap": 0, "implicates": [], "detail": ""}],
        "values": _values(pl, bs),
        "ratios": ratios,
        "mapping_check": {"ok": True, "harvested": 20, "mapped": 20, "diff": 0, "unmapped_count": 0, "unmapped_amount": 0},
        "warnings": [] if pl else ["Profit and loss statement not read for this year."],
        "source_name": f"itr_fy{year}.pdf",
    }


COLUMNS = [
    _column(2023, True, True, {
        "Current Ratio": 1.42, "Interest Coverage": 3.1, "DSCR": 2.2, "PAT / Income (%)": 0.08,
        "Mystery Ratio": 9.9, "Debtor Days": None,
    }),
    # Balance sheet only: Interest Coverage came back a confident 0.0 from the engine's
    # `d.get(key) or 0`; it must be withheld, not shown.
    _column(2024, False, True, {
        "Current Ratio": 1.5, "Interest Coverage": 0.0, "DSCR": 0.0, "PAT / Income (%)": 0.0,
    }),
]

FLAGS = [{"year": 2024, "severity": "amber", "flag": "P&L not read", "detail": "Check the ITR."}]
SUMMARY = {"revenue_cagr": None, "red": 0, "amber": 1}
ATTENTION = [{"year": 2024, "status": "failed", "rows": ["Sales"], "why": "P&L not read", "where": "itr_fy2024.pdf"}]
FORMULAS = {
    "Current Ratio": "IFERROR(({debtors}+{current_assets})/({sundry_creditors}+{current_liabilities}),0)",
    "Interest Coverage": "IFERROR(({profit_before_tax}+{interest_finance}+{depreciation})/{interest_finance},0)",
    "DSCR": "IFERROR(({profit_after_tax}+{depreciation}+{interest_finance})/({interest_finance}+({secured_loan_asset_financed}/4)),0)",
    "PAT / Income (%)": "IFERROR({profit_after_tax}/{gross_receipts},0)",
    "Debtor Days": "IFERROR({debtors}/{gross_receipts}*365,0)",
}
LABELS = {"sales_other_income": "Sales and other Income", "total_assets": "Total Assets"}


def columns():
    return copy.deepcopy(COLUMNS)
