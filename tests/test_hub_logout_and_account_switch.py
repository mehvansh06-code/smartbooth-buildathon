"""
test_hub_logout_and_session_switch.py
Verification suite for:
1. Hub Screen visible Logout buttons (Top Bar + Hero Banner).
2. Clean compact grid layout on Hub.
3. /login?switch=1 clearing stale sessions for smooth account switching.
4. Cross-tenant login URL resolution preventing Jelatra/demo session hijacking.
"""

import unittest
from electionapp import create_app


class TestHubLogoutAndSessionSwitch(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.client = self.app.test_client()

    def test_hub_renders_top_bar_and_logout_button(self):
        """Verify Hub renders the top header with logged in user and logout button."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 7
            sess['username'] = 'sanchore_op'
            sess['full_name'] = 'Sanchore Operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'sanchore'

        res = self.client.get('/?tenant=sanchore', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')

        # Check top bar header has logout link and label
        self.assertIn('/logout', html)
        self.assertIn('लॉगआउट', html)
        # Check user profile display
        self.assertIn('Sanchore Operator', html)
        # Verify copy link script is absent
        self.assertNotIn('copyCleanPortalLink', html)

    def test_login_switch_param_clears_session(self):
        """Verify /login?switch=1 clears existing session and shows login form."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 7
            sess['username'] = 'jelatra'
            sess['tenant_slug'] = 'jelatra'

        # Visiting /login?switch=1 should NOT redirect to /?tenant=jelatra
        res = self.client.get('/login?switch=1&tenant=sanchore')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)

        # Verify session was cleared
        with self.client.session_transaction() as sess:
            self.assertNotIn('user_id', sess)

    def test_cross_tenant_login_request_clears_mismatched_session(self):
        """Verify visiting /login for another tenant clears mismatched operator session."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 7
            sess['username'] = 'jelatra'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'jelatra'

        # User is logged in as jelatra, but visits link for sanchore
        res = self.client.get('/login?tenant=sanchore')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('name="username"', html)

        # Verify session cleared
        with self.client.session_transaction() as sess:
            self.assertNotIn('user_id', sess)

    def test_logout_preserves_target_tenant(self):
        """Verify /logout?tenant=sanchore redirects to /login?tenant=sanchore."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 7
            sess['username'] = 'jelatra'

        res = self.client.get('/logout?tenant=sanchore')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login?tenant=sanchore', res.headers.get('Location', ''))

    def test_super_admin_session_survives_a_cross_campaign_login_link(self):
        """A super admin spans every campaign, so ?tenant= must NOT log them out.

        Guards the tenant_slug == '*' carve-out in the mismatch check: without it,
        switching campaigns would sign the super admin out on every hop.
        """
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = '*'

        self.client.get('/login?tenant=sanchore')
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('user_id'), 1,
                             "super admin must stay logged in across campaigns")

    def test_matching_tenant_login_link_keeps_the_session(self):
        """Opening the login page for your OWN campaign must not sign you out."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 7
            sess['username'] = 'sanchore_op'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'sanchore'

        self.client.get('/login?tenant=sanchore')
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('user_id'), 7)

    def test_logout_ignores_an_unknown_tenant_value(self):
        """An unprovisioned or malformed ?tenant= must never be reflected back."""
        for bad in ['../../etc/passwd', 'no_such_campaign', 'a b c', 'https://evil.example']:
            res = self.client.get('/logout', query_string={'tenant': bad})
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers.get('Location', ''), '/login',
                             f"unexpected redirect target for tenant={bad!r}")


if __name__ == '__main__':
    unittest.main()
