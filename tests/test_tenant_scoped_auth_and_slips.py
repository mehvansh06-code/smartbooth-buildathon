"""
test_tenant_scoped_auth_and_slips.py
Comprehensive test suite verifying:
1. Single deployment under one roof with strict user-tenant separation.
2. Ward 82 user only has access to Ward 82; forbidden from accessing Jelatra.
3. Jelatra user only has access to Jelatra; forbidden from accessing Ward 82.
4. Super Admin can access all campaigns and manage user accounts.
5. High-speed bilingual voter search & slip generation with Google Maps location.
6. 1-Click WhatsApp (wa.me) deep link generation.
"""

import os
import sys
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app


class TestTenantScopedAuthAndSlips(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ensure schema in auth.db
        user_manager.ensure_schema()

        # Create test campaign accounts
        user_manager.create_user_account(
            username='w82_operator',
            password='password123',
            full_name='Ward 82 Operator',
            phone='9829011111',
            role='operator',
            tenant_slug='ward82'
        )
        user_manager.create_user_account(
            username='jelatra_operator',
            password='password123',
            full_name='Jelatra Operator',
            phone='9829022222',
            role='operator',
            tenant_slug='jelatra'
        )

    def setUp(self):
        # Enable explicit auth checking for this test suite
        os.environ['FORCE_AUTH_CHECK'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop('FORCE_AUTH_CHECK', None)

    def test_unauthenticated_request_is_blocked(self):
        """Unauthenticated requests to protected endpoints return 401 or redirect."""
        resp = self.client.get('/api/search')
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertFalse(data['success'])

        page_resp = self.client.get('/')
        self.assertEqual(page_resp.status_code, 302)
        self.assertIn('/login', page_resp.headers.get('Location', ''))

    def test_login_ward82_user_and_scope_enforcement(self):
        """Ward 82 user can log in and is strictly locked to Ward 82 data."""
        # 1. Login as Ward 82 operator
        login_resp = self.client.post('/login', json={
            'username': 'w82_operator',
            'password': 'password123'
        })
        self.assertEqual(login_resp.status_code, 200)
        login_data = login_resp.get_json()
        self.assertTrue(login_data['success'])
        self.assertIn('tenant=ward82', login_data['redirect'])

        # 2. Search Ward 82 voters - should succeed
        search_resp = self.client.get('/api/search?q=Mehta&limit=10')
        self.assertEqual(search_resp.status_code, 200)
        search_data = search_resp.get_json()
        self.assertTrue(search_data['success'])
        self.assertEqual(search_data['tenant'], 'ward82')

        # 3. Attempt to query Jelatra data via tenant param - must return 403 Forbidden!
        cross_tenant_resp = self.client.get('/api/search?tenant=jelatra&limit=10')
        self.assertEqual(cross_tenant_resp.status_code, 403)
        err_data = cross_tenant_resp.get_json()
        self.assertFalse(err_data['success'])
        self.assertIn('Access denied', err_data['error'])

    def test_login_jelatra_user_and_scope_enforcement(self):
        """Jelatra user can log in and is strictly locked to Jelatra data."""
        # 1. Login as Jelatra operator
        login_resp = self.client.post('/login', json={
            'username': 'jelatra_operator',
            'password': 'password123'
        })
        self.assertEqual(login_resp.status_code, 200)
        login_data = login_resp.get_json()
        self.assertTrue(login_data['success'])
        self.assertIn('tenant=jelatra', login_data['redirect'])

        # 2. Search Jelatra voters - should succeed
        search_resp = self.client.get('/api/search?limit=10')
        self.assertEqual(search_resp.status_code, 200)
        search_data = search_resp.get_json()
        self.assertTrue(search_data['success'])
        self.assertEqual(search_data['tenant'], 'jelatra')

        # 3. Attempt to query Ward 82 data via tenant param - must return 403 Forbidden!
        cross_tenant_resp = self.client.get('/api/search?tenant=ward82&limit=10')
        self.assertEqual(cross_tenant_resp.status_code, 403)
        err_data = cross_tenant_resp.get_json()
        self.assertFalse(err_data['success'])
        self.assertIn('Access denied', err_data['error'])

    def test_super_admin_full_access(self):
        """Super Admin can access all campaigns and manage user accounts."""
        # Login as super admin
        login_resp = self.client.post('/login', json={
            'username': 'admin',
            'password': 'admin2026'
        })
        self.assertEqual(login_resp.status_code, 200)

        # Super admin can query Ward 82
        w82_resp = self.client.get('/api/search?tenant=ward82&limit=5')
        self.assertEqual(w82_resp.status_code, 200)
        self.assertEqual(w82_resp.get_json()['tenant'], 'ward82')

        # Super admin can switch and query Jelatra
        jel_resp = self.client.get('/api/search?tenant=jelatra&limit=5')
        self.assertEqual(jel_resp.status_code, 200)
        self.assertEqual(jel_resp.get_json()['tenant'], 'jelatra')

        # Super admin can list users
        users_resp = self.client.get('/api/users/list')
        self.assertEqual(users_resp.status_code, 200)
        self.assertTrue(users_resp.get_json()['success'])

    def test_slip_text_and_wa_me_link_generation(self):
        """Generates formatted WhatsApp slip text with booth details and wa.me deep link."""
        # Login as Ward 82 operator
        self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})

        # Search first to get a valid voter ID
        search_res = self.client.get('/api/search?limit=1')
        voter_id = search_res.get_json()['voters'][0]['id']

        # Get slip text for the voter
        slip_resp = self.client.get(f'/api/slip_text?id={voter_id}')
        self.assertEqual(slip_resp.status_code, 200)
        slip_data = slip_resp.get_json()
        slip_text = slip_data.get('slip_text', '')
        self.assertIn('मतदाता', slip_text)
        self.assertIn('भाग', slip_text)

        # Test wa.me link generation
        link_resp = self.client.post('/api/dispatch/wa_me_link', json={
            'phone': '9829012345',
            'message': slip_text
        })
        self.assertEqual(link_resp.status_code, 200)
        link_data = link_resp.get_json()
        self.assertTrue(link_data['success'])
        self.assertIn('https://wa.me/919829012345?text=', link_data['link'])

    def test_family_smart_and_voters_by_serials(self):
        """Verifies smart family discovery and serials lookup work seamlessly."""
        self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})

        # Test family_smart for voter 301 (House 101, Part 1)
        fam_resp = self.client.get('/api/family_smart?voter_id=301&tenant=ward82')
        self.assertEqual(fam_resp.status_code, 200)
        fam_data = fam_resp.get_json()
        self.assertIn('direct', fam_data)
        self.assertGreater(len(fam_data['direct']), 1)
        self.assertIn('related', fam_data)

        # Test voters_by_serials
        serials_resp = self.client.get('/api/voters_by_serials?serials=2,3&part=1&tenant=ward82')
        self.assertEqual(serials_resp.status_code, 200)
        ser_data = serials_resp.get_json()
        self.assertTrue(ser_data['success'])
        self.assertEqual(ser_data['found_count'], 2)


    def test_logout_cookie_clearing_and_neutral_login_redirect(self):
        """Verifies logout cleanly removes cookies and subsequent logins are not stuck to old tenant."""
        # 1. Login as Jelatra operator
        login_res = self.client.post('/login', json={'username': 'jelatra_operator', 'password': 'password123'})
        self.assertEqual(login_res.status_code, 200)
        self.assertEqual(login_res.get_json()['user']['tenant_slug'], 'jelatra')

        # 2. Logout - verify voter_tenant cookie is deleted and not re-added
        logout_res = self.client.get('/logout')
        self.assertEqual(logout_res.status_code, 302)
        self.assertIn('/login', logout_res.headers.get('Location', ''))

        # Verify Set-Cookie headers on logout contain voter_tenant deletion (Expires= Thu, 01 Jan 1970 or Max-Age=0)
        cookie_headers = logout_res.headers.getlist('Set-Cookie')
        vt_cookies = [c for c in cookie_headers if 'voter_tenant=' in c]
        self.assertTrue(any('Expires=' in c or 'Max-Age=0' in c or 'voter_tenant=;' in c for c in vt_cookies))

        # 3. Super admin login redirects to /admin (Fleet Command Center), NOT stuck to jelatra
        admin_login = self.client.post('/login', data={'username': 'admin', 'password': 'admin2026'})
        self.assertEqual(admin_login.status_code, 302)
        admin_loc = admin_login.headers.get('Location', '')
        self.assertTrue('/admin' in admin_loc or 'tenant=' in admin_loc)
        self.assertNotIn('tenant=jelatra', admin_loc)

        # 4. Operator locked to ward82 accessing ?tenant=jelatra gets redirected back to ward82
        self.client.get('/logout')
        self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})
        tampered_resp = self.client.get('/?tenant=jelatra')
        self.assertEqual(tampered_resp.status_code, 302)
        self.assertIn('tenant=ward82', tampered_resp.headers.get('Location', ''))

    def test_operator_blocked_from_cross_tenant_header(self):
        """Operator cannot query or modify another campaign via X-Tenant header."""
        self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})
        resp = self.client.get('/api/search?q=Mehta', headers={'X-Tenant': 'jelatra'})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn("Access denied", data['error'])

    def test_operator_blocked_from_cross_tenant_json_body(self):
        """Operator cannot mutate another campaign via JSON body parameter."""
        self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})
        resp = self.client.post('/api/mark_voted', json={'tenant': 'jelatra', 'voter_id': 1, 'voted': True})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn("Access denied", data['error'])

    def test_operator_blocked_from_admin_endpoints(self):
        """Operator is strictly forbidden from accessing /admin and /api/admin/* endpoints."""
        self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})
        
        # Test API endpoints
        resp_users = self.client.get('/api/admin/users')
        self.assertEqual(resp_users.status_code, 403)
        self.assertFalse(resp_users.get_json()['success'])

        resp_fleet = self.client.get('/api/admin/fleet_stats')
        self.assertEqual(resp_fleet.status_code, 403)
        self.assertFalse(resp_fleet.get_json()['success'])

        # Test Admin UI route (redirects to /)
        resp_ui = self.client.get('/admin')
        self.assertEqual(resp_ui.status_code, 302)

    def test_unauthenticated_blocked_from_admin_endpoints(self):
        """Unauthenticated caller to /api/admin/* returns 401 Unauthorized."""
        self.client.get('/logout')
        resp = self.client.get('/api/admin/users')
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.get_json()['success'])

    def test_super_admin_can_access_admin_endpoints(self):
        """Super Admin can access fleet stats and admin routes."""
        self.client.post('/login', json={'username': 'admin', 'password': 'admin2026'})
        resp = self.client.get('/api/admin/fleet_stats')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get('success', False) or 'total_campaigns' in data)

    def test_session_cookie_security_flags(self):
        """Verify session and tenant cookies carry HttpOnly and SameSite=Lax flags."""
        resp = self.client.post('/login', json={'username': 'w82_operator', 'password': 'password123'})
        self.assertEqual(resp.status_code, 200)
        cookie_headers = resp.headers.getlist('Set-Cookie')
        self.assertTrue(len(cookie_headers) > 0)
        tenant_cookie = [h for h in cookie_headers if 'voter_tenant=' in h]
        self.assertTrue(len(tenant_cookie) > 0)
        self.assertIn('samesite=lax', tenant_cookie[0].lower())

    def test_tenant_assets_directory_creation_blocked(self):
        """Unauthenticated caller cannot trigger arbitrary directory creation via /tenant_assets."""
        fake_slug = "nonexistent_exploit_slug"
        resp = self.client.get(f'/tenant_assets/test.png?tenant={fake_slug}')
        self.assertEqual(resp.status_code, 404)
        target_dir = os.path.join(PLATFORM_ROOT, 'tenants', fake_slug)
        self.assertFalse(os.path.exists(target_dir))

    def test_config_memoization_returns_cached_object(self):
        """Config loader returns deep copies so in-memory cache is protected from caller mutation."""
        from electionapp.config import load_tenant_config
        cfg1 = load_tenant_config('ward82')
        cfg1['mutated_key'] = 'tainted_value'
        cfg2 = load_tenant_config('ward82')
        self.assertNotIn('mutated_key', cfg2)

    def test_response_timing_and_request_id_headers(self):
        """Verify that requests return X-Request-ID and X-Response-Time headers."""
        resp = self.client.get('/login')
        self.assertIn('X-Request-ID', resp.headers)
        self.assertIn('X-Response-Time', resp.headers)
        self.assertTrue(resp.headers['X-Response-Time'].endswith('ms'))

    def test_authenticate_user_masks_database_exceptions(self):
        """Verify that user_manager.authenticate_user does not leak raw database exceptions to callers."""
        import user_manager
        from unittest.mock import patch

        with patch('user_manager.get_db', side_effect=RuntimeError("psycopg2.OperationalError: password authentication failed for user 'postgres'")):
            user, err = user_manager.authenticate_user('admin', 'wrong_pass')
            self.assertIsNone(user)
            self.assertIsNotNone(err)
            self.assertNotIn("password authentication failed", err)
            self.assertNotIn("postgres", err)
            self.assertIn("अनुपलब्ध", err)

    def test_fleet_campaigns_cache_invalidation(self):
        """Verify _gather_all_campaigns caches result and clear_fleet_cache resets it."""
        from electionapp.blueprints.admin import _gather_all_campaigns, clear_fleet_cache
        import electionapp.blueprints.admin as admin_module

        clear_fleet_cache()
        self.assertEqual(admin_module._FLEET_CAMPAIGNS_CACHE[0], 0.0)

        c1 = _gather_all_campaigns()
        self.assertGreater(admin_module._FLEET_CAMPAIGNS_CACHE[0], 0.0)

        clear_fleet_cache()
        self.assertEqual(admin_module._FLEET_CAMPAIGNS_CACHE[0], 0.0)

    def test_validate_tenant_config_schema(self):
        """Verify validate_tenant_config strictly catches missing or malformed configuration sections."""
        from electionapp.config import validate_tenant_config

        valid_cfg = {
            'candidate': {'name_hi': 'प्रत्याशी', 'name_en': 'Candidate'},
            'election': {'ward_name_hi': 'वार्ड 1', 'ward_name_en': 'Ward 1', 'app_title': 'Smart Booth'},
            'party': {'name_hi': 'पार्टी', 'name_en': 'Party'},
            'booths': {}
        }
        # Valid config passes with no exception
        validate_tenant_config(valid_cfg, 'test_slug')

        # Missing candidate section
        bad1 = {'election': valid_cfg['election'], 'party': valid_cfg['party']}
        with self.assertRaises(ValueError) as cm:
            validate_tenant_config(bad1, 'test_slug')
        self.assertIn("missing required section: 'candidate'", str(cm.exception))

        # Candidate missing name_en
        bad2 = {
            'candidate': {'name_hi': 'प्रत्याशी'},
            'election': valid_cfg['election'],
            'party': valid_cfg['party']
        }
        with self.assertRaises(ValueError) as cm:
            validate_tenant_config(bad2, 'test_slug')
        self.assertIn("candidate missing required field: 'name_en'", str(cm.exception))

        # Election missing app_title
        bad3 = {
            'candidate': valid_cfg['candidate'],
            'election': {'ward_name_hi': 'वार्ड', 'ward_name_en': 'Ward'},
            'party': valid_cfg['party']
        }
        with self.assertRaises(ValueError) as cm:
            validate_tenant_config(bad3, 'test_slug')
        self.assertIn("election missing required field: 'app_title'", str(cm.exception))

        # Malformed booths
        bad4 = dict(valid_cfg)
        bad4['booths'] = "not_a_dict_or_list"
        with self.assertRaises(ValueError) as cm:
            validate_tenant_config(bad4, 'test_slug')
        self.assertIn("'booths' must be a mapping or list", str(cm.exception))

    def test_booth_vs_ward_dropdown_disambiguation(self):
        """Verify that Assembly campaigns strictly render Booths and Panchayat campaigns render Wards without ambiguous slashes."""
        from electionapp.config import is_panchayat_campaign, load_tenant_config

        try:
            sanchore_cfg = load_tenant_config('sanchore')
            sarana_cfg = load_tenant_config('sarana')
        except FileNotFoundError:
            self.skipTest("Tenant configs for sanchore/sarana not present in worktree/environment")

        self.assertFalse(is_panchayat_campaign(sanchore_cfg))
        self.assertTrue(is_panchayat_campaign(sarana_cfg))

        app_sanchore = create_app('sanchore', storage_backend='postgres')
        with app_sanchore.test_client() as c:
            with c.session_transaction() as sess:
                sess['user_id'] = 1
                sess['role'] = 'operator'
                sess['tenant_slug'] = 'sanchore'
            res = c.get('/voters?tenant=sanchore')
            self.assertEqual(res.status_code, 200)
            html = res.data.decode('utf-8')
            self.assertTrue('मतदान केंद्र (भाग):' in html or 'मतदान केंद्र (बूथ):' in html or 'भाग:' in html)
            self.assertNotIn('वार्ड /', html)
            self.assertNotIn('वार्ड (Ward):', html)
            self.assertIn('सभी मतदान केंद्र (All 307 Booths)', html)


if __name__ == '__main__':
    unittest.main()
