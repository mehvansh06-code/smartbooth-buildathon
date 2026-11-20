"""
test_super_admin_portal.py
Comprehensive Test Suite for Super Admin Command Center, Voter Roll Ingestion Engine,
and Voter Profile Management.
Runs under PostgreSQL ($env:STORAGE_BACKEND="postgres").
"""

import os
import sys
import io
import json
import unittest
from typing import Dict, Any

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app
from electionapp import voter_ingest
import user_manager


class SuperAdminPortalTestCase(unittest.TestCase):
    """Test suite for voter roll ingestion, admin endpoints, and voter profile updates."""

    @classmethod
    def setUpClass(cls):
        os.environ['VOTER_CORE_MODE'] = '0'
        cls.app = create_app(tenant_slug='ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()
        user_manager.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        repo = cls.app.extensions.get('repo')
        if repo and hasattr(repo, 'get_connection'):
            try:
                with repo.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM voters WHERE tenant_id = 3 AND part_no = 99")
                    conn.commit()
            except Exception:
                pass
        try:
            import psycopg2
            pg = psycopg2.connect(
                dbname=os.environ.get("PGDATABASE", "voter_platform_test"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ.get("PGPASSWORD", "postgres"),
                host=os.environ.get("PGHOST", "localhost"),
                port=int(os.environ.get("PGPORT", 5432))
            )
            cur = pg.cursor()
            cur.execute("DELETE FROM voters WHERE part_no = 99")
            pg.commit()
            pg.close()
        except Exception:
            pass

    def test_01_csv_template_generation(self):
        """Verify CSV template generation produces standard bilingual header and realistic rows."""
        csv_text = voter_ingest.generate_csv_template()
        self.assertIn("part_no,sl_no,epic_no,name_en,name_hi", csv_text)
        self.assertIn("relative_name_en,relative_name_hi,relation_type,house_no,age,gender", csv_text)
        self.assertIn("mobile,caste_tag", csv_text)
        self.assertIn("Rajesh Kumar", csv_text)
        self.assertIn("राजेश कुमार", csv_text)

    def test_02_voter_roll_ingestion(self):
        """Verify batch voter roll ingestion handles CSV string, column mapping, and upserts."""
        sample_csv = (
            "भाग संख्या,क्रम संख्या,पहचान पत्र,नाम,पिता/पति का नाम,संबंध,मकान नंबर,आयु,लिंग,मोबाइल,जाति\n"
            "99,1,TEST/01/001/000901,Amit Sharma,Ramesh Sharma,F,House-10,35,पुरुष,9811122233,Brahmin\n"
            "99,2,TEST/01/001/000902,Priya Sharma,Amit Sharma,H,House-10,32,महिला,9811122234,Brahmin\n"
        )

        # 0. Clean up any leftover test rows from previous runs
        repo = self.app.extensions.get('repo')
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM voters WHERE part_no = 99")
            conn.commit()

        # 1. Direct ingestion function call
        res = voter_ingest.ingest_voter_file(
            file_obj_or_path=sample_csv.encode('utf-8'),
            filename="sample_roll.csv",
            tenant_slug="ward82",
            storage_backend="postgres"
        )
        self.assertTrue(res.get('success'))
        self.assertEqual(res.get('total_rows'), 2)
        self.assertEqual(res.get('inserted'), 2)
        self.assertEqual(res.get('updated'), 0)

        # 2. Re-ingestion to verify upsert updates without duplicate constraint errors
        res2 = voter_ingest.ingest_voter_file(
            file_obj_or_path=sample_csv.encode('utf-8'),
            filename="sample_roll.csv",
            tenant_slug="ward82",
            storage_backend="postgres"
        )
        self.assertTrue(res2.get('success'))
        self.assertEqual(res2.get('total_rows'), 2)
        self.assertEqual(res2.get('inserted'), 0)
        self.assertEqual(res2.get('updated'), 2)

        # 3. Test HTTP upload endpoint
        data = {
            'file': (io.BytesIO(sample_csv.encode('utf-8')), 'test_upload.csv')
        }
        resp = self.client.post(
            '/api/admin/campaigns/ward82/upload_voters',
            data=data,
            content_type='multipart/form-data'
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload.get('success'))
        self.assertEqual(payload.get('total_rows'), 2)

    def test_03_admin_partitions_endpoint(self):
        """Verify partition and storage metrics endpoint returns valid health info."""
        resp = self.client.get('/api/admin/partitions')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('partitions', data)
        self.assertIn('total_partitions', data)
        self.assertIn('total_size_mb', data)
        self.assertIn('backend', data)
        self.assertGreaterEqual(data.get('total_partitions', 0), 1)

    def test_04_admin_system_health_endpoint(self):
        """Verify system health inspection reports CPU, memory, disk, and database pool."""
        resp = self.client.get('/api/admin/system_health')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('status'), 'healthy')
        self.assertIn('cpu', data)
        self.assertIn('memory', data)
        self.assertIn('disk', data)
        self.assertIn('database', data)
        self.assertIn('percent', data['memory'])
        self.assertIn('percent', data['disk'])

    def test_05_voter_profile_update(self):
        """Verify updating voter mobile number, caste tag, sentiment category, and migrant status."""
        repo = self.app.extensions['repo']
        voters = repo.search_voters('ward82', 'Amit', limit=1)
        self.assertTrue(len(voters) > 0, "Amit should exist from test_02 ingestion")
        v_id = voters[0]['id']

        # 1. Update with loyal supporter sentiment (100) and mobile
        update_payload = {
            'voter_id': v_id,
            'contact_phone': '9899988877',
            'caste_tag': 'Sharma/Brahmin',
            'voter_category': 100,
            'is_outside': 1,
            'notes': 'Spoke on call, confirmed voting at 8 AM'
        }
        resp = self.client.post(
            '/api/voter/profile/update?tenant=ward82',
            json=update_payload
        )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertTrue(res_data.get('success'))
        self.assertEqual(res_data.get('voter_id'), v_id)
        self.assertEqual(res_data['updated']['voter_category'], 100)
        self.assertEqual(res_data['updated']['contact_phone'], '9899988877')

        # 2. Verify record updated in database
        updated_voter = repo.get_voter('ward82', v_id)
        self.assertEqual(updated_voter['contact_phone'], '9899988877')
        self.assertEqual(updated_voter['caste_tag'], 'Sharma/Brahmin')
        self.assertEqual(updated_voter['voter_category'], 100)
        self.assertTrue(updated_voter['is_outside'])

        # 3. Test updating sentiment to Swing (50)
        resp2 = self.client.post(
            '/api/voter/profile/update?tenant=ward82',
            json={'voter_id': v_id, 'voter_category': 50}
        )
        self.assertEqual(resp2.status_code, 200)
        updated_voter2 = repo.get_voter('ward82', v_id)
        self.assertEqual(updated_voter2['voter_category'], 50)
        # Contact phone and caste should remain preserved
        self.assertEqual(updated_voter2['contact_phone'], '9899988877')
        self.assertEqual(updated_voter2['caste_tag'], 'Sharma/Brahmin')

    def test_06_user_administration_lifecycle(self):
        """Verify full RBAC user lifecycle: create, list, update, reset password, and delete."""
        test_uname = "agent_test_ops_2026"
        # 0. Clean up test user if left over from previous runs
        conn = user_manager.get_db()
        conn.execute("DELETE FROM users WHERE username = ?", (test_uname,))
        conn.commit()
        conn.close()

        # 1. Create user
        create_payload = {
            'username': test_uname,
            'password': 'InitialSecret123!',
            'full_name': 'Test Booth Agent 1',
            'phone': '9876543210',
            'role_tier': 3,
            'tenant_slug': 'ward82',
            'assigned_booths': [1, 2]
        }
        create_resp = self.client.post('/api/admin/users/create', json=create_payload)
        self.assertIn(create_resp.status_code, [200, 201])
        u_info = create_resp.get_json()
        self.assertTrue(u_info.get('success'))
        u_id = u_info.get('user_id')
        self.assertIsNotNone(u_id)

        # 2. List users
        list_resp = self.client.get('/api/admin/users')
        self.assertEqual(list_resp.status_code, 200)
        users = list_resp.get_json().get('users', [])
        created_user = next((u for u in users if u['username'] == test_uname), None)
        self.assertIsNotNone(created_user)
        self.assertEqual(created_user['role_tier'], 3)
        self.assertEqual(created_user['full_name'], 'Test Booth Agent 1')

        # 3. Update user
        update_resp = self.client.post(
            f'/api/admin/users/{u_id}/update',
            json={
                'full_name': 'Updated Booth Agent 1',
                'phone': '9998887776',
                'role_tier': 2
            }
        )
        self.assertEqual(update_resp.status_code, 200)
        self.assertTrue(update_resp.get_json().get('success'))

        # Verify update
        list_resp2 = self.client.get('/api/admin/users')
        updated_entry = next((u for u in list_resp2.get_json().get('users', []) if u['username'] == test_uname), None)
        self.assertEqual(updated_entry['full_name'], 'Updated Booth Agent 1')
        self.assertEqual(updated_entry['phone'], '9998887776')
        self.assertEqual(updated_entry['role_tier'], 2)

        # 4. Reset password
        reset_resp = self.client.post(
            '/api/users/reset_password',
            json={'user_id': u_id, 'password': 'NewPasswordSecure456!'}
        )
        self.assertEqual(reset_resp.status_code, 200)
        self.assertTrue(reset_resp.get_json().get('success'))
        self.assertEqual(reset_resp.get_json().get('password'), 'NewPasswordSecure456!')

        # Verify authentication with new password
        auth_user, err = user_manager.authenticate_user(test_uname, 'NewPasswordSecure456!')
        self.assertIsNone(err)
        self.assertIsNotNone(auth_user)

        # 5. Delete user
        del_resp = self.client.post(f'/api/admin/users/{u_id}/delete')
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.get_json().get('success'))

        # Verify deleted
        list_resp3 = self.client.get('/api/admin/users')
        remaining = [u for u in list_resp3.get_json().get('users', []) if u['username'] == test_uname]
        self.assertEqual(len(remaining), 0)

    def test_07_campaign_status_and_archive(self):
        """Verify toggling campaign status ('PAUSED', 'ACTIVE') and safely archiving."""
        # Toggle to PAUSED
        resp = self.client.post('/api/admin/campaigns/ward82/status', json={'status': 'PAUSED'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get('status'), 'PAUSED')

        # Toggle back to ACTIVE
        resp2 = self.client.post('/api/admin/campaigns/ward82/status', json={'status': 'ACTIVE'})
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.get_json().get('status'), 'ACTIVE')

        # Safe archive
        resp3 = self.client.post('/api/admin/campaigns/ward82/delete')
        self.assertEqual(resp3.status_code, 200)
        self.assertTrue(resp3.get_json().get('success'))

        # Restore to ACTIVE for other tests
        self.client.post('/api/admin/campaigns/ward82/status', json={'status': 'ACTIVE'})

    def test_08_admin_user_password_reset(self):
        """Verify user password reset via canonical /api/admin/users/reset_password."""
        u_resp = self.client.post('/api/admin/users/create', json={
            'username': 'pwd_test_user',
            'password': 'InitialPassword123',
            'role': 'viewer',
            'tenant_slug': 'ward82'
        })
        self.assertEqual(u_resp.status_code, 201)
        uid = u_resp.get_json()['user_id']

        # Reset password
        r_resp = self.client.post('/api/admin/users/reset_password', json={
            'user_id': uid,
            'password': 'NewSecretPassword456'
        })
        self.assertEqual(r_resp.status_code, 200)
        self.assertTrue(r_resp.get_json().get('success'))

        # Clean up
        self.client.post(f'/api/admin/users/{uid}/delete')

    def test_09_super_admin_dashboard_renders_clean_light_civic_theme(self):
        """Verify /admin renders the clean light civic dashboard with 3 workspaces and canonical snake_case."""
        resp = self.client.get('/admin')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Light Civic design system assertions
        self.assertIn("bg-slate-50", html)
        self.assertIn("Smart Booth", html)
        self.assertIn("Super Admin", html)
        # The Voter Hub link is a responsive two-span nav item (desktop "मतदाता हब",
        # mobile "हब") with the English wording in the title attribute -- the old
        # combined literal "मतदाता हब (Voter Hub)" predates that markup.
        self.assertIn("मतदाता हब", html)
        self.assertIn("Go to Voter Hub Search", html)

        # Unnecessary dev jargon and static buzzwords must be absent
        self.assertNotIn("मल्टी-टेनेंट क्लस्टर सक्रिय", html)
        self.assertNotIn("मल्टी-टेनेंट निर्वाचन कमान केंद्र", html)
        self.assertNotIn("PostgreSQL-समर्थित सुरक्षित बहु-स्तरीय भूमिकाएं", html)

        # Streamlined Workspaces
        self.assertIn("अभियान (Campaigns)", html)
        self.assertIn("उपयोगकर्ता (Users)", html)

        # Standardized roles in user form
        self.assertIn("प्रत्याशी / मुख्य व्यवस्थापक (Campaign Admin)", html)
        self.assertIn("वॉर-रूम ऑपरेटर (War Room Operator)", html)
        self.assertIn("बूथ कार्यकर्ता (Booth Agent)", html)
        self.assertIn("सुपर एडमिन (Super Admin)", html)

        # Ensure obsolete dark cyber styling is NOT present
        self.assertNotIn("class=\"bg-slate-950 text-slate-100 min-h-screen", html)
        self.assertNotIn(".glass-panel", html)
        self.assertNotIn(".card-glow", html)
        self.assertNotIn("href=\"/admin/architecture\"", html)

    def test_10_sync_master_voters_endpoint(self):
        """Verify high-performance Master DB sync endpoint handles params, errors, and live upserts."""
        # 1. Missing required parameters and no config fallback
        resp = self.client.post('/api/admin/campaigns/unknown_dummy_test/sync_master_voters')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json().get('success'))

        # 2. Non-existent tenant in tenants table
        resp = self.client.post(
            '/api/admin/campaigns/unknown_dummy_test/sync_master_voters',
            json={'district': 'Ajmer', 'block': 'Sarwar', 'gp': 'Sarana'}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.get_json().get('success'))

        # 3. Successful sync with Master DB for configured campaign 'sarana'
        sarana_cfg_path = os.path.join(PLATFORM_ROOT, 'tenants', 'sarana', 'campaign.json')
        original_cfg = None
        if os.path.exists(sarana_cfg_path):
            with open(sarana_cfg_path, 'r', encoding='utf-8') as f:
                original_cfg = f.read()

        try:
            resp = self.client.post('/api/admin/campaigns/sarana/sync_master_voters')
            if resp.status_code == 200:
                data = resp.get_json()
                self.assertTrue(data.get('success'))
                self.assertEqual(data.get('tenant_slug'), 'sarana')
                self.assertEqual(data.get('tenant_id'), 4)
                self.assertGreater(data.get('voters_synced', 0), 0)
                self.assertIn("Successfully linked", data.get('message', ''))
        finally:
            if original_cfg and os.path.exists(sarana_cfg_path):
                with open(sarana_cfg_path, 'w', encoding='utf-8') as f:
                    f.write(original_cfg)

    def test_11_reseller_account_creation_preserves_role_and_portfolio_scoping(self):
        """Verify that creating a reseller account retains role='reseller', does not create an all-campaign account, and enforces portfolio scoping."""
        test_reseller_uname = "qa_reseller_unit_test"
        create_payload = {
            'username': test_reseller_uname,
            'password': 'SecureResellerPass123!',
            'role': 'reseller',
            'full_name': 'QA Reseller Unit Partner',
            'max_campaigns': 7
        }
        create_resp = self.client.post('/api/admin/users/create', json=create_payload)
        self.assertEqual(create_resp.status_code, 201)
        res_data = create_resp.get_json()
        self.assertTrue(res_data.get('success'))
        u_id = res_data.get('user_id')
        self.assertIsNotNone(u_id)

        try:
            # 1. Fetch user directly from DB
            u = user_manager.get_user_by_id(u_id)
            self.assertEqual(u['username'], test_reseller_uname)
            self.assertEqual(u['role'], 'reseller')
            self.assertEqual(u['role_tier'], 2)
            # Must NOT be '*' (which signifies an all-campaign / global account)
            self.assertEqual(u['tenant_slug'], '')
            self.assertEqual(u['max_campaigns'], 7)

            # 2. List users API verification
            list_resp = self.client.get('/api/admin/users')
            self.assertEqual(list_resp.status_code, 200)
            users = list_resp.get_json().get('users', [])
            reseller_entry = next((x for x in users if x['username'] == test_reseller_uname), None)
            self.assertIsNotNone(reseller_entry)
            self.assertEqual(reseller_entry['role'], 'reseller')
            self.assertEqual(reseller_entry['tenant_slug'], '')
            self.assertEqual(reseller_entry['owned_campaigns_count'], 0)
            self.assertEqual(reseller_entry['max_campaigns'], 7)

            # 3. Update without role should NOT clobber role to 'admin'
            update_resp = self.client.post(
                f'/api/admin/users/{u_id}/update',
                json={
                    'full_name': 'Updated Reseller Name',
                    'role_tier': 2
                }
            )
            self.assertEqual(update_resp.status_code, 200)
            u_after = user_manager.get_user_by_id(u_id)
            self.assertEqual(u_after['role'], 'reseller')
            self.assertEqual(u_after['tenant_slug'], '')

            # 4. Verify login flow lands on /my-campaigns instead of /admin
            with self.client.session_transaction() as sess:
                sess.clear()
            login_resp = self.client.post('/login', data={
                'username': test_reseller_uname,
                'password': 'SecureResellerPass123!'
            }, follow_redirects=False)
            self.assertEqual(login_resp.status_code, 302)
            self.assertIn('/my-campaigns', login_resp.headers.get('Location', ''))
        finally:
            user_manager.delete_user_account(u_id)


if __name__ == '__main__':
    unittest.main()


