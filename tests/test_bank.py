import copy
import json

import pytest

from lending_mcp import bank
from tests.fixtures_bank import ACCOUNT, EMI, ENGINE_COMPLETE, STATEMENT, statement, with_signal


def doc_of(result=None, complete=None, merge=None):
    d = bank.normalise(result or copy.deepcopy(STATEMENT), EMI, merge, complete or dict(ENGINE_COMPLETE))
    d.update({"document_id": "bank-abc-12345678", "source_files": ["statement.pdf"]})
    return d


def months(d):
    return {m["month"]: m for m in d["months"]}


# --- what is stored ------------------------------------------------------------------------


def test_score_is_dropped_at_normalise():
    d = doc_of()
    assert "score" not in d
    text = json.dumps(bank.summary(d, unmasked=True)).lower()
    for word in ("foir", "dscr", "score"):
        assert word not in text


# --- month completeness --------------------------------------------------------------------


def test_month_partial_when_transactions_do_not_span():
    m = months(doc_of())
    assert m["2025-01"]["status"] == "complete" and m["2025-02"]["status"] == "complete"
    assert m["2025-03"]["status"] == "partial"
    assert "transactions do not span the month" in m["2025-03"]["reasons"]


def test_month_partial_when_printed_period_short():
    s = statement(statement_check={"period": ["2025-01-01", "2025-02-20"]})
    m = months(doc_of(s))
    assert m["2025-01"]["status"] == "complete"
    assert m["2025-02"]["status"] == "partial"
    assert "the statement's printed period does not cover the whole month" in m["2025-02"]["reasons"]


@pytest.mark.parametrize("signal,month,reason", [
    ({"code": "PAGE_BOUNDARY_BREAK", "severity": "MEDIUM", "detail": "d",
      "breaks": [{"after_page": 1, "page": 2, "date_before": "2025-02-10", "date_after": "2025-02-12"}]},
     "2025-02", "page boundary break: a page may be missing"),
    ({"code": "DATE_GAP", "severity": "LOW", "detail": "d",
      "gaps": [{"from": "2025-01-02", "to": "2025-02-14", "days": 43, "at_page_break": False}]},
     "2025-01", "no transactions for 43 days"),
    ({"code": "STATEMENT_CHAIN_BREAK", "severity": "MEDIUM", "detail": "d",
      "instances": [{"Previous file ends": "2025-01-30", "Next file starts": "2025-02-01"}]},
     "2025-02", "gap between merged statements"),
    ({"code": "PERIOD_NOT_COVERED", "severity": "MEDIUM", "detail": "d"},
     "2025-01", "transactions do not cover the stated period"),
])
def test_month_doubtful_for_each_signal(signal, month, reason):
    m = months(doc_of(with_signal(STATEMENT, signal)))
    assert m[month]["status"] == "doubtful"
    assert reason in m[month]["reasons"]


def test_date_gap_marks_every_month_it_touches():
    gap = {"code": "DATE_GAP", "severity": "LOW", "detail": "d",
           "gaps": [{"from": "2025-01-02", "to": "2025-02-14", "days": 43}]}
    m = months(doc_of(with_signal(STATEMENT, gap)))
    assert m["2025-01"]["status"] == m["2025-02"]["status"] == "doubtful"


def test_period_not_covered_marks_first_and_last_month():
    m = months(doc_of(with_signal(STATEMENT, {"code": "PERIOD_NOT_COVERED", "severity": "MEDIUM", "detail": "d"})))
    assert "transactions do not cover the stated period" in m["2025-01"]["reasons"]
    assert "transactions do not cover the stated period" in m["2025-03"]["reasons"]
    assert m["2025-02"]["status"] == "complete"


def test_month_doubtful_for_unresolved_row():
    s = copy.deepcopy(STATEMENT)
    s["transactions"][4].update(debit=None, credit=None, unresolved_amount=2000.0, direction_source=None)
    m = months(doc_of(s))
    assert m["2025-02"]["status"] == "doubtful"
    assert "rows whose debit or credit could not be decided" in m["2025-02"]["reasons"]


def test_unplaceable_signal_marks_every_month_doubtful():
    s = with_signal(STATEMENT, {"code": "DATE_GAP", "severity": "LOW", "detail": "d", "gaps": [{"days": 40}]})
    for m in doc_of(s)["months"]:
        assert m["status"] == "doubtful"
        assert "a continuity check could not be placed in time" in m["reasons"]


# --- averages -----------------------------------------------------------------------------


def test_average_over_complete_months_only():
    avg = bank.summary(doc_of(), unmasked=False)["averages"]
    assert avg["months_used"] == ["2025-01", "2025-02"]
    assert avg["monthly_credit"] == 40000.0
    assert avg["monthly_debit"] == 11500.0


def test_no_complete_month_gives_no_average():
    avg = bank.summary(doc_of(complete={k: False for k in ENGINE_COMPLETE}), unmasked=False)["averages"]
    assert avg["monthly_credit"] is None and avg["monthly_debit"] is None
    assert avg["months_used"] == [] and avg["reason"] == "no complete month"


def test_missing_period_adds_limitation():
    out = bank.summary(doc_of(statement(statement_check=None)), unmasked=False)
    assert ("statement period not printed; completeness judged from transaction dates and "
            "continuity checks only") in out["limitations"]


# --- naming and evidence -------------------------------------------------------------------


def test_salary_named_salary_like_credits_with_note():
    out = bank.summary(doc_of(), unmasked=False)
    assert "salary" not in out
    assert out["salary_like_credits"]["average_amount"] == 40000.0
    assert out["salary_like_credits"]["note"] == (
        "salary like credits detected by the engine (recurring credits matching salary patterns); "
        "not verified income")


def test_signals_keep_full_evidence_and_note():
    out = bank.summary(doc_of(), unmasked=False)
    codes = {s["code"]: s for s in out["signals"]}
    assert set(codes) == {"EMI_BOUNCE", "METADATA_EDITED"}
    assert codes["EMI_BOUNCE"]["instances"] == STATEMENT["red_flags"][0]["instances"]
    assert all(s["note"] == "an attention signal from the engine's checks; not a conclusion" for s in out["signals"])


def test_limitations_name_scanned_pages_vlm_and_unresolved():
    s = statement(scanned_pages=2, vlm_recommended=True)
    s["summary"] = dict(s["summary"], unresolved_count=1, unresolved_amount=2000.0)
    lims = bank.summary(doc_of(s), unmasked=False)["limitations"]
    assert "2 scanned page(s) were not read: Vision is switched off in this server" in lims
    assert ("the engine suggests a closer read of pages where OCR was thin; Vision is switched off "
            "in this server") in lims
    assert "1 row(s) worth Rs.2,000.00 could not be read as debit or credit; they are in neither total" in lims


def test_envelope():
    out = bank.summary(doc_of(), unmasked=False)
    assert out["document_type"] == "bank" and out["engine"] == "Bank Statement Analyser"
    assert out["document_id"] == "bank-abc-12345678" and out["masking"] == "on"
    assert isinstance(out["limitations"], list)


# --- masking ------------------------------------------------------------------------------


def test_account_number_masked_everywhere_including_narration():
    out = bank.summary(doc_of(), unmasked=False)
    assert ACCOUNT not in json.dumps(out)
    assert out["statement"]["account_number"] == "**********6789"


def test_unmasked_shows_account_number():
    out = bank.summary(doc_of(), unmasked=True)
    assert out["statement"]["account_number"] == ACCOUNT and out["masking"] == "off"


# --- finding transactions (Task 4) -----------------------------------------------------------


def find(**kw):
    d = kw.pop("doc", None) or doc_of()
    return bank.find_transactions(d, kw.pop("unmasked", False), **kw)


def dates(result):
    return [t["date"] for t in result["transactions"]]


@pytest.mark.parametrize("kwargs,expected", [
    ({"date_from": "2025-02-01", "date_to": "2025-02-28"}, ["2025-02-01", "2025-02-14", "2025-02-27"]),
    ({"direction": "credit"}, ["2025-01-15", "2025-02-14", "2025-03-20"]),
    ({"direction": "debit", "min_amount": 8000}, ["2025-01-30", "2025-02-27", "2025-03-03"]),
    ({"max_amount": 2000}, ["2025-02-01"]),
    ({"narration_contains": "payroll"}, ["2025-01-15", "2025-02-14"]),
    ({"category": "EMI"}, ["2025-01-30", "2025-02-27"]),
    ({"status": "verified", "date_to": "2025-01-31"}, ["2025-01-02", "2025-01-15", "2025-01-30"]),
])
def test_find_each_filter(kwargs, expected):
    assert dates(find(**kwargs)) == expected


def test_find_never_returns_opening_rows():
    assert "2025-01-01" not in dates(find())
    assert find()["total_matches"] == 8


def test_find_combined():
    assert dates(find(direction="debit", narration_contains="loan", date_from="2025-02-01")) == ["2025-02-27"]


def test_find_totals_cover_all_matches_not_page():
    r = find(limit=1)
    assert len(r["transactions"]) == 1 and r["has_more"] is True and r["total_matches"] == 8
    assert r["totals"] == {"credit_count": 3, "credit_sum": 86000.0, "debit_count": 5, "debit_sum": 33000.0}


def test_find_never_sums_unknown():
    s = copy.deepcopy(STATEMENT)
    s["transactions"][4].update(debit=None, credit=None, unresolved_amount=2000.0, direction_source=None)
    d = doc_of(s)
    everything = find(doc=d)
    assert everything["totals"]["debit_sum"] == 31000.0  # the undecided 2,000 is in neither total
    assert everything["excluded_because_value_unknown"]["count"] == 0  # no amount filter: listed, not excluded
    filtered = find(doc=d, min_amount=1)
    assert "2025-02-01" not in dates(filtered)
    assert filtered["excluded_because_value_unknown"] == {"count": 1, "dates": ["2025-02-01"]}


def test_find_sort_and_paging():
    by_amount = find(sort_by="amount", descending=True)
    assert dates(by_amount)[:2] == ["2025-01-15", "2025-02-14"]
    page2 = find(limit=3, offset=3)
    assert dates(page2) == ["2025-02-01", "2025-02-14", "2025-02-27"] and page2["has_more"] is True
    assert find(limit=3, offset=6)["has_more"] is False


@pytest.mark.parametrize("kwargs,message", [
    ({"date_from": "31-01-2025"}, "date_from must be a date as YYYY-MM-DD"),
    ({"limit": 0}, "limit must be 1 to 200"),
    ({"limit": 201}, "limit must be 1 to 200"),
    ({"offset": -1}, "offset must be 0 or more"),
    ({"direction": "sideways"}, "direction must be"),
    ({"sort_by": "colour"}, "sort_by must be"),
    ({"status": "maybe"}, "status must be"),
    ({"min_amount": 10, "max_amount": 5}, "min_amount is more than max_amount"),
])
def test_find_refuses_bad_arguments(kwargs, message):
    with pytest.raises(bank.QueryError, match=message):
        find(**kwargs)


def test_find_masks_account_number_in_narration():
    masked = find(narration_contains="own a/c")
    assert ACCOUNT not in json.dumps(masked)
    assert ACCOUNT in json.dumps(find(narration_contains="own a/c", unmasked=True))


# --- balance-only rows (batch run on real files, 2026-11-02) ------------------------------
# On one real statement the amount column landed in the narration, so 862 transactions kept
# only their balance and the engine took each for an opening balance line. A statement has
# at most one opening row; every further one is a transaction whose amount was not read.


def _with_balance_only_rows(n, day="2025-02-05"):
    s = copy.deepcopy(STATEMENT)
    for i in range(n):
        s["transactions"].insert(5, {**s["transactions"][0], "date": day, "narration": f"UPI/DR/{i}",
                                     "balance": 30000.0 + i})
    s["summary"]["counts"]["OPENING"] = 1 + n
    return s


def test_balance_only_rows_make_their_month_doubtful():
    m = months(doc_of(_with_balance_only_rows(2)))
    assert m["2025-01"]["status"] == "complete"  # the real opening row stays harmless
    assert m["2025-02"]["status"] == "doubtful"
    assert bank.R_BALANCE_ONLY in m["2025-02"]["reasons"]


def test_balance_only_rows_are_a_limitation():
    d = doc_of(_with_balance_only_rows(3))
    assert any(l.startswith("3 row(s) carried only a balance") for l in bank.limitations(d))
    assert not any("only a balance" in l for l in bank.limitations(doc_of()))
