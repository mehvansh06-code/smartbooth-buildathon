"""
test_search_filters.py
Unit and integration tests for bilingual multi-field search filters (Gender, Age, Part, Caste, Category).
"""

import unittest
from electionapp import create_app

class SearchFiltersTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'

    def test_filter_gender_male(self):
        resp = self.client.get('/api/search?gender=M&limit=50')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
        self.assertGreater(len(voters), 0)
        for v in voters:
            self.assertIn(v['gender'], ['M', 'Male', 'male', 'MALE', 'पुरुष', 'पुरूष'])

    def test_filter_gender_female(self):
        resp = self.client.get('/api/search?gender=F&limit=50')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
        self.assertGreater(len(voters), 0)
        for v in voters:
            self.assertIn(v['gender'], ['F', 'Female', 'female', 'FEMALE', 'स्त्री', 'महिला'])

    def test_filter_age_range(self):
        resp = self.client.get('/api/search?age_min=20&age_max=25&limit=50')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
        self.assertGreater(len(voters), 0)
        for v in voters:
            self.assertTrue(20 <= v['age'] <= 25, f"Age {v['age']} not in 20..25")

    def test_filter_combined_female_and_age_and_part(self):
        resp = self.client.get('/api/search?gender=F&age_min=18&age_max=22&part_no=1&limit=50')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
        self.assertGreater(len(voters), 0)
        for v in voters:
            self.assertEqual(v['part_no'], 1)
            self.assertIn(v['gender'], ['F', 'Female', 'female', 'FEMALE', 'स्त्री', 'महिला'])
            self.assertTrue(18 <= v['age'] <= 22)

    def test_filter_name_query_with_gender(self):
        resp = self.client.get('/api/search?q=Devi&gender=F&limit=20')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
    def test_default_sort_is_serial_number_wise(self):
        resp = self.client.get('/api/search?part_no=1&limit=30')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
        self.assertGreater(len(voters), 1)
        serials = [v.get('sl_no') for v in voters if v.get('sl_no') is not None]
        for i in range(len(serials) - 1):
            self.assertLessEqual(serials[i], serials[i + 1], f"Expected serial sort: {serials[i]} <= {serials[i+1]}")

    def test_filter_az_sorting_with_sort_param(self):
        resp = self.client.get('/api/search?part_no=1&sort=name&limit=30')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        voters = data['voters']
        self.assertGreater(len(voters), 1)
        names = [(v.get('name_en') or v.get('name_hi') or '').strip().lower() for v in voters]
        for i in range(len(names) - 1):
            self.assertLessEqual(names[i], names[i + 1], f"Expected A-Z sort: '{names[i]}' should precede or equal '{names[i+1]}'")

if __name__ == '__main__':
    unittest.main()
