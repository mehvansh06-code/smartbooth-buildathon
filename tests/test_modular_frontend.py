import os
import re
import sys
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app


class ModularFrontendTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def test_modular_frontend_templates(self):
        templates_to_test = [
            ('/warroom', 'warroom.js'),
            ('/turnout', 'turnout_live.js'),
            ('/voting-day', 'voting_day.js'),
            ('/karyakartas', 'karyakartas.js'),
            ('/caste', 'caste_census.js'),
        ]

        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

        for route, expected_js in templates_to_test:
            res = self.client.get(route)
            self.assertEqual(res.status_code, 200, f"{route} returned status {res.status_code}")
            html = res.data.decode('utf-8')
            res.close()
            
            # 1. Zero references to app_bundle
            self.assertNotIn('app_bundle', html, f"{route} still references app_bundle!")

            # 2. Contains expected module
            self.assertIn(expected_js, html, f"{route} does not load {expected_js}")

            # 3. Check script references
            scripts = re.findall(r'src=[\'"](/static/[^\'"]+)[\'"]', html)
            for s in scripts:
                clean_s = s.split('?')[0]
                with self.client.get(clean_s) as static_res:
                    self.assertEqual(static_res.status_code, 200, f"Script {clean_s} returned status {static_res.status_code}")

    def test_deleted_monolith_bundle(self):
        app_bundle_path = os.path.join(PLATFORM_ROOT, 'static', 'app_bundle.js')
        self.assertFalse(os.path.exists(app_bundle_path), f"{app_bundle_path} still exists!")
        res_del = self.client.get('/static/app_bundle.js')
        self.assertEqual(res_del.status_code, 404)


if __name__ == '__main__':
    unittest.main()
