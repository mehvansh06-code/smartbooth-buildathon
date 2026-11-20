import pytest
from electionapp.card_templates import _clean_booth_text, _resolve_booth_details
from electionapp.tenant_branding import get_tenant_branding

def test_clean_booth_text_mojibake_healing():
    # Corrupted Mojibake string from Chetan Ram campaign
    corrupted_mojibake = "37 - à¤®à¤¹à¤¾à¤¤à¥\ufffdà¤®à¤¾ à¤—à¤¾à¤‚à¤§à¥€ à¤°à¤¾à¤œà¤•à¥€à¤¯"
    # Unrecoverable Mojibake with replacement characters should be cleanly discarded
    assert _clean_booth_text(corrupted_mojibake) == ""

    # Recoverable Mojibake
    recoverable_area = "à¤¨à¤¾à¤¥à¥‹à¤‚ à¤•à¤¾ à¤¬à¤¾à¤¸"
    assert _clean_booth_text(recoverable_area) == "नाथों का बास"

    # Clean Hindi string
    clean_hindi = "10 - राजकीय उच्च माध्यमिक विद्यालय बागोरिया ब्लॉक बी (कमरा नं. 1)"
    assert _clean_booth_text(clean_hindi) == clean_hindi

def test_resolve_booth_details_prefers_clean_voter_booth():
    voter = {
        "booth_name": "10 - राजकीय उच्च माध्यमिक विद्यालय बागोरिया ब्लॉक बी (कमरा नं. 1)",
        "booth_area": "सूंडिया बेरा"
    }
    # Corrupted booth config from campaign.json
    corrupted_booth = {
        "name": "37 - à¤®à¤¹à¤¾à¤¤à¥\ufffdà¤®à¤¾ à¤—à¤¾à¤‚à¤§à¥€",
        "area": "à¤¨à¤¾à¤¥à¥‹à¤‚ à¤•à¤¾ à¤¬à¤¾à¤¸"
    }

    booth_name, booth_area = _resolve_booth_details(voter, corrupted_booth)
    assert booth_name == "10 - राजकीय उच्च माध्यमिक विद्यालय बागोरिया ब्लॉक बी (कमरा नं. 1)"
    assert booth_area == "सूंडिया बेरा"

def test_resolve_booth_details_fallback_when_voter_booth_missing():
    voter = {
        "booth_name": "",
        "booth_area": ""
    }
    clean_booth = {
        "name": "25 - राजकीय प्राथमिक विद्यालय",
        "area": "मुख्य बाजार"
    }

    booth_name, booth_area = _resolve_booth_details(voter, clean_booth)
    assert booth_name == "25 - राजकीय प्राथमिक विद्यालय"
    assert booth_area == "मुख्य बाजार"
