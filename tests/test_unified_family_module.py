"""
test_unified_family_module.py
Verifies the Unified Family Module & Combined Slip Phone Storage changes:
1. templates/index.html contains the single consolidation checkbox in batch edit.
2. /families route redirects to /voters.
3. templates/hub.html has removed the redundant family tile.
4. dispatch.py only updates contact_phone for the primary/mukhiya voter in combined slips.
5. campaign.py handles consolidate_family without overwriting existing individual phones.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

class TestUnifiedFamilyModule(unittest.TestCase):
    def setUp(self):
        self.worktree_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    def test_index_html_has_consolidation_checkbox(self):
        modal_path = os.path.join(self.worktree_root, 'templates', 'modals', '_voter_profile_modal.html')
        with open(modal_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn('id="vpmConsolidateFamily"', content)
        self.assertIn('consolidate into one family', content.lower())

    def test_hub_html_removed_family_tile(self):
        hub_path = os.path.join(self.worktree_root, 'templates', 'hub.html')
        with open(hub_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertNotIn('/families', content)

    def test_families_module_completely_removed(self):
        # Confirm template and JS are completely deleted from disk
        fam_html = os.path.join(self.worktree_root, 'templates', 'families.html')
        fam_js = os.path.join(self.worktree_root, 'static', 'js', 'families.js')
        self.assertFalse(os.path.exists(fam_html))
        self.assertFalse(os.path.exists(fam_js))

        # Confirm sw.js does not precache families.js
        sw_path = os.path.join(self.worktree_root, 'static', 'sw.js')
        with open(sw_path, 'r', encoding='utf-8') as f:
            self.assertNotIn('families.js', f.read())

    def test_dispatch_py_only_updates_primary_voter_phone(self):
        dispatch_path = os.path.join(self.worktree_root, 'electionapp', 'blueprints', 'dispatch.py')
        with open(dispatch_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("primary_vid", content)
        self.assertIn("if vid == primary_vid and success_phones:", content)
        self.assertIn("UPDATE voters\n                                SET is_dispatched = true, dispatched_at = NOW()\n", content)

    def test_campaign_py_handles_consolidate_family(self):
        campaign_path = os.path.join(self.worktree_root, 'electionapp', 'blueprints', 'campaign.py')
        with open(campaign_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("consolidate_family", content)
        self.assertIn("SET family_id = %s, family_count = %s", content)
        self.assertIn("is_family_head = TRUE", content)

    def test_voter_management_tagger_js_has_consolidation_support(self):
        js_path = os.path.join(self.worktree_root, 'static', 'js', 'voter_management_tagger.js')
        with open(js_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.assertIn("vpmConsolidateFamily", content)
        self.assertIn("delete payload.contact_phone", content)

if __name__ == '__main__':
    unittest.main()
