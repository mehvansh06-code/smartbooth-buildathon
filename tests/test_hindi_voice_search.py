"""
test_hindi_voice_search.py
Comprehensive unit tests for Indic compound de-compounding, whitespace folding,
Stage 1 search query generation, and SIMD Rapidfuzz Stage 2 ranking.
"""

import unittest
from electionapp.search.two_stage_search import (
    split_indic_compound,
    expand_search_tokens,
    fold_indic_name,
    build_stage1_query,
    build_count_query,
    stage2_rapidfuzz_rank
)

class HindiVoiceSearchTestCase(unittest.TestCase):
    def test_split_indic_compound_hindi(self):
        """Verifies proper de-compounding of joined Hindi honorific suffixes."""
        cases = {
            'शिवकुमार': ['शिव', 'कुमार'],
            'रामप्रसाद': ['राम', 'प्रसाद'],
            'मोहनलाल': ['मोहन', 'लाल'],
            'सुरेशचन्द्र': ['सुरेश', 'चन्द्र'],
            'सुरेशचन्द': ['सुरेश', 'चन्द'],
            'रामसिंह': ['राम', 'सिंह'],
            'ओमप्रकाश': ['ओम', 'प्रकाश'],
            'हनुमानराम': ['हनुमान', 'राम'],
            'हीरालाल': ['हीरा', 'लाल'],
            'देवीलाल': ['देवी', 'लाल'],
            'किशनलाल': ['किशन', 'लाल'],
            'बाबूलाल': ['बाबू', 'लाल'],
            'श्यामलाल': ['श्याम', 'लाल'],
            'भंवरलाल': ['भंवर', 'लाल'],
            'आशाकुमारी': ['आशा', 'कुमारी'],
        }
        for token, expected in cases.items():
            result = split_indic_compound(token)
            self.assertEqual(result, expected, f"Failed splitting '{token}': expected {expected}, got {result}")

    def test_split_indic_compound_preserves_non_compounds(self):
        """Ensures non-compound Hindi words and short stems are never erroneously split."""
        safe_words = [
            'कमलेश', 'रामेश्वर', 'राजेश', 'सुनील', 'राम', 'कुमार', 'लाल',
            'सिंह', 'प्रसाद', 'प्रकाश', 'शर्मा', 'सैनी', 'मीणा', 'जाट'
        ]
        for w in safe_words:
            result = split_indic_compound(w)
            self.assertEqual(result, [w], f"Erroneously split non-compound '{w}': got {result}")

    def test_split_indic_compound_english(self):
        """Verifies de-compounding of common Romanized Indian compounds."""
        cases = {
            'shivkumar': ['shiv', 'kumar'],
            'ramprasad': ['ram', 'prasad'],
            'mohanlal': ['mohan', 'lal'],
            'omprakash': ['om', 'prakash'],
            'ramsingh': ['ram', 'singh'],
            'heeramal': ['heeramal'],  # not in suffix list
        }
        for token, expected in cases.items():
            result = split_indic_compound(token)
            self.assertEqual(result, expected, f"Failed splitting English token '{token}'")

    def test_expand_search_tokens(self):
        """Tests query tokenization with compound expansion."""
        self.assertEqual(expand_search_tokens('शिवकुमार सैनी'), ['शिव', 'कुमार', 'सैनी'])
        self.assertEqual(expand_search_tokens('shivkumar saini'), ['shiv', 'kumar', 'saini'])
        self.assertEqual(expand_search_tokens('सुनील कुमार सैनी'), ['सुनील', 'कुमार', 'सैनी'])
        self.assertEqual(expand_search_tokens('रामप्रसाद जी शर्मा'), ['राम', 'प्रसाद', 'जी', 'शर्मा'])

    def test_fold_indic_name(self):
        """Tests canonical whitespace and punctuation folding."""
        self.assertEqual(fold_indic_name('शिव कुमार सैनी'), 'शिवकुमारसैनी')
        self.assertEqual(fold_indic_name('शिवकुमार सैनी'), 'शिवकुमारसैनी')
        self.assertEqual(fold_indic_name('Shiv Kumar Saini'), 'shivkumarsaini')
        self.assertEqual(fold_indic_name('Shivkumar Saini'), 'shivkumarsaini')
        self.assertEqual(
            fold_indic_name('शिव कुमार सैनी'),
            fold_indic_name('शिवकुमार सैनी')
        )

    def test_build_stage1_query_two_box_compound_name(self):
        """Verifies build_stage1_query produces multi-token search_doc conditions and relevance ordering."""
        sql, params = build_stage1_query(34, name='शिवकुमार सैनी')
        self.assertEqual(params['tenant_id'], 34)
        self.assertIn('search_doc ILIKE %(name_tok_0)s', sql)
        self.assertIn('search_doc ILIKE %(name_tok_1)s', sql)
        self.assertIn('search_doc ILIKE %(name_tok_2)s', sql)
        self.assertEqual(params['name_tok_0'], '%शिव%')
        self.assertEqual(params['name_tok_1'], '%कुमार%')
        self.assertEqual(params['name_tok_2'], '%सैनी%')
        self.assertIn('similarity(name_hi, %(name_raw)s) DESC', sql)

    def test_build_count_query_matches_stage1(self):
        """Verifies build_count_query produces matching search_doc conditions."""
        sql, params = build_count_query(34, name='शिवकुमार सैनी')
        self.assertIn('search_doc ILIKE %(name_tok_0)s', sql)
        self.assertIn('search_doc ILIKE %(name_tok_1)s', sql)
        self.assertIn('search_doc ILIKE %(name_tok_2)s', sql)

    def test_stage2_rapidfuzz_rank_boosts_spaced_variant(self):
        """Verifies Stage 2 ranks spaced voter names as top result when searching compound voice query."""
        candidates = [
            {'voter_id': 1, 'name_en': 'Shiv Kumar Saini', 'name_hi': 'शिव कुमार सैनी', 'rel_name_hi': 'हीरा लाल', 'part_no': 77, 'sl_no': 326},
            {'voter_id': 2, 'name_en': 'Shivam Saini', 'name_hi': 'शिवम सैनी', 'rel_name_hi': 'पटपू राम', 'part_no': 85, 'sl_no': 116},
            {'voter_id': 3, 'name_en': 'Shivani Saini', 'name_hi': 'शिवानी सैनी', 'rel_name_hi': 'विरेन्द्र कुमार', 'part_no': 77, 'sl_no': 397},
            {'voter_id': 4, 'name_en': 'Anil Kumar Saini', 'name_hi': 'अनिल कुमार सैनी', 'rel_name_hi': 'लक्ष्मण राम', 'part_no': 78, 'sl_no': 400},
        ]

        ranked = stage2_rapidfuzz_rank(candidates, name='शिवकुमार सैनी', limit=10)
        self.assertGreater(len(ranked), 0)
        top = ranked[0]
        self.assertEqual(top['voter_id'], 1)
        self.assertEqual(top['name_hi'], 'शिव कुमार सैनी')
        self.assertGreaterEqual(top['search_score'], 120.0)

    def test_stage2_rapidfuzz_rank_english_variant(self):
        """Verifies Stage 2 ranks spaced English name as top result when searching compound English query."""
        candidates = [
            {'voter_id': 1, 'name_en': 'Shiv Kumar Saini', 'name_hi': 'शिव कुमार सैनी', 'rel_name_hi': 'Heera Lal', 'part_no': 77, 'sl_no': 326},
            {'voter_id': 2, 'name_en': 'Shivam Saini', 'name_hi': 'शिवम सैनी', 'rel_name_hi': 'Patpu Ram', 'part_no': 85, 'sl_no': 116},
        ]

        ranked = stage2_rapidfuzz_rank(candidates, name='shivkumar saini', limit=10)
        self.assertEqual(ranked[0]['voter_id'], 1)
        self.assertGreaterEqual(ranked[0]['search_score'], 120.0)

if __name__ == '__main__':
    unittest.main()
