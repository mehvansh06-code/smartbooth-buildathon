"""
test_outside_and_karyakarta.py
Unit and integration tests for Outside Voter Marking and Karyakarta Assignment features.
"""

import unittest
from electionapp import create_app


class TestOutsideAndKaryakarta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'

        self.test_voter_id = 3513

    def test_karyakarta_lifecycle(self):
        # 1. Add karyakarta via /api/karyakartas/save
        add_res = self.client.post('/api/karyakartas/save', json={
            'name': 'Test Karyakarta',
            'phone': '9988776655',
            'booth_no': 1,
            'area': 'Ward 82 North',
            'notes': 'Unit test'
        })
        self.assertEqual(add_res.status_code, 200)
        data = add_res.get_json()
        self.assertTrue(data.get('success'))
        k_id = data.get('id')
        self.assertIsNotNone(k_id)

        # 2. Assign to voter
        assign_res = self.client.post('/api/karyakartas/assign', json={
            'voter_ids': [self.test_voter_id],
            'karyakarta_id': k_id
        })
        self.assertEqual(assign_res.status_code, 200)
        a_data = assign_res.get_json()
        self.assertTrue(a_data.get('success'))
        self.assertEqual(a_data.get('karyakarta_name'), 'Test Karyakarta')

        # 3. Check /api/karyakartas/<k_id>/voters
        voters_res = self.client.get(f'/api/karyakartas/{k_id}/voters')
        self.assertEqual(voters_res.status_code, 200)
        v_data = voters_res.get_json()
        self.assertTrue(v_data.get('success'))
        self.assertTrue(any(v.get('id') == self.test_voter_id for v in v_data.get('voters', [])))

        # 4. Unassign voter
        unassign_res = self.client.post('/api/karyakartas/unassign', json={
            'voter_ids': [self.test_voter_id]
        })
        self.assertEqual(unassign_res.status_code, 200)
        self.assertTrue(unassign_res.get_json().get('success'))

        # 5. Delete karyakarta via URL parameter /api/karyakartas/delete/<k_id>
        del_res = self.client.post(f'/api/karyakartas/delete/{k_id}')
        self.assertEqual(del_res.status_code, 200)
        self.assertTrue(del_res.get_json().get('success'))

    def test_outside_voter_lifecycle(self):
        # 1. Mark test voter as outside
        mark_res = self.client.post('/api/outside_voters/mark', json={
            'voter_ids': [self.test_voter_id],
            'notes': 'Mumbai, Travel needed'
        })
        self.assertEqual(mark_res.status_code, 200)
        self.assertTrue(mark_res.get_json().get('success'))

        # 2. Check /api/outside_voters
        list_res = self.client.get('/api/outside_voters')
        self.assertEqual(list_res.status_code, 200)
        l_data = list_res.get_json()
        self.assertTrue(any(v.get('voter_id') == self.test_voter_id for v in l_data.get('outside_voters', [])))

        # 3. Unmark test voter
        unmark_res = self.client.post('/api/outside_voters/unmark', json={
            'voter_ids': [self.test_voter_id]
        })
        self.assertEqual(unmark_res.status_code, 200)
        self.assertTrue(unmark_res.get_json().get('success'))

    def test_profile_update_karyakarta_and_outside(self):
        # Create temporary karyakarta
        k_res = self.client.post('/api/karyakartas/add', json={
            'name': 'Profile Karyakarta',
            'phone': '9123456789',
            'booth_no': 2
        })
        k_id = k_res.get_json().get('id')

        # Update voter profile with both outside and karyakarta
        up_res = self.client.post('/api/voter/profile/update', json={
            'voter_id': self.test_voter_id,
            'is_outside': True,
            'assigned_karyakarta_id': k_id,
            'notes': 'Test profile update'
        })
        self.assertEqual(up_res.status_code, 200)
        up_data = up_res.get_json()
        self.assertTrue(up_data.get('success'))
        self.assertEqual(up_data['updated']['assigned_karyakarta_id'], k_id)
        self.assertEqual(up_data['updated']['assigned_karyakarta_name'], 'Profile Karyakarta')
        self.assertEqual(up_data['updated']['is_outside'], 1)

        # Cleanup
        self.client.post(f'/api/karyakartas/delete/{k_id}')
        self.client.post('/api/outside_voters/unmark', json={'voter_ids': [self.test_voter_id]})

    def test_karyakartas_options_and_subaccount_sync(self):
        import user_manager
        # 1. Create a sub-account with role 'karyakarta' in users table
        ok, msg, uid = user_manager.create_user_account(
            username='unit_test_worker',
            password='Password@123',
            full_name='SubAccount Worker Unit',
            phone='9876543210',
            role='karyakarta',
            tenant_slug='ward82',
            assigned_booth=3
        )
        self.assertTrue(ok)

        # 2. Call /api/karyakartas/options (used by voter edit modal dropdown)
        res = self.client.get('/api/karyakartas/options?tenant=ward82')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        karyas = data.get('karyakartas', [])
        # The sub-account worker must have been auto-synced into karyakartas
        synced = next((k for k in karyas if k.get('name') == 'SubAccount Worker Unit'), None)
        self.assertIsNotNone(synced)
        self.assertEqual(synced.get('booth_no'), 3)

        # 3. Assign this synced karyakarta to a voter via /api/voter/profile/update
        up_res = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_id': self.test_voter_id,
            'assigned_karyakarta_id': synced['id']
        })
        self.assertEqual(up_res.status_code, 200)
        up_data = up_res.get_json()
        self.assertTrue(up_data.get('success'))
        self.assertEqual(up_data['updated']['assigned_karyakarta_id'], synced['id'])
        self.assertEqual(up_data['updated']['assigned_karyakarta_name'], 'SubAccount Worker Unit')

        # Cleanup
        self.client.post(f"/api/karyakartas/delete/{synced['id']}")
        user_manager.delete_user_account(uid)

    def test_warroom_stats_voted_sentiment_breakdown(self):
        # Verify /api/warroom/stats returns voted_favored, voted_swing, voted_opp per booth
        res = self.client.get('/api/warroom/stats?tenant=ward82')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('voted_favored_count', data)
        self.assertIn('voted_swing_count', data)
        self.assertIn('voted_opposition_count', data)

        booths = data.get('booth_breakdown') or data.get('booths') or []
        self.assertTrue(len(booths) > 0)
        b0 = booths[0]
        self.assertIn('favored', b0)
        self.assertIn('voted_favored', b0)
        self.assertIn('swing', b0)
        self.assertIn('voted_swing', b0)
        self.assertIn('opp', b0)
        self.assertIn('voted_opp', b0)


if __name__ == '__main__':
    unittest.main()
