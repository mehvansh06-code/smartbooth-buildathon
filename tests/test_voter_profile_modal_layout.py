import os
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestVoterProfileModalLayout(unittest.TestCase):
    """Test suite ensuring that the voter edit popup modal prioritizes Phone Number,
    Political Sentiment, and Community Tagging in the primary tab, with secondary
    attributes organized in the More Details tab."""

    def setUp(self):
        self.modal_path = os.path.join(PLATFORM_ROOT, 'templates', 'modals', '_voter_profile_modal.html')
        self.assertTrue(os.path.exists(self.modal_path), f"Modal file not found: {self.modal_path}")
        with open(self.modal_path, 'r', encoding='utf-8') as f:
            self.content = f.read()

    def test_tab_bar_structure(self):
        """Verifies the 2-tab navigation: Quick Tag and More Details."""
        self.assertIn("vpmSwitchTab('quick', this)", self.content, "Missing Quick Tag tab button")
        self.assertIn("vpmSwitchTab('details', this)", self.content, "Missing More Details tab button")
        self.assertIn('id="vpmMoreDetailsBadge"', self.content, "Missing More Details indicator badge")

    def test_quick_tab_pane_and_element_order(self):
        """Verifies that Phone, Sentiment, and Community Tag are all within #vpmTab-quick
        and appear in the exact required sequence."""
        self.assertIn('id="vpmTab-quick"', self.content)
        self.assertIn('id="vpmTab-details"', self.content)

        # Slice content of the quick tab pane
        quick_start = self.content.find('id="vpmTab-quick"')
        details_start = self.content.find('id="vpmTab-details"')
        self.assertGreater(details_start, quick_start)
        quick_pane = self.content[quick_start:details_start]

        # 1. Phone Number inside quick pane
        phone_idx = quick_pane.find('id="vpmPhone"')
        self.assertNotEqual(phone_idx, -1, "vpmPhone must be inside #vpmTab-quick")

        # 2. Sentiment Group inside quick pane
        sentiment_idx = quick_pane.find('id="vpmSentimentButtonGroup"')
        self.assertNotEqual(sentiment_idx, -1, "vpmSentimentButtonGroup must be inside #vpmTab-quick")

        # 3. Community Tag inside quick pane
        caste_idx = quick_pane.find('id="vpmCaste"')
        self.assertNotEqual(caste_idx, -1, "vpmCaste must be inside #vpmTab-quick")
        self.assertIn('id="vpmCasteChips"', quick_pane, "vpmCasteChips must be inside #vpmTab-quick")

        # Strict order verification: Phone -> Sentiment -> Community Tag
        self.assertLess(phone_idx, sentiment_idx, "Phone input must come BEFORE Sentiment group")
        self.assertLess(sentiment_idx, caste_idx, "Sentiment group must come BEFORE Community Tag")

    def test_details_tab_contains_secondary_fields(self):
        """Verifies that Special Status, Assigned Worker, Family Head Name, and Notes are in #vpmTab-details."""
        details_start = self.content.find('id="vpmTab-details"')
        details_pane = self.content[details_start:]

        self.assertIn('id="vpmSpecialAccBtn"', details_pane)
        self.assertIn('id="vpmIsOutside"', details_pane)
        self.assertIn('id="vpmIsDeceased"', details_pane)
        self.assertIn('id="vpmAssignedKaryakarta"', details_pane)
        self.assertIn('id="vpmFamilyHeadName"', details_pane)
        self.assertIn('id="vpmNotes"', details_pane)

    def test_script_logic_and_observer(self):
        """Verifies tab switching script, badge indicator updater, and modal open reset."""
        self.assertIn("function vpmUpdateDetailsBadge()", self.content)
        self.assertIn("vpmSwitchTab('quick', firstTab)", self.content, "Observer must reset to quick tab on open")


if __name__ == '__main__':
    unittest.main()
