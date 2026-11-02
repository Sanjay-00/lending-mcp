import pytest

from lending_mcp import bureau
from lending_mcp.bureau import NOT_PROVIDED, QueryError
from tests.fixtures import COMMERCIAL, RETAIL, report


@pytest.fixture
def retail():
    return report(RETAIL)


@pytest.fixture
def commercial():
    return report(COMMERCIAL)


def srs(result):
    return [a["sr_no"] for a in result["accounts"]]


# --- summary ---------------------------------------------------------------------


def test_summary_totals_count_only_known_values(retail):
    s = bureau.summary(retail)
    out = s["active_accounts_totals"]["outstanding"]
    # active: 1, 2, 3, 5; account 3's balance is unknown
    assert out == {"known_total": 50000 + 600000 + 2500000, "accounts_counted": 3, "accounts_unknown": [3]}
    assert s["active_accounts_totals"]["emi"]["accounts_unknown"] == [3]
    assert s["active_accounts_totals"]["sanction_amount"]["known_total"] == 100000 + 800000 + 50000 + 3000000


def test_summary_worst_dpd_names_holders_unknown_and_doubtful(retail):
    w = bureau.summary(retail)["worst_dpd_all_accounts"]
    assert w["ever"]["worst_known"] == 120 and w["ever"]["held_by"] == [4]
    assert w["ever"]["accounts_unknown"] == [3]
    assert w["ever"]["doubtful_readings"] == [5]
    assert w["12m"]["worst_known"] == 30 and w["12m"]["held_by"] == [2, 5]


def test_summary_flags_not_provided_on_retail(retail):
    flags = bureau.summary(retail)["accounts_flagged"]
    assert flags["written_off"] == [4]
    assert flags["settled"] == flags["suit_filed"] == flags["delinquent"] == NOT_PROVIDED


def test_summary_flags_on_commercial(commercial):
    flags = bureau.summary(commercial)["accounts_flagged"]
    assert flags == {"written_off": [3], "settled": [4], "suit_filed": [3], "delinquent": [2]}


def test_summary_score_kinds(retail, commercial):
    assert bureau.summary(retail)["borrower"]["score"] == {"value": 742, "kind": "credit score"}
    assert "rank" in bureau.summary(commercial)["borrower"]["score"]["kind"]
    retail["parsed"]["score"] = "NA"
    assert "no score" in bureau.summary(retail)["borrower"]["score"]["kind"]


def test_summary_counts_by_status(commercial):
    assert bureau.summary(commercial)["accounts"]["by_status"] == {
        "Active": 2, "Written Off": 1, "Settled": 1, "Closed": 1}


# --- envelope and limitations ------------------------------------------------------


def test_every_answer_carries_provider_and_limitations(retail):
    for result in (bureau.summary(retail), bureau.find_accounts(retail), bureau.account_detail(retail, 1)):
        assert result["provider"] == "crif"
        assert result["masking"] == "on"
        assert any("enquiries" in l.lower() for l in result["limitations"])
        assert any("payment history" in l for l in result["limitations"])


def test_limitations_name_retail_missing_flags_and_doubts(retail):
    lim = bureau.limitations(retail)
    assert any("no settled, suit filed or delinquent" in l for l in lim)
    assert any("[3, 5]" in l for l in lim)


def test_limitations_count_a_long_doubt_list_instead_of_listing_it(retail):
    for a in retail["parsed"]["accounts"]:
        a["dpd_sources_disagree"] = True
    retail["parsed"]["accounts"] *= 3
    assert any("15 accounts (list them with find_bureau_accounts" in l for l in bureau.limitations(retail))


def test_limitations_name_unreconciled_reading_and_vision(commercial):
    lim = bureau.limitations(commercial)
    assert any("does not reconcile" in l and "Active balance differs" in l for l in lim)
    assert any("Vision" in l for l in lim)
    assert bureau.envelope(commercial, False)["reconciles_with_report_totals"] is False


# --- masking ------------------------------------------------------------------------


def test_identity_masked_by_default(retail):
    ident = bureau.summary(retail)["borrower"]["identity"]
    assert ident == {"pan": "******234F", "dob": "**-**-1985", "phone": "******3210"}


def test_identity_unmasked_on_request(retail):
    s = bureau.summary(retail, unmasked=True)
    assert s["borrower"]["identity"]["pan"] == "ABCDE1234F"
    assert s["masking"] == "off"


def test_missing_identity_stays_missing(commercial):
    assert bureau.summary(commercial)["borrower"]["identity"] == {"pan": "******234Q", "dob": None, "phone": None}


# --- find: each filter ----------------------------------------------------------------


def test_find_no_filters_returns_all_in_order(retail):
    r = bureau.find_accounts(retail)
    assert srs(r) == [1, 2, 3, 4, 5] and r["total_matches"] == 5 and r["has_more"] is False


def test_find_status(retail, commercial):
    assert srs(bureau.find_accounts(retail, status="active")) == [1, 2, 3, 5]
    assert srs(bureau.find_accounts(retail, status="closed")) == [4]
    assert srs(bureau.find_accounts(commercial, status="written_off")) == [3]
    assert srs(bureau.find_accounts(commercial, status="settled")) == [4]
    assert bureau.find_accounts(commercial, status="any")["total_matches"] == 5


def test_find_text_filters_are_case_insensitive_contains(retail):
    assert srs(bureau.find_accounts(retail, lender="beta")) == [2, 5]
    assert srs(bureau.find_accounts(retail, loan_type="LOAN")) == [1, 2, 4, 5]
    assert srs(bureau.find_accounts(retail, ownership="joint")) == [5]


def test_find_min_dpd_per_window_lists_unknown(retail):
    r = bureau.find_accounts(retail, min_dpd=90)
    assert srs(r) == [2, 4]
    assert r["excluded_because_value_unknown"] == {"min_dpd": [3]}
    assert srs(bureau.find_accounts(retail, min_dpd=30, dpd_window="12m")) == [2, 5]
    assert srs(bureau.find_accounts(retail, min_dpd=1, dpd_window="last_reported")) == [5]


def test_find_min_dpd_zero_still_excludes_unknown(retail):
    r = bureau.find_accounts(retail, min_dpd=0)
    assert 3 not in srs(r) and r["excluded_because_value_unknown"]["min_dpd"] == [3]


def test_find_amount_minimums(retail):
    r = bureau.find_accounts(retail, min_outstanding=500000)
    assert srs(r) == [2, 5] and r["excluded_because_value_unknown"] == {"min_outstanding": [3]}
    assert srs(bureau.find_accounts(retail, min_overdue=1)) == [3]


def test_find_flags(retail, commercial):
    assert srs(bureau.find_accounts(retail, written_off=True)) == [4]
    assert srs(bureau.find_accounts(retail, written_off=False)) == [1, 2, 3, 5]
    assert srs(bureau.find_accounts(commercial, suit_filed=True)) == [3]
    assert srs(bureau.find_accounts(commercial, delinquent=True)) == [2]
    assert srs(bureau.find_accounts(commercial, settled=True)) == [4]
    assert srs(bureau.find_accounts(commercial, written_off=True)) == [3]


def test_find_doubtful_dpd(retail):
    assert srs(bureau.find_accounts(retail, dpd_doubtful_only=True)) == [3, 5]
    assert srs(bureau.find_accounts(retail, dpd_doubtful_only=False)) == [1, 2, 4]


# --- find: combinations, sorting, paging, aggregates -----------------------------------


def test_find_combined_filters(retail, commercial):
    assert srs(bureau.find_accounts(retail, status="active", lender="beta", min_dpd=30)) == [2, 5]
    assert srs(bureau.find_accounts(retail, status="active", min_dpd=90)) == [2]
    assert srs(bureau.find_accounts(commercial, status="active", delinquent=True, min_overdue=100000)) == [2]
    assert bureau.find_accounts(retail, status="closed", lender="alpha")["total_matches"] == 0


def test_find_sort_puts_unknown_last_both_ways(retail):
    up = bureau.find_accounts(retail, sort_by="outstanding")
    assert srs(up) == [4, 1, 2, 5, 3]
    down = bureau.find_accounts(retail, sort_by="outstanding", descending=True)
    assert srs(down) == [5, 2, 1, 4, 3]


def test_find_sort_by_date(retail):
    assert srs(bureau.find_accounts(retail, sort_by="date_of_sanction")) == [4, 5, 1, 2, 3]


def test_find_paging(retail):
    p1 = bureau.find_accounts(retail, limit=2)
    p3 = bureau.find_accounts(retail, limit=2, offset=4)
    assert srs(p1) == [1, 2] and p1["has_more"] is True and p1["total_matches"] == 5
    assert srs(p3) == [5] and p3["has_more"] is False
    assert bureau.find_accounts(retail, limit=2, offset=10)["accounts"] == []


def test_find_aggregates_cover_all_matches_not_just_the_page(retail):
    r = bureau.find_accounts(retail, status="active", limit=1)
    agg = r["aggregates_over_all_matches"]
    assert agg["count"] == 4 and len(r["accounts"]) == 1
    assert agg["amounts"]["emi"] == {"known_total": 5000 + 18000 + 28000, "accounts_counted": 3, "accounts_unknown": [3]}
    assert agg["worst_dpd"]["ever"]["worst_known"] == 95


def test_find_rows_show_only_flags_the_format_provides(retail, commercial):
    row = bureau.find_accounts(retail)["accounts"][0]
    assert "written_off" in row and "suit_filed" not in row and "settled" not in row
    assert row["status_printed_on_report"] == "Active"
    assert bureau.find_accounts(retail)["flags_not_provided"] == ["settled", "suit_filed", "delinquent"]
    crow = bureau.find_accounts(commercial)["accounts"][0]
    assert {"written_off", "settled", "suit_filed", "delinquent", "dpd_reported_by_bureau"} <= set(crow)


# --- find: invalid questions ----------------------------------------------------------


@pytest.mark.parametrize("kwargs, message", [
    ({"suit_filed": True}, "not provided"),
    ({"delinquent": False}, "not provided"),
    ({"settled": True}, "not provided"),
    ({"status": "settled"}, "not provided"),
    ({"status": "written_off"}, "not provided"),
    ({"status": "dormant"}, "Unknown status"),
    ({"dpd_window": "6m"}, "Unknown dpd_window"),
    ({"min_dpd": -1}, "0 or more"),
    ({"min_outstanding": -5}, "0 or more"),
    ({"sort_by": "lender"}, "Cannot sort"),
    ({"limit": 0}, "between 1 and 200"),
    ({"limit": 201}, "between 1 and 200"),
    ({"offset": -1}, "0 or more"),
])
def test_find_refuses_invalid_questions(retail, kwargs, message):
    with pytest.raises(QueryError, match=message):
        bureau.find_accounts(retail, **kwargs)


# --- account detail -------------------------------------------------------------------


def test_account_detail_names_unknowns_and_doubts(retail):
    d = bureau.account_detail(retail, 3)
    assert set(d["unknown_fields"]) == {"outstanding", "emi", "max_dpd_ever", "max_dpd_12m", "last_reported_dpd"}
    assert any("another account" in n for n in d["dpd_note"])
    assert d["labels"]["outstanding"].startswith("Current outstanding")
    assert d["flags_not_provided"] == ["settled", "suit_filed", "delinquent"]


def test_account_detail_disagreeing_dpd(retail):
    assert any("disagreed" in n for n in bureau.account_detail(retail, 5)["dpd_note"])


def test_account_detail_dpd_not_reported_by_bureau(commercial):
    d = bureau.account_detail(commercial, 3)
    assert any("not reported, not zero" in n for n in d["dpd_note"])
    assert d["account"]["written_off"] is True and d["account"]["suit_filed"] is True


def test_account_detail_unknown_sr_no(retail):
    with pytest.raises(QueryError, match="numbered 1 to 5"):
        bureau.account_detail(retail, 99)
