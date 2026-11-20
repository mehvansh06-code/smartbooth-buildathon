import unittest
from electionapp import create_app

class FamilyMemberOpsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('sarana')
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_family_page_ui_elements(self):
        """Verify the new Add Member and Bulk Messaging filters are in the family page HTML."""
        res = self.client.get('/family?tenant=sarana')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Member drawer additions
        self.assertIn('addMemberSearchPane', html)
        self.assertIn('candidateSearchInput', html)
        self.assertIn('toggleAddMemberPane', html)
        self.assertIn('confirmRemoveFamilyMember', html)

        # Enriched batch modal additions
        self.assertIn('batchFilterCaste', html)
        self.assertIn('batchRecipientMode', html)
        self.assertIn('batchSentFavored', html)
        self.assertIn('batchSentSwing', html)
        self.assertIn('batchSentOpposed', html)
        self.assertIn('batchSentNeutral', html)
        self.assertIn('batchOnlyWithPhone', html)
        self.assertIn('setBatchSentimentPreset', html)

    def test_api_family_search_candidates(self):
        """Verify GET /api/family/search_candidates endpoint."""
        res = self.client.get('/api/family/search_candidates?tenant=sarana&q=राम')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('candidates', data)

    def test_api_family_search_candidates_short_query(self):
        """Verify query < 2 chars returns empty candidates list."""
        res = self.client.get('/api/family/search_candidates?tenant=sarana&q=a')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('candidates'), [])

    def test_api_family_remove_member_validation(self):
        """Verify POST /api/family/remove_member validation."""
        # Missing voter_id
        res = self.client.post('/api/family/remove_member?tenant=sarana', json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertIn('voter_id is required', data.get('error'))

        # Invalid voter_id
        res = self.client.post('/api/family/remove_member?tenant=sarana', json={'voter_id': 'abc'})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json().get('success'))

    def test_api_family_add_member_validation(self):
        """Verify POST /api/family/add_member validation."""
        # Missing voter_id
        res = self.client.post('/api/family/add_member?tenant=sarana', json={'target_family_id': '101'})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json().get('success'))

        # Missing target_family_id
        res = self.client.post('/api/family/add_member?tenant=sarana', json={'voter_id': 1})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.get_json().get('success'))

if __name__ == '__main__':
    unittest.main()
