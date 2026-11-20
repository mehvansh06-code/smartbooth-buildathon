"""
test_split_blueprints_and_cards.py
Unit tests verifying the modular blueprint split and slip template decoupling:
- card_templates & card_generator functions
- turnout_bp, karyakartas_bp, caste_bp, family_bp, campaign_bp endpoint routing
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app
from electionapp.card_templates import (
    format_bilingual,
    build_custom_bg_single_html,
    build_standard_single_html,
)
from electionapp.blueprints import (
    turnout_bp,
    karyakartas_bp,
    caste_bp,
    family_bp,
    campaign_bp
)


class TestSplitBlueprintsAndCards(unittest.TestCase):
    def setUp(self):
        os.environ['FLASK_TESTING'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    def test_blueprint_registration(self):
        registered_bp_names = set(self.app.blueprints.keys())
        expected = {'campaign', 'turnout', 'karyakartas', 'caste', 'family'}
        for bp_name in expected:
            self.assertIn(bp_name, registered_bp_names, f"Blueprint {bp_name} must be registered")

    def test_caste_presets_endpoint(self):
        res = self.client.get('/api/caste/presets')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIsInstance(data.get('presets'), list)
        self.assertTrue(len(data['presets']) > 0)
        # Check that expected preset keys exist
        keys = [p['key'] for p in data['presets']]
        self.assertIn('choudhary_patel', keys)
        self.assertIn('rajput_darbar', keys)

    def test_campaign_openclaw_status(self):
        with patch('meta_whatsapp.check_health') as mock_health:
            mock_health.return_value = {
                'connected': True,
                'display_number': '+91 73001 79679',
                'verified_name': 'Test Org',
                'messaging_limit': 'TIER_10K',
                'quality_rating': 'GREEN'
            }
            res = self.client.get('/api/openclaw_status')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get('status'), 'online')
            self.assertTrue(data.get('is_logged_in'))

    def test_turnout_vote_toggle_validation(self):
        res = self.client.post('/api/vote/toggle', json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertIn('Voter ID is required', data.get('error', ''))

    def test_family_set_head_validation(self):
        res = self.client.post('/api/family/set_head', json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertIn('voter_id is required', data.get('error', ''))

    def test_karyakarta_assign_validation(self):
        res = self.client.post('/api/karyakartas/assign', json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertIn('voter_ids and karyakarta_id are required', data.get('error', ''))

    def test_card_templates_format_bilingual(self):
        res = format_bilingual("RAMESH CHOUDHARY", "रमेश चौधरी")
        self.assertIn("रमेश चौधरी", res)
        self.assertIn("Ramesh Choudhary", res)

        # English only
        res_en = format_bilingual("RAMESH CHOUDHARY", "")
        self.assertEqual(res_en, "Ramesh Choudhary")

        # Identical
        res_same = format_bilingual("Ward 82", "Ward 82")
        self.assertEqual(res_same, "Ward 82")

    def test_card_templates_html_generation(self):
        from electionapp.tenant_branding import get_tenant_branding
        branding = get_tenant_branding('ward82')
        voter = {
            'sl_no': 123,
            'part_no': 82,
            'epic_no': 'ABC1234567',
            'name_en': 'RAMESH',
            'name_hi': 'रमेश',
            'rel_name_en': 'SURESH',
            'rel_name_hi': 'सुरेश',
            'rel_type': 'पिता',
            'age': 35,
            'gender': 'M',
            'house_no': '45-A'
        }
        booth = branding['get_booth'](82)

        html_custom = build_custom_bg_single_html(
            voter, booth, branding, custom_bg_src="data:image/png;base64,abc", tenant='ward82'
        )
        self.assertIn("रमेश", html_custom)
        self.assertIn("123", html_custom)
        self.assertIn("ABC1234567", html_custom)

        html_std = build_standard_single_html(
            voter, booth, branding, tenant='ward82'
        )
        self.assertIn("रमेश", html_std)
        self.assertIn("ABC1234567", html_std)


if __name__ == '__main__':
    unittest.main()
