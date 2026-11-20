"""
test_voting_day_omr_and_unvoted.py
Verification suite for Voting Day Desk:
1. Strict Single Ward / Single Booth view (no 'All Wards' toggle, no W1/W2 clutter)
2. Live Booth Marking Sheet modal and triggers
3. Filterable Unvoted Voter List modal, filters (Caste, Sentiment, Residence, Gender), and print triggers
"""

import unittest
from electionapp import create_app

class TestVotingDayOmrAndUnvoted(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'

    def test_voting_day_page_renders_single_unit_and_triggers(self):
        """Verify /voting-day renders single unit tabs, action island, and print modals."""
        res = self.client.get('/voting-day?tenant=ward82', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')

        # Universal Navigation
        self.assertIn('Hub', html)
        self.assertIn('id="vdQuickSerialInput"', html)
        self.assertIn('id="vdSerialGrid"', html)
        self.assertIn('id="vdWardSelectorContainer"', html)
        self.assertIn('id="vdWardTabsList"', html)

        # Action Island Print Triggers
        self.assertIn('बूथ अंकन पत्रक', html)
        self.assertIn('शेष मतदाता सूची', html)
        self.assertIn('vdOpenMarkingSheetModal()', html)
        self.assertIn('vdOpenUnvotedModal()', html)

        # Modals Present
        self.assertIn('id="vdMarkingSheetModal"', html)
        self.assertIn('id="vdUnvotedModal"', html)
        self.assertIn('vdPrintMarkingSheet()', html)
        self.assertIn('vdPrintUnvotedList()', html)

        # Unvoted List Filters
        self.assertIn('id="vdUnvotedCasteSelect"', html)
        self.assertIn('id="vdUnvotedCategorySelect"', html)
        self.assertIn('id="vdUnvotedOutsideSelect"', html)
        self.assertIn('id="vdUnvotedGenderSelect"', html)
        self.assertIn('id="vdUnvotedLifeStatusSelect"', html)
        self.assertIn('id="vdUnvotedSortSelect"', html)
        self.assertIn('id="vdUnvotedMatchCount"', html)

        # Marking Sheet Controls
        self.assertIn('vdSheetModeRadio', html)
        self.assertIn('id="vdIncOutsideCb"', html)
        self.assertIn('id="vdIncDupCb"', html)
        self.assertIn('id="vdIncDeceasedCb"', html)
        self.assertIn('id="vdIncSupporterCb"', html)

    def test_marking_sheet_layout_safeguards_in_js(self):
        """Verify voting_day.js contains strict column locking, overflow prevention, and dup badge positioning."""
        with open('static/js/voting_day.js', 'r', encoding='utf-8') as f:
            js = f.read()

        # Strict 20-column locking
        self.assertIn('colgroup', js)
        self.assertIn('col class="c-w"', js)
        self.assertIn('col.c-w { width: 5%; }', js)

        # Height constrained for 1-page (600 voters) / 2-page (1200 voters)
        self.assertIn('height: 276mm;', js)
        self.assertIn('max-height: 276mm;', js)

        # Duplicate badge positioned without overflowing cell borders
        self.assertIn('.strike-num', js)
        self.assertIn('.dup-badge', js)
        self.assertIn('position: absolute;', js)

        # Seamless iframe printing (no window.open about:blank popup tabs)
        self.assertIn('function vdPrintHtml(htmlContent)', js)
        self.assertIn('vdPrintFrame', js)
        self.assertNotIn("window.open('', '_blank')", js)

    def test_voting_day_script_version_bumped(self):
        """Verify voting_day.html references the bumped voting_day.js script version."""
        res = self.client.get('/voting-day?tenant=ward82', follow_redirects=True)
        html = res.data.decode('utf-8')
        self.assertRegex(html, r'/static/js/voting_day\.js\?v=3\.\d+\.\d+')

if __name__ == '__main__':
    unittest.main()
