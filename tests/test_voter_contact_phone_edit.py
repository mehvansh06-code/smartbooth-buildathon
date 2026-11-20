"""
test_voter_contact_phone_edit.py
Unit tests verifying single and bulk contact phone editing, phone clearing,
and targeted phone allocation modes (missing_only, mukhiya_only, all) in
api_voter_profile_update.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app


class TestVoterContactPhoneEdit(unittest.TestCase):
    def setUp(self):
        os.environ['FLASK_TESTING'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_single_voter_phone_update(self, mock_get_tenant):
        """Verifies saving phone for a single voter sets contact_phone and RECONCILED_V2."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_id': 101,
            'contact_phone': '9828579249'
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['updated']['contact_phone'], '9828579249')

        # Check executed SQL
        calls = [c[0] for c in mock_cur.execute.call_args_list]
        update_calls = [c for c in calls if 'UPDATE voters SET' in c[0]]
        self.assertTrue(len(update_calls) >= 1)
        sql, params = update_calls[0]
        self.assertIn('contact_phone = %s', sql)
        self.assertIn("phone_source_type = 'RECONCILED_V2'", sql)
        self.assertIn('9828579249', params)

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_single_voter_phone_clear(self, mock_get_tenant):
        """Verifies clearing phone for a single voter sets contact_phone = NULL and phone_source_type = NULL."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_id': 101,
            'contact_phone': ''
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertIsNone(data['updated']['contact_phone'])

        calls = [c[0] for c in mock_cur.execute.call_args_list]
        update_calls = [c for c in calls if 'UPDATE voters SET' in c[0]]
        self.assertTrue(len(update_calls) >= 1)
        sql, params = update_calls[0]
        self.assertIn('contact_phone = NULL', sql)
        self.assertIn('phone_source_type = NULL', sql)

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_bulk_voters_phone_missing_only_mode(self, mock_get_tenant):
        """Verifies bulk edit with missing_only mode updates only voters with null/empty phones."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_ids': [101, 102],
            'contact_phone': '9828579249',
            'phone_target_mode': 'missing_only',
            'voter_category': 100
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])

        calls = [c[0] for c in mock_cur.execute.call_args_list]
        # Look for the targeted phone update
        targeted_calls = [c for c in calls if "phone_source_type = 'RECONCILED_V2_KINSHIP'" in c[0]]
        self.assertEqual(len(targeted_calls), 1)
        sql, params = targeted_calls[0]
        self.assertIn('contact_phone IS NULL OR trim(contact_phone) = \'\'', sql)
        self.assertEqual(params[0], '9828579249')
        self.assertEqual(params[1], 3)
        self.assertEqual(params[2], [101, 102])

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_bulk_voters_phone_mukhiya_only_mode(self, mock_get_tenant):
        """Verifies bulk edit with mukhiya_only mode updates only the designated family head."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_ids': [101, 102],
            'mukhiya_voter_id': 102,
            'contact_phone': '9828579249',
            'phone_target_mode': 'mukhiya_only'
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])

        calls = [c[0] for c in mock_cur.execute.call_args_list]
        targeted_calls = [c for c in calls if 'voter_id = %s OR legacy_id = %s' in c[0] and 'contact_phone = %s' in c[0]]
        self.assertEqual(len(targeted_calls), 1)
        sql, params = targeted_calls[0]
        self.assertEqual(params[0], '9828579249')
        self.assertEqual(params[2], 102)
        self.assertEqual(params[3], 102)

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_bulk_voters_phone_all_mode(self, mock_get_tenant):
        """Verifies bulk edit with 'all' mode updates contact_phone for all selected voters."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_ids': [101, 102],
            'contact_phone': '9828579249',
            'phone_target_mode': 'all'
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])

        calls = [c[0] for c in mock_cur.execute.call_args_list]
        update_calls = [c for c in calls if 'UPDATE voters SET' in c[0] and 'contact_phone = %s' in c[0]]
        self.assertEqual(len(update_calls), 1)
        sql, params = update_calls[0]
        self.assertIn("voter_id = ANY(%s)", sql)
        self.assertIn('9828579249', params)


if __name__ == '__main__':
    unittest.main()
