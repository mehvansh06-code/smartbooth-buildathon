# -*- coding: utf-8 -*-
import unittest
import json
from electionapp import create_app
from electionapp.translit.bilingual_search import (
    get_bilingual_search_variants,
    offline_phonetic_translit
)
from electionapp.translit.rules import transliterate_name


class TestFamilyBilingualSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def test_transliterate_name_canonical(self):
        self.assertEqual(transliterate_name('बागरिया'), 'Bagriya')
        self.assertEqual(transliterate_name('रामकरण'), 'Ramkaran')
        self.assertEqual(transliterate_name('कालू'), 'Kalu')

    def test_offline_phonetic_translit(self):
        res = offline_phonetic_translit('bagariya')
        self.assertTrue(len(res) > 0)
        self.assertIn('ब', res)
        self.assertIn('र', res)

    def test_bilingual_search_variants_english(self):
        res = get_bilingual_search_variants('bagariya')
        self.assertTrue(res['is_latin'])
        self.assertIn('bagariya', res['en_variants'])
        self.assertTrue(any('बागरिया' in v or 'बगरिया' in v for v in res['hi_variants']))
        self.assertTrue(any('%bagariya%' in p for p in res['en_patterns']))

    def test_bilingual_search_variants_hindi(self):
        res = get_bilingual_search_variants('बागरिया')
        self.assertTrue(res['is_devanagari'])
        self.assertIn('बागरिया', res['hi_variants'])
        self.assertTrue(any('Bagriya' in v for v in res['en_variants']))

    def _check_pg(self):
        import os
        from electionapp.repo import get_repository
        if os.environ.get('STORAGE_BACKEND') != 'postgres':
            self.skipTest("family summary API routes require PostgreSQL backend")
        repo = get_repository('postgres')
        tid, _ = repo._resolve_tenant('jelatra')
        if not tid:
            self.skipTest("jelatra tenant not found in PostgreSQL test database")

    def test_api_families_summary_english_search(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'jelatra'
            sess['role'] = 'admin'

        res = self.client.get('/api/families/summary?tenant=jelatra&q=prahlad')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('families', data)
        families = data['families']
        self.assertTrue(len(families) >= 1)
        first_fam = families[0]
        self.assertTrue('प्रहलाद' in first_fam['mukhiya_name'] or 'बाबू' in first_fam['mukhiya_name'])

    def test_api_families_summary_hindi_search(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'jelatra'
            sess['role'] = 'admin'

        res = self.client.get('/api/families/summary?tenant=jelatra&q=प्रहलाद')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertTrue(len(data['families']) >= 1)

    def test_api_families_summary_member_english_search(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'jelatra'
            sess['role'] = 'admin'

        # 'santosh' is an individual member in the Prahlad Ram family
        res = self.client.get('/api/families/summary?tenant=jelatra&q=santosh')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertTrue(len(data['families']) >= 1)

    def test_api_family_members_by_head_bilingual(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'jelatra'
            sess['role'] = 'admin'

        # Test with Hindi head name
        res_hi = self.client.get('/api/family/members_by_head?tenant=jelatra&mukhiya_name=प्रहलाद राम')
        self.assertEqual(res_hi.status_code, 200)
        data_hi = res_hi.get_json()
        self.assertTrue(data_hi.get('success'))
        self.assertTrue(data_hi.get('total') > 0)

        # Test with English transliterated head name
        res_en = self.client.get('/api/family/members_by_head?tenant=jelatra&mukhiya_name=Prahlad Ram')
        self.assertEqual(res_en.status_code, 200)
        data_en = res_en.get_json()
        self.assertTrue(data_en.get('success'))
        self.assertTrue(data_en.get('total') > 0)

    def test_api_families_summary_ward_filter(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'jelatra'
            sess['role'] = 'admin'

        res = self.client.get('/api/families/summary?tenant=jelatra&ward_no=1')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        families = data.get('families', [])
        self.assertTrue(len(families) > 0)
        for f in families:
            self.assertEqual(f['ward_no'], 1)

    def test_api_families_summary_part_filter(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'ward82'
            sess['role'] = 'admin'

        res = self.client.get('/api/families/summary?tenant=ward82&part_no=1')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        families = data.get('families', [])
        self.assertTrue(len(families) > 0)

    def test_api_family_members_by_family_id(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'jelatra'
            sess['role'] = 'admin'

        # First get a family_id from summary
        res_sum = self.client.get('/api/families/summary?tenant=jelatra&ward_no=1')
        self.assertEqual(res_sum.status_code, 200)
        data_sum = res_sum.get_json()
        families = data_sum.get('families', [])
        self.assertTrue(len(families) > 0)
        test_fid = families[0]['family_id']

        # Now fetch members by family_id
        res = self.client.get(f'/api/family/members_by_head?tenant=jelatra&family_id={test_fid}')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertTrue(data.get('total') > 0)
        self.assertEqual(len(data.get('members', [])), data.get('total'))

    def test_api_families_summary_dynamic_mukhiya_without_fhn(self):
        self._check_pg()
        with self.client.session_transaction() as sess:
            sess['tenant_id'] = 'ward82'
            sess['role'] = 'admin'

        # In ward82, family_head_name is NULL in the DB, but dynamic clustering finds Mukhiya
        res = self.client.get('/api/families/summary?tenant=ward82')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        families = data.get('families', [])
        self.assertTrue(len(families) > 0)
        first_fam = families[0]
        self.assertTrue(len(first_fam.get('mukhiya_name', '')) > 0)
        self.assertNotEqual(first_fam.get('mukhiya_name'), 'अज्ञात मुखिया')

    def test_ganpat_phonetic_expansion(self):
        """Verifies ganpat, गणपत, and गनपत symmetrically generate both Devanagari spellings."""
        from electionapp.translit.bilingual_search import get_bilingual_search_variants
        v_en = get_bilingual_search_variants('ganpat')
        v_hi_ana = get_bilingual_search_variants('गणपत')
        v_hi_na = get_bilingual_search_variants('गनपत')

        # English 'ganpat' must include both गणपत and गनपत
        self.assertIn('गणपत', v_en['hi_variants'])
        self.assertIn('गनपत', v_en['hi_variants'])

        # Devanagari 'गणपत' must include 'गनपत'
        self.assertIn('गणपत', v_hi_ana['hi_variants'])
        self.assertIn('गनपत', v_hi_ana['hi_variants'])

        # Devanagari 'गनपत' must include 'गणपत'
        self.assertIn('गणपत', v_hi_na['hi_variants'])
        self.assertIn('गनपत', v_hi_na['hi_variants'])

        # All must produce SQL patterns for both
        self.assertTrue(any('गणपत' in p for p in v_en['hi_patterns']))
        self.assertTrue(any('गनपत' in p for p in v_en['hi_patterns']))
        self.assertTrue(any('गणपत' in p for p in v_hi_ana['hi_patterns']))
        self.assertTrue(any('गनपत' in p for p in v_hi_ana['hi_patterns']))
        self.assertTrue(any('गणपत' in p for p in v_hi_na['hi_patterns']))
        self.assertTrue(any('गनपत' in p for p in v_hi_na['hi_patterns']))

    def test_rajasthani_indic_phonetic_variants(self):
        """Verifies Indic and Rajasthani phonetic sound substitutions (AGENTS.md Rule 2)."""
        from electionapp.translit.bilingual_search import get_bilingual_search_variants
        pairs = [
            ('बिश्नोई', 'विश्नोई'),   # b <-> v/w
            ('सुरेश', 'सुरेस'),       # sh <-> s
            ('नारायण', 'नारायन'),     # retroflex n <-> dental n
            ('जाणी', 'जानी'),         # retroflex n <-> dental n
            ('लाधु', 'लाघु'),         # dh <-> gh OCR & dialect
            ('चंपालाल', 'चम्पालाल'),   # anusvara <-> half nasal
            ('संतोष', 'सन्तोष'),       # anusvara <-> half nasal
        ]
        for w1, w2 in pairs:
            v1 = get_bilingual_search_variants(w1)['hi_variants']
            v2 = get_bilingual_search_variants(w2)['hi_variants']
            self.assertIn(w2, v1, f"{w2} must be in variants of {w1}")
            self.assertIn(w1, v2, f"{w1} must be in variants of {w2}")

    def test_stage2_rapidfuzz_phonetic_ganpat_match(self):
        """Verifies stage2_rapidfuzz_rank scores गणपत and गनपत as exact match for each other."""
        from electionapp.search.two_stage_search import stage2_rapidfuzz_rank
        candidates = [
            {'voter_id': 1, 'name_en': 'Ganpat Ram', 'name_hi': 'गणपत राम', 'rel_name_hi': 'केसा राम', 'ward_no': 1, 'part_no': 1, 'sl_no': 10},
            {'voter_id': 2, 'name_en': 'Kalu Ram', 'name_hi': 'कालू राम', 'rel_name_hi': 'मंगला राम', 'ward_no': 1, 'part_no': 1, 'sl_no': 20},
        ]

        # Search with 'गनपत' (dental न) against candidate with 'गणपत' (retroflex ण)
        ranked = stage2_rapidfuzz_rank(candidates, name='गनपत', limit=5)
        self.assertEqual(len(ranked), 2)
        top = ranked[0]
        self.assertEqual(top['voter_id'], 1)
        # Exact match boost should award >= 130 score
        self.assertGreaterEqual(top['search_score'], 130.0)

        # Reverse: Search with 'गणपत' against candidate with 'गनपत'
        cand_reverse = [
            {'voter_id': 10, 'name_en': 'Ganpat Ram', 'name_hi': 'गनपत राम', 'rel_name_hi': 'केसा राम', 'ward_no': 1, 'part_no': 1, 'sl_no': 10},
        ]
        ranked_rev = stage2_rapidfuzz_rank(cand_reverse, name='गणपत', limit=5)
        self.assertEqual(ranked_rev[0]['voter_id'], 10)
        self.assertGreaterEqual(ranked_rev[0]['search_score'], 130.0)


if __name__ == '__main__':
    unittest.main()

