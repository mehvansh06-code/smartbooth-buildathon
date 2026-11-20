import unittest
from electionapp import create_app

class FamilyModuleTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('sarana')
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_family_page_get(self):
        """Verify GET /family and /families return 200."""
        res = self.client.get('/family?tenant=sarana')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('परिवार व मुखिया प्रबंधन', html)
        self.assertIn('familySearchInput', html)
        self.assertIn('familiesGridContainer', html)

    def test_api_families_summary(self):
        """Verify GET /api/families/summary endpoint."""
        res = self.client.get('/api/families/summary?tenant=sarana')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('families', data)
        self.assertIn('stats', data)
        self.assertGreater(data['stats']['total_families'], 0)
        self.assertGreater(data['stats']['total_members'], 0)

    def test_api_family_members_by_head(self):
        """Verify GET /api/family/members_by_head endpoint."""
        # Find first mukhiya from summary
        summary_res = self.client.get('/api/families/summary?tenant=sarana')
        self.assertEqual(summary_res.status_code, 200)
        families = summary_res.get_json().get('families', [])
        if families:
            first_mukhiya = families[0]['mukhiya_name']
            res = self.client.get(f'/api/family/members_by_head?tenant=sarana&mukhiya_name={first_mukhiya}')
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get('success'))
            self.assertIn('members', data)
            self.assertGreater(len(data['members']), 0)

    def test_api_families_summary_member_ids_and_all_phones(self):
        """Verify member_ids ordering and all_phones aggregation."""
        res = self.client.get('/api/families/summary?tenant=sarana&limit=10')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        families = data.get('families', [])
        self.assertGreater(len(families), 0)

        for fam in families:
            # Check member_ids
            member_ids = fam.get('member_ids')
            self.assertIsInstance(member_ids, list)
            self.assertEqual(len(member_ids), fam['total_members'])

            # Check all_phones
            all_phones = fam.get('all_phones')
            self.assertIsInstance(all_phones, list)
            for phone in all_phones:
                self.assertIsInstance(phone, str)
                self.assertGreaterEqual(len(phone.strip()), 10)

    def test_api_families_summary_text_search(self):
        """Verify text search branch returns ordered member_ids and all_phones."""
        # Grab a Mukhiya name from browse
        browse_res = self.client.get('/api/families/summary?tenant=sarana&limit=1')
        fam = browse_res.get_json()['families'][0]
        mukhiya_name = fam['mukhiya_name']

        # Search for that Mukhiya
        search_res = self.client.get(f'/api/families/summary?tenant=sarana&q={mukhiya_name}')
        self.assertEqual(search_res.status_code, 200)
        sdata = search_res.get_json()
        self.assertTrue(sdata.get('success'))
        sfamilies = sdata.get('families', [])
        self.assertGreater(len(sfamilies), 0)

        for sfam in sfamilies:
            self.assertIsInstance(sfam.get('member_ids'), list)
            self.assertIsInstance(sfam.get('all_phones'), list)
            self.assertEqual(len(sfam['member_ids']), sfam['total_members'])

    def test_api_families_summary_limit_parameters(self):
        """Verify flexible limit parameters (0, -1, all, up to 5000)."""
        # Small limit
        res_5 = self.client.get('/api/families/summary?tenant=sarana&limit=5')
        self.assertEqual(len(res_5.get_json()['families']), 5)

        # limit=0 (treat as no limit / 10000)
        res_0 = self.client.get('/api/families/summary?tenant=sarana&limit=0')
        count_0 = len(res_0.get_json()['families'])
        self.assertGreater(count_0, 100)

        # limit=-1 (treat as no limit / 10000)
        res_neg1 = self.client.get('/api/families/summary?tenant=sarana&limit=-1')
        count_neg1 = len(res_neg1.get_json()['families'])
        self.assertEqual(count_0, count_neg1)

        # limit=all
        res_all = self.client.get('/api/families/summary?tenant=sarana&limit=all')
        count_all = len(res_all.get_json()['families'])
        self.assertEqual(count_0, count_all)

    def test_api_families_summary_excludes_deleted_voters(self):
        """Verify voters marked as Deleted are excluded from family summary."""
        from electionapp.tenancy import resolve_tenant
        with self.app.test_request_context('/?tenant=sarana'):
            tenant, repo = resolve_tenant()
            tid, _ = repo._resolve_tenant(tenant)
            with repo.get_connection() as conn:
                with conn.cursor() as cur:
                    # Find a voter to mark as deleted temporarily
                    cur.execute("""
                        SELECT voter_id, status FROM voters 
                        WHERE tenant_id = %s 
                        LIMIT 1
                    """, (tid,))
                    voter = cur.fetchone()
                    test_vid = voter[0]
                    orig_status = voter[1]

                    try:
                        cur.execute("UPDATE voters SET status = 'Deleted' WHERE voter_id = %s", (test_vid,))
                        conn.commit()

                        # Check that this voter does NOT appear in any member_ids in summary
                        res = self.client.get('/api/families/summary?tenant=sarana&limit=all')
                        all_fams = res.get_json()['families']
                        for f in all_fams:
                            self.assertNotIn(test_vid, f['member_ids'], f"Deleted voter {test_vid} was found in member_ids")
                    finally:
                        cur.execute("UPDATE voters SET status = %s WHERE voter_id = %s", (orig_status, test_vid))
                        conn.commit()

if __name__ == '__main__':
    unittest.main()
