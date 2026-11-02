"""
Questions over one parsed bureau report: pure functions, no I/O.

`report` is what lending_mcp.loader stores: {"report_id", "source_file",
"loaded_at", "parsed"}, where "parsed" is AutoCAM's own parse() result,
unchanged. Everything here reads that dict; nothing reparses a PDF.

Two rules hold in every answer:

  Missing is not zero. AutoCAM leaves a value it could not read as None
  (its Excel prints "Check CIBIL"). A None is never summed, compared or
  counted as 0: sums report the known total plus which accounts were
  unknown, and a DPD filter lists the accounts it could not judge.

  False is not unavailable. CRIF Retail reports carry no settled, suit
  filed or delinquent flag at all. Those read "not provided by this report
  format", never False, and filtering on one is refused, not answered with
  an empty list.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from lending_mcp.masking import mask_dob, mask_tail

PROVIDER_NAMES = {
    "crif": "CRIF High Mark, retail report",
    "crif_commercial": "CRIF High Mark, Commercial ACE report",
}
NOT_PROVIDED = "not provided by this report format"

FLAGS = ("written_off", "settled", "suit_filed", "delinquent")
_PROVIDER_FLAGS = {
    "crif": {"written_off"},
    "crif_commercial": set(FLAGS),
}
_PROVIDER_STATUSES = {
    "crif": {"active", "closed"},
    "crif_commercial": {"active", "closed", "written off", "settled"},
}
_STATUS_ALIASES = {
    "active": "active", "closed": "closed", "settled": "settled",
    "written_off": "written off", "written off": "written off", "writtenoff": "written off",
    "any": None, "all": None,
}

# Output name -> AutoCAM account key.
AMOUNTS = {
    "sanction_amount": "sanction_amount",
    "outstanding": "current_balance",
    "emi": "emi",
    "overdue": "overdue",
}
DPD_WINDOWS = {
    "ever": ("max_dpd_ever", "max_dpd"),
    "12m": ("max_dpd_12m", "max_dpd_12mo"),
    "last_reported": ("last_reported_dpd", "last_reported_dpd"),
}
SORT_FIELDS = (
    "sr_no", "date_of_sanction", "sanction_amount", "outstanding", "emi", "overdue",
    "max_dpd_ever", "max_dpd_12m", "last_reported_dpd",
)
FIELD_LABELS = {
    "sr_no": "Account number as listed in the report (AutoCAM's serial)",
    "lender": "Lender (credit institution) reporting the account",
    "loan_type": "Type of loan",
    "ownership": "Ownership: individual, joint, guarantor and so on",
    "status": "Account status as read by AutoCAM",
    "status_printed_on_report": "The Active/Closed tag printed on the report itself (CRIF Retail)",
    "date_of_sanction": "Sanction or disbursal date",
    "sanction_amount": "Sanctioned amount, rupees",
    "outstanding": "Current outstanding balance, rupees",
    "emi": "Instalment (EMI) amount reported, rupees",
    "overdue": "Amount overdue, rupees",
    "max_dpd_ever": "Worst days past due in the whole payment history shown",
    "max_dpd_12m": "Worst days past due in the last 12 reported months",
    "last_reported_dpd": "Days past due in the latest reported month",
    "dpd_doubtful": "AutoCAM doubts this account's DPD reading (see dpd_note)",
    "dpd_reported_by_bureau": "Whether the bureau reports a DPD history for this account (CRIF Commercial)",
    "written_off": "Written off",
    "settled": "Settled",
    "suit_filed": "Suit filed",
    "delinquent": "Delinquent, as classified by the bureau",
}

_COMMON_LIMITATIONS = [
    "Credit enquiries are not extracted from the report.",
    "The month by month payment history is not extracted; only the worst DPD ever, the worst "
    "DPD in the last 12 months and the last reported DPD are.",
    "Account numbers, closed dates and last payment dates are not extracted.",
    "No page references: every value comes from AutoCAM's parse of the report, not a cited page.",
]


class QueryError(ValueError):
    """A question that cannot be answered as asked; the message says why."""


# --- reading one account -------------------------------------------------------


def provider_of(report: dict) -> str:
    return report["parsed"].get("provider", "")


def status_of(acc: dict) -> str:
    return str(acc.get("status") or "").strip().lower()


def flag_of(acc: dict, provider: str, name: str) -> Optional[bool]:
    """True or False when the format reports the flag; None when it does not,
    or when this account's value could not be read."""
    if name not in _PROVIDER_FLAGS.get(provider, set()):
        return None
    if provider == "crif_commercial" and name in ("written_off", "settled"):
        # Commercial carries these as the canonical status itself.
        return status_of(acc) == ("written off" if name == "written_off" else "settled")
    value = acc.get(name)
    return None if value is None else bool(value)


def dpd_doubtful(acc: dict) -> bool:
    return bool(acc.get("dpd_sources_disagree") or acc.get("dpd_block_contaminated"))


def account_row(acc: dict, provider: str) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "sr_no": acc.get("sr_no"),
        "lender": acc.get("entity"),
        "loan_type": acc.get("type_of_loan"),
        "ownership": acc.get("ownership"),
        "status": acc.get("status"),
        "date_of_sanction": acc.get("date_of_sanction"),
    }
    for out, key in AMOUNTS.items():
        row[out] = acc.get(key)
    for out, key in DPD_WINDOWS.values():
        row[out] = acc.get(key)
    row["dpd_doubtful"] = dpd_doubtful(acc)
    for name in FLAGS:
        if name in _PROVIDER_FLAGS.get(provider, set()):
            row[name] = flag_of(acc, provider, name)
    if provider == "crif" and "status_per_cibil" in acc:
        row["status_printed_on_report"] = acc.get("status_per_cibil")
    if provider == "crif_commercial" and "dpd_reported" in acc:
        row["dpd_reported_by_bureau"] = acc.get("dpd_reported")
    return row


def _value(acc: dict, out_field: str):
    """An account's value by output field name."""
    if out_field in AMOUNTS:
        return acc.get(AMOUNTS[out_field])
    for out, key in DPD_WINDOWS.values():
        if out == out_field:
            return acc.get(key)
    if out_field == "date_of_sanction":
        return _date(acc.get("date_of_sanction"))
    if out_field == "sr_no":
        return acc.get("sr_no")
    raise QueryError(f"Cannot sort by {out_field!r}. Sortable fields: {', '.join(SORT_FIELDS)}.")


def _date(text) -> Optional[datetime]:
    # The formats AutoCAM's own Excel generator accepts.
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(text), fmt)
        except (TypeError, ValueError):
            continue
    return None


# --- aggregates -----------------------------------------------------------------


def amount_total(accounts: List[dict], out_field: str) -> Dict[str, Any]:
    known = [a for a in accounts if a.get(AMOUNTS[out_field]) is not None]
    return {
        "known_total": sum(a[AMOUNTS[out_field]] for a in known),
        "accounts_counted": len(known),
        "accounts_unknown": [a.get("sr_no") for a in accounts if a.get(AMOUNTS[out_field]) is None],
    }


def worst_dpd(accounts: List[dict], window: str) -> Dict[str, Any]:
    _out, key = DPD_WINDOWS[window]
    known = [a for a in accounts if a.get(key) is not None]
    worst = max((a[key] for a in known), default=None)
    return {
        "worst_known": worst,
        "held_by": [a.get("sr_no") for a in known if worst is not None and a[key] == worst],
        "accounts_unknown": [a.get("sr_no") for a in accounts if a.get(key) is None],
        "doubtful_readings": [a.get("sr_no") for a in known if dpd_doubtful(a)],
    }


def _aggregates(accounts: List[dict]) -> Dict[str, Any]:
    return {
        "count": len(accounts),
        "amounts": {f: amount_total(accounts, f) for f in AMOUNTS},
        "worst_dpd": {w: worst_dpd(accounts, w) for w in DPD_WINDOWS},
    }


# --- what every answer carries ----------------------------------------------------


def limitations(report: dict) -> List[str]:
    parsed = report["parsed"]
    provider = provider_of(report)
    out = list(_COMMON_LIMITATIONS)
    if provider == "crif":
        out.append("This report format carries no settled, suit filed or delinquent flag.")
    validation = parsed.get("validation") or {}
    if validation.get("valid") is False:
        issues = "; ".join(str(i) for i in validation.get("issues") or [])
        out.append("AutoCAM's reading does not reconcile with the report's own printed totals"
                   + (f": {issues}" if issues else "") + ". Treat totals with care.")
    if "ocr" in str(parsed.get("extraction_method") or "").lower():
        out.append("The report is scanned and was read by OCR; unreadable values are left unknown.")
    doubtful = [a.get("sr_no") for a in parsed.get("accounts", []) if dpd_doubtful(a)]
    if doubtful:
        which = f"account(s) {doubtful}" if len(doubtful) <= 10 else (
            f"{len(doubtful)} accounts (list them with find_bureau_accounts, dpd_doubtful_only=true)")
        out.append(f"AutoCAM doubts the DPD reading of {which}; confirm them against the report.")
    if parsed.get("dpd_vision_recommended") or parsed.get("vision_fallback_recommended"):
        out.append("Some DPD grids could not be read by rules; AutoCAM's paid Vision re-read is "
                   "switched off in this server.")
    return out


def envelope(report: dict, unmasked: bool) -> Dict[str, Any]:
    parsed = report["parsed"]
    provider = provider_of(report)
    validation = parsed.get("validation") or {}
    return {
        "report_id": report["report_id"],
        "provider": provider,
        "provider_name": PROVIDER_NAMES.get(provider, provider),
        "extraction_method": parsed.get("extraction_method"),
        "reconciles_with_report_totals": validation.get("valid"),
        "amounts_unit": "INR, whole rupees as extracted",
        "limitations": limitations(report),
        "masking": "off" if unmasked else "on",
    }


def identity(report: dict, unmasked: bool) -> Dict[str, Any]:
    ident = report["parsed"].get("identity") or {}
    if unmasked:
        return {k: ident.get(k) for k in ("pan", "dob", "phone")}
    return {"pan": mask_tail(ident.get("pan")), "dob": mask_dob(ident.get("dob")),
            "phone": mask_tail(ident.get("phone"))}


def score(report: dict) -> Dict[str, Any]:
    raw = report["parsed"].get("score")
    if provider_of(report) == "crif_commercial":
        kind = "CRIF commercial rank (1 is the best)"
    elif isinstance(raw, int):
        kind = "credit score"
    else:
        kind = "no score on the report (for example new to credit)"
    return {"value": raw, "kind": kind}


# --- the three questions ------------------------------------------------------------


def summary(report: dict, unmasked: bool = False) -> Dict[str, Any]:
    parsed = report["parsed"]
    provider = provider_of(report)
    accounts = parsed.get("accounts", [])
    active = [a for a in accounts if status_of(a) == "active"]
    flags = {}
    for name in FLAGS:
        if name in _PROVIDER_FLAGS.get(provider, set()):
            flags[name] = [a.get("sr_no") for a in accounts if flag_of(a, provider, name)]
        else:
            flags[name] = NOT_PROVIDED
    return {
        **envelope(report, unmasked),
        "borrower": {"name": parsed.get("name"), "score": score(report), "identity": identity(report, unmasked)},
        "accounts": {
            "total": len(accounts),
            "by_status": dict(Counter(a.get("status") for a in accounts)),
        },
        "active_accounts_totals": {f: amount_total(active, f) for f in AMOUNTS},
        "worst_dpd_all_accounts": {w: worst_dpd(accounts, w) for w in DPD_WINDOWS},
        "accounts_flagged": flags,
        "bureau_analysis": parsed.get("analysis"),
        "validation": parsed.get("validation"),
    }


def _require_flag(provider: str, name: str) -> None:
    if name not in _PROVIDER_FLAGS.get(provider, set()):
        raise QueryError(f"The {name.replace('_', ' ')} filter cannot be applied: {NOT_PROVIDED} "
                         f"({PROVIDER_NAMES.get(provider, provider)}).")


def find_accounts(
    report: dict,
    unmasked: bool = False,
    *,
    status: Optional[str] = None,
    lender: Optional[str] = None,
    loan_type: Optional[str] = None,
    ownership: Optional[str] = None,
    min_dpd: Optional[int] = None,
    dpd_window: str = "ever",
    min_outstanding: Optional[int] = None,
    min_overdue: Optional[int] = None,
    written_off: Optional[bool] = None,
    settled: Optional[bool] = None,
    suit_filed: Optional[bool] = None,
    delinquent: Optional[bool] = None,
    dpd_doubtful_only: Optional[bool] = None,
    sort_by: str = "sr_no",
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    provider = provider_of(report)
    accounts = report["parsed"].get("accounts", [])

    # Validate the question before answering any of it.
    wanted_status = None
    if status is not None:
        key = status.strip().lower()
        if key not in _STATUS_ALIASES:
            raise QueryError(f"Unknown status {status!r}. Use active, closed, written_off, settled or any.")
        wanted_status = _STATUS_ALIASES[key]
        if wanted_status and wanted_status not in _PROVIDER_STATUSES.get(provider, set()):
            raise QueryError(f"Status {wanted_status!r} is {NOT_PROVIDED} "
                             f"({PROVIDER_NAMES.get(provider, provider)}).")
    if dpd_window not in DPD_WINDOWS:
        raise QueryError(f"Unknown dpd_window {dpd_window!r}. Use ever, 12m or last_reported.")
    for name, value in (("min_dpd", min_dpd), ("min_outstanding", min_outstanding), ("min_overdue", min_overdue)):
        if value is not None and value < 0:
            raise QueryError(f"{name} must be 0 or more.")
    flag_filters = {"written_off": written_off, "settled": settled, "suit_filed": suit_filed, "delinquent": delinquent}
    for name, value in flag_filters.items():
        if value is not None:
            _require_flag(provider, name)
    if sort_by not in SORT_FIELDS:
        raise QueryError(f"Cannot sort by {sort_by!r}. Sortable fields: {', '.join(SORT_FIELDS)}.")
    if not 1 <= limit <= 200:
        raise QueryError("limit must be between 1 and 200.")
    if offset < 0:
        raise QueryError("offset must be 0 or more.")

    dpd_key = DPD_WINDOWS[dpd_window][1]
    unknown: Dict[str, List] = {}

    def unknown_for(name: str, acc: dict) -> bool:
        unknown.setdefault(name, []).append(acc.get("sr_no"))
        return False

    def contains(text, needle) -> bool:
        return needle.strip().lower() in str(text or "").lower()

    def keep(acc: dict) -> bool:
        if wanted_status and status_of(acc) != wanted_status:
            return False
        if lender and not contains(acc.get("entity"), lender):
            return False
        if loan_type and not contains(acc.get("type_of_loan"), loan_type):
            return False
        if ownership and not contains(acc.get("ownership"), ownership):
            return False
        for name, value in flag_filters.items():
            if value is None:
                continue
            actual = flag_of(acc, provider, name)
            if actual is None:
                return unknown_for(name, acc)
            if actual != value:
                return False
        if dpd_doubtful_only is not None and dpd_doubtful(acc) != dpd_doubtful_only:
            return False
        for name, key, minimum in (("min_dpd", dpd_key, min_dpd),
                                   ("min_outstanding", "current_balance", min_outstanding),
                                   ("min_overdue", "overdue", min_overdue)):
            if minimum is None:
                continue
            if acc.get(key) is None:
                return unknown_for(name, acc)
            if acc[key] < minimum:
                return False
        return True

    matches = [a for a in accounts if keep(a)]
    present = [a for a in matches if _value(a, sort_by) is not None]
    absent = [a for a in matches if _value(a, sort_by) is None]
    present.sort(key=lambda a: _value(a, sort_by), reverse=descending)
    ordered = present + absent  # unknown values last, whichever the direction

    page = ordered[offset: offset + limit]
    return {
        **envelope(report, unmasked),
        "total_matches": len(matches),
        "offset": offset,
        "returned": len(page),
        "has_more": offset + len(page) < len(matches),
        "accounts": [account_row(a, provider) for a in page],
        "aggregates_over_all_matches": _aggregates(matches),
        "excluded_because_value_unknown": unknown,
        "flags_not_provided": [f for f in FLAGS if f not in _PROVIDER_FLAGS.get(provider, set())],
    }


def account_detail(report: dict, sr_no: int, unmasked: bool = False) -> Dict[str, Any]:
    provider = provider_of(report)
    accounts = report["parsed"].get("accounts", [])
    acc = next((a for a in accounts if a.get("sr_no") == sr_no), None)
    if acc is None:
        numbers = [a.get("sr_no") for a in accounts]
        raise QueryError(f"No account {sr_no} in this report. Accounts are numbered "
                         f"{min(numbers) if numbers else '-'} to {max(numbers) if numbers else '-'}.")
    row = account_row(acc, provider)
    notes = []
    if acc.get("dpd_block_contaminated"):
        notes.append("The DPD grid's text ran into another account's data, so the DPD values were left unknown.")
    if acc.get("dpd_sources_disagree"):
        notes.append("The DPD readings behind the worst DPD disagreed, so it may carry a neighbouring "
                     "account's figure; confirm against the report.")
    if provider == "crif_commercial" and acc.get("dpd_reported") is False:
        notes.append("The bureau reports no DPD history for this account; the DPD values are not reported, not zero.")
    return {
        **envelope(report, unmasked),
        "account": row,
        "labels": {k: FIELD_LABELS[k] for k in row if k in FIELD_LABELS},
        "unknown_fields": [k for k, v in row.items() if v is None],
        "flags_not_provided": [f for f in FLAGS if f not in _PROVIDER_FLAGS.get(provider, set())],
        "dpd_note": notes,
    }
