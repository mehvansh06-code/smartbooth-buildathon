"""
test_hub_launcher_and_voting_day.py
Verification suite for:
1. Central App Launcher Hub (/ and /hub)
2. Voter Hub & Search (/voters and /search)
3. Voting Day Desk (/voting-day) dynamic ward selector and deduplication
4. Universal Top Bar navigation across modules
"""

import unittest
from electionapp import create_app

class TestHubLauncherAndVotingDay(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'

    def test_hub_route_renders_launcher(self):
        """Verify / and /hub render the Central App Launcher."""
        for path in ['/', '/hub']:
            with self.subTest(path=path):
                res = self.client.get(f'{path}?tenant=ward82', follow_redirects=True)
                self.assertEqual(res.status_code, 200)
                html = res.data.decode('utf-8')
                self.assertIn('मतदाता खोज व पर्ची', html)
                self.assertIn('मतदान दिवस डेस्क', html)
                self.assertIn('वॉर-रूम', html)
                self.assertIn('/voters', html)
                self.assertIn('/voting-day', html)
                self.assertIn('/warroom', html)

    def test_voters_route_renders_search_module(self):
        """Verify /voters and /search render index.html (the voter search module)."""
        for path in ['/voters', '/search']:
            with self.subTest(path=path):
                res = self.client.get(f'{path}?tenant=ward82', follow_redirects=True)
                self.assertEqual(res.status_code, 200)
                html = res.data.decode('utf-8')
                self.assertIn('Hub', html)
                self.assertIn('id="nameSearchInput"', html)

    def test_voting_day_has_ward_container_and_top_bar(self):
        """Verify /voting-day renders the universal top bar with Hub back button and dynamic ward selector."""
        res = self.client.get('/voting-day?tenant=ward82', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('Hub', html)
        self.assertIn('id="vdWardSelectorContainer"', html)
        self.assertIn('id="vdWardTabsList"', html)
        self.assertIn('id="vdSerialGrid"', html)
        self.assertIn('id="vdQuickSerialInput"', html)

if __name__ == '__main__':
    unittest.main()
