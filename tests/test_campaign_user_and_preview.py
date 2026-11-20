# -*- coding: utf-8 -*-
"""
tests/test_campaign_user_and_preview.py
Verifies:
1. /api/admin/transliterate endpoint (English -> Hindi and Hindi -> English).
2. Custom admin username and password persistence without manual reset.
3. Cross-tenant username collision handling.
4. Panchayat non-party campaign creation and subtitle localization.
5. Strict auto_ingest_rolls='false' enforcement.
"""

import unittest
import json
import os
import shutil
import re
from electionapp import create_app
from electionapp.config import TENANTS_DIR
from electionapp import campaign_manager
import user_manager

class CampaignUserAndPreviewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def tearDown(self):
        # Clean up any test campaigns created
        for slug in ['test_panchayat_unit', 'test_auth_unit', 'test_collision_unit']:
            p = os.path.join(TENANTS_DIR, slug)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

    def test_01_transliterate_endpoint(self):
        """Verify /api/admin/transliterate handles en_to_hi and hi_to_en cleanly."""
        # 1. English to Hindi personal name
        res = self.client.get('/api/admin/transliterate?text=Ramesh+Kumar+Sharma&direction=en_to_hi')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('रमेश', data.get('result', ''))

        # 2. Political party shortcut
        res_party = self.client.get('/api/admin/transliterate?text=BJP&direction=en_to_hi')
        self.assertEqual(res_party.status_code, 200)
        data_party = res_party.get_json()
        self.assertEqual(data_party.get('result'), 'भारतीय जनता पार्टी')

        # 3. Hindi to English transliteration
        res_hi = self.client.get('/api/admin/transliterate?text=सुरेश+बिश्नोई&direction=hi_to_en')
        self.assertEqual(res_hi.status_code, 200)
        data_hi = res_hi.get_json()
        self.assertTrue(data_hi.get('success'))
        self.assertIn('Suresh', data_hi.get('result', ''))

    def test_02_campaign_creation_with_admin_password(self):
        """Verify custom admin user and password can authenticate immediately without reset."""
        slug = "test_auth_unit"
        custom_user = "auth_unit_admin"
        custom_pass = "MySecretPass@2026"

        payload = {
            'campaign_slug': slug,
            'election_type': 'MUNICIPAL',
            'ward_no': '99',
            'ward_name_hi': 'वार्ड 99',
            'candidate_name_hi': 'अमित कुमार',
            'candidate_name_en': 'Amit Kumar',
            'admin_username': custom_user,
            'admin_password': custom_pass,
            'auto_ingest_rolls': 'false'
        }

        success, result, status_code = campaign_manager.create_campaign(payload)
        self.assertTrue(success)
        self.assertEqual(status_code, 201)
        self.assertEqual(result.get('admin_username'), custom_user)
        self.assertEqual(result.get('admin_password'), custom_pass)
        self.assertEqual(result.get('voters_imported'), 0)

        # Authenticate immediately with the custom password
        auth_user, auth_err = user_manager.authenticate_user(custom_user, custom_pass)
        self.assertIsNotNone(auth_user, f"Authentication failed: {auth_err}")
        self.assertEqual(auth_user['role'], 'admin')

    def test_03_cross_tenant_username_collision(self):
        """Verify username collision across different campaigns returns clear error."""
        # 1. Create first user under campaign A
        u1_ok, u1_msg, u1_id = user_manager.create_user_account(
            username="global_unique_test",
            password="PassOne@2026",
            full_name="User One",
            tenant_slug="test_auth_unit",
            overwrite_password=True
        )
        self.assertTrue(u1_ok)

        # 2. Attempt to create same username under a different tenant
        u2_ok, u2_msg, u2_id = user_manager.create_user_account(
            username="global_unique_test",
            password="PassTwo@2026",
            full_name="User Two",
            tenant_slug="sanchore",
            overwrite_password=True
        )
        self.assertFalse(u2_ok)
        self.assertIn("पहले से किसी अन्य अभियान", u2_msg)

    def test_04_panchayat_campaign_creation_and_subtitle(self):
        """Verify Panchayat / Sarpanch campaign sets localized subtitle and runs without party."""
        slug = "test_panchayat_unit"
        payload = {
            'campaign_slug': slug,
            'election_type': 'PANCHAYAT',
            'ward_no': '',
            'ward_name_hi': 'ग्राम पंचायत दीगोद',
            'candidate_name_hi': 'रमेश पटेल',
            'candidate_name_en': 'Ramesh Patel',
            'auto_ingest_rolls': 'false'
        }

        success, result, status_code = campaign_manager.create_campaign(payload)
        self.assertTrue(success)
        self.assertEqual(status_code, 201)

        cfg_path = os.path.join(TENANTS_DIR, slug, 'campaign.json')
        self.assertTrue(os.path.exists(cfg_path))
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)

        self.assertEqual(cfg['election']['election_type'], 'PANCHAYAT')
        self.assertEqual(cfg['election']['portal_subtitle'], 'पंचायती राज (सरपंच) चुनाव 2026 — मतदाता सेवा व वॉर-रूम')

    def test_05_auto_ingest_rolls_flag(self):
        """Verify that sending auto_ingest_rolls='false' prevents automatic voter import."""
        slug = "test_collision_unit"
        payload = {
            'campaign_slug': slug,
            'election_type': 'PANCHAYAT',
            'ward_name_hi': 'दीगोद',
            'candidate_name_hi': 'सुरेश कुमार',
            'rajasthan_district': 'KOTA',
            'rajasthan_gp': 'DIGOD',
            'auto_ingest_rolls': 'false'
        }

        success, result, status_code = campaign_manager.create_campaign(payload)
        self.assertTrue(success)
        self.assertEqual(result.get('voters_imported'), 0)


if __name__ == '__main__':
    unittest.main()
