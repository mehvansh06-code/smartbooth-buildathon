"""
test_session_revocation.py
Unit tests verifying server-side session revocation:
1. Deleted user account: active session cookie is immediately rejected and cleared.
2. Inactive user account (is_active=False or campaign paused): session is rejected.
3. Password changed / hash mismatch: session is rejected.
4. Active user: session is accepted.
"""

import os
import sys
import unittest
from unittest.mock import patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app


class TestSessionRevocation(unittest.TestCase):
    def setUp(self):
        os.environ['FORCE_AUTH_CHECK'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('FORCE_AUTH_CHECK', None)

    @patch('user_manager.validate_user_session_active')
    def test_deleted_user_session_is_immediately_evicted(self, mock_validate):
        """When user_id is deleted from database, validate_user_session_active returns (False, 'deleted'),
        causing immediate session clearance and 401/redirect."""
        mock_validate.return_value = (False, 'deleted')

        # 1. Protected web page should redirect to login with error
        with self.client.session_transaction() as sess:
            sess['user_id'] = 99999  # Deleted user
            sess['username'] = 'deleted_operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

        page_resp = self.client.get('/')
        self.assertEqual(page_resp.status_code, 302)
        self.assertIn('/login?error=deleted', page_resp.headers.get('Location', ''))

        # 2. Protected API endpoint should return 401
        with self.client.session_transaction() as sess:
            sess['user_id'] = 99999
            sess['username'] = 'deleted_operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

        resp = self.client.get('/api/search?q=test')
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertEqual(data.get('code'), 'deleted')

    @patch('user_manager.validate_user_session_active')
    def test_inactive_user_session_is_evicted(self, mock_validate):
        """When user account is disabled or campaign is paused, session is rejected with 'inactive'."""
        mock_validate.return_value = (False, 'inactive')

        with self.client.session_transaction() as sess:
            sess['user_id'] = 88888
            sess['username'] = 'inactive_user'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

        resp = self.client.get('/api/search?q=test')
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertEqual(data.get('code'), 'inactive')

    @patch('user_manager.validate_user_session_active')
    def test_password_changed_evicts_stale_sessions(self, mock_validate):
        """When user password changes, old sessions with stale hash prefix are rejected."""
        mock_validate.return_value = (False, 'password_changed')

        with self.client.session_transaction() as sess:
            sess['user_id'] = 77777
            sess['username'] = 'pwd_changed_user'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['pw_hash'] = 'old_hash12'

        resp = self.client.get('/api/search?q=test')
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertEqual(data.get('code'), 'password_changed')

    def test_validate_user_session_active_unit_logic(self):
        """Direct test of user_manager.validate_user_session_active with mocked DB cursor."""
        with patch('user_manager.get_db') as mock_db:
            mock_conn = mock_db.return_value.__enter__.return_value
            mock_cur = mock_conn.cursor.return_value.__enter__.return_value

            # 1. User not found (deleted)
            mock_cur.fetchone.return_value = None
            is_valid, reason = user_manager.validate_user_session_active(99999)
            self.assertFalse(is_valid)
            self.assertEqual(reason, 'deleted')

            # 2. User inactive
            mock_cur.fetchone.return_value = (False, 'scrypt:abc', None)
            is_valid, reason = user_manager.validate_user_session_active(12345)
            self.assertFalse(is_valid)
            self.assertEqual(reason, 'inactive')

            # 3. User active, matching pw_hash
            mock_cur.fetchone.return_value = (True, 'scrypt:abc1234567', None)
            is_valid, reason = user_manager.validate_user_session_active(12345, expected_hash_prefix='scrypt:abc')
            self.assertTrue(is_valid)
            self.assertIsNone(reason)

            # 4. User active, mismatched pw_hash
            mock_cur.fetchone.return_value = (True, 'scrypt:NEW1234567', None)
            is_valid, reason = user_manager.validate_user_session_active(12345, expected_hash_prefix='scrypt:OLD')
            self.assertFalse(is_valid)
            self.assertEqual(reason, 'password_changed')


if __name__ == '__main__':
    unittest.main()
