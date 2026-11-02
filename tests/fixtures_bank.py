"""A synthetic bank statement in the shape lending_mcp.loader stores it: Bank Statement
Analyser's AnalysisResult after dataclasses.asdict and to_jsonable. No real data."""

import copy

ACCOUNT = "50100123456789"


def _txn(date, narration, debit=None, credit=None, balance=0.0, status="VERIFIED", category="upi", **kw):
    row = {
        "date": date, "narration": narration, "chq_ref": None, "debit": debit, "credit": credit,
        "balance": balance, "status": status, "expected_balance": None, "category": category,
        "category_confidence": None, "page": 0, "direction_source": "column",
        "unresolved_amount": None, "date_inferred": False,
    }
    row.update(kw)
    return row


TRANSACTIONS = [
    _txn("2025-01-01", "OPENING BALANCE", balance=10000.0, status="OPENING", category=None, direction_source=None),
    _txn("2025-01-02", "UPI/RAMESH/rent", debit=5000.0, balance=5000.0),
    _txn("2025-01-15", "NEFT ACME PAYROLL SALARY", credit=40000.0, balance=45000.0, category="salary"),
    _txn("2025-01-30", "ACH HDFC LOAN EMI", debit=8000.0, balance=37000.0, category="emi"),
    _txn("2025-02-01", "UPI/SHOP/grocery", debit=2000.0, balance=35000.0),
    _txn("2025-02-14", "NEFT ACME PAYROLL SALARY", credit=40000.0, balance=75000.0, category="salary"),
    _txn("2025-02-27", "ACH HDFC LOAN EMI", debit=8000.0, balance=67000.0, category="emi"),
    _txn("2025-03-03", f"IMPS TO OWN A/C {ACCOUNT}", debit=10000.0, balance=57000.0, category="transfer"),
    _txn("2025-03-20", "CASH DEPOSIT", credit=6000.0, balance=63000.0, category="cash"),
]

STATEMENT = {
    "bank_key": "hdfc", "bank_name": "HDFC Bank", "account_holder": "TEST HOLDER",
    "account_number": ACCOUNT, "page_count": 3, "scanned_pages": 0,
    "transactions": TRANSACTIONS,
    "summary": {
        "counts": {"VERIFIED": 8, "FAILED": 0, "UNVERIFIED": 0, "OPENING": 1},
        "total_debit": 33000.0, "total_credit": 86000.0, "opening_balance": 10000.0,
        "closing_balance": 63000.0, "transaction_count": 8, "direction_sources": {"column": 8},
        "inferred_direction_count": 0, "unresolved_count": 0, "dates_inferred": 0, "unresolved_amount": 0,
    },
    "due_date_analysis": {"monthly": [], "averages": {}, "recommendations": [], "months_used": 0},
    "monthly": {
        "2025-01": {"debit": 13000.0, "credit": 40000.0, "debit_count": 2, "credit_count": 1, "txn_count": 3, "net": 27000.0},
        "2025-02": {"debit": 10000.0, "credit": 40000.0, "debit_count": 2, "credit_count": 1, "txn_count": 3, "net": 30000.0},
        "2025-03": {"debit": 10000.0, "credit": 6000.0, "debit_count": 1, "credit_count": 1, "txn_count": 2, "net": -4000.0},
    },
    "fraud_signals": [{"code": "METADATA_EDITED", "severity": "LOW", "detail": "Producer field differs."}],
    "category_summary": {"emi": {"count": 2, "total_debit": 16000.0, "total_credit": 0.0}},
    "cashflow": {"cash_dependency_ratio": 0.07, "expense_concentration": {"category": "emi", "ratio": 0.48}},
    "abb": {"as_of": "2025-03-20", "windows": {"1": {"average": 60000.0}, "3": {"average": 45000.0}}},
    "salary": {"detected": True, "months_covered": 3, "months_with_salary": 2, "recurrence_rate": 0.67,
               "average_amount": 40000.0, "coefficient_of_variation": 0.0, "day_spread": 1,
               "irregular": False, "monthly": []},
    "bounces": {"count": 1, "rate_per_month": 0.33, "deposited_count": 0, "technical_count": 0,
                "policy_count": 0, "evidence_rows": 2,
                "instances": [{"date": "2025-02-10", "amount": 8000.0, "narration": "ACH RETURN", "kind": "own",
                               "reason": "insufficient funds", "page": 2}]},
    "red_flags": [{"code": "EMI_BOUNCE", "severity": "HIGH", "detail": "An EMI mandate was returned.",
                   "instances": [{"date": "2025-02-10", "amount": 8000.0, "page": 2, "narration": "ACH RETURN",
                                  "reason": "insufficient funds"}]}],
    "score": {"score": 640.0, "components": {"foir": 50.0}, "weights": {"foir": 0.25}, "red_flag_penalty": 0,
              "volatility": 0.1, "foir_dscr": {"monthly_income": 40000.0, "foir": 40.0, "foir_band": "within",
                                               "dscr": 1.5, "dscr_band": "ok", "months_used": 2,
                                               "income_source": "salary", "monthly_debt_service": 8000.0,
                                               "monthly_fixed_obligations": 8000.0}},
    "top_counterparties": {},
    "vlm_used": False, "vlm_recommended": False, "low_confidence_pages": 0,
    "account_type": "savings",
    "overdraft": {"account_type": "savings", "basis": "header", "facility": False},
    "credits": {"monthly": [{"month": "2025-01", "gross": 40000.0, "net": 40000.0}],
                "totals": {"gross": 86000.0, "labelled": {}, "labelled_count": {}, "net": 86000.0},
                "labelled_credits": []},
    "statement_check": {"period": ["2025-01-01", "2025-03-31"], "printed": {}, "matches": True},
    "ledger_notes": [],
}

EMI = [{"identity": "HDFC LOAN", "occurrences": 2, "amount_min": 8000.0, "amount_max": 8000.0,
        "amount_avg": 8000.0, "first_date": "2025-01-30", "last_date": "2025-02-27", "irregular": False,
        "months_seen": 2, "mandates": 2, "monthly_amount": 8000.0, "recurring": True}]

# The engine's own month edge test on this statement: Jan and Feb are spanned, March
# ends on the 20th.
ENGINE_COMPLETE = {"2025-01": True, "2025-02": True, "2025-03": False}


def statement(**changes):
    s = copy.deepcopy(STATEMENT)
    s.update(changes)
    return s


def with_signal(s, signal):
    s = copy.deepcopy(s)
    s["fraud_signals"].append(signal)
    return s
