"""
test_caste_demographics_verification.py
Automated test suite verifying:
1. /api/caste/analytics endpoint:
   - Tenant whole-campaign analytics (tenant='jelatra', tenant_id=1, role='admin')
   - Filtered scope by ward (ward_no='1')
2. GET /caste?tenant=jelatra rendering and DOM controls:
   - tabBtnVoters, tabBtnAnalytics, votersTabView, analyticsTabView, casteAnalyticsCardsContainer
3. Rule 6 PWA Scroll-Chain compliance:
   - Zero 'safe-main-viewport' in templates/caste.html
   - Zero 'overflow-y: auto' / 'contain' on inner divs of casteViewContainer
"""

import os
import re
import unittest
from electionapp import create_app

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestCasteDemographicsVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('jelatra', storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'admin'
            sess['tenant_id'] = 1
            sess['tenant_slug'] = 'jelatra'

    def test_api_caste_analytics_whole_campaign(self):
        """Test 1a: /api/caste/analytics for tenant='jelatra' (whole campaign)."""
        resp = self.client.get('/api/caste/analytics?tenant=jelatra')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        # Verify success and total_voters
        self.assertTrue(data.get('success'), f"Expected success=True, got {data}")
        self.assertGreater(data.get('total_voters', 0), 0, "total_voters must be > 0")
        self.assertEqual(data.get('scope'), 'ALL')
        self.assertIsNone(data.get('filter_ward'))

        # Verify metrics
        self.assertGreaterEqual(data.get('tagged_voters', 0), 0)
        self.assertGreaterEqual(data.get('phone_count', 0), 0)

        # Verify communities list
        communities = data.get('communities')
        self.assertIsInstance(communities, list)
        self.assertGreater(len(communities), 0, "communities list must be populated")
        
        # Verify structure of first community
        first_c = communities[0]
        self.assertIn('caste', first_c)
        self.assertIn('count', first_c)
        self.assertIn('pct', first_c)
        self.assertIn('phone_count', first_c)
        self.assertIn('phone_pct', first_c)
        self.assertIn('is_untagged', first_c)
        self.assertIn('wards', first_c)

        # Verify ward_summaries list
        ward_summaries = data.get('ward_summaries')
        self.assertIsInstance(ward_summaries, list)
        self.assertGreater(len(ward_summaries), 0, "ward_summaries must be populated for whole campaign")
        first_w = ward_summaries[0]
        self.assertIn('ward_no', first_w)
        self.assertIn('total_voters', first_w)
        self.assertIn('phone_count', first_w)
        self.assertIn('phone_pct', first_w)

    def test_api_caste_analytics_filtered_ward(self):
        """Test 1b: /api/caste/analytics for tenant='jelatra' and ward_no='1'."""
        resp = self.client.get('/api/caste/analytics?tenant=jelatra&ward_no=1')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        # Verify filtered scope
        self.assertTrue(data.get('success'), f"Expected success=True, got {data}")
        self.assertEqual(data.get('scope'), 'WARD_1')
        self.assertEqual(data.get('filter_ward'), 1)
        self.assertGreater(data.get('total_voters', 0), 0, "Ward 1 total_voters must be > 0")

        # Communities should be populated for ward 1
        communities = data.get('communities')
        self.assertIsInstance(communities, list)
        self.assertGreater(len(communities), 0)

        # Ward summaries should NOT be populated when filtered to a single ward
        ward_summaries = data.get('ward_summaries', [])
        self.assertEqual(len(ward_summaries), 0, "ward_summaries should be empty when ward_no filter is applied")

    def test_caste_page_dom_controls(self):
        """Test 2: GET /caste?tenant=jelatra returns 200 and required DOM controls."""
        resp = self.client.get('/caste?tenant=jelatra')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')

        required_elements = [
            'tabBtnVoters',
            'tabBtnAnalytics',
            'votersTabView',
            'analyticsTabView',
            'casteAnalyticsCardsContainer',
        ]
        for elem_id in required_elements:
            self.assertIn(elem_id, html, f"Required element '{elem_id}' not found in /caste HTML")

    def test_rule_6_scroll_chain_compliance(self):
        """Test 3: Rule 6 PWA Mobile Viewport & Scroll-Chain Safety Standard."""
        template_path = os.path.join(PLATFORM_ROOT, 'templates', 'caste.html')
        self.assertTrue(os.path.exists(template_path))

        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. Confirm 'safe-main-viewport' is NEVER in templates/caste.html
        self.assertNotIn('safe-main-viewport', content, 
                         "STRICT PROHIBITION VIOLATION: 'safe-main-viewport' found in caste.html!")

        # 2. Extract casteViewContainer inner content (everything between casteViewContainer and end of content)
        match = re.search(r'<div id="casteViewContainer"[^>]*>(.*?)<!-- CASTE SELECTION BOTTOM SHEET MODAL -->', 
                          content, re.DOTALL)
        self.assertIsNotNone(match, "Could not isolate casteViewContainer content")
        view_container_html = match.group(1)

        # Confirm neither 'overflow-y: auto' nor 'contain' / 'overscroll-behavior-y' are on inner divs
        inner_divs = re.findall(r'<div[^>]*class="([^"]*)"[^>]*>', view_container_html)
        for class_attr in inner_divs:
            classes = class_attr.split()
            for cls_name in classes:
                self.assertNotIn('safe-main-viewport', cls_name,
                                 f"Forbidden class '{cls_name}' found in inner div")
                self.assertNotIn('contain', cls_name,
                                 f"Forbidden class '{cls_name}' found in inner div")
                self.assertNotEqual('overflow-y-auto', cls_name,
                                    f"Forbidden inner scroll trap '{cls_name}' found in inner div")


if __name__ == '__main__':
    unittest.main()
