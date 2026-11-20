import sys, os
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electionapp import create_app
import unittest

class TestSuperAdminUniversalCampaignSwitcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These tests assert the REAL authorization gates (operators and campaign
        # admins blocked from /admin). FLASK_TESTING=1 deliberately bypasses those
        # gates, so without forcing the real check this suite silently passes
        # everything with 200s. FORCE_AUTH_CHECK=1 overrides the bypass, making the
        # suite independent of the ambient environment.
        cls._prev_force_auth = os.environ.get('FORCE_AUTH_CHECK')
        os.environ['FORCE_AUTH_CHECK'] = '1'
        cls.app = create_app()

    @classmethod
    def tearDownClass(cls):
        if cls._prev_force_auth is None:
            os.environ.pop('FORCE_AUTH_CHECK', None)
        else:
            os.environ['FORCE_AUTH_CHECK'] = cls._prev_force_auth

    def setUp(self):
        self.client = self.app.test_client()

    def test_super_admin_has_switcher_on_all_pages(self):
        # 1. Login as admin
        resp = self.client.post('/login', data={'username': 'admin', 'password': 'admin2026'}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers.get('Location'), '/admin')

        # 2. Check /admin page
        r_admin = self.client.get('/admin')
        self.assertEqual(r_admin.status_code, 200)
        h_admin = r_admin.data.decode('utf-8')
        self.assertIn('id="globalCampaignSwitcher"', h_admin, "Switcher missing on /admin")
        self.assertIn('switchGlobalCampaign', h_admin, "switchGlobalCampaign missing on /admin")
        self.assertIn('/logout', h_admin, "Logout link missing on /admin")

        # 3. Check Hub page for active campaign
        r_hub = self.client.get('/?tenant=sanchore')
        self.assertEqual(r_hub.status_code, 200)
        h_hub = r_hub.data.decode('utf-8')
        self.assertIn('id="globalCampaignSwitcher"', h_hub, "Switcher missing on Hub")
        self.assertIn('/admin', h_hub, "Fleet admin link missing on Hub")

        # 4. Check all sub-module pages
        modules = ['/voters', '/search', '/turnout', '/warroom', '/voting-day', '/history', '/caste', '/users']
        for mod in modules:
            r_mod = self.client.get(f'{mod}?tenant=sanchore')
            self.assertEqual(r_mod.status_code, 200, f"Failed on {mod}")
            h_mod = r_mod.data.decode('utf-8')
            self.assertIn('id="globalCampaignSwitcher"', h_mod, f"Switcher missing on {mod}")
            self.assertIn('href="/admin"', h_mod, f"Admin link missing on {mod}")

        # 5. Check switching to another campaign
        r_sw = self.client.get('/?tenant=bonli_w12')
        self.assertEqual(r_sw.status_code, 200)
        h_sw = r_sw.data.decode('utf-8')
        self.assertIn('id="globalCampaignSwitcher"', h_sw)
        self.assertIn('data-active-tenant="bonli_w12"', h_sw)

    def test_campaign_admin_is_strictly_locked_and_has_no_switcher(self):
        # 1. Login as sanchore_admin
        resp = self.client.post('/login', data={'username': 'sanchore_admin', 'password': 'Sanchore@2026'}, follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('tenant=sanchore', resp.headers.get('Location'))

        # 2. Check Hub
        r_hub = self.client.get('/?tenant=sanchore')
        self.assertEqual(r_hub.status_code, 200)
        h_hub = r_hub.data.decode('utf-8')
        self.assertNotIn('id="globalCampaignSwitcher"', h_hub, "Campaign admin should NOT see global switcher")
        self.assertIn('SANCHORE', h_hub.upper(), "Campaign admin should see campaign name in header")

        # 3. Check sub-modules
        r_mod = self.client.get('/voters?tenant=sanchore')
        self.assertEqual(r_mod.status_code, 200)
        h_mod = r_mod.data.decode('utf-8')
        self.assertNotIn('id="globalCampaignSwitcher"', h_mod, "Campaign admin should NOT see global switcher on /voters")
        self.assertIn('📍', h_mod, "Campaign admin should see static pin badge on subpages")

        # 4. Attempt to access other tenant is blocked/redirected
        r_blocked = self.client.get('/?tenant=bonli_w12', follow_redirects=False)
        self.assertEqual(r_blocked.status_code, 302)
        self.assertIn('tenant=sanchore', r_blocked.headers.get('Location'))

        # 5. Attempt to access /admin is redirected to /
        r_admin_blocked = self.client.get('/admin', follow_redirects=False)
        self.assertEqual(r_admin_blocked.status_code, 302)
        self.assertEqual(r_admin_blocked.headers.get('Location'), '/')

    def test_regular_operator_is_strictly_locked(self):
        # 1. Set up operator session directly (hermetic and environment-independent)
        with self.client.session_transaction() as sess:
            sess['user_id'] = 9999
            sess['username'] = 'test_operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'sanchore'

        r_hub = self.client.get('/?tenant=sanchore')
        self.assertEqual(r_hub.status_code, 200)
        h_hub = r_hub.data.decode('utf-8')
        self.assertNotIn('id="globalCampaignSwitcher"', h_hub)
        self.assertNotIn('id="hubCampaignSwitcher"', h_hub)

        # 2. Blocked from accessing other tenant campaigns
        r_blocked = self.client.get('/?tenant=bonli_w12', follow_redirects=False)
        self.assertEqual(r_blocked.status_code, 302)
        self.assertIn('tenant=sanchore', r_blocked.headers.get('Location'))

        # 3. Blocked from accessing super admin command center
        r_admin = self.client.get('/admin', follow_redirects=False)
        self.assertEqual(r_admin.status_code, 302)
        self.assertEqual(r_admin.headers.get('Location'), '/')

if __name__ == '__main__':
    unittest.main()
