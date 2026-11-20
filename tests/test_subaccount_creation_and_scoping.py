"""
test_subaccount_creation_and_scoping.py
Comprehensive test suite verifying:
1. Campaign operators can create sub-accounts with booth/ward/village scoping.
2. Quota limits set by Super Admin are strictly enforced (HTTP 400 on limit reached).
3. Super Admin can update the campaign sub-account quota.
4. Privilege escalation prevention: operators cannot create admin or super_admin.
5. Cross-tenant isolation: operators cannot create accounts for other tenants.
6. Search scoping: sub-accounts only see voters within their assigned booths/wards/villages.
7. Voting day war room: viewers cannot mark voted; booth agents cannot mark outside assigned booths.
8. Sub-account deletion and password reset with tenant ownership checks.
"""

import os
import sys
import unittest
import psycopg2

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app


class TestSubAccountCreationAndScoping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        user_manager.ensure_schema()

        # Clean any old test users
        try:
            with user_manager.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username LIKE 'test_sub_%' OR username IN ('test_op_jelatra', 'test_super_adm', 'test_op_other')")
        except Exception:
            pass

        # Create test super_admin
        user_manager.create_user_account(
            username='test_super_adm',
            password='Password@123',
            full_name='Test Super Admin',
            role='super_admin',
            tenant_slug='*'
        )

        # Create test operator for jelatra
        user_manager.create_user_account(
            username='test_op_jelatra',
            password='Password@123',
            full_name='Jelatra Operator',
            role='operator',
            tenant_slug='jelatra'
        )

        # Create test operator for another tenant (e.g. ward82)
        user_manager.create_user_account(
            username='test_op_other',
            password='Password@123',
            full_name='Other Operator',
            role='operator',
            tenant_slug='ward82'
        )

    @classmethod
    def tearDownClass(cls):
        try:
            with user_manager.get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE username LIKE 'test_sub_%' OR username IN ('test_op_jelatra', 'test_super_adm', 'test_op_other')")
        except Exception:
            pass

    def setUp(self):
        os.environ['FORCE_AUTH_CHECK'] = '1'
        user_manager.set_tenant_sub_account_limit('jelatra', 50)
        with user_manager.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username LIKE 'test_sub_%'")
        self.app = create_app('jelatra', storage_backend='postgres')
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('FORCE_AUTH_CHECK', None)
        user_manager.set_tenant_sub_account_limit('jelatra', 50)
        with user_manager.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username LIKE 'test_sub_%'")

    def test_operator_create_booth_scoped_subaccount(self):
        """Campaign operator can create a sub-account scoped to specific booths."""
        # 1. Login as jelatra operator
        login_res = self.client.post('/login', json={
            'username': 'test_op_jelatra',
            'password': 'Password@123'
        })
        self.assertEqual(login_res.status_code, 200)

        # 2. Create booth agent for booth 1
        create_res = self.client.post('/api/users/create', json={
            'username': 'test_sub_booth1',
            'password': 'Password@123',
            'full_name': 'Booth 1 Agent',
            'phone': '9829011111',
            'role': 'booth_agent',
            'tenant_slug': 'jelatra',
            'scope_type': 'booth',
            'assigned_booths': [1]
        })
        self.assertIn(create_res.status_code, [200, 201])
        data = create_res.get_json()
        self.assertTrue(data['success'])

        # 3. Verify in database
        u = user_manager.get_user_by_username('test_sub_booth1')
        self.assertIsNotNone(u)
        self.assertEqual(u['assigned_booths'], [1])
        self.assertEqual(u['scope_type'], 'booth')
        self.assertEqual(u['role'], 'booth_agent')
        self.assertEqual(u['tenant_slug'], 'jelatra')

    def test_privilege_escalation_blocked(self):
        """Operators cannot create admin or super_admin accounts."""
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})

        # Attempt to create super_admin
        res = self.client.post('/api/users/create', json={
            'username': 'test_sub_fake_super',
            'password': 'Password@123',
            'full_name': 'Fake Super Admin',
            'role': 'super_admin',
            'tenant_slug': 'jelatra'
        })
        self.assertEqual(res.status_code, 403)

        # Attempt to create admin
        res2 = self.client.post('/api/users/create', json={
            'username': 'test_sub_fake_admin',
            'password': 'Password@123',
            'full_name': 'Fake Admin',
            'role': 'admin',
            'tenant_slug': 'jelatra'
        })
        self.assertEqual(res2.status_code, 403)

    def test_cross_tenant_subaccount_creation_blocked(self):
        """Operator cannot create sub-accounts for other campaigns."""
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})

        res = self.client.post('/api/users/create', json={
            'username': 'test_sub_cross',
            'password': 'Password@123',
            'full_name': 'Cross Tenant Agent',
            'role': 'booth_agent',
            'tenant_slug': 'ward82',
            'assigned_booths': [1]
        })
        self.assertEqual(res.status_code, 403)

    def test_quota_limit_enforcement(self):
        """Super admin sets quota to 2; 3rd account creation attempt is rejected with 400."""
        # 1. Super admin sets quota for jelatra to 2
        user_manager.set_tenant_sub_account_limit('jelatra', 2)

        # Clear existing test sub accounts in jelatra
        with user_manager.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE tenant_slug = 'jelatra' AND username LIKE 'test_sub_%'")

        # 2. Login as jelatra operator
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})

        # Account 1 -> Success
        res1 = self.client.post('/api/users/create', json={
            'username': 'test_sub_quota1',
            'password': 'Password@123',
            'full_name': 'Quota 1',
            'role': 'karyakarta',
            'tenant_slug': 'jelatra'
        })
        self.assertIn(res1.status_code, [200, 201])

        # Account 2 -> Success
        res2 = self.client.post('/api/users/create', json={
            'username': 'test_sub_quota2',
            'password': 'Password@123',
            'full_name': 'Quota 2',
            'role': 'karyakarta',
            'tenant_slug': 'jelatra'
        })
        self.assertIn(res2.status_code, [200, 201])

        # Account 3 -> Rejected with 400 (quota limit exceeded)
        res3 = self.client.post('/api/users/create', json={
            'username': 'test_sub_quota3',
            'password': 'Password@123',
            'full_name': 'Quota 3',
            'role': 'karyakarta',
            'tenant_slug': 'jelatra'
        })
        self.assertEqual(res3.status_code, 400)
        self.assertIn('Sub-account quota', res3.get_json()['error'])

        # 3. Super admin increases limit to 5 via API endpoint
        self.client.post('/login', json={'username': 'test_super_adm', 'password': 'Password@123'})
        limit_res = self.client.post('/api/admin/campaigns/jelatra/sub_account_limit', json={'limit': 5})
        self.assertEqual(limit_res.status_code, 200)
        self.assertEqual(limit_res.get_json()['limit'], 5)

        # 4. Now jelatra operator can create account 3
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})
        res4 = self.client.post('/api/users/create', json={
            'username': 'test_sub_quota3',
            'password': 'Password@123',
            'full_name': 'Quota 3',
            'role': 'karyakarta',
            'tenant_slug': 'jelatra'
        })
        self.assertIn(res4.status_code, [200, 201])

    def test_search_scoping_enforcement(self):
        """Sub-account with assigned_booths=[1] cannot see voters outside booth 1."""
        # Create user scoped to booth 1
        user_manager.create_user_account(
            username='test_sub_scoped_search',
            password='Password@123',
            full_name='Booth 1 Worker',
            role='booth_agent',
            tenant_slug='jelatra',
            scope_type='booth',
            assigned_booths=[1]
        )

        # Login as scoped user
        self.client.post('/login', json={'username': 'test_sub_scoped_search', 'password': 'Password@123'})

        # 1. Search without booth filter -> automatically filtered to booth 1
        res = self.client.get('/api/search?q=राम')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        for v in data.get('voters', []):
            self.assertEqual(v.get('part_no'), 1)

        # 2. Attempt to search in booth 2 specifically -> blocked / empty
        res2 = self.client.get('/api/search?q=राम&booth=2')
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(len(res2.get_json().get('voters', [])), 0)

        # 3. Direct booth API /api/voters_by_part/2 -> HTTP 403 Forbidden
        res3 = self.client.get('/api/voters_by_part/2')
        self.assertEqual(res3.status_code, 403)

    def test_warroom_marking_permissions_and_scope(self):
        """Viewer role cannot mark voted; booth agents cannot mark outside assigned booth."""
        # Create viewer
        user_manager.create_user_account(
            username='test_sub_viewer',
            password='Password@123',
            full_name='Viewer User',
            role='viewer',
            tenant_slug='jelatra'
        )

        # Create booth agent for booth 1
        user_manager.create_user_account(
            username='test_sub_agent1',
            password='Password@123',
            full_name='Agent 1',
            role='booth_agent',
            tenant_slug='jelatra',
            scope_type='booth',
            assigned_booths=[1]
        )

        # 1. Viewer attempts to mark voter -> 403 Forbidden
        self.client.post('/login', json={'username': 'test_sub_viewer', 'password': 'Password@123'})
        res_v = self.client.post('/api/mark_voted', json={'voter_id': 1, 'is_voted': True})
        self.assertEqual(res_v.status_code, 403)

        # 2. Booth agent 1 attempts to mark a voter belonging to booth 2 -> 403 Forbidden
        # Find a voter in booth 2 in jelatra
        with psycopg2.connect(dbname='voter_platform', user='postgres', password='postgres', host='localhost', port=5432) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT voter_id FROM voters_t1 WHERE part_no != 1 LIMIT 1")
                row = cur.fetchone()
                if row:
                    other_booth_voter_id = row[0]
                else:
                    other_booth_voter_id = None

        if other_booth_voter_id:
            self.client.post('/login', json={'username': 'test_sub_agent1', 'password': 'Password@123'})
            res_mark = self.client.post('/api/mark_voted', json={'voter_id': other_booth_voter_id, 'is_voted': True})
            self.assertEqual(res_mark.status_code, 403)

    def test_operator_delete_and_reset_subaccount(self):
        """Operators can delete and reset password of their own sub-accounts."""
        user_manager.create_user_account(
            username='test_sub_to_delete',
            password='Password@123',
            full_name='Temp Sub Account',
            role='booth_agent',
            tenant_slug='jelatra'
        )
        target = user_manager.get_user_by_username('test_sub_to_delete')
        self.assertIsNotNone(target)

        # Login as operator
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})

        # Reset password
        reset_res = self.client.post('/api/users/reset_password', json={
            'user_id': target['id'],
            'password': 'NewPassword@456'
        })
        self.assertEqual(reset_res.status_code, 200)

        # Delete sub account
        del_res = self.client.post(f"/api/users/{target['id']}/delete")
        self.assertEqual(del_res.status_code, 200)
        self.assertIsNone(user_manager.get_user_by_username('test_sub_to_delete'))

    def test_subaccount_module_creation_and_persistence(self):
        """Operator creates sub-account with specific allowed_modules; verified in DB."""
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})

        res = self.client.post('/api/users/create', json={
            'username': 'test_sub_booth_desk',
            'password': 'Password@123',
            'full_name': 'Booth Desk Incharge',
            'role': 'booth_agent',
            'tenant_slug': 'jelatra',
            'scope_type': 'booth',
            'assigned_booths': [1],
            'allowed_modules': ['search', 'voting_day']
        })
        self.assertIn(res.status_code, [200, 201])
        u = user_manager.get_user_by_username('test_sub_booth_desk')
        self.assertIsNotNone(u)
        self.assertEqual(u['allowed_modules'], ['search', 'voting_day'])

    def test_module_access_enforcement_pages_and_apis(self):
        """Sub-account with restricted modules is blocked from unauthorized pages and APIs."""
        user_manager.create_user_account(
            username='test_sub_restricted',
            password='Password@123',
            full_name='Restricted User',
            role='booth_agent',
            tenant_slug='jelatra',
            allowed_modules=['search']
        )

        # Login as restricted user
        login_res = self.client.post('/login', json={'username': 'test_sub_restricted', 'password': 'Password@123'})
        self.assertEqual(login_res.status_code, 200)

        # Authorized page: /voters (search) -> 200
        res_voters = self.client.get('/voters')
        self.assertEqual(res_voters.status_code, 200)

        # Authorized API: /api/voter/profile/update (mapped to search, NOT voting_day) -> should not return 403 module restricted
        res_voter_update = self.client.post('/api/voter/profile/update', json={'voter_id': 1, 'contact_phone': '9829012345'})
        self.assertNotEqual(res_voter_update.status_code, 403)

        # Unauthorized page: /voting-day (voting_day) -> redirects to /?denied_module=voting_day
        res_vd = self.client.get('/voting-day')
        self.assertEqual(res_vd.status_code, 302)
        self.assertIn('denied_module=voting_day', res_vd.location)

        # Unauthorized page: /caste -> redirects to /?denied_module=caste
        res_caste = self.client.get('/caste')
        self.assertEqual(res_caste.status_code, 302)
        self.assertIn('denied_module=caste', res_caste.location)

        # Unauthorized page: /turnout -> redirects to /?denied_module=voting_day
        res_turnout = self.client.get('/turnout')
        self.assertEqual(res_turnout.status_code, 302)
        self.assertIn('denied_module=voting_day', res_turnout.location)

        # Unauthorized API: /api/turnout_stats -> 403 Forbidden
        res_api_turnout = self.client.get('/api/turnout_stats')
        self.assertEqual(res_api_turnout.status_code, 403)
        self.assertIn("Access to 'voting_day' module is restricted", res_api_turnout.get_json()['error'])

        # Unauthorized API: /api/vote/toggle -> 403 Forbidden
        res_api_vote = self.client.post('/api/vote/toggle', json={'voter_id': 1})
        self.assertEqual(res_api_vote.status_code, 403)
        self.assertIn("Access to 'voting_day' module is restricted", res_api_vote.get_json()['error'])

        # Unauthorized page: /karyakartas -> redirects to /?denied_module=karyakartas
        res_karya = self.client.get('/karyakartas')
        self.assertEqual(res_karya.status_code, 302)
        self.assertIn('denied_module=karyakartas', res_karya.location)

        # Unauthorized page: /outside -> redirects to /?denied_module=outside
        res_outside = self.client.get('/outside')
        self.assertEqual(res_outside.status_code, 302)
        self.assertIn('denied_module=outside', res_outside.location)

        # Unauthorized API: /api/karyakartas -> 403 Forbidden
        res_api_karya = self.client.get('/api/karyakartas')
        self.assertEqual(res_api_karya.status_code, 403)
        self.assertIn("Access to 'karyakartas' module is restricted", res_api_karya.get_json()['error'])

        # Unauthorized API: /api/outside_voters -> 403 Forbidden
        res_api_outside = self.client.get('/api/outside_voters')
        self.assertEqual(res_api_outside.status_code, 403)
        self.assertIn("Access to 'outside' module is restricted", res_api_outside.get_json()['error'])

    def test_operator_update_subaccount_modules(self):
        """Operator can update allowed_modules for their campaign's sub-accounts."""
        user_manager.create_user_account(
            username='test_sub_to_update',
            password='Password@123',
            full_name='Modifiable User',
            role='booth_agent',
            tenant_slug='jelatra',
            allowed_modules=['search']
        )
        target = user_manager.get_user_by_username('test_sub_to_update')

        # 1. Login as operator
        self.client.post('/login', json={'username': 'test_op_jelatra', 'password': 'Password@123'})

        # 2. Update modules to add turnout and caste
        up_res = self.client.post(f"/api/users/{target['id']}/update_modules", json={
            'allowed_modules': ['search', 'turnout', 'caste']
        })
        self.assertEqual(up_res.status_code, 200)
        data = up_res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['allowed_modules'], ['search', 'turnout', 'caste'])

        # 3. Verify in DB
        u = user_manager.get_user_by_username('test_sub_to_update')
        self.assertEqual(u['allowed_modules'], ['search', 'turnout', 'caste'])

        # 4. Cross-tenant attempt: try to update a ward82 user -> 403
        user_manager.create_user_account(
            username='test_sub_ward82_user',
            password='Password@123',
            full_name='Ward82 Sub User',
            role='booth_agent',
            tenant_slug='ward82',
            allowed_modules=['search']
        )
        other_user = user_manager.get_user_by_username('test_sub_ward82_user')
        cross_res = self.client.post(f"/api/users/{other_user['id']}/update_modules", json={
            'allowed_modules': ['search', 'turnout']
        })
        self.assertEqual(cross_res.status_code, 403)


if __name__ == '__main__':
    unittest.main()
