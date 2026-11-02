from lending_mcp.masking import mask_dob, mask_gstin, mask_tail, scrub


def test_mask_tail_and_dob_match_v1():
    assert mask_tail("ABCDE1234F") == "******234F"
    assert mask_tail("9876543210") == "******3210"
    assert mask_tail("123") == "***"
    assert mask_tail(None) is None and mask_tail("") is None
    assert mask_dob("12-05-1990") == "**-**-1990"
    assert mask_dob("unknown") == "masked"
    assert mask_dob(None) is None


def test_mask_gstin():
    assert mask_gstin("29ABCDE1234F1Z5") == "29**********1Z5"
    assert mask_gstin(None) is None and mask_gstin("") is None


def test_scrub_replaces_in_keys_values_and_nested_text():
    data = {
        "29ABCDE1234F1Z5": {"gstin": "29ABCDE1234F1Z5", "note": "filed under 29ABCDE1234F1Z5 late"},
        "list": ["pan ABCDE1234F here", 5, None],
    }
    out = scrub(data, {"29ABCDE1234F1Z5": "29**********1Z5", "ABCDE1234F": "******234F"})
    assert out == {
        "29**********1Z5": {"gstin": "29**********1Z5", "note": "filed under 29**********1Z5 late"},
        "list": ["pan ******234F here", 5, None],
    }
    assert data["list"][0] == "pan ABCDE1234F here"  # the input is not changed


def test_scrub_replaces_longest_first():
    # The PAN sits inside the GSTIN: the GSTIN must be masked whole, not via its PAN.
    out = scrub("29ABCDE1234F1Z5", {"ABCDE1234F": "PAN", "29ABCDE1234F1Z5": "GSTIN"})
    assert out == "GSTIN"
