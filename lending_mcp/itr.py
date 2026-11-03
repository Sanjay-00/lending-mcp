"""
Questions over one ITR bundle that ITR Extractor read: pure functions, no I/O.

`doc` is what lending_mcp.loader stores: spread()'s year columns made JSON safe, the
engine's flags, summary and attention rows, each ratio's formula and each value's label.

Ratios are filtered. The engine's ratio helper reads an unread value as 0, so when a year's
profit and loss statement or balance sheet was not read, a ratio drawing on it can come back
as a confident 0.0. A ratio is published only when every statement it draws on was read.
DSCR is never published: its formula assumes term debt is repaid over four years.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from lending_mcp.masking import mask_tail, scrub

ENGINE = "ITR Extractor"

# Which statements each of ITR Extractor's ratios draws on: "pl" the profit and loss
# statement, "bs" the balance sheet. Copied from FinSight/backend/business/ratios.py
# (RATIO_INPUTS), itself taken from the engine's mapping/template_config.py RATIOS. FinSight
# is not a dependency of this server, so the table is copied, not imported.
RATIO_INPUTS: Dict[str, Set[str]] = {
    "Return on Capital Employed": {"pl", "bs"},
    "Return On Share Holders Fund": {"pl", "bs"},
    "PAT / Income (%)": {"pl"},
    "PAT / Assets employed (%)": {"pl", "bs"},
    "Long Term Debt / Equity (inclusive q/e)": {"bs"},
    "Total Debt / Equity(inclusive q/e)": {"bs"},
    "Interest Coverage": {"pl"},
    "Current Ratio": {"bs"},
    "DSCR": {"pl", "bs"},
    "Debtor Days": {"pl", "bs"},
    "Creditor Days": {"pl", "bs"},
}
# One value per statement, as FinSight's sections_read: taxonomy.compute sets every key of
# an unread statement to None together.
_PL_PROBE, _BS_PROBE = "sales_other_income", "total_assets"
_STATEMENT_NAME = {"pl": "profit and loss statement", "bs": "balance sheet"}

RATIO_NOTE = "a standard ratio derived from this year's statements"
DSCR_WITHHELD = "DSCR is not shown: the formula assumes asset financed loans are repaid over four years"
UNKNOWN_RATIO = "not in the MCP's ratio table"
NOT_COMPUTED = "the engine could not compute it this year"


def normalise(columns: list, flags: list, summary: dict, attention: list,
              formulas: Dict[str, str], labels: Dict[str, str]) -> dict:
    return {"columns": columns, "flags": flags, "summary": summary, "attention": attention,
            "formulas": formulas, "labels": labels}


def sections_read(values: dict) -> Set[str]:
    read = set()
    if values.get(_PL_PROBE) is not None:
        read.add("pl")
    if values.get(_BS_PROBE) is not None:
        read.add("bs")
    return read


def _ratios(col: dict, formulas: Dict[str, str]):
    values = col.get("values") or {}
    read = sections_read(values)
    published, withheld = {}, {}
    for name, value in (col.get("ratios") or {}).items():
        needed = RATIO_INPUTS.get(name)
        if name == "DSCR":
            withheld[name] = DSCR_WITHHELD
        elif needed is None:
            withheld[name] = UNKNOWN_RATIO
        elif not needed <= read:
            missing = " and the ".join(_STATEMENT_NAME[s] for s in sorted(needed - read, reverse=True))
            withheld[name] = f"needs the {missing}, which was not read this year"
        elif value is None:
            withheld[name] = NOT_COMPUTED
        else:
            published[name] = {"value": value, "formula": formulas.get(name), "note": RATIO_NOTE}
    return published, withheld


def _year(col: dict, doc: dict, unmasked: bool) -> Dict[str, Any]:
    ident = col.get("identity") or {}
    labels = doc.get("labels") or {}
    values = col.get("values") or {}
    read = sections_read(values)
    published, withheld = _ratios(col, doc.get("formulas") or {})
    return {
        "year": col.get("year"),
        "financial_year": f"FY{col.get('year')}" if col.get("year") else None,
        "provisional": col.get("provisional"),
        "entity": col.get("entity"),
        "form": ident.get("form"),
        "assessment_year": ident.get("ay"),
        "filing_date": ident.get("filing_date"),
        "pan": ident.get("pan") if unmasked else mask_tail(ident.get("pan")),
        "source_file": col.get("source_name"),
        "scanned": col.get("scanned"),
        "statements_read": {"profit_and_loss": "pl" in read, "balance_sheet": "bs" in read},
        "statements": [{"kind": b.get("kind"), "status": b.get("status"), "page": b.get("page")}
                       for b in col.get("blocks_used") or []],
        "checks": [{"name": c.get("name"), "status": c.get("status"), "detail": c.get("detail")}
                   for c in col.get("checks") or []],
        "mapping_check": col.get("mapping_check"),
        "values": {k: {"label": labels.get(k, k), "value": v, "read": v is not None} for k, v in values.items()},
        "ratios": published,
        "ratios_withheld": withheld,
        "warnings": col.get("warnings") or [],
    }


def limitations(doc: dict) -> List[str]:
    out = []
    for col in doc.get("columns") or []:
        read = sections_read(col.get("values") or {})
        for s in ("pl", "bs"):
            if s not in read:
                out.append(f"FY{col.get('year')}: the {_STATEMENT_NAME[s]} was not read; its figures are "
                           "unknown, not zero")
        unmapped = (col.get("mapping_check") or {}).get("unmapped_count")
        if unmapped:
            out.append(f"FY{col.get('year')}: {unmapped} label(s) could not be classified (Gemini is switched "
                       "off in this server); their amounts are not in the figures")
    out.append("DSCR is not shown: the engine's formula assumes term debt is repaid over four years")
    return out


def envelope(doc: dict, unmasked: bool) -> Dict[str, Any]:
    return {
        "document_id": doc.get("document_id"),
        "document_type": "itr",
        "engine": ENGINE,
        "source_files": doc.get("source_files", []),
        "amounts_unit": "INR, rupees as read (the engine's own Excel shows lakhs)",
        "limitations": limitations(doc),
        "masking": "off" if unmasked else "on",
    }


def _masked(doc: dict, out: Any, unmasked: bool) -> Any:
    if unmasked:
        return out
    pans = {str((c.get("identity") or {}).get("pan")) for c in doc.get("columns") or []
            if (c.get("identity") or {}).get("pan")}
    return scrub(out, {p: mask_tail(p) for p in pans})


def financials(doc: dict, unmasked: bool = False) -> Dict[str, Any]:
    summary = doc.get("summary") or {}
    out = {
        **envelope(doc, unmasked),
        "years": [_year(c, doc, unmasked) for c in doc.get("columns") or []],
        "flags": doc.get("flags") or [],
        "revenue_cagr": summary.get("revenue_cagr"),
        "attention": doc.get("attention") or [],
    }
    return _masked(doc, out, unmasked)


def loaded(doc: dict, unmasked: bool = False) -> Dict[str, Any]:
    """What load_itr answers: which years were found and whether each one's statements were
    read. A reading still going on in the background is answered as it is."""
    if doc.get("status") == "reading":
        return doc
    cols = doc.get("columns") or []
    out = {
        **envelope(doc, unmasked),
        "from_cache": doc.get("from_cache"),
        "load_seconds": doc.get("load_seconds"),
        "entity": next((c.get("entity") for c in cols if c.get("entity")), None),
        "years": [{
            "year": c.get("year"),
            # An ITR-1 carries no P&L or balance sheet at all; the engine's warning says so.
            "form": (c.get("identity") or {}).get("form"),
            "warnings": c.get("warnings") or [],
            "statements_read": {"profit_and_loss": "pl" in sections_read(c.get("values") or {}),
                                "balance_sheet": "bs" in sections_read(c.get("values") or {})},
            "statements": [{"kind": b.get("kind"), "status": b.get("status")} for b in c.get("blocks_used") or []],
        } for c in cols],
        "workbook": doc.get("workbook"),
    }
    return _masked(doc, out, unmasked)
