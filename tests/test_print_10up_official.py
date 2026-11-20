# -*- coding: utf-8 -*-
"""
tests/test_print_10up_official.py
=================================
Verifies the A4 10-Up cut-sheet official print feature:
1. Endpoint /api/print_slips_10up renders successfully.
2. 100% neutral: Zero candidate name, zero symbol, zero candidate ballot serial.
3. Multi-voter selection (e.g. 5 voters) renders 5 individual cards in 1 A4 grid page.
4. Each card contains voter details, prominent serial number, and polling booth details.
"""

import os
import sys
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app


class MockRepo:
    def __init__(self):
        self.mock_voters = {
            1: {
                'id': 1, 'voter_id': 1, 'sl_no': 101, 'ward_no': 2, 'part_no': 2,
                'name_hi': 'मोहन लाल', 'name_en': 'Mohan Lal',
                'rel_name_hi': 'रामेश्वर', 'rel_name_en': 'Rameshwar', 'rel_type': 'पिता',
                'house_no': '45', 'age': 48, 'gender': 'पुरुष',
                'epic_no': 'RJ23178001', 'booth_name': 'राजकीय उच्च माध्यमिक विद्यालय',
                'status': 'Active'
            },
            2: {
                'id': 2, 'voter_id': 2, 'sl_no': 102, 'ward_no': 2, 'part_no': 2,
                'name_hi': 'सुनीता देवी', 'name_en': 'Sunita Devi',
                'rel_name_hi': 'मोहन लाल', 'rel_name_en': 'Mohan Lal', 'rel_type': 'पति',
                'house_no': '45', 'age': 44, 'gender': 'महिला',
                'epic_no': 'RJ23178002', 'booth_name': 'राजकीय उच्च माध्यमिक विद्यालय',
                'status': 'Active'
            },
            3: {
                'id': 3, 'voter_id': 3, 'sl_no': 103, 'ward_no': 2, 'part_no': 2,
                'name_hi': 'विकास कुमार', 'name_en': 'Vikas Kumar',
                'rel_name_hi': 'मोहन लाल', 'rel_name_en': 'Mohan Lal', 'rel_type': 'पिता',
                'house_no': '45', 'age': 22, 'gender': 'पुरुष',
                'epic_no': 'RJ23178003', 'booth_name': 'राजकीय उच्च माध्यमिक विद्यालय',
                'status': 'Active'
            },
            4: {
                'id': 4, 'voter_id': 4, 'sl_no': 104, 'ward_no': 2, 'part_no': 2,
                'name_hi': 'पूजा कुमारी', 'name_en': 'Pooja Kumari',
                'rel_name_hi': 'मोहन लाल', 'rel_name_en': 'Mohan Lal', 'rel_type': 'पिता',
                'house_no': '45', 'age': 20, 'gender': 'महिला',
                'epic_no': 'RJ23178004', 'booth_name': 'राजकीय उच्च माध्यमिक विद्यालय',
                'status': 'Active'
            },
            5: {
                'id': 5, 'voter_id': 5, 'sl_no': 105, 'ward_no': 2, 'part_no': 2,
                'name_hi': 'कैलाश', 'name_en': 'Kailash',
                'rel_name_hi': 'रामेश्वर', 'rel_name_en': 'Rameshwar', 'rel_type': 'पिता',
                'house_no': '45', 'age': 40, 'gender': 'पुरुष',
                'epic_no': 'RJ23178005', 'booth_name': 'राजकीय उच्च माध्यमिक विद्यालय',
                'status': 'Active'
            }
        }

    def get_voter(self, tenant_id, voter_id):
        return self.mock_voters.get(int(voter_id))

    def get_voters_by_ids(self, tenant_id, voter_ids):
        return [self.mock_voters[vid] for vid in voter_ids if vid in self.mock_voters]

    def get_booths_dict(self, tenant_id):
        return {'2': {'name': 'राजकीय उच्च माध्यमिक विद्यालय कमरा 1', 'area': 'वार्ड 2'}}


class TestPrint10UpOfficial(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.mock_repo = MockRepo()
        self.app.extensions['repo'] = self.mock_repo
        with self.client.session_transaction() as sess:
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_user'
            sess['role'] = 'operator'

    def test_single_voter_print_10up(self):
        resp = self.client.get('/api/print_slips_10up?id=1')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("मोहन लाल", html)
        self.assertIn("101", html)
        self.assertIn("क्र.सं.", html)
        self.assertIn("A4 10-Up कट-शीट", html)
        # Verify NO candidate branding
        self.assertNotIn("प्रत्याशी", html)
        self.assertNotIn("चुनाव चिन्ह", html)

    def test_five_voters_print_10up(self):
        resp = self.client.get('/api/print_slips_10up?voter_ids=1,2,3,4,5')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # All 5 voters must be present as individual cards
        self.assertIn("मोहन लाल", html)
        self.assertIn("सुनीता देवी", html)
        self.assertIn("विकास कुमार", html)
        self.assertIn("पूजा कुमारी", html)
        self.assertIn("कैलाश", html)
        # Check serial numbers
        self.assertIn("101", html)
        self.assertIn("105", html)
        # Check that 5 cards are generated
        card_count = html.count("class=\"parchi-card\"")
        self.assertEqual(card_count, 5)
        # Check 1 single A4 sheet
        page_count = html.count("class=\"page-sheet\"")
        self.assertEqual(page_count, 1)

    def test_empty_voters_404(self):
        resp = self.client.get('/api/print_slips_10up')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
