"""
Tests for route and module permission gating for Voice Broadcast.

Guarantees:
1. When user has allowed_modules = ['search', 'voting_day'] (no 'voice'):
   - Accessing /campaigns/voice returns 403 Forbidden.
   - Accessing /api/voice/dispatch returns 403 Forbidden.
   - Accessing other /api/voice/* endpoints returns 403 Forbidden.
2. When user has allowed_modules = ['voice']:
   - Accessing /campaigns/voice returns 200 OK.
   - Accessing /api/voice/balance returns 200 OK.
3. When user has allowed_modules = ['all']:
   - Accessing /campaigns/voice returns 200 OK.
   - Accessing /api/voice/balance returns 200 OK.
4. Super admin bypasses module restrictions.
"""

import os
import sys
import unittest
from unittest.mock import patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app


class TestVoicePermissions(unittest.TestCase):
    """Verifies PAGE_TO_MODULE and API_TO_MODULE gating in electionapp/__init__.py."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_voice_routes_blocked_when_voice_module_not_allowed(self):
        """User with ['search', 'voting_day'] is blocked with 403 on voice routes."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 101
            sess['username'] = 'operator_no_voice'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = ['search', 'voting_day']

        # 1. UI Page route: /campaigns/voice -> 403 Forbidden
        resp_page = self.client.get('/campaigns/voice')
        self.assertEqual(resp_page.status_code, 403)
        self.assertIn('restricted', resp_page.get_json().get('error', '').lower())

        # 2. Dispatch API route: /api/voice/dispatch -> 403 Forbidden
        resp_dispatch = self.client.post('/api/voice/dispatch', json={'sound_name': 'test'})
        self.assertEqual(resp_dispatch.status_code, 403)
        self.assertIn('restricted', resp_dispatch.get_json().get('error', '').lower())

        # 3. Balance API route: /api/voice/balance -> 403 Forbidden
        resp_bal = self.client.get('/api/voice/balance')
        self.assertEqual(resp_bal.status_code, 403)

        # 4. Sounds API route: /api/voice/sounds -> 403 Forbidden
        resp_snd = self.client.get('/api/voice/sounds')
        self.assertEqual(resp_snd.status_code, 403)

    def test_voice_routes_allowed_when_voice_in_allowed_modules(self):
        """User with ['voice'] is granted access (200 OK) to voice page and balance."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 102
            sess['username'] = 'voice_operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = ['voice']

        resp_page = self.client.get('/campaigns/voice')
        self.assertEqual(resp_page.status_code, 200)

        resp_bal = self.client.get('/api/voice/balance')
        self.assertEqual(resp_bal.status_code, 200)
        self.assertTrue(resp_bal.get_json().get('success'))

    def test_voice_routes_allowed_when_all_in_allowed_modules(self):
        """User with ['all'] has full access to voice console and APIs."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 103
            sess['username'] = 'all_operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = ['all']

        resp_page = self.client.get('/campaigns/voice')
        self.assertEqual(resp_page.status_code, 200)

        resp_bal = self.client.get('/api/voice/balance')
        self.assertEqual(resp_bal.status_code, 200)
        self.assertTrue(resp_bal.get_json().get('success'))

    def test_super_admin_bypasses_module_restrictions(self):
        """Super Admin role has unrestricted access to voice endpoints."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'superadmin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = []  # Even if empty, super admin has full access

        resp_page = self.client.get('/campaigns/voice')
        self.assertEqual(resp_page.status_code, 200)

        resp_bal = self.client.get('/api/voice/balance')
        self.assertEqual(resp_bal.status_code, 200)
        self.assertTrue(resp_bal.get_json().get('success'))


    def test_scoped_operator_cannot_broadcast_to_unauthorized_booth(self):
        """Operator assigned to booth 14 cannot broadcast calls to booth 99."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 202
            sess['username'] = 'booth14_op'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = ['voice', 'all']
            sess['assigned_booth'] = 14
            sess['assigned_booths'] = [14]
            sess['is_scoped'] = True

        payload = {
            'target_type': 'unvoted',
            'booths': [99],
            'sound_name': 'test.wav'
        }
        resp = self.client.post('/api/voice/dispatch', json=payload)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.get_json().get('success'))


if __name__ == '__main__':
    unittest.main()
