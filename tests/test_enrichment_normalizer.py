"""
tests/test_enrichment_normalizer.py
===================================
Unit tests for voter mobile enrichment core algorithms:
- Normalizer & phone sanitization
- Marwari phonetic soundex & consonant skeleton
- Age drift calibration
- Gender compatibility blocking
- Address tokenization
- Kinship household propagation
"""

import unittest
from scripts.enrichment_core.normalizer import (
    clean_phone,
    sanitize_string,
    clean_txt,
    get_core_name,
    consonant_skel,
    infer_gender,
    is_gender_compatible
)
from scripts.enrichment_core.age_model import is_age_compatible
from scripts.enrichment_core.address_tokenizer import AddressTokenizer
from scripts.enrichment_core.household_matcher import HouseholdMatcher


class TestEnrichmentCore(unittest.TestCase):

    def test_clean_phone(self):
        # 10 digit standard
        self.assertEqual(clean_phone('9825243426'), '9825243426')
        # Float representation from Excel
        self.assertEqual(clean_phone('9825243426.0'), '9825243426')
        # 12-digit international format with +91 or 91
        self.assertEqual(clean_phone('919825243426'), '9825243426')
        self.assertEqual(clean_phone('+91-9825243426'), '9825243426')
        # 11-digit format with leading zero
        self.assertEqual(clean_phone('09825243426'), '9825243426')
        # Invalid numbers
        self.assertIsNone(clean_phone('1234567890'))
        self.assertIsNone(clean_phone(''))
        self.assertIsNone(clean_phone(None))

    def test_sanitize_string(self):
        # Cleans tabs, dots, commas
        self.assertEqual(sanitize_string("Prakash.\tKumar.."), "Prakash Kumar")
        # Cleans repeated honorific titles
        self.assertEqual(sanitize_string("Ram Ram"), "Ram")
        self.assertEqual(sanitize_string("Singh Singh"), "Singh")

    def test_marwari_phonetics(self):
        # V/W/B equivalence
        self.assertEqual(clean_txt("Vishnoi"), clean_txt("Bishnoi"))
        self.assertEqual(clean_txt("Devi"), clean_txt("Debi"))
        # OCR confusion healing: Dh vs Gh (Ladhu vs Laghu)
        self.assertEqual(clean_txt("Ladhu"), clean_txt("Laghu"))
        # Dialect alias: Pelad vs Prahlad
        self.assertEqual(clean_txt("Pelad"), clean_txt("Prahlad"))

    def test_consonant_skeleton(self):
        # Strips particles (ram, lal, kumar, etc.) and vowels
        skel1 = consonant_skel("Prakash Kumar")
        self.assertEqual(skel1, "prks")
        skel2 = consonant_skel("Prahalad Ram")
        self.assertEqual(skel2, "prhld")

    def test_age_drift_model(self):
        # Historical welfare rolls (e.g. registered 10 years ago)
        # db_age (38) - sheet_age (28) = 10 -> Compatible
        compat, drift = is_age_compatible(sheet_age=28, db_age=38, is_historical=True)
        self.assertTrue(compat)
        self.assertEqual(drift, 10)

        # Extreme mismatch (e.g. 20 vs 75) -> Incompatible
        compat, drift = is_age_compatible(sheet_age=20, db_age=75, is_historical=True)
        self.assertFalse(compat)

        # Recent Telecom KYC (+/- 6 years)
        compat, drift = is_age_compatible(sheet_age=30, db_age=32, is_historical=False)
        self.assertTrue(compat)
        compat, drift = is_age_compatible(sheet_age=20, db_age=40, is_historical=False)
        self.assertFalse(compat)

    def test_gender_compatibility(self):
        # Male vs Male -> Compatible
        self.assertTrue(is_gender_compatible('M', 'Prakash Ram', 'M', 'Prakash'))
        # Female vs Female -> Compatible
        self.assertTrue(is_gender_compatible('F', 'Dhapu Devi', 'F', 'Dhapu Bai'))
        # Male vs Female -> Incompatible
        self.assertFalse(is_gender_compatible('M', 'Prakash Ram', 'F', 'Dhapu Devi'))

    def test_address_tokenizer(self):
        tokenizer = AddressTokenizer()
        tokenizer.register_village("Hotigaon", "gp_hotigaon")
        tokenizer.register_village("Bhadroona", "gp_bhadroona")

        candidates = tokenizer.extract_gp_candidates(
            "House 45, Near Hanuman Mandir, Gram Panchayat Hotigaon, Sanchore"
        )
        self.assertEqual(candidates, ["gp_hotigaon"])

    def test_household_matcher(self):
        matcher = HouseholdMatcher()
        # Head of household with phone
        matcher.register_elector(voter_id=101, gp_key="hotigaon", rel_name="Dajaram", mobile="9825243426")
        # Son and wife without phone
        matcher.register_elector(voter_id=102, gp_key="hotigaon", rel_name="Dajaram", mobile=None)
        matcher.register_elector(voter_id=103, gp_key="hotigaon", rel_name="Dajaram", mobile=None)

        propagated = matcher.propagate_household_phones()
        self.assertIn(102, propagated)
        self.assertIn(103, propagated)
        self.assertEqual(propagated[102]['mobile'], "9825243426")
        self.assertEqual(propagated[102]['source_type'], "RECONCILED_V2_KINSHIP")


if __name__ == '__main__':
    unittest.main()
