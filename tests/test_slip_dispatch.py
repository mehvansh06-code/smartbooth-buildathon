"""
test_slip_dispatch.py
Unit tests verifying slip dispatch engine and card templates:
- api_send_openclaw with single voter (Single)
- api_send_openclaw with same household multi-voter selection (Family)
- api_send_openclaw with unrelated/multi-household selection (Combined)
- card_templates header/title switching logic between Family and Combined
"""

import unittest
from unittest.mock import patch, MagicMock
from electionapp import create_app
from electionapp.card_templates import (
    build_custom_bg_family_html,
    build_standard_family_html
)


class TestSlipDispatchEngine(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

    @patch('electionapp.card_generator.generate_all_bjp_voter_card_pages')
    @patch('electionapp.blueprints.dispatch.whatsapp_quota.reserve')
    def test_send_openclaw_single_voter(self, mock_reserve, mock_gen_cards):
        """Single voter dispatch produces slip_type='Single' and voter name."""
        mock_gen_cards.return_value = []
        mock_credit = MagicMock()
        mock_credit.remaining = 1
        mock_credit.success = True
        mock_reserve.return_value = mock_credit

        # Mock repo to return a single voter
        with patch.object(self.app.extensions['repo'], 'get_voters_by_ids') as mock_get_voters:
            mock_get_voters.return_value = [
                {'id': 101, 'name_hi': 'रमेश कुमार', 'name_en': 'Ramesh Kumar', 'house_no': '12', 'part_no': 1, 'sl_no': 5}
            ]

            resp = self.client.post('/api/send_openclaw', json={
                'phone': '9829012345',
                'voter_ids': [101],
                'slip_type': 'Single'
            })

            # Check that generate_all_bjp_voter_card_pages was called with slip_type='Single'
            self.assertTrue(mock_gen_cards.called)
            args, kwargs = mock_gen_cards.call_args
            self.assertEqual(kwargs.get('slip_type'), 'Single')

    @patch('electionapp.card_generator.generate_all_bjp_voter_card_pages')
    @patch('electionapp.blueprints.dispatch.whatsapp_quota.reserve')
    def test_send_openclaw_same_household_family(self, mock_reserve, mock_gen_cards):
        """Multiple voters from same house produce slip_type='Family' and family p_name."""
        mock_gen_cards.return_value = []
        mock_credit = MagicMock()
        mock_credit.remaining = 1
        mock_credit.success = True
        mock_reserve.return_value = mock_credit

        with patch.object(self.app.extensions['repo'], 'get_voters_by_ids') as mock_get_voters:
            mock_get_voters.return_value = [
                {'id': 101, 'name_hi': 'रमेश कुमार', 'name_en': 'Ramesh Kumar', 'house_no': '12', 'part_no': 1, 'sl_no': 5},
                {'id': 102, 'name_hi': 'सुनीता देवी', 'name_en': 'Sunita Devi', 'house_no': '12', 'part_no': 1, 'sl_no': 6}
            ]

            resp = self.client.post('/api/send_openclaw', json={
                'phone': '9829012345',
                'voter_ids': [101, 102]
            })

            self.assertTrue(mock_gen_cards.called)
            args, kwargs = mock_gen_cards.call_args
            self.assertEqual(kwargs.get('slip_type'), 'Family')

    @patch('electionapp.card_generator.generate_all_bjp_voter_card_pages')
    @patch('electionapp.blueprints.dispatch.whatsapp_quota.reserve')
    def test_send_openclaw_unrelated_households_combined(self, mock_reserve, mock_gen_cards):
        """Multiple voters from different houses produce slip_type='Combined'."""
        mock_gen_cards.return_value = []
        mock_credit = MagicMock()
        mock_credit.remaining = 1
        mock_credit.success = True
        mock_reserve.return_value = mock_credit

        with patch.object(self.app.extensions['repo'], 'get_voters_by_ids') as mock_get_voters:
            mock_get_voters.return_value = [
                {'id': 101, 'name_hi': 'रमेश कुमार', 'name_en': 'Ramesh Kumar', 'house_no': '12', 'part_no': 1, 'sl_no': 5},
                {'id': 103, 'name_hi': 'महेश सिंह', 'name_en': 'Mahesh Singh', 'house_no': '45', 'part_no': 1, 'sl_no': 20}
            ]

            resp = self.client.post('/api/send_openclaw', json={
                'phone': '9829012345',
                'voter_ids': [101, 103]
            })

            self.assertTrue(mock_gen_cards.called)
            args, kwargs = mock_gen_cards.call_args
            self.assertEqual(kwargs.get('slip_type'), 'Combined')


class TestCardTemplates(unittest.TestCase):
    def setUp(self):
        self.branding = {
            'lotus_src': 'lotus.png',
            'candidate_src': 'cand.png',
            'has_symbol': True,
            'has_candidate': True,
            'has_ballot': True,
            'ballot_no': 1,
            'symbol_name_hi': 'कमल',
            'party_line': 'भाजपा',
            'candidate_disp': 'सुरेश कुमार',
            'place_hi': 'वार्ड 82',
            'appeal_msg': 'मतदान करें',
            'slogan': 'सबका साथ',
            'theme_color': '#ea580c',
            'get_booth': lambda p: {'name': f'बूथ #{p}', 'area': 'क्षेत्र'}
        }

    def test_custom_bg_family_html_same_house(self):
        voters = [
            {'id': 1, 'name_hi': 'रमेश', 'house_no': '10', 'part_no': 1, 'sl_no': 1},
            {'id': 2, 'name_hi': 'सुरेश', 'house_no': '10', 'part_no': 1, 'sl_no': 2}
        ]
        html = build_custom_bg_family_html(voters, voters, self.branding, 'bg.png', 1, 1, slip_type='Family')
        self.assertIn('परिवार मतदाता पर्ची', html)
        self.assertNotIn('संयुक्त मतदाता पर्ची', html)

    def test_custom_bg_family_html_multiple_houses(self):
        voters = [
            {'id': 1, 'name_hi': 'रमेश', 'house_no': '10', 'part_no': 1, 'sl_no': 1},
            {'id': 2, 'name_hi': 'सुरेश', 'house_no': '25', 'part_no': 1, 'sl_no': 2}
        ]
        # Even if slip_type='Family' is passed, multiple houses force title to 'संयुक्त मतदाता पर्ची'
        html = build_custom_bg_family_html(voters, voters, self.branding, 'bg.png', 1, 1, slip_type='Family')
        self.assertIn('संयुक्त मतदाता पर्ची', html)
        self.assertNotIn('परिवार मतदाता पर्ची', html)

    def test_custom_bg_family_html_combined_slip_type(self):
        voters = [
            {'id': 1, 'name_hi': 'रमेश', 'house_no': '10', 'part_no': 1, 'sl_no': 1},
            {'id': 2, 'name_hi': 'सुरेश', 'house_no': '10', 'part_no': 1, 'sl_no': 2}
        ]
        html = build_custom_bg_family_html(voters, voters, self.branding, 'bg.png', 1, 1, slip_type='Combined')
        self.assertIn('संयुक्त मतदाता पर्ची', html)
        self.assertNotIn('परिवार मतदाता पर्ची', html)

    def test_standard_family_html_same_house(self):
        voters = [
            {'id': 1, 'name_hi': 'रमेश', 'house_no': '10', 'part_no': 1, 'sl_no': 1},
            {'id': 2, 'name_hi': 'सुरेश', 'house_no': '10', 'part_no': 1, 'sl_no': 2}
        ]
        html = build_standard_family_html(voters, self.branding, 1, 1, slip_type='Family', tenant='ward82', all_voters=voters)
        self.assertIn('परिवार मतदाता पर्ची', html)
        self.assertNotIn('संयुक्त मतदाता पर्ची', html)

    def test_standard_family_html_multiple_houses(self):
        voters = [
            {'id': 1, 'name_hi': 'रमेश', 'house_no': '10', 'part_no': 1, 'sl_no': 1},
            {'id': 2, 'name_hi': 'सुरेश', 'house_no': '99', 'part_no': 1, 'sl_no': 2}
        ]
        html = build_standard_family_html(voters, self.branding, 1, 1, slip_type='Family', tenant='ward82', all_voters=voters)
        self.assertIn('संयुक्त मतदाता पर्ची', html)
        self.assertNotIn('परिवार मतदाता पर्ची', html)

    def test_standard_family_html_combined_slip_type(self):
        voters = [
            {'id': 1, 'name_hi': 'रमेश', 'house_no': '10', 'part_no': 1, 'sl_no': 1},
            {'id': 2, 'name_hi': 'सुरेश', 'house_no': '10', 'part_no': 1, 'sl_no': 2}
        ]
        html = build_standard_family_html(voters, self.branding, 1, 1, slip_type='Combined', tenant='ward82', all_voters=voters)
        self.assertIn('संयुक्त मतदाता पर्ची', html)
        self.assertNotIn('परिवार मतदाता पर्ची', html)


if __name__ == '__main__':
    unittest.main()
