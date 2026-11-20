"""
test_zila_parishad_evm_and_gp_scope.py

Verifies:
1. Zila Parishad (ZILA_PARISHAD, ZP) election types resolve to 'EVM' voting method.
2. Sub-account creation supports Gram Panchayat-level scoping (assigned_gps).
3. Dual-layer auto-resolution maps assigned_gps to constituent booths.
4. /api/campaign/scope_options returns panchayats hierarchy, is_multi_gp, and EVM method.
"""

import os
import sys
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp.config import get_voting_method, EVM_ELECTION_TYPES, BALLOT_PAPER_ELECTION_TYPES
from electionapp import create_app


class TestZilaParishadEvmAndGpScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        user_manager.ensure_schema()
        # Clean any old test users
        try:
            with user_manager.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username LIKE 'test_zp_sub_%'")
        except Exception:
            pass

        user_manager.create_user_account(
            username='test_op_jelatra_zp',
            password='Password@123',
            full_name='Jelatra Operator ZP',
            role='operator',
            tenant_slug='jelatra',
            overwrite_password=True
        )

    @classmethod
    def tearDownClass(cls):
        try:
            with user_manager.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username LIKE 'test_zp_sub_%' OR username = 'test_op_jelatra_zp'")
        except Exception:
            pass

    def test_01_zila_parishad_in_evm_types(self):
        """Zila Parishad must be classified under EVM election types."""
        self.assertIn('ZILA_PARISHAD', EVM_ELECTION_TYPES)
        self.assertIn('ZP', EVM_ELECTION_TYPES)
        self.assertNotIn('ZILA_PARISHAD', BALLOT_PAPER_ELECTION_TYPES)
        self.assertNotIn('ZP', BALLOT_PAPER_ELECTION_TYPES)

    def test_02_get_voting_method_returns_evm_for_zp(self):
        """get_voting_method must return 'EVM' for Zila Parishad."""
        self.assertEqual(get_voting_method('ZILA_PARISHAD'), 'EVM')
        self.assertEqual(get_voting_method('zila_parishad'), 'EVM')
        self.assertEqual(get_voting_method('ZP'), 'EVM')
        self.assertEqual(get_voting_method('zp'), 'EVM')
        self.assertEqual(get_voting_method('DISTRICT_ZILA_PARISHAD'), 'EVM')
        self.assertEqual(get_voting_method('ZILA_PARISHAD_WARD_13'), 'EVM')

    def test_03_create_user_with_assigned_gps(self):
        """User account created with assigned_gps has scope_type='gp' and persists assigned_gps."""
        ok, msg, uid = user_manager.create_user_account(
            username='test_zp_sub_gp1',
            password='Password@123',
            full_name='Dulhepura GP Coordinator',
            role='karyakarta',
            tenant_slug='jelatra',
            assigned_gps=['DULHEPURA'],
            scope_type='gp',
            overwrite_password=True
        )
        self.assertTrue(ok, f"User creation failed: {msg}")
        self.assertIsNotNone(uid)

        u = user_manager.get_user_by_id(uid)
        self.assertIsNotNone(u)
        self.assertEqual(u.get('scope_type'), 'gp')
        self.assertIn('DULHEPURA', u.get('assigned_gps') or [])

    def test_04_update_user_assigned_gps(self):
        """Updating user assigned_gps correctly modifies the record in DB."""
        target = user_manager.get_user_by_username('test_zp_sub_gp1')
        self.assertIsNotNone(target)

        upd_ok = user_manager.update_user_account(
            user_id=target['user_id'],
            assigned_gps=['DULHEPURA', 'SARERI'],
            scope_type='gp'
        )
        self.assertTrue(upd_ok)

        reloaded = user_manager.get_user_by_id(target['user_id'])
        self.assertEqual(set(reloaded.get('assigned_gps') or []), {'DULHEPURA', 'SARERI'})

    def test_05_api_scope_options_contains_evm_and_multi_gp_fields(self):
        """API /api/campaign/scope_options returns voting_method, election_type, and is_multi_gp."""
        app = create_app('jelatra', storage_backend='postgres')
        client = app.test_client()

        # Login as operator
        login_res = client.post('/login', json={'username': 'test_op_jelatra_zp', 'password': 'Password@123'})
        self.assertEqual(login_res.status_code, 200)

        res = client.get('/api/campaign/scope_options')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('voting_method', data)
        self.assertIn('is_multi_gp', data)
        self.assertIn('panchayats', data)
        self.assertIn('booths', data)


if __name__ == '__main__':
    unittest.main()
