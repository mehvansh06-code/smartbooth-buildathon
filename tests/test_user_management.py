"""
test_user_management.py
Comprehensive test suite for editing user accounts across:
1. Super Admin API: POST /api/admin/users/<id>/update
2. Single User Details: GET /api/admin/users/<id>
3. Campaign-Level Sub-Account API: POST /api/users/<id>/update
4. Username uniqueness validation & duplicate rejection
5. Optional password update during profile edit
6. Last active super_admin lockout prevention guard
7. Privilege escalation prevention (operators cannot create/promote to super_admin or reseller)
8. Campaign isolation (operators cannot reassign users across tenants)
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import psycopg2
from werkzeug.security import check_password_hash

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app


class TestUserManagement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        user_manager.ensure_schema()
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

        # Clean old test users
        try:
            with user_manager.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username LIKE 'test_mgmt_%'")
        except Exception:
            pass

        # 1. Test Super Admin
        _, _, cls.super_id = user_manager.create_user_account(
            username='test_mgmt_super',
            password='SuperPassword@123',
            full_name='Super Administrator',
            role='super_admin',
            tenant_slug='*'
        )

        # 2. Test Operator
        _, _, cls.op_id = user_manager.create_user_account(
            username='test_mgmt_op_ward82',
            password='OperatorPassword@123',
            full_name='Ward 82 Operator',
            role='operator',
            tenant_slug='ward82'
        )

        # 3. Test Sub-Account / Karyakarta under ward82
        _, _, cls.karya_id = user_manager.create_user_account(
            username='test_mgmt_karya_1',
            password='KaryaPassword@123',
            full_name='Karyakarta One',
            role='karyakarta',
            tenant_slug='ward82',
            assigned_booths=[1, 2]
        )

        # 4. Another user for duplicate collision testing
        _, _, cls.other_id = user_manager.create_user_account(
            username='test_mgmt_existing_user',
            password='OtherPassword@123',
            full_name='Existing Other User',
            role='operator',
            tenant_slug='ward82'
        )

    @classmethod
    def tearDownClass(cls):
        try:
            with user_manager.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username LIKE 'test_mgmt_%'")
        except Exception:
            pass

    def test_01_super_admin_get_user_profile(self):
        """GET /api/admin/users/<id> returns formatted user dictionary."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_id
            sess['role'] = 'super_admin'
            sess['username'] = 'test_mgmt_super'

        res = self.client.get(f'/api/admin/users/{self.karya_id}')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['user']['username'], 'test_mgmt_karya_1')
        self.assertEqual(data['user']['full_name'], 'Karyakarta One')
        self.assertEqual(data['user']['role'], 'karyakarta')
        self.assertIn(1, data['user']['assigned_booths'])

    def test_02_super_admin_update_user_profile(self):
        """Super Admin can update full_name, phone, role, and assigned booths."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_id
            sess['role'] = 'super_admin'
            sess['username'] = 'test_mgmt_super'

        payload = {
            'full_name': 'Karyakarta Updated Name',
            'phone': '9876543210',
            'role': 'booth_agent',
            'assigned_booths': [3, 4, 5],
            'is_active': True
        }
        res = self.client.post(f'/api/admin/users/{self.karya_id}/update', json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])

        # Verify updated data in database
        u = user_manager.get_user_by_id(self.karya_id)
        self.assertEqual(u['full_name'], 'Karyakarta Updated Name')
        self.assertEqual(u['phone'], '9876543210')
        self.assertEqual(u['role'], 'booth_agent')
        self.assertEqual(sorted(u['assigned_booths']), [3, 4, 5])

    def test_03_username_update_and_uniqueness_validation(self):
        """Updating username must reject collision with existing user and succeed with fresh username."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_id
            sess['role'] = 'super_admin'
            sess['username'] = 'test_mgmt_super'

        # Attempt to take already existing username
        res = self.client.post(f'/api/admin/users/{self.karya_id}/update', json={
            'username': 'test_mgmt_existing_user'
        })
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data['success'])
        self.assertIn('उपयोग में है', data['error'])

        # Update with unique username
        res_ok = self.client.post(f'/api/admin/users/{self.karya_id}/update', json={
            'username': 'test_mgmt_karya_renamed'
        })
        self.assertEqual(res_ok.status_code, 200)
        u = user_manager.get_user_by_id(self.karya_id)
        self.assertEqual(u['username'], 'test_mgmt_karya_renamed')

    def test_04_optional_password_update_during_profile_edit(self):
        """Setting a new password via update profile hashes it and allows login with new password."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_id
            sess['role'] = 'super_admin'
            sess['username'] = 'test_mgmt_super'

        res = self.client.post(f'/api/admin/users/{self.karya_id}/update', json={
            'password': 'BrandNewPassword@2026'
        })
        self.assertEqual(res.status_code, 200)

        # Authenticate with the new password
        user, err = user_manager.authenticate_user('test_mgmt_karya_renamed', 'BrandNewPassword@2026')
        self.assertIsNone(err)
        self.assertIsNotNone(user)
        self.assertEqual(user['user_id'], self.karya_id)

    def test_05_prevent_lockout_of_last_super_admin(self):
        """System must reject demoting or disabling the only active super_admin."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.super_id
            sess['role'] = 'super_admin'
            sess['username'] = 'test_mgmt_super'

        # Attempt to disable super admin
        res = self.client.post(f'/api/admin/users/{self.super_id}/update', json={
            'is_active': False
        })
        # If there are no other active super admins, it must return 400
        with user_manager.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users WHERE role = 'super_admin' AND is_active = TRUE AND user_id != %s", (self.super_id,))
                other_count = cur.fetchone()[0]

        if other_count == 0:
            self.assertEqual(res.status_code, 400)
            self.assertIn('सक्रिय सुपर एडमिन', res.get_json()['error'])

    def test_06_campaign_operator_update_sub_account(self):
        """Campaign operator can update sub-accounts within their assigned campaign via /api/users/<id>/update."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_mgmt_op_ward82'

        payload = {
            'full_name': 'Karyakarta Sub-Account Edited',
            'phone': '9111222333',
            'scope_type': 'booth',
            'assigned_booths': [10, 11]
        }
        res = self.client.post(f'/api/users/{self.karya_id}/update', json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])

        u = user_manager.get_user_by_id(self.karya_id)
        self.assertEqual(u['full_name'], 'Karyakarta Sub-Account Edited')
        self.assertEqual(u['phone'], '9111222333')
        self.assertEqual(sorted(u['assigned_booths']), [10, 11])

    def test_07_privilege_escalation_guard(self):
        """Campaign operators cannot promote sub-accounts to super_admin or reseller."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_mgmt_op_ward82'

        res = self.client.post(f'/api/users/{self.karya_id}/update', json={
            'role': 'super_admin'
        })
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.get_json()['success'])

        res_reseller = self.client.post(f'/api/users/{self.karya_id}/update', json={
            'role': 'reseller'
        })
        self.assertEqual(res_reseller.status_code, 403)
        self.assertFalse(res_reseller.get_json()['success'])

    def test_08_cross_tenant_isolation_guard(self):
        """Operators cannot move accounts to other campaigns."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_mgmt_op_ward82'

        res = self.client.post(f'/api/users/{self.karya_id}/update', json={
            'tenant_slug': 'sanchore'
        })
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.get_json()['success'])

    def test_09_send_credentials_whatsapp_invalid_phone(self):
        """User without valid 10-digit phone number returns 400."""
        user_manager.update_user_account(self.karya_id, phone='12345')
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_mgmt_op_ward82'

        res = self.client.post(f'/api/users/{self.karya_id}/send_credentials_whatsapp', json={'password': 'abc'})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data['success'])
        self.assertIn('कम से कम 10 अंक', data['error'])

    def test_10_send_credentials_whatsapp_success(self):
        """Valid user with phone sends WhatsApp message and consumes quota."""
        user_manager.update_user_account(self.karya_id, phone='9829012345')
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_mgmt_op_ward82'

        fake_credit = MagicMock()
        fake_credit.__enter__.return_value = fake_credit
        fake_credit.__exit__.return_value = None

        with patch('electionapp.whatsapp_quota.reserve', return_value=fake_credit) as mock_res, \
             patch('meta_whatsapp.send_credentials_template', return_value=(True, {'messages': [{'id': 'msg_123'}]}, None)) as mock_send:
            res = self.client.post(f'/api/users/{self.karya_id}/send_credentials_whatsapp', json={'password': 'SecretPass@123'})
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data['success'])
            self.assertEqual(data['phone'], '9829012345')
            mock_res.assert_called_once()
            mock_send.assert_called_once()
            self.assertEqual(mock_send.call_args.kwargs.get('password'), 'SecretPass@123')
            u = user_manager.get_user_by_id(self.karya_id)
            self.assertEqual(mock_send.call_args.kwargs.get('username'), u['username'])

    def test_11_send_credentials_whatsapp_quota_blocked(self):
        """Quota error returns 429 quota_blocked."""
        from electionapp.whatsapp_quota import QuotaExhausted
        user_manager.update_user_account(self.karya_id, phone='9829012345')
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'
            sess['username'] = 'test_mgmt_op_ward82'

        with patch('electionapp.whatsapp_quota.reserve', side_effect=QuotaExhausted("Credit exhausted")):
            res = self.client.post(f'/api/users/{self.karya_id}/send_credentials_whatsapp', json={'password': 'pwd'})
            self.assertEqual(res.status_code, 429)
            data = res.get_json()
            self.assertFalse(data['success'])
            self.assertTrue(data.get('quota_blocked'))

    def test_12_send_credentials_whatsapp_cross_tenant_denied(self):
        """Operator from another tenant cannot send credentials."""
        user_manager.update_user_account(self.karya_id, phone='9829012345')
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.op_id
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'other_campaign'
            sess['username'] = 'other_op'

        res = self.client.post(f'/api/users/{self.karya_id}/send_credentials_whatsapp', json={'password': 'pwd'})
        self.assertEqual(res.status_code, 403)
        data = res.get_json()
        self.assertFalse(data['success'])


if __name__ == '__main__':
    unittest.main()

