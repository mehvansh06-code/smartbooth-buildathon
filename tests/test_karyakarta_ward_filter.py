"""
test_karyakarta_ward_filter.py
Unit tests for ward and multi-ward filtering in Karyakarta Directory & Assigned Voters API.
"""

import unittest
from electionapp import create_app


class TestKaryakartaWardFilter(unittest.TestCase):
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

    def test_karyakartas_list_unfiltered(self):
        res = self.client.get('/api/karyakartas?tenant=ward82')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('karyakartas', data)
        self.assertIsInstance(data['karyakartas'], list)

    def test_karyakartas_ward_filter_lifecycle(self):
        # 1. Create Karyakarta for Ward 1
        res1 = self.client.post('/api/karyakartas/save?tenant=ward82', json={
            'name': 'Worker Ward One',
            'phone': '9800000001',
            'booth_no': 'ward:1',
            'area': 'Ward 1 Sector'
        })
        self.assertEqual(res1.status_code, 200)
        d1 = res1.get_json()
        self.assertTrue(d1.get('success'))
        k1_id = d1.get('id')

        # 2. Create Karyakarta for Ward 2
        res2 = self.client.post('/api/karyakartas/save?tenant=ward82', json={
            'name': 'Worker Ward Two',
            'phone': '9800000002',
            'booth_no': 'ward:2',
            'area': 'Ward 2 Sector'
        })
        self.assertEqual(res2.status_code, 200)
        d2 = res2.get_json()
        self.assertTrue(d2.get('success'))
        k2_id = d2.get('id')

        # 3. Create Karyakarta for Ward 99
        res3 = self.client.post('/api/karyakartas/save?tenant=ward82', json={
            'name': 'Worker Ward NinetyNine',
            'phone': '9800000099',
            'booth_no': 'ward:99',
            'area': 'Ward 99 Sector'
        })
        self.assertEqual(res3.status_code, 200)
        d3 = res3.get_json()
        self.assertTrue(d3.get('success'))
        k3_id = d3.get('id')

        try:
            # 4. Filter by single ward (Ward 1)
            list_w1 = self.client.get('/api/karyakartas?tenant=ward82&ward_no=1')
            self.assertEqual(list_w1.status_code, 200)
            data_w1 = list_w1.get_json()
            ids_w1 = [k['id'] for k in data_w1.get('karyakartas', [])]
            self.assertIn(k1_id, ids_w1)
            self.assertNotIn(k2_id, ids_w1)
            self.assertNotIn(k3_id, ids_w1)

            # 5. Filter with "ward:1" syntax
            list_w1_pref = self.client.get('/api/karyakartas?tenant=ward82&ward_no=ward:1')
            self.assertEqual(list_w1_pref.status_code, 200)
            data_w1_pref = list_w1_pref.get_json()
            ids_w1_pref = [k['id'] for k in data_w1_pref.get('karyakartas', [])]
            self.assertIn(k1_id, ids_w1_pref)
            self.assertNotIn(k2_id, ids_w1_pref)

            # 6. Multi-ward filter (Wards 1 and 2): "wards:1,2"
            list_multi = self.client.get('/api/karyakartas?tenant=ward82&ward_no=wards:1,2')
            self.assertEqual(list_multi.status_code, 200)
            data_multi = list_multi.get_json()
            ids_multi = [k['id'] for k in data_multi.get('karyakartas', [])]
            self.assertIn(k1_id, ids_multi)
            self.assertIn(k2_id, ids_multi)
            self.assertNotIn(k3_id, ids_multi)

            # 7. Check karyakarta voters endpoint returns ward_no
            voters_res = self.client.get(f'/api/karyakartas/{k1_id}/voters?tenant=ward82')
            self.assertEqual(voters_res.status_code, 200)
            v_data = voters_res.get_json()
            self.assertTrue(v_data.get('success'))
            self.assertIn('voters', v_data)

        finally:
            # Cleanup
            self.client.post(f'/api/karyakartas/delete/{k1_id}?tenant=ward82')
            self.client.post(f'/api/karyakartas/delete/{k2_id}?tenant=ward82')
            self.client.post(f'/api/karyakartas/delete/{k3_id}?tenant=ward82')


if __name__ == '__main__':
    unittest.main()
