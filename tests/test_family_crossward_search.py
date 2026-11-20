# -*- coding: utf-8 -*-
"""
test_family_crossward_search.py
Automated Verification for Cross-Ward Family Unification and Bilingual Stopword Filtering:
1. 'ramdhan daji' returns unified SARANA_F0127 with 23 members spanning wards [2, 6, 7, 9].
2. 'ramdhan daji ka' returns unified SARANA_F0127 with 23 members (stopwords filtered).
3. 'रामधन दाजी का घर' returns unified SARANA_F0127 with 23 members.
4. /api/family/members_by_head?family_id=SARANA_F0127 returns all 23 members including Ward 6 (Ajit and Sajjan Lal).
5. /api/family/members_by_head with ward_no parameter does NOT truncate cross-ward members when family_id is supplied.
6. Stopword filtering prevents %ka% and %का% from wildcard explosion on multi-word queries.
"""

import unittest
import urllib.parse
from electionapp import create_app
from electionapp.translit.bilingual_search import get_bilingual_search_variants, SEARCH_STOPWORDS


class TestFamilyCrosswardSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('sarana')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def test_bilingual_stopwords_filtering(self):
        """Ensure multi-word queries filter out particle stopwords and len<=2 words as standalone wildcards."""
        res_en = get_bilingual_search_variants('ramdhan daji ka')
        self.assertNotIn('%ka%', res_en['en_patterns'])
        self.assertNotIn('ka', res_en['en_variants'])
        self.assertIn('%ramdhan daji ka%', res_en['en_patterns'])
        self.assertIn('%ramdhan%', res_en['en_patterns'])
        self.assertIn('%daji%', res_en['en_patterns'])

        res_hi = get_bilingual_search_variants('रामधन दाजी का घर')
        self.assertNotIn('%का%', res_hi['hi_patterns'])
        self.assertNotIn('%घर%', res_hi['hi_patterns'])
        self.assertNotIn('का', res_hi['hi_variants'])
        self.assertNotIn('घर', res_hi['hi_variants'])
        self.assertIn('%रामधन दाजी का घर%', res_hi['hi_patterns'])

    def test_search_ramdhan_daji(self):
        """Verify 'ramdhan daji' returns unified SARANA_F0127 with 23 members."""
        url = f'/api/families/summary?tenant=sarana&q={urllib.parse.quote("ramdhan daji")}'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))

        fams = [f for f in data.get('families', []) if f.get('family_id') == 'SARANA_F0127']
        self.assertEqual(len(fams), 1, "SARANA_F0127 must be unified into exactly one card")
        fam = fams[0]
        self.assertEqual(fam['total_members'], 23)
        self.assertEqual(len(fam['member_ids']), 23)
        self.assertEqual(sorted(fam.get('all_wards', [])), [2, 6, 7, 9])
        self.assertIn('रामधन दाजी', fam['mukhiya_name'])

    def test_search_ramdhan_daji_ka(self):
        """Verify 'ramdhan daji ka' returns unified SARANA_F0127 with 23 members without exploding."""
        url = f'/api/families/summary?tenant=sarana&q={urllib.parse.quote("ramdhan daji ka")}'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))

        fams = [f for f in data.get('families', []) if f.get('family_id') == 'SARANA_F0127']
        self.assertEqual(len(fams), 1, "SARANA_F0127 must be unified into exactly one card")
        fam = fams[0]
        self.assertEqual(fam['total_members'], 23)
        self.assertEqual(len(fam['member_ids']), 23)
        self.assertEqual(sorted(fam.get('all_wards', [])), [2, 6, 7, 9])

    def test_search_ramdhan_daji_ka_ghar_hindi(self):
        """Verify 'रामधन दाजी का घर' returns unified SARANA_F0127 with 23 members."""
        url = f'/api/families/summary?tenant=sarana&q={urllib.parse.quote("रामधन दाजी का घर")}'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))

        fams = [f for f in data.get('families', []) if f.get('family_id') == 'SARANA_F0127']
        self.assertEqual(len(fams), 1, "SARANA_F0127 must be unified into exactly one card")
        fam = fams[0]
        self.assertEqual(fam['total_members'], 23)
        self.assertEqual(len(fam['member_ids']), 23)
        self.assertEqual(sorted(fam.get('all_wards', [])), [2, 6, 7, 9])

    def test_members_by_head_all_23_members(self):
        """Verify /api/family/members_by_head returns all 23 members including Ward 6 (Ajit and Sajjan Lal)."""
        url = '/api/family/members_by_head?tenant=sarana&family_id=SARANA_F0127'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        members = data.get('members', [])
        self.assertEqual(len(members), 23)

        wards = set(m.get('ward_no') for m in members)
        self.assertEqual(wards, {2, 6, 7, 9})

        names_en = [m.get('name_en') for m in members]
        self.assertIn('Ajit', names_en)
        self.assertIn('Sajjan Lal', names_en)

    def test_members_by_head_ignores_ward_filter_for_explicit_family_id(self):
        """Verify /api/family/members_by_head does not filter out cross-ward members even when ward_no=9 is passed."""
        url = '/api/family/members_by_head?tenant=sarana&family_id=SARANA_F0127&ward_no=9'
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        members = data.get('members', [])
        self.assertEqual(len(members), 23, "All 23 members must be returned regardless of ward_no param")
        wards = set(m.get('ward_no') for m in members)
        self.assertEqual(wards, {2, 6, 7, 9})


if __name__ == '__main__':
    unittest.main()
