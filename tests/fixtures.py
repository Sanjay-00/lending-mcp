"""
Synthetic parsed reports, shaped exactly like AutoCAM's parse() output for
each provider (the keys were read off real parses; every name, PAN and
figure here is fictitious).
"""

import copy


def _retail_account(sr, **kw):
    acc = {
        "sr_no": sr, "dpd_block_contaminated": False, "dpd_sources_disagree": False,
        "date_of_sanction": "01-01-2020", "sanction_amount": 100000, "current_balance": 50000,
        "emi": 5000, "overdue": 0, "entity": "Alpha Bank", "ownership": "Individual",
        "type_of_loan": "Personal Loan", "max_dpd": 0, "last_reported_dpd": 0, "max_dpd_12mo": 0,
        "status": "Active", "written_off": False, "status_per_cibil": "Active",
    }
    acc.update(kw)
    return acc


RETAIL = {
    "name": "TEST BORROWER ONE",
    "score": 742,
    "accounts": [
        _retail_account(1),
        _retail_account(2, entity="Beta Finance", type_of_loan="Auto Loan", sanction_amount=800000,
                        current_balance=600000, emi=18000, max_dpd=95, max_dpd_12mo=30,
                        last_reported_dpd=0, date_of_sanction="15-06-2021"),
        _retail_account(3, entity="Alpha Bank", type_of_loan="Credit Card", sanction_amount=50000,
                        current_balance=None, emi=None, overdue=2000, max_dpd=None, max_dpd_12mo=None,
                        last_reported_dpd=None, dpd_block_contaminated=True, date_of_sanction="NA"),
        _retail_account(4, status="Closed", status_per_cibil="Closed", current_balance=0, emi=0,
                        max_dpd=120, max_dpd_12mo=0, last_reported_dpd=0, written_off=True,
                        entity="Gamma NBFC", type_of_loan="Gold Loan", date_of_sanction="10-02-2018"),
        _retail_account(5, ownership="Joint", entity="Beta Finance", type_of_loan="Housing Loan",
                        sanction_amount=3000000, current_balance=2500000, emi=28000, max_dpd=30,
                        max_dpd_12mo=30, last_reported_dpd=30, dpd_sources_disagree=True,
                        date_of_sanction="20-11-2019"),
    ],
    "extraction_method": "Rule-based extraction",
    "validation": {"valid": True, "issues": []},
    "provider": "crif",
    "tesseract_version": None,
    "dpd_vision_recommended": False,
    "dpd_vision_used": False,
    "analysis": {"credit_profile_summary": [], "derog_summary": {"written_off": {"count": 1, "amount": 100000},
                                                                   "overdue": {"count": 1, "amount": 2000}}},
    "identity": {"pan": "ABCDE1234F", "dob": "05-07-1985", "phone": "9876543210"},
}


def _commercial_account(sr, **kw):
    acc = {
        "sr_no": sr, "date_of_sanction": "01-04-2022", "sanction_amount": 5000000,
        "current_balance": 4000000, "emi": 120000, "overdue": 0, "entity": "Delta Bank",
        "ownership": "Borrower", "type_of_loan": "Term Loan", "max_dpd": 0, "last_reported_dpd": 0,
        "max_dpd_12mo": 0, "dpd_reported": True, "status": "Active", "delinquent": False,
        "suit_filed": False,
    }
    acc.update(kw)
    return acc


COMMERCIAL = {
    "name": "TEST TRADERS PRIVATE LIMITED",
    "score": "3 (Medium Risk)",
    "accounts": [
        _commercial_account(1),
        _commercial_account(2, entity="Epsilon Finance", type_of_loan="Cash Credit", emi=None,
                            overdue=300000, max_dpd=91, max_dpd_12mo=91, last_reported_dpd=60,
                            delinquent=True),
        _commercial_account(3, status="Written Off", entity="Zeta NBFC", current_balance=900000,
                            max_dpd=180, max_dpd_12mo=None, last_reported_dpd=None, dpd_reported=False,
                            suit_filed=True),
        _commercial_account(4, status="Settled", current_balance=0, emi=0),
        _commercial_account(5, status="Closed", current_balance=0, emi=0),
    ],
    "extraction_method": "Rule-based extraction",
    "validation": {"valid": False, "issues": ["Active balance differs from the printed total"]},
    "provider": "crif_commercial",
    "analysis": {"borrower_summary": {}, "credit_profile_summary": [], "derog_summary": {}},
    "tesseract_version": None,
    "vision_fallback_recommended": False,
    "dpd_vision_recommended": True,
    "identity": {"pan": "AAACT1234Q", "dob": None, "phone": None},
}

TRANSUNION = {"name": "X", "score": 700, "accounts": [], "provider": "transunion",
              "extraction_method": "Rule-based extraction", "validation": {}, "identity": {}}


def report(parsed, report_id="r1"):
    return {"report_id": report_id, "source_file": "test.pdf", "loaded_at": "2026-10-31T00:00:00+00:00",
            "workbook": "test.xlsx", "parsed": copy.deepcopy(parsed)}
