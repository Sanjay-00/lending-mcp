"""
Questions over one bank statement that Bank Statement Analyser read: pure functions, no I/O.

`doc` is what lending_mcp.loader stores: the engine's AnalysisResult made JSON safe, without
its `score` (the engine's internal FOIR, DSCR and cash flow score, which this server never
exposes), plus the EMI series, merge details for several files, and `months`, each month
judged complete, partial or doubtful.

A month counts as complete only when the transactions span it (the engine's own edge test),
the statement's printed period covers it, and none of the engine's continuity checks or an
undecided row falls in it. Averages use complete months only. Credits are credits, never
income.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from lending_mcp.masking import mask_tail, scrub

ENGINE = "Bank Statement Analyser"
SIGNAL_NOTE = "an attention signal from the engine's checks; not a conclusion"
SALARY_NOTE = ("salary like credits detected by the engine (recurring credits matching salary patterns); "
               "not verified income")
CREDITS_NOTE = ("credits, not income: net credits leave out the engine's own transfers, loan disbursals, "
                "reversals and bounce returns")
NO_PERIOD = ("statement period not printed; completeness judged from transaction dates and "
             "continuity checks only")

R_SPAN = "transactions do not span the month"
R_PERIOD = "the statement's printed period does not cover the whole month"
R_PAGE = "page boundary break: a page may be missing"
R_CHAIN = "gap between merged statements"
R_NOT_COVERED = "transactions do not cover the stated period"
R_UNRESOLVED = "rows whose debit or credit could not be decided"
R_UNPLACED = "a continuity check could not be placed in time"
R_BALANCE_ONLY = "rows read with a balance but no debit or credit"


def balance_only_rows(doc: dict) -> List[dict]:
    """Rows the engine marked OPENING after the first. A statement has at most one opening
    row (a merge keeps only the earliest file's), and the engine marks any row with a single
    amount OPENING: on a real statement whose amounts fell into the narration, 862
    transactions lost their amount this way and no engine check noticed."""
    openings = [t for t in doc.get("transactions") or [] if t.get("status") == "OPENING"]
    return openings[1:]


class QueryError(ValueError):
    """A question this statement cannot answer as asked; the message says why."""


# --- normalising (at load time) -----------------------------------------------------------


def normalise(result: dict, emi: list, merge: Optional[dict], engine_month_complete: Dict[str, bool]) -> dict:
    doc = {k: v for k, v in result.items() if k != "score"}
    doc["emi"] = emi
    doc["merge"] = merge
    doc["months"] = _months(doc, engine_month_complete)
    return doc


def _iso(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _bounds(month: str) -> Tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _touched(month: str, start: date, end: date) -> bool:
    first, last = _bounds(month)
    return start <= last and end >= first


def _signal_spans(signal: dict) -> Optional[List[Tuple[date, date, str]]]:
    """(start, end, reason) for each place a continuity signal points at, or None when its
    dates cannot be read."""
    code = signal.get("code")
    if code == "PAGE_BOUNDARY_BREAK":
        items, keys, reason = signal.get("breaks"), ("date_before", "date_after"), lambda i: R_PAGE
    elif code == "DATE_GAP":
        items, keys = signal.get("gaps"), ("from", "to")
        reason = lambda i: f"no transactions for {i.get('days')} days"  # noqa: E731
    elif code == "STATEMENT_CHAIN_BREAK":
        items, keys, reason = signal.get("instances"), ("Previous file ends", "Next file starts"), lambda i: R_CHAIN
    else:
        return []
    if not items:
        return None
    spans = []
    for item in items:
        start, end = _iso(item.get(keys[0])), _iso(item.get(keys[1]))
        if start is None or end is None:
            return None
        spans.append((min(start, end), max(start, end), reason(item)))
    return spans


def _months(doc: dict, engine_complete: Dict[str, bool]) -> List[dict]:
    keys = sorted(doc.get("monthly") or {})
    reasons: Dict[str, List[str]] = {k: [] for k in keys}
    doubt: Dict[str, bool] = {k: False for k in keys}

    def add(month, reason, doubtful):
        if reason not in reasons[month]:
            reasons[month].append(reason)
        doubt[month] = doubt[month] or doubtful

    period = (doc.get("statement_check") or {}).get("period")
    p_start, p_end = (_iso(period[0]), _iso(period[1])) if period else (None, None)
    for k in keys:
        if not engine_complete.get(k, False):
            add(k, R_SPAN, False)
        first, last = _bounds(k)
        if p_start and p_end and not (p_start <= first and p_end >= last):
            add(k, R_PERIOD, False)

    signals = list(doc.get("fraud_signals") or []) + list(doc.get("red_flags") or [])
    for signal in signals:
        if signal.get("code") == "PERIOD_NOT_COVERED":
            for k in keys[:1] + keys[-1:]:
                add(k, R_NOT_COVERED, True)
            continue
        spans = _signal_spans(signal)
        if spans is None:
            for k in keys:
                add(k, R_UNPLACED, True)
            continue
        for start, end, reason in spans:
            for k in keys:
                if _touched(k, start, end):
                    add(k, reason, True)

    for t in doc.get("transactions") or []:
        if t.get("unresolved_amount") is None:
            continue
        when = _iso(t.get("date"))
        for k in keys:
            if when is None or k == when.strftime("%Y-%m"):
                add(k, R_UNPLACED if when is None else R_UNRESOLVED, True)

    for t in balance_only_rows(doc):
        when = _iso(t.get("date"))
        for k in keys:
            if when is None or k == when.strftime("%Y-%m"):
                add(k, R_BALANCE_ONLY, True)

    out = []
    for k in keys:
        m = doc["monthly"][k]
        status = "doubtful" if doubt[k] else ("partial" if reasons[k] else "complete")
        out.append({
            "month": k, "credits": m.get("credit"), "debits": m.get("debit"),
            "credit_count": m.get("credit_count"), "debit_count": m.get("debit_count"),
            "net": m.get("net"), "status": status, "reasons": reasons[k],
        })
    return out


# --- answering ----------------------------------------------------------------------------


def _masked(doc: dict, out: Any, unmasked: bool) -> Any:
    number = doc.get("account_number")
    if unmasked or not number:
        return out
    return scrub(out, {str(number): mask_tail(number)})


def limitations(doc: dict) -> List[str]:
    out = []
    if doc.get("scanned_pages"):
        out.append(f"{doc['scanned_pages']} scanned page(s) were not read: Vision is switched off in this server")
    if doc.get("vlm_recommended"):
        out.append("the engine suggests a closer read of pages where OCR was thin; Vision is switched off "
                   "in this server")
    s = doc.get("summary") or {}
    if s.get("unresolved_count"):
        out.append(f"{s['unresolved_count']} row(s) worth Rs.{float(s.get('unresolved_amount') or 0):,.2f} could "
                   "not be read as debit or credit; they are in neither total")
    extra = balance_only_rows(doc)
    if extra:
        out.append(f"{len(extra)} row(s) carried only a balance: the engine read no debit or credit on them and "
                   "treated them as opening balance lines, so their amounts are in neither total. Read the "
                   "totals of their months with care")
    if not (doc.get("statement_check") or {}).get("period"):
        out.append(NO_PERIOD)
    not_complete = [m["month"] for m in doc.get("months", []) if m["status"] != "complete"]
    if not_complete:
        out.append("months not counted in averages (partial or doubtful): " + ", ".join(not_complete))
    return out


def envelope(doc: dict, unmasked: bool) -> Dict[str, Any]:
    return {
        "document_id": doc.get("document_id"),
        "document_type": "bank",
        "engine": ENGINE,
        "source_files": doc.get("source_files", []),
        "amounts_unit": "INR, rupees as printed",
        "limitations": limitations(doc),
        "masking": "off" if unmasked else "on",
    }


def _dated(doc: dict) -> List[str]:
    return sorted(t["date"] for t in doc.get("transactions") or [] if t.get("date") and t.get("status") != "OPENING")


def averages(doc: dict) -> Dict[str, Any]:
    complete = [m for m in doc.get("months", []) if m["status"] == "complete"]
    if not complete:
        return {"monthly_credit": None, "monthly_debit": None, "months_used": [], "reason": "no complete month"}
    credits = [m["credits"] for m in complete if m["credits"] is not None]
    debits = [m["debits"] for m in complete if m["debits"] is not None]
    return {
        "monthly_credit": round(sum(credits) / len(credits), 2) if len(credits) == len(complete) else None,
        "monthly_debit": round(sum(debits) / len(debits), 2) if len(debits) == len(complete) else None,
        "months_used": [m["month"] for m in complete],
    }


def loaded(doc: dict, unmasked: bool = False) -> Dict[str, Any]:
    """What load_bank_statement answers: enough to know the right statement was read. A
    reading still going on in the background is answered as it is."""
    if doc.get("status") == "reading":
        return doc
    s = doc.get("summary") or {}
    counts = s.get("counts") or {}
    rows = sum(v for k, v in counts.items() if k != "OPENING")
    dates = _dated(doc)
    out = {
        **envelope(doc, unmasked),
        "from_cache": doc.get("from_cache"),
        "statement": {
            "bank": doc.get("bank_name"),
            "account_type": doc.get("account_type"),
            "account_number": doc.get("account_number") if unmasked else mask_tail(doc.get("account_number")),
            "first_transaction": dates[0] if dates else None,
            "last_transaction": dates[-1] if dates else None,
        },
        "transactions": s.get("transaction_count"),
        "rows_verified": f"{counts.get('VERIFIED', 0)} of {rows}",
        "months_by_status": {st: [m["month"] for m in doc.get("months", []) if m["status"] == st]
                             for st in ("complete", "partial", "doubtful")},
        "merge": doc.get("merge"),
    }
    return _masked(doc, out, unmasked)


DIRECTIONS = ("any", "credit", "debit")
SORTS = ("date", "amount")
STATUSES = ("verified", "failed", "unverified")


def _amount(t: dict) -> Optional[float]:
    if t.get("credit") is not None:
        return float(t["credit"])
    if t.get("debit") is not None:
        return float(t["debit"])
    return None


def _direction(t: dict) -> Optional[str]:
    if t.get("credit") is not None:
        return "credit"
    if t.get("debit") is not None:
        return "debit"
    return None


def _date_arg(name: str, value: Optional[str]) -> Optional[date]:
    if value is None:
        return None
    parsed = _iso(value) if len(str(value)) == 10 else None
    if parsed is None:
        raise QueryError(f"{name} must be a date as YYYY-MM-DD.")
    return parsed


def find_transactions(
    doc: dict, unmasked: bool = False, *,
    date_from: Optional[str] = None, date_to: Optional[str] = None, direction: str = "any",
    min_amount: Optional[float] = None, max_amount: Optional[float] = None,
    narration_contains: Optional[str] = None, category: Optional[str] = None, status: Optional[str] = None,
    sort_by: str = "date", descending: bool = False, limit: int = 50, offset: int = 0,
) -> Dict[str, Any]:
    """Transactions matching every filter given, one page of them, and totals over all
    matches. A row whose debit or credit could not be decided has no known amount or
    direction: it never matches an amount or direction filter and is never summed."""
    start, end = _date_arg("date_from", date_from), _date_arg("date_to", date_to)
    direction = (direction or "any").lower()
    if direction not in DIRECTIONS:
        raise QueryError("direction must be one of: " + ", ".join(DIRECTIONS) + ".")
    if sort_by not in SORTS:
        raise QueryError("sort_by must be one of: " + ", ".join(SORTS) + ".")
    if status is not None and status.lower() not in STATUSES:
        raise QueryError("status must be one of: " + ", ".join(STATUSES) + ".")
    if not 1 <= limit <= 200:
        raise QueryError("limit must be 1 to 200.")
    if offset < 0:
        raise QueryError("offset must be 0 or more.")
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise QueryError("min_amount is more than max_amount.")

    needs_amount = direction != "any" or min_amount is not None or max_amount is not None
    needs_date = start is not None or end is not None
    matches, unknown = [], []
    for t in doc.get("transactions") or []:
        if t.get("status") == "OPENING":
            continue
        if status is not None and str(t.get("status", "")).lower() != status.lower():
            continue
        if narration_contains and narration_contains.lower() not in str(t.get("narration") or "").lower():
            continue
        if category and str(t.get("category") or "").lower() != category.lower():
            continue
        when, amount = _iso(t.get("date")), _amount(t)
        if (needs_date and when is None) or (needs_amount and amount is None):
            unknown.append(t)
            continue
        if (start and when < start) or (end and when > end):
            continue
        if direction != "any" and _direction(t) != direction:
            continue
        if (min_amount is not None and amount < min_amount) or (max_amount is not None and amount > max_amount):
            continue
        matches.append(t)

    if sort_by == "amount":
        known = sorted((t for t in matches if _amount(t) is not None), key=_amount, reverse=descending)
        matches = known + [t for t in matches if _amount(t) is None]
    else:
        known = sorted((t for t in matches if t.get("date")), key=lambda t: t["date"], reverse=descending)
        matches = known + [t for t in matches if not t.get("date")]

    credits = [t["credit"] for t in matches if t.get("credit") is not None]
    debits = [t["debit"] for t in matches if t.get("debit") is not None]
    out = {
        **envelope(doc, unmasked),
        "total_matches": len(matches),
        "has_more": offset + limit < len(matches),
        "transactions": matches[offset:offset + limit],
        "totals": {
            "credit_count": len(credits), "credit_sum": round(sum(credits), 2),
            "debit_count": len(debits), "debit_sum": round(sum(debits), 2),
        },
        "excluded_because_value_unknown": {"count": len(unknown), "dates": [t.get("date") for t in unknown]},
    }
    return _masked(doc, out, unmasked)


def summary(doc: dict, unmasked: bool = False) -> Dict[str, Any]:
    s = doc.get("summary") or {}
    dates = _dated(doc)
    out = {
        **envelope(doc, unmasked),
        "statement": {
            "bank": doc.get("bank_name"),
            "account_type": doc.get("account_type"),
            "account_number": doc.get("account_number") if unmasked else mask_tail(doc.get("account_number")),
            "account_holder": doc.get("account_holder"),
            "first_transaction": dates[0] if dates else None,
            "last_transaction": dates[-1] if dates else None,
            "printed_period": (doc.get("statement_check") or {}).get("period"),
            "pages": doc.get("page_count"),
            "scanned_pages": doc.get("scanned_pages"),
        },
        "reading": {
            "rows_by_status": s.get("counts"),
            "unresolved_count": s.get("unresolved_count"),
            "unresolved_amount": s.get("unresolved_amount"),
            "dates_inferred": s.get("dates_inferred"),
            "statement_check": doc.get("statement_check"),
            "ledger_notes": doc.get("ledger_notes"),
        },
        "totals": {
            "opening_balance": s.get("opening_balance"),
            "closing_balance": s.get("closing_balance"),
            "total_credits": s.get("total_credit"),
            "total_debits": s.get("total_debit"),
            "transaction_count": s.get("transaction_count"),
        },
        "months": doc.get("months", []),
        "averages": averages(doc),
        "average_balance": doc.get("abb"),
        "credits": {**(doc.get("credits") or {}), "note": CREDITS_NOTE},
        "bounces": doc.get("bounces"),
        "emi_debits_seen": doc.get("emi"),
        "salary_like_credits": {**(doc.get("salary") or {}), "note": SALARY_NOTE},
        "cash_deposit_share": (doc.get("cashflow") or {}).get("cash_dependency_ratio"),
        "cashflow": doc.get("cashflow"),
        "category_totals": doc.get("category_summary"),
        "overdraft": doc.get("overdraft"),
        "signals": [{**sig, "note": SIGNAL_NOTE}
                    for sig in list(doc.get("red_flags") or []) + list(doc.get("fraud_signals") or [])],
        "merge": doc.get("merge"),
    }
    return _masked(doc, out, unmasked)
