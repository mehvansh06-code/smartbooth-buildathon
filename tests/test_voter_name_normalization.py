# -*- coding: utf-8 -*-
"""Comprehensive unit tests for voter name cleaning, normalization, and idempotency."""
import unittest
from electionapp.translit.normalizer import (
    normalize_voter_names,
    clean_devnagari_name,
    clean_english_name,
    build_search_doc
)
from electionapp.translit.rules import transliterate_name


class TestVoterNameNormalization(unittest.TestCase):

    def test_idempotency_guarantee(self):
        """Verify that normalizing twice yields the exact same output: norm(norm(x)) == norm(x)."""
        test_cases = [
            ('महेन्द्र कुमार', 'Mahendra Kumar', 'राजेन्द्र कुमार', 'Rajendra Kumar'),
            ('कंचन कंवर राठौड़', 'Kanchan Kanwar Rathore', 'प्रेम सिंह राठौड़', 'Prem Singh Rathore'),
            ('रमेश कुमार', '', 'पूरा राम', ''),
            ('शक्ति सिंह', 'शक्ति सिंह', 'रघुवीरसिंह', 'Raghuvirsingh'),
            ('Priya Sharma', 'Priya Sharma', 'Amit Sharma', 'Amit Sharma'),
            ('सुनील कुमार', 'Sunil Kumar', 'Available', 'Available'),
        ]
        for n_hi, n_en, r_hi, r_en in test_cases:
            res1 = normalize_voter_names(n_hi, n_en, r_hi, r_en)
            res2 = normalize_voter_names(res1[0], res1[1], res1[3], res1[4])
            self.assertEqual(res1, res2, f"Failed idempotency on input: {(n_hi, n_en, r_hi, r_en)}")

    def test_sanskrit_cluster_expansion(self):
        """Verify -dr endings in Latin expand to -dra."""
        self.assertEqual(clean_english_name('Mahendr Kumar'), 'Mahendra Kumar')
        self.assertEqual(clean_english_name('Jitendr Sinh'), 'Jitendra Singh')
        self.assertEqual(clean_english_name('Rajendr Singh'), 'Rajendra Singh')
        self.assertEqual(clean_english_name('Dharmendr Kumar'), 'Dharmendra Kumar')
        self.assertEqual(clean_english_name('Narendr Kumar'), 'Narendra Kumar')
        self.assertEqual(clean_english_name('Surendr Singh'), 'Surendra Singh')
        self.assertEqual(clean_english_name('Devendr Singh'), 'Devendra Singh')

    def test_scraper_leak_cleanup(self):
        """Verify 'Available' or 'Not Available' scraper leaks are stripped."""
        self.assertEqual(clean_devnagari_name('Available'), '')
        self.assertEqual(clean_devnagari_name('Not Available'), '')
        self.assertEqual(clean_english_name('Available'), '')
        self.assertEqual(clean_english_name('available'), '')

        res = normalize_voter_names('सुनील', 'Sunil', 'Available', 'Available')
        self.assertEqual(res[3], '')
        self.assertEqual(res[4], '')
        self.assertEqual(res[5], '')

    def test_devanagari_in_english_healing(self):
        """Verify pure or predominant Hindi in English column is converted to proper Latin."""
        res1 = normalize_voter_names('शक्ति सिंह', 'शक्ति सिंह', 'रघुवीरसिंह', 'Raghuvirsingh')
        self.assertEqual(res1[1], 'Shakti Singh')

        res2 = normalize_voter_names('बशीर', 'बशीर', 'बहादुर', 'Bahadur')
        self.assertEqual(res2[1], 'Bashir')

        res3 = normalize_voter_names('पार्वती', 'पार्वती', 'मंजू', 'Manju')
        self.assertEqual(res3[1], 'Parvati')

    def test_diacritic_leaks_in_latin_stripped(self):
        """Verify Devanagari nukta (\\u093c) and virama (\\u094d) in Latin text are stripped."""
        # Laksh्mi Knvar -> Lakshmi Kanwar
        self.assertEqual(clean_english_name('Laksh\u094dmi Knvar'), 'Lakshmi Kanwar')
        # Knchan Knvar Rathauda़ -> Kanchan Kanwar Rathore
        res = normalize_voter_names('कंचन कंवर राठौड़', 'Knchan Knvar Rathauda\u093c', 'राजेंद्र सिंह', 'Rajendr Sinh')
        self.assertEqual(res[1], 'Kanchan Kanwar Rathore')
        self.assertEqual(res[4], 'Rajendra Singh')

    def test_missing_english_regeneration(self):
        """Verify voters with empty name_en get clean canonical English generated."""
        res = normalize_voter_names('रमेश कुमार', '', 'मूंगा राम', '')
        self.assertEqual(res[1], 'Ramesh Kumar')
        self.assertEqual(res[2], 'ramesh kumar')
        self.assertEqual(res[4], 'Moonga Ram')
        self.assertEqual(res[5], 'moonga ram')

    def test_reverse_transliteration_verified(self):
        """Verify known Latin-only names are reverse-transliterated using verified lexicon."""
        self.assertEqual(clean_devnagari_name('ARWIND KUMAR'), 'अरविंद कुमार')
        self.assertEqual(clean_devnagari_name('Priya Sharma'), 'प्रिया शर्मा')
        self.assertEqual(clean_devnagari_name('Amit Sharma'), 'अमित शर्मा')
        self.assertEqual(clean_devnagari_name('Narinder Babar'), 'नरेंद्र बब्बर')

    def test_search_doc_builder(self):
        """Verify standard search_doc format."""
        sdoc = build_search_doc('रमेश कुमार', 'Ramesh Kumar', 'ramesh kumar', 'मूंगा राम', 'Moonga Ram', 'moonga ram', '45', 'ABC1234567')
        self.assertIn('रमेश कुमार', sdoc)
        self.assertIn('Ramesh Kumar', sdoc)
        self.assertIn('ABC1234567', sdoc)
        self.assertIn('45', sdoc)


if __name__ == '__main__':
    unittest.main()
