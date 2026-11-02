"""A synthetic GST Analyser report (res.report after to_jsonable): two GSTINs under one PAN,
a finding whose text quotes a GSTIN in full, and a bank statement listed as skipped. No
real data. Both GSTINs share the PAN and state, so a key built from a shorter mask could
collide; the summary keeps them apart as records with opaque entity ids instead."""

import copy

PAN = "ABCDE1234F"
GSTIN_1 = "29ABCDE1234F1Z5"
GSTIN_2 = "29ABCDE1234F2Z5"


def _point(key, label, turnover):
    return {
        "period": {"key": key, "label": label, "type": "monthly", "start": f"{key}-01", "end": f"{key}-28", "fy": "2025-26"},
        "status": "AVAILABLE", "doc_ids": [f"doc-{key}"], "rolled_up_from_monthly": False, "note": "",
        "extraction_confidence": "HIGH",
        "base": {"aggregate_turnover_3b": turnover, "taxable_outward": turnover, "output_tax": turnover * 0.18,
                 "net_itc": turnover * 0.1, "tax_paid_itc": turnover * 0.1, "tax_paid_cash": turnover * 0.08,
                 "interest_paid": 0.0, "late_fee_paid": None, "rcm_inward_value": 0.0},
        "ratios": {"cash_tax_ratio": 0.44, "itc_utilisation_ratio": 0.56},
    }


def _return(gstin, key, label, delay):
    return {
        "doc_id": f"doc-{key}", "file_name": f"3b_{key}.pdf", "gstin": gstin, "legal_name": "TEST TRADERS",
        "trade_name": "TEST", "period": {"key": key, "label": label, "type": "monthly"},
        "arn": "AA290000000000X", "arn_date": f"{key}-22", "in_window": True,
        "filing": {"due_date": f"{key}-20", "arn_date": f"{key}-22", "delay_days": delay, "late": delay > 0, "note": ""},
        "extraction": {"confidence": "HIGH", "disagreements": []},
        "tables": {"t3_1": {"a": 1}}, "sources": {"t3_1.a": {"page": 1}},
    }


def _gstin(gstin, turnover):
    return {
        "gstin": gstin, "pan": PAN, "state_code": "29", "legal_names": ["TEST TRADERS"], "trade_names": ["TEST"],
        "filing_pattern": "monthly",
        "series": {
            "scope": "gstin", "grain": "monthly",
            "coverage": {"expected_periods": ["2025-04", "2025-05"], "available": ["2025-04", "2025-05"],
                         "unavailable": [], "coverage_pct": 100.0, "basis": "window"},
            "trends": {"metric": "aggregate_turnover_3b", "n_available": 2, "volatility_cov": 0.1},
            "fy_totals": {"2025-26": {"aggregate_turnover_3b": turnover * 2, "months_covered": 2, "months_expected": 12,
                                      "periods_available": 2, "periods_expected": 12, "coverage_pct": 16.7, "note": ""}},
            "points": [_point("2025-04", "April 2025", turnover), _point("2025-05", "May 2025", turnover)],
        },
        "returns": [_return(gstin, "2025-04", "April 2025", 0), _return(gstin, "2025-05", "May 2025", 4)],
        "superseded_returns": [],
    }


REPORT = {
    "meta": {"engine": "gst_analyser", "version": "1", "generated_at": "2026-01-01T00:00:00",
             "interpretation": "attention signals only",
             "window": {"start": "2025-04-01", "end": "2025-05-31", "months": 2, "basis": "returns", "label": "Apr to May 2025"},
             "policy": {"window_months": 12}},
    "files": [
        {"file_name": "3b_2025-04.pdf", "sha256": "a", "doc_type": "GSTR3B", "status": "PARSED", "reason": "",
         "doc_id": "doc-2025-04", "gstin": GSTIN_1, "period_label": "April 2025"},
        {"file_name": "bank.pdf", "sha256": "b", "doc_type": "BANK_STATEMENT", "status": "SKIPPED",
         "reason": "not a GST return", "doc_id": None, "gstin": None, "period_label": None},
    ],
    "canonical": {"borrowers": {PAN: {"gstins": {GSTIN_1: _gstin(GSTIN_1, 1000000.0), GSTIN_2: _gstin(GSTIN_2, 200000.0)}}},
                  "consolidated": None},
    "findings": [{
        "finding_id": "FILING_TIMELINESS-01", "finding_type": "OBSERVATION", "severity": "INFO",
        "title": "Filing delays", "evidence": [{"doc_id": "doc-2025-05", "field": "arn_date"}],
        "calculation": "ARN date minus due date",
        "explanation": f"GSTIN {GSTIN_1} filed May 2025 four days late.",
        "limitation": "Notified extensions are unknown.", "suggested_verification": "Check the portal.",
        "scope": GSTIN_1, "data_coverage": {"coverage_pct": 100.0}, "analytical_confidence": "MEDIUM",
        "interpretation_scope": "attention_signal_only",
    }],
    "validation": {"counts": {"PASS": 3, "WARNING": 1, "FAIL": 0, "UNABLE_TO_VALIDATE": 0},
                   "checks": [{"check_id": "V1", "scope": "doc-2025-04", "description": "3.1 total", "expected": 1,
                               "actual": 1, "variance": 0, "tolerance": 1, "status": "PASS", "note": ""},
                              {"check_id": "V2", "scope": "doc-2025-05", "description": "4C vs 4A-4B", "expected": 10,
                               "actual": 9, "variance": 1, "tolerance": 0, "status": "WARNING", "note": "rounding"}]},
    "confidence": {"extraction": {"doc-2025-04": "HIGH"}, "extraction_summary": {"HIGH": 4, "MEDIUM": 0, "LOW": 0},
                   "data_coverage": {GSTIN_1: 100.0, GSTIN_2: 100.0},
                   "findings_by_analytical_confidence": {"HIGH": 0, "MEDIUM": 1, "LOW": 0}},
    "vendor_compat": {"taxpayerInformation": {"gstin": GSTIN_1}},
    "vendor_summary": [{"financialYearPeriod": "2025-26", "revenue": 2400000.0}],
}


def report():
    return copy.deepcopy(REPORT)
