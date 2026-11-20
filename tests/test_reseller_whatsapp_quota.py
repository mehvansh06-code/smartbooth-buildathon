"""
Tests for Reseller WhatsApp Quota & Wallet Management.

Validates:
1. Super Admin setting reseller's whatsapp_credits.
2. user_manager atomic deduction and refund of reseller whatsapp credits.
3. Reseller creating a campaign with credits deducting from their balance.
4. Reseller creating a campaign with credits exceeding wallet balance (rejected with 400).
5. Reseller topping up an existing campaign via POST /api/admin/campaigns/<slug>/whatsapp_quota (deducts from wallet).
6. Reseller blocked from using 'set', 'block', 'unblock' actions.
"""

import os
import sys
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

import user_manager
from electionapp import create_app, whatsapp_quota


def postgres_available():
    try:
        pool = whatsapp_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        finally:
            pool.putconn(conn)
    except Exception:
        return False


class TestResellerWhatsAppQuota(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not postgres_available():
            raise unittest.SkipTest("PostgreSQL not available for reseller quota tests")
        user_manager.ensure_schema()
        whatsapp_quota.ensure_quota_schema()

    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        # Clean up test users and test campaigns in DB
        with user_manager.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE tenant_id IN (SELECT tenant_id FROM tenants WHERE campaign_slug LIKE 'test_wa_camp%') OR tenant_slug LIKE 'test_wa_camp%'")
                cur.execute("DELETE FROM users WHERE username IN ('test_reseller_wa', 'test_reseller_wa2')")
                cur.execute("DELETE FROM whatsapp_quota WHERE tenant_id IN (SELECT tenant_id FROM tenants WHERE campaign_slug LIKE 'test_wa_camp%')")
                cur.execute("DELETE FROM tenants WHERE campaign_slug LIKE 'test_wa_camp%'")
            conn.commit()

        # Clean up test tenant directories on disk
        import shutil
        from electionapp.campaign_manager import TENANTS_DIR
        for slug in ('test_wa_camp_1', 'test_wa_camp_fail', 'test_wa_camp_topup', 'test_wa_camp_block'):
            p = os.path.join(TENANTS_DIR, slug)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

        # Create test reseller with 500 WhatsApp credits
        ok, msg, uid = user_manager.create_user_account(
            username='test_reseller_wa',
            password='Password123',
            role='reseller',
            full_name='Test WA Reseller',
            whatsapp_credits=500,
            max_campaigns=5
        )
        self.assertTrue(ok)
        self.reseller_id = uid

    def tearDown(self):
        with user_manager.get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE tenant_id IN (SELECT tenant_id FROM tenants WHERE campaign_slug LIKE 'test_wa_camp%') OR tenant_slug LIKE 'test_wa_camp%'")
                cur.execute("DELETE FROM users WHERE username IN ('test_reseller_wa', 'test_reseller_wa2')")
                cur.execute("DELETE FROM whatsapp_quota WHERE tenant_id IN (SELECT tenant_id FROM tenants WHERE campaign_slug LIKE 'test_wa_camp%')")
                cur.execute("DELETE FROM tenants WHERE campaign_slug LIKE 'test_wa_camp%'")
            conn.commit()

        import shutil
        from electionapp.campaign_manager import TENANTS_DIR
        for slug in ('test_wa_camp_1', 'test_wa_camp_fail', 'test_wa_camp_topup', 'test_wa_camp_block'):
            p = os.path.join(TENANTS_DIR, slug)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

    def test_user_manager_deduct_and_refund(self):
        """Test atomic wallet deduction and refund logic."""
        credits = user_manager.get_reseller_whatsapp_credits(self.reseller_id)
        self.assertEqual(credits, 500)

        # Deduct 200 -> balance 300
        ok = user_manager.deduct_reseller_whatsapp_credits(self.reseller_id, 200)
        self.assertTrue(ok)
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 300)

        # Try to deduct 400 when only 300 left -> should fail
        ok2 = user_manager.deduct_reseller_whatsapp_credits(self.reseller_id, 400)
        self.assertFalse(ok2)
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 300)

        # Refund 100 -> balance 400
        user_manager.refund_reseller_whatsapp_credits(self.reseller_id, 100)
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 400)

    def test_super_admin_can_update_reseller_credits(self):
        """Super Admin can top-up or set reseller's whatsapp_credits."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        res = self.client.post(f'/api/admin/users/{self.reseller_id}/update', json={
            'whatsapp_credits': 1200
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 1200)

    def test_reseller_create_campaign_deducts_wallet(self):
        """Reseller creating a campaign with 150 credits deducts from their wallet."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.reseller_id
            sess['username'] = 'test_reseller_wa'
            sess['role'] = 'reseller'

        res = self.client.post('/api/admin/campaigns/create', json={
            'slug': 'test_wa_camp_1',
            'candidate_name_hi': 'टेस्ट प्रत्याशी',
            'candidate_name_en': 'Test Candidate',
            'whatsapp_credits': 150
        })
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data['success'])

        # Check reseller wallet balance reduced from 500 to 350
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 350)

        # Check campaign quota received 150
        quota = whatsapp_quota.get_quota('test_wa_camp_1')
        self.assertEqual(quota['granted'], 150)
        self.assertEqual(quota['available'], 150)

    def test_reseller_create_campaign_insufficient_wallet(self):
        """Reseller attempting to create a campaign with more credits than wallet balance fails."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.reseller_id
            sess['username'] = 'test_reseller_wa'
            sess['role'] = 'reseller'

        res = self.client.post('/api/admin/campaigns/create', json={
            'slug': 'test_wa_camp_fail',
            'candidate_name_hi': 'टेस्ट प्रत्याशी',
            'candidate_name_en': 'Test Candidate',
            'whatsapp_credits': 600  # only has 500
        })
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data['success'])
        self.assertIn('अपर्याप्त', data.get('error', ''))

        # Balance remains 500
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 500)

    def test_reseller_topup_campaign_quota(self):
        """Reseller can top-up quota on their owned campaign via grant action."""
        # First create campaign with 100
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.reseller_id
            sess['username'] = 'test_reseller_wa'
            sess['role'] = 'reseller'

        create_res = self.client.post('/api/admin/campaigns/create', json={
            'slug': 'test_wa_camp_topup',
            'candidate_name_hi': 'टेस्ट प्रत्याशी',
            'candidate_name_en': 'Test Candidate',
            'whatsapp_credits': 100
        })
        self.assertEqual(create_res.status_code, 201)
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 400)

        # Reseller tops up campaign by granting 150
        res = self.client.post('/api/admin/campaigns/test_wa_camp_topup/whatsapp_quota', json={
            'action': 'grant',
            'amount': 150,
            'note': 'Reseller top-up'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['reseller_credits'], 250)
        self.assertEqual(data['quota']['granted'], 250)

        # Balance in wallet is now 400 - 150 = 250
        self.assertEqual(user_manager.get_reseller_whatsapp_credits(self.reseller_id), 250)

    def test_reseller_forbidden_actions(self):
        """Reseller cannot use 'set', 'block', or 'unblock' actions on whatsapp quota."""
        # Create campaign first
        with self.client.session_transaction() as sess:
            sess['user_id'] = self.reseller_id
            sess['username'] = 'test_reseller_wa'
            sess['role'] = 'reseller'

        self.client.post('/api/admin/campaigns/create', json={
            'slug': 'test_wa_camp_block',
            'candidate_name_hi': 'टेस्ट प्रत्याशी',
            'whatsapp_credits': 50
        })

        # Try to block -> 403
        res_block = self.client.post('/api/admin/campaigns/test_wa_camp_block/whatsapp_quota', json={
            'action': 'block'
        })
        self.assertEqual(res_block.status_code, 403)

        # Try to set -> 403
        res_set = self.client.post('/api/admin/campaigns/test_wa_camp_block/whatsapp_quota', json={
            'action': 'set',
            'amount': 500
        })
        self.assertEqual(res_set.status_code, 403)


if __name__ == '__main__':
    unittest.main()
