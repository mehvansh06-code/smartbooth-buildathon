import unittest
from electionapp import create_app
from electionapp.config import is_ward_campaign, load_tenant_config


class TestWarroomMobile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('sarana')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant'] = 'sarana'

    def test_warroom_dashboard_html_structure(self):
        """Verify warroom page renders mobile card deck, footer summary, and winning margin breakdown."""
        resp = self.client.get('/warroom?tenant=sarana')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # 1. Cards deck must be present
        self.assertIn('id="wrCardsContainer"', html)

        # 2. Search and sort toolbar must be present
        self.assertIn('id="wrSearchInput"', html)
        self.assertIn('id="wrSortSelect"', html)

        # 3. Table must be hidden on mobile/PWA (wrapped in hidden md:block)
        self.assertIn('hidden md:block', html)
        self.assertIn('id="wrBoothTableBody"', html)
        self.assertIn('id="wrBoothTableFoot"', html, "Table footer for Total Voting summary row must be present")

        # 4. Winning margin forecast must be in a responsive 3-column grid
        self.assertIn('grid grid-cols-3', html)
        self.assertIn('id="wrProjVotes"', html)
        self.assertIn('id="wrProjOpp"', html)
        self.assertIn('id="wrProjLead"', html)

        # 5. Scientific margin breakdown drawer and live polled lead strip
        self.assertIn('id="wrMarginHelpDrawer"', html, "Interactive margin formula breakdown drawer must be present")
        self.assertIn('id="wrLiveTurnoutStrip"', html, "Live polled ballots reality strip must be present")
        self.assertIn('id="wrPolledLeadBadge"', html)

    def test_api_warroom_stats_ward_election_flag(self):
        """Verify /api/warroom/stats correctly flags local body campaigns as is_ward_election."""
        resp = self.client.get('/api/warroom/stats?tenant=sarana')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get('success'))
        self.assertTrue(data.get('is_ward_election'), "Panchayat campaign must have is_ward_election=True")

        booths = data.get('booth_breakdown') or data.get('booths') or []
        self.assertTrue(len(booths) > 0, "Expected at least one ward unit in breakdown")

        for b in booths:
            # Name must be formatted strictly as 'वार्ड {N}', NOT with long booth school descriptions
            part_no = b.get('part_no')
            self.assertEqual(b.get('name'), f"वार्ड {part_no}", f"Ward unit name should strictly be 'वार्ड {part_no}', got '{b.get('name')}'")

    def test_api_warroom_stats_voted_breakdown_keys(self):
        """Verify all required breakdown keys exist for rendering cards and table."""
        resp = self.client.get('/api/warroom/stats?tenant=sarana')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        booths = data.get('booth_breakdown') or data.get('booths') or []
        self.assertTrue(len(booths) > 0)
        b0 = booths[0]
        for key in ['part_no', 'name', 'total', 'favored', 'voted_favored', 'swing', 'voted_swing', 'opp', 'voted_opp', 'turnout_pct']:
            self.assertIn(key, b0, f"Expected key '{key}' in breakdown unit")


if __name__ == '__main__':
    unittest.main()
