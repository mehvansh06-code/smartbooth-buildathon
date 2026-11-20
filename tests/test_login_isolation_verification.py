"""
test_login_isolation_verification.py
Verifies:
1. GET /login never auto-bounces to a previous tenant.
2. Logging in with sarana binds strictly to sarana.
3. Logging in with jelatra binds strictly to jelatra.
4. Logging out wipes session & cookies completely.
5. Subsequent visit to / requires login.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app


class TestLoginIsolationVerification(unittest.TestCase):
    def setUp(self):
        os.environ['FORCE_AUTH_CHECK'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('FORCE_AUTH_CHECK', None)

    @patch('user_manager.authenticate_user')
    def test_pure_credential_login_and_logout_lifecycle(self, mock_auth):
        # 1. Unauthenticated visit to / redirects to /login
        resp_root = self.client.get('/')
        self.assertEqual(resp_root.status_code, 302)
        self.assertIn('/login', resp_root.headers.get('Location', ''))

        # 2. GET /login renders clean form with 200 OK
        resp_login_get = self.client.get('/login')
        self.assertEqual(resp_login_get.status_code, 200)
        self.assertIn(b'usernameInput', resp_login_get.data)

        # 3. Login as sarana
        mock_auth.return_value = ({
            'id': 28,
            'user_id': 28,
            'username': 'sarana',
            'full_name': 'Sarana Admin',
            'role': 'admin',
            'role_tier': 2,
            'tenant_slug': 'sarana',
            'assigned_booths': [],
            'is_active': 1
        }, None)

        login_sarana = self.client.post('/login', json={'username': 'sarana', 'password': 'password123'})
        self.assertEqual(login_sarana.status_code, 200)
        data_sarana = login_sarana.get_json()
        self.assertTrue(data_sarana['success'])
        self.assertIn('tenant=sarana', data_sarana['redirect'])

        # 4. While logged in as sarana, GET /login does NOT bounce to jelatra
        resp_login_while_logged = self.client.get('/login')
        self.assertEqual(resp_login_while_logged.status_code, 200)
        self.assertIn(b'usernameInput', resp_login_while_logged.data)

        # 5. Login as jelatra immediately switches session to jelatra
        mock_auth.return_value = ({
            'id': 7,
            'user_id': 7,
            'username': 'jelatra',
            'full_name': 'Jelatra Operator',
            'role': 'operator',
            'role_tier': 2,
            'tenant_slug': 'jelatra',
            'assigned_booths': [],
            'is_active': 1
        }, None)

        login_jelatra = self.client.post('/login', json={'username': 'jelatra', 'password': 'password123'})
        self.assertEqual(login_jelatra.status_code, 200)
        data_jelatra = login_jelatra.get_json()
        self.assertTrue(data_jelatra['success'])
        self.assertIn('tenant=jelatra', data_jelatra['redirect'])

        # 6. Logout cleanly terminates session and wipes cookies
        logout_resp = self.client.get('/logout')
        self.assertEqual(logout_resp.status_code, 302)
        self.assertEqual(logout_resp.headers.get('Location'), '/login')

        cookie_headers = logout_resp.headers.getlist('Set-Cookie')
        self.assertTrue(any('voter_tenant=' in c for c in cookie_headers))
        self.assertTrue(any('session=' in c for c in cookie_headers))

        # 7. Visit to / after logout redirects to /login
        after_logout = self.client.get('/')
        self.assertEqual(after_logout.status_code, 302)
        self.assertIn('/login', after_logout.headers.get('Location', ''))


if __name__ == '__main__':
    unittest.main()
