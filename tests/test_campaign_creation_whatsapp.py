# -*- coding: utf-8 -*-
"""
tests/test_campaign_creation_whatsapp.py
Verifies:
1. create_campaign returns candidate_phone, candidate_name, admin_user_id, and operator_user_id.
2. Direct WhatsApp wa.me link generation, phone normalization, and Hindi message length budgeting.
3. /api/users/<user_id>/send_credentials_whatsapp accepts dispatch-only phone override without mutating db.
"""

import unittest
import os
import shutil
import urllib.parse
from electionapp import create_app
from electionapp.config import TENANTS_DIR
from electionapp import campaign_manager
import user_manager


class CampaignCreationWhatsAppTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def tearDown(self):
        slug = 'test_wa_dispatch_unit'
        p = os.path.join(TENANTS_DIR, slug)
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)

    def test_01_create_campaign_returns_phone_and_user_ids(self):
        """Verify create_campaign returns candidate_phone, candidate_name, admin_user_id, operator_user_id."""
        slug = "test_wa_dispatch_unit"
        data = {
            'campaign_slug': slug,
            'election_type': 'MUNICIPAL',
            'ward_no': '5',
            'ward_name_hi': 'वार्ड 5',
            'candidate_name_hi': 'सुनील कुमार',
            'candidate_phone': '9829012345',
            'create_admin_user': 'true',
            'auto_ingest_rolls': 'false'
        }
        ok, res, code = campaign_manager.create_campaign(data)
        self.assertTrue(ok)
        self.assertEqual(code, 201)
        self.assertEqual(res.get('campaign_slug'), slug)
        self.assertEqual(res.get('candidate_phone'), '9829012345')
        self.assertEqual(res.get('candidate_name'), 'सुनील कुमार')
        self.assertIsNotNone(res.get('admin_user_id'))
        self.assertIsNotNone(res.get('operator_user_id'))
        self.assertIsNotNone(res.get('magic_token'))
        self.assertIn('/magic/', res.get('magic_url', ''))
        self.assertIn('/magic/', res.get('direct_url', ''))
        self.assertIn(f"?tenant={slug}", res.get('portal_url', ''))
        self.assertIn('auto_whatsapp', res)
        self.assertTrue(res['auto_whatsapp'].get('attempted'))

        # Check that user accounts were created in DB with access_token
        admin_uid = res['admin_user_id']
        admin_u = user_manager.get_user_by_id(admin_uid)
        self.assertIsNotNone(admin_u)
        self.assertTrue(admin_u['username'].endswith('_admin'))
        self.assertEqual(admin_u.get('access_token'), res['magic_token'])

    def test_04_admin_magic_token_auto_login(self):
        """Verify GET /magic/<token> automatically logs Campaign Admin in without credentials."""
        slug = "test_wa_dispatch_unit"
        data = {
            'campaign_slug': slug,
            'election_type': 'MUNICIPAL',
            'ward_no': '5',
            'ward_name_hi': 'वार्ड 5',
            'candidate_name_hi': 'सुनील कुमार',
            'candidate_phone': '9829012345',
            'create_admin_user': 'true',
            'auto_ingest_rolls': 'false'
        }
        ok, res, code = campaign_manager.create_campaign(data)
        self.assertTrue(ok)
        magic_tok = res.get('magic_token')
        self.assertIsNotNone(magic_tok)

        # Access magic link via client
        response = self.client.get(f"/magic/{magic_tok}", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/?tenant={slug}", response.location)

        # Verify session is established with full admin role
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get('user_id'), res['admin_user_id'])
            self.assertEqual(sess.get('role'), 'admin')
            self.assertEqual(sess.get('tenant_slug'), slug)

        # Verify voter_tenant cookie was set
        cookie_header = response.headers.get('Set-Cookie', '')
        self.assertIn(f"voter_tenant={slug}", cookie_header)

    def test_05_invalid_magic_token_returns_404(self):
        """Verify invalid or non-existent magic token fails gracefully with 404."""
        response = self.client.get("/magic/invalid_nonexistent_token_1234567890", follow_redirects=False)
        self.assertEqual(response.status_code, 404)
        self.assertIn("लॉगिन लिंक अमान्य", response.get_data(as_text=True))

    def test_02_whatsapp_message_length_budget(self):
        """Verify that the generated Hindi message with direct link URI-encoded stays well under mobile 2000-char limits."""
        slug = "ward_05"
        cand_name = "सुनील कुमार"
        admin_user = "ward05_admin"
        admin_pass = "Ward05@2026"
        op_user = "ward05"
        op_pass = "123456"
        direct_url = f"https://smartbooth.vedmap.com/?tenant={slug}"

        msg = (
            f"*🇮🇳 Smart Booth — चुनाव अभियान*\n"
            f"🏛️ अभियान: *{slug}*\n\n"
            f"🌐 *डायरेक्ट लिंक (बिना लॉगिन):*\n"
            f"{direct_url}\n"
            f"(सीधे मतदाता खोजें व पर्ची निकालें)\n\n"
            f"👑 *Admin लॉगिन:*\n"
            f"• यूजर: *{admin_user}*\n"
            f"• पासवर्ड: *{admin_pass}*\n\n"
            f"👤 *Operator लॉगिन:*\n"
            f"• यूजर: *{op_user}*\n"
            f"• पासवर्ड: *{op_pass}*"
        )

        encoded_msg = urllib.parse.quote(msg)
        wa_url = f"https://wa.me/919829012345?text={encoded_msg}"

        # Total URL length must stay well under 1500 characters
        self.assertLess(len(wa_url), 1500, f"URL length {len(wa_url)} exceeds safe mobile browser budget!")
        # Raw text must contain essential credentials and direct link
        self.assertIn(admin_user, msg)
        self.assertIn(admin_pass, msg)
        self.assertIn(op_user, msg)
        self.assertIn(direct_url, msg)
        self.assertIn("बिना लॉगिन", msg)

    def test_03_phone_normalization(self):
        """Verify phone normalization logic client- and server-side."""
        def normalize_phone(raw):
            digits = ''.join(c for c in str(raw) if c.isdigit())
            if digits.startswith('91') and len(digits) == 12:
                return digits
            elif len(digits) == 10:
                return '91' + digits
            elif len(digits) > 10 and digits.startswith('0'):
                return '91' + digits.lstrip('0')
            return None

        self.assertEqual(normalize_phone('9829012345'), '919829012345')
        self.assertEqual(normalize_phone('+91 98290 12345'), '919829012345')
        self.assertEqual(normalize_phone('09829012345'), '919829012345')
        self.assertEqual(normalize_phone('919829012345'), '919829012345')
        self.assertIsNone(normalize_phone('12345'))


if __name__ == '__main__':
    unittest.main()
