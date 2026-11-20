"""
test_login_redesign_and_rajasthan_campaign_creator.py
Unit & Integration Tests for:
1. Modern Light Civic Login Page (`templates/login.html`)
2. Rajasthan Electoral Catalog APIs (`rajasthan_catalog.py`)
3. 1-Click Rajasthan Area Campaign & User Creation with Instant Roll Ingestion
"""

import os
import sys
import shutil
import json
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app
from electionapp import rajasthan_catalog
import user_manager


class LoginRedesignAndRajasthanCreatorTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['VOTER_CORE_MODE'] = '0'
        cls.app = create_app(tenant_slug='ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()
        user_manager.ensure_schema()
        cls.test_slug = "test_ajaysar_demo"
        cls.test_tenant_dir = os.path.join(PLATFORM_ROOT, 'tenants', cls.test_slug)

    @classmethod
    def tearDownClass(cls):
        # Cleanup test tenant directory if created
        if os.path.exists(cls.test_tenant_dir):
            try:
                shutil.rmtree(cls.test_tenant_dir)
            except Exception:
                pass

        # Cleanup PostgreSQL partition and tenant entry
        try:
            import psycopg2
            pg = psycopg2.connect(
                dbname=os.environ.get("PGDATABASE", "voter_platform_test"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ.get("PGPASSWORD", "postgres"),
                host=os.environ.get("PGHOST", "localhost"),
                port=int(os.environ.get("PGPORT", 5432))
            )
            with pg.cursor() as cur:
                cur.execute("SELECT tenant_id FROM tenants WHERE campaign_slug = %s", (cls.test_slug,))
                row = cur.fetchone()
                if row:
                    tid = row[0]
                    cur.execute(f"DROP TABLE IF EXISTS voters_t{tid};")
                    cur.execute("DELETE FROM users WHERE tenant_slug = %s", (cls.test_slug,))
                    cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tid,))
                pg.commit()
            pg.close()
        except Exception:
            pass

    def test_01_login_page_renders_clean_light_theme(self):
        """Verify login page uses modern light theme with password toggle and tricolor ribbon."""
        resp = self.client.get('/login')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Check light theme elements
        self.assertIn("bg-gradient-to-br from-slate-50", html)
        self.assertIn("tricolor-stripe", html)
        self.assertIn("सुरक्षित वॉर-रूम पोर्टल", html)
        self.assertIn("togglePasswordVisibility", html)
        self.assertIn("passwordToggleBtn", html)
        # Verify dark blue/black background is NOT used on the body
        self.assertNotIn("class=\"bg-slate-950 text-slate-100 min-h-screen", html)

    def test_02_login_page_neutral_smart_booth_branding(self):
        """Verify login page displays neutral Smart Booth branding without candidate names, photos, or party symbols."""
        resp = self.client.get('/login?tenant=ward82')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Smart Booth", html)
        self.assertIn("स्मार्ट बूथ", html)
        self.assertIn("/static/icons/logo.png", html)
        self.assertNotIn("प्रसन चन्द मेहता", html)
        self.assertNotIn("प्रसन्न चंद मेहता", html)
        self.assertNotIn("candidate_photo.png", html)
        self.assertNotIn("Party Symbol", html)
        self.assertNotIn("अभियान:", html)
        self.assertNotIn("Ward 82", html)

    def test_03_rajasthan_catalog_apis(self):
        """Verify districts, blocks, GPs, area preview, and assembly seats APIs."""
        # 1. Districts
        resp = self.client.get('/api/admin/rajasthan/districts')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        districts = [d['district'] for d in data['districts']]
        self.assertIn("AJMER", districts)
        self.assertIn("JAIPUR", districts)

        # 2. Blocks in Ajmer
        resp = self.client.get('/api/admin/rajasthan/blocks?district=AJMER')
        self.assertEqual(resp.status_code, 200)
        b_data = resp.get_json()
        self.assertTrue(b_data['success'])
        blocks = [b['ps_name'] for b in b_data['blocks']]
        self.assertIn("AJMER RURAL PANCHAYAT SAMITI", blocks)

        # 3. GPs in Ajmer Rural
        resp = self.client.get('/api/admin/rajasthan/gps?district=AJMER&block=AJMER%20RURAL%20PANCHAYAT%20SAMITI')
        self.assertEqual(resp.status_code, 200)
        g_data = resp.get_json()
        self.assertTrue(g_data['success'])
        gps = [g['gp_name'] for g in g_data['gps']]
        self.assertIn("Ajaysar", gps)

        # 4. Area Preview for Ajaysar
        resp = self.client.get('/api/admin/rajasthan/area_preview?district=AJMER&block=AJMER%20RURAL%20PANCHAYAT%20SAMITI&gp=Ajaysar')
        self.assertEqual(resp.status_code, 200)
        prev = resp.get_json()
        self.assertTrue(prev['success'])
        self.assertTrue(prev['has_records'])
        self.assertGreaterEqual(prev['ward_count'], 10)
        self.assertGreaterEqual(prev['total_voters'], 4000)
        self.assertEqual(prev['suggested_slug'], "gp_ajaysar")
        self.assertIn("Ajaysar", prev['suggested_ward_hi'])
        self.assertGreaterEqual(len(prev['booths']), 1)

        # 5. Assembly Constituencies
        resp = self.client.get('/api/admin/rajasthan/assembly_constituencies')
        self.assertEqual(resp.status_code, 200)
        ac_data = resp.get_json()
        self.assertTrue(ac_data['success'])
        self.assertGreaterEqual(len(ac_data['constituencies']), 60)

    def test_04_create_campaign_with_rajasthan_data_and_admin_user(self):
        """Verify 1-click campaign creation provisions PostgreSQL partition, imports rolls, and creates user."""
        payload = {
            'campaign_slug': self.test_slug,
            'election_type': 'PANCHAYAT',
            'ward_no': '13',
            'ward_name_hi': 'ग्राम पंचायत अजयसर',
            'ward_name_en': 'Gram Panchayat Ajaysar',
            'constituency': 'Ajmer Rural',
            'district': 'Ajmer',
            'state': 'Rajasthan',
            'candidate_name_hi': 'रामेश्वर लाल गुर्जर',
            'candidate_name_en': 'Rameshwar Lal Gurjar',
            'party_name_hi': 'भारतीय जनता पार्टी',
            'party_name_en': 'BJP',
            'ballot_serial': '1',
            'theme_color': '#ea580c',
            'storage_backend': 'postgres',
            'rajasthan_district': 'AJMER',
            'rajasthan_block': 'AJMER RURAL PANCHAYAT SAMITI',
            'rajasthan_gp': 'Ajaysar',
            'create_admin_user': '1',
            'admin_username': f'admin_{self.test_slug}',
            'admin_password': 'TestPassword@2026',
            'admin_full_name': 'Rameshwar Lal Gurjar',
            'admin_role': 'candidate_admin',
            'auto_ingest_rolls': '1'
        }

        resp = self.client.post('/api/admin/campaigns/create', data=payload)
        self.assertIn(resp.status_code, [200, 201])
        res = resp.get_json()
        self.assertTrue(res['success'])
        self.assertEqual(res['tenant_slug'], self.test_slug)
        self.assertGreaterEqual(res['voters_imported'], 4000)
        self.assertIsNotNone(res['user_created'])
        self.assertEqual(res['user_created']['username'], f'admin_{self.test_slug}')

        # Verify campaign.json was written with booths
        cfg_path = os.path.join(self.test_tenant_dir, 'campaign.json')
        self.assertTrue(os.path.exists(cfg_path))
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.assertGreater(len(cfg.get('booths', {})), 0)

        # Verify user can authenticate with user_manager
        user, err = user_manager.authenticate_user(f'admin_{self.test_slug}', 'TestPassword@2026')
        self.assertIsNone(err)
        self.assertIsNotNone(user)
        self.assertEqual(user['role'], 'candidate_admin')
        self.assertEqual(user['tenant_slug'], self.test_slug)


if __name__ == '__main__':
    unittest.main()
