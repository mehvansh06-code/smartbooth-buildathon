# -*- coding: utf-8 -*-
"""
tests/test_sarpanch_campaign_backend.py
Tests specifically verifying Sarpanch / Panchayat campaign rules:
1. tenant_schema.json relaxation (party optional, no required name_hi/name_en minLength).
2. config.py validate_tenant_config() relaxation for party and optional ballot/symbol fields.
3. campaign_manager.py create_campaign() for Panchayat, Municipal, and Assembly defaults.
4. tenant_branding.py cand_title and party_line permutations.
5. card_templates.py header rendering with photo when symbol is pending, and ballot tag omission when empty.
6. admin.py /campaign/new and /admin/campaign/new route registration.
"""

import unittest
import os
import json
import shutil
from electionapp.config import validate_tenant_config, PLATFORM_ROOT, TENANTS_DIR, get_voting_method
from electionapp import campaign_manager, tenant_branding, card_templates
from electionapp import create_app


class SarpanchCampaignBackendTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def tearDown(self):
        for slug in ['test_sarpanch_unit', 'test_assembly_unit', 'test_municipal_unit', 'test_preset_symbol_unit', 'test_ballot_paper_unit', 'test_ballot_branding_unit', 'test_ballot_slip_unit']:
            p = os.path.join(TENANTS_DIR, slug)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

    def test_01_tenant_schema_json(self):
        """Verify tenant_schema.json allows configs without party."""
        schema_path = os.path.join(PLATFORM_ROOT, 'electionapp', 'tenant_schema.json')
        with open(schema_path, 'r', encoding='utf-8') as f:
            schema = json.load(f)
        self.assertNotIn("party", schema.get("required", []))
        party_props = schema.get("properties", {}).get("party", {})
        self.assertNotIn("required", party_props)

    def test_02_validate_tenant_config_relaxed(self):
        """Verify validate_tenant_config allows missing party, empty party fields, and empty ballot/symbol."""
        cfg_no_party = {
            "candidate": {"name_hi": "रामनिवास", "name_en": "Ramniwas"},
            "election": {"ward_name_hi": "ग्राम पंचायत", "ward_name_en": "GP", "app_title": "GP App"}
        }
        # Should not raise
        validate_tenant_config(cfg_no_party, "test_slug")

        cfg_empty_party = {
            "candidate": {"name_hi": "रामनिवास", "name_en": "Ramniwas"},
            "election": {"ward_name_hi": "ग्राम पंचायत", "ward_name_en": "GP", "app_title": "GP App"},
            "party": {
                "name_hi": "",
                "name_en": "",
                "symbol_name_hi": "",
                "symbol_name_en": "",
                "symbol_filename": "",
                "ballot_serial": ""
            }
        }
        # Should not raise
        validate_tenant_config(cfg_empty_party, "test_slug")

    def test_03_create_campaign_panchayat_defaults(self):
        """Verify create_campaign sets Sarpanch defaults, auto-computes ward_no, and campaign_msg."""
        slug = "test_sarpanch_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "सोहन लाल",
            "candidate_name_en": "Sohan Lal",
            "rajasthan_district": "JAIPUR",
            "rajasthan_gp": "KALYANPURA",
            "auto_ingest_rolls": "false"
        }
        success, result, status = campaign_manager.create_campaign(data)
        self.assertTrue(success)
        self.assertEqual(status, 201)

        cfg_path = os.path.join(TENANTS_DIR, slug, "campaign.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.assertEqual(cfg["party"]["name_hi"], "")
        self.assertEqual(cfg["party"]["name_en"], "")
        self.assertEqual(cfg["party"]["ballot_serial"], "")
        self.assertEqual(cfg["party"]["symbol_name_hi"], "")
        # ward_no auto-computed to f"1-{ward_count}" (1-6 for Kalyanpura or 1-9)
        self.assertTrue(cfg["election"]["ward_no"].startswith("1-"))
        self.assertEqual(cfg["election"]["ward_name_hi"], "ग्राम पंचायत KALYANPURA")
        # Default campaign message
        expected_msg = "ग्राम पंचायत KALYANPURA से सरपंच पद हेतु प्रत्याशी सोहन लाल को अपना अमूल्य मत देकर भारी मतों से विजयी बनाएं।"
        self.assertEqual(cfg["campaign_message"], expected_msg)

    def test_04_create_campaign_municipal_and_assembly_messages(self):
        """Verify default messages for Municipal and Assembly elections."""
        # Municipal
        m_slug = "test_municipal_unit"
        m_data = {
            "campaign_slug": m_slug,
            "election_type": "MUNICIPAL",
            "ward_no": "12",
            "ward_name_hi": "आदर्श नगर",
            "candidate_name_hi": "अमित शर्मा",
            "auto_ingest_rolls": "false"
        }
        s1, _, _ = campaign_manager.create_campaign(m_data)
        self.assertTrue(s1)
        with open(os.path.join(TENANTS_DIR, m_slug, "campaign.json"), "r", encoding="utf-8") as f:
            m_cfg = json.load(f)
        self.assertIn("वार्ड संख्या 12 (आदर्श नगर) से पार्षद पद हेतु प्रत्याशी अमित शर्मा", m_cfg["campaign_message"])

        # Assembly
        a_slug = "test_assembly_unit"
        a_data = {
            "campaign_slug": a_slug,
            "election_type": "ASSEMBLY",
            "ward_name_hi": "हवामहल",
            "party_name_hi": "भाजपा",
            "candidate_name_hi": "बालमुकुंद",
            "auto_ingest_rolls": "false"
        }
        s2, _, _ = campaign_manager.create_campaign(a_data)
        self.assertTrue(s2)
        with open(os.path.join(TENANTS_DIR, a_slug, "campaign.json"), "r", encoding="utf-8") as f:
            a_cfg = json.load(f)
        self.assertIn("हवामहल विधान सभा क्षेत्र से भाजपा प्रत्याशी बालमुकुंद", a_cfg["campaign_message"])

    def test_05_tenant_branding_cand_title_and_party_line(self):
        """Verify role title and party_line permutations in tenant_branding."""
        # Test Panchayat campaign branding
        slug = "test_sarpanch_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "सोहन लाल",
            "candidate_name_en": "Sohan Lal",
            "auto_ingest_rolls": "false"
        }
        campaign_manager.create_campaign(data)
        branding = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(branding.get("cand_title"), "सरपंच प्रत्याशी")
        self.assertEqual(branding.get("party_line"), "सरपंच प्रत्याशी")

        # Test with ballot_no and symbol
        cfg_path = os.path.join(TENANTS_DIR, slug, "campaign.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["party"]["ballot_serial"] = "4"
        cfg["party"]["symbol_name_hi"] = "ट्रैक्टर"
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        tenant_branding.invalidate_tenant_branding(slug)

        b2 = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(b2.get("party_line"), "सरपंच प्रत्याशी (क्रम संख्या 4 पर ट्रैक्टर का निशान)")

    def test_06_card_templates_render_cleanly_without_symbol(self):
        """Verify single and family slips render candidate photo when symbol is pending and omit ballot tag if ballot_no empty."""
        voter = {
            "part_no": 1,
            "sl_no": 42,
            "name_hi": "मोहन सिंह",
            "name_en": "Mohan Singh",
            "rel_name_hi": "राम सिंह",
            "rel_name_en": "Ram Singh",
            "gender": "पुरुष",
            "age": 35,
            "epic_no": "XYZ1234567",
            "house_no": "101"
        }
        booth = {"name": "प्राथमिक विद्यालय", "area": "वार्ड 1"}
        branding = {
            "tenant_slug": "test_slug",
            "candidate_name_hi": "सोहन लाल",
            "candidate_name_en": "Sohan Lal",
            "candidate_disp": "सोहन लाल",
            "candidate_src": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            "has_candidate": True,
            "party_name_hi": "",
            "party_name_en": "",
            "party_line": "सरपंच प्रत्याशी",
            "symbol_name_hi": "",
            "symbol_name_en": "",
            "symbol_file": None,
            "lotus_src": "",
            "has_symbol": False,
            "ballot_no": "",
            "has_ballot": False,
            "slogan": "मतदान हमारा अधिकार है",
            "theme_color": "#2563eb",
            "place_hi": "ग्राम पंचायत कल्याणपुरा",
            "ward_name_hi": "ग्राम पंचायत कल्याणपुरा",
            "ward_no": "1-9",
            "constituency": "",
            "district": "जयपुर",
            "state": "Rajasthan",
            "election_type": "PANCHAYAT",
            "campaign_message": "मतदान अवश्य करें",
            "appeal_msg": "मतदान अवश्य करें",
            "booths": {},
            "get_booth": lambda p: booth,
            "custom_bg": None
        }

        # Single card HTML
        single_html = card_templates.build_standard_single_html(voter, booth, branding)
        self.assertIn('class="candidate-badge"', single_html)
        self.assertIn('src="data:image/png;base64,', single_html)
        self.assertNotIn('class="ballot-tag"', single_html)

        # Family card HTML
        family_html = card_templates.build_standard_family_html([voter], branding, 1, 1)
        self.assertIn('class="candidate-badge"', family_html)
        self.assertIn('src="data:image/png;base64,', family_html)
        self.assertNotIn('class="ballot-tag"', family_html)

    def test_07_admin_campaign_new_routes_registered(self):
        """Verify @admin_bp routes /campaign/new and /admin/campaign/new exist."""
        rules = [r.rule for r in self.app.url_map.iter_rules()]
        self.assertIn('/campaign/new', rules)
        self.assertIn('/admin/campaign/new', rules)

    def test_08_create_campaign_with_preset_symbol_button(self):
        """Verify creating a campaign with preset_symbol (e.g. tractor) and empty file input succeeds and resolves symbol."""
        slug = "test_preset_symbol_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "नेनु देवी",
            "candidate_name_en": "Nenu Devi",
            "rajasthan_district": "JALORE",
            "rajasthan_gp": "CHHOTI_VIROL",
            "preset_symbol": "tractor",
            "symbol_name_hi": "ट्रैक्टर",
            "auto_ingest_rolls": "false"
        }
        # Simulate browser submitting empty file field
        class EmptyFileStorage:
            filename = ''
        files = {
            'party_symbol': EmptyFileStorage(),
            'candidate_photo': EmptyFileStorage()
        }

        success, result, status = campaign_manager.create_campaign(data, files=files)
        self.assertTrue(success)
        self.assertEqual(status, 201)

        cfg_path = os.path.join(TENANTS_DIR, slug, "campaign.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.assertEqual(cfg["party"]["symbol_filename"], "symbol.png")
        self.assertEqual(cfg["party"]["symbol_name_hi"], "ट्रैक्टर")

        # Verify symbol.png physically exists in tenant assets
        asset_symbol = os.path.join(TENANTS_DIR, slug, "assets", "symbol.png")
        self.assertTrue(os.path.exists(asset_symbol), "symbol.png must exist in tenant assets")
        self.assertGreater(os.path.getsize(asset_symbol), 1000)

        # Verify get_campaign_details returns correct symbol_url
        d_ok, d_res, d_status = campaign_manager.get_campaign_details(slug)
        self.assertTrue(d_ok)
        self.assertEqual(d_res.get('symbol_url'), f"/tenant_assets/symbol.png?tenant={slug}")

        # Verify tenant_branding resolves symbol
        branding = tenant_branding.get_tenant_branding(slug)
        self.assertTrue(branding['has_symbol'])
        self.assertTrue(branding['lotus_src'].startswith('data:image/png;base64,'))

    def test_09_update_campaign_with_preset_symbol(self):
        """Verify updating an existing campaign to a new preset symbol (e.g. handpump) succeeds."""
        slug = "test_preset_symbol_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "नेनु देवी",
            "candidate_name_en": "Nenu Devi",
            "rajasthan_district": "JALORE",
            "rajasthan_gp": "CHHOTI_VIROL",
            "preset_symbol": "tractor",
            "symbol_name_hi": "ट्रैक्टर",
            "auto_ingest_rolls": "false"
        }
        campaign_manager.create_campaign(data)

        # Now update to handpump
        update_data = {
            "preset_symbol": "handpump",
            "symbol_name_hi": "हैंडपंप"
        }
        class EmptyFileStorage:
            filename = ''
        files = {'party_symbol': EmptyFileStorage()}

        u_ok, u_res, u_status = campaign_manager.update_campaign(slug, update_data, files=files)
        self.assertTrue(u_ok)
        self.assertIn("Party Symbol (Preset)", u_res.get('updated_items', []))

        cfg_path = os.path.join(TENANTS_DIR, slug, "campaign.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["party"]["symbol_filename"], "symbol.png")
        self.assertEqual(cfg["party"]["symbol_name_hi"], "हैंडपंप")

    def test_10_get_voting_method(self):
        """Verify get_voting_method resolves PRI and EVM types, custom overrides, and heuristics."""
        pri_types = ['PANCHAYAT', 'GRAM_PANCHAYAT', 'SARPANCH', 'WARD_PANCH', 'PANCHAYAT_SAMITI', 'PS', 'ZILA_PARISHAD', 'ZP']
        for pt in pri_types:
            self.assertEqual(get_voting_method(pt), 'BALLOT_PAPER')
            self.assertEqual(get_voting_method(pt.lower()), 'BALLOT_PAPER')

        evm_types = ['ASSEMBLY', 'VIDHAN_SABHA', 'PARLIAMENTARY', 'LOK_SABHA', 'MUNICIPAL', 'NAGAR_PALIKA', 'NAGAR_PARISHAD', 'NAGAR_NIGAM']
        for et in evm_types:
            self.assertEqual(get_voting_method(et), 'EVM')
            self.assertEqual(get_voting_method(et.lower()), 'EVM')

        # Custom overrides take precedence
        self.assertEqual(get_voting_method('PANCHAYAT', custom_override='EVM'), 'EVM')
        self.assertEqual(get_voting_method('ASSEMBLY', custom_override='BALLOT_PAPER'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('MUNICIPAL', custom_override='  ballot_paper  '), 'BALLOT_PAPER')

        # Heuristics & defaults
        self.assertEqual(get_voting_method('RAJASTHAN_PANCHAYAT_2026'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('DELHI_MUNICIPAL_CORP'), 'EVM')
        self.assertEqual(get_voting_method('UNKNOWN_BODY'), 'BALLOT_PAPER')

    def test_11_validate_tenant_config_voting_method_and_ballot_color(self):
        """Verify validate_tenant_config accepts optional ballot_color and voting_method strings."""
        cfg_valid = {
            "candidate": {"name_hi": "रामनिवास", "name_en": "Ramniwas"},
            "election": {
                "ward_name_hi": "ग्राम पंचायत",
                "ward_name_en": "GP",
                "app_title": "GP App",
                "voting_method": "BALLOT_PAPER",
                "ballot_color": "white"
            },
            "party": {
                "name_hi": "",
                "name_en": "",
                "ballot_color": "white",
                "voting_method": "BALLOT_PAPER"
            }
        }
        # Should succeed without error
        validate_tenant_config(cfg_valid, "test_slug")

        # Non-string voting_method should fail validation
        cfg_bad = {
            "candidate": {"name_hi": "रामनिवास", "name_en": "Ramniwas"},
            "election": {
                "ward_name_hi": "ग्राम पंचायत",
                "ward_name_en": "GP",
                "app_title": "GP App",
                "voting_method": 123
            }
        }
        with self.assertRaises(ValueError):
            validate_tenant_config(cfg_bad, "test_slug")

    def test_12_create_and_update_campaign_ballot_attributes_and_message(self):
        """Verify create_campaign and update_campaign store ballot attributes and generate PRI message with official stamp wording."""
        slug = "test_ballot_paper_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "सुरेश कुमार",
            "candidate_name_en": "Suresh Kumar",
            "rajasthan_gp": "रामपुरा",
            "ballot_serial": "3",
            "ballot_color": "white",
            "voting_method": "BALLOT_PAPER",
            "auto_ingest_rolls": "false"
        }
        success, result, status = campaign_manager.create_campaign(data)
        self.assertTrue(success)
        self.assertEqual(status, 201)

        cfg_path = os.path.join(TENANTS_DIR, slug, "campaign.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.assertEqual(cfg["election"]["voting_method"], "BALLOT_PAPER")
        self.assertEqual(cfg["party"]["ballot_color"], "white")
        # Default campaign message must contain stamp instruction
        self.assertIn("आधिकारिक मुहर लगाकर", cfg["campaign_message"])
        self.assertIn("क्रम संख्या 3", cfg["campaign_message"])

        # Test updating ballot_color and voting_method
        update_data = {
            "ballot_color": "yellow",
            "voting_method": "EVM"
        }
        u_ok, u_res, u_status = campaign_manager.update_campaign(slug, update_data)
        self.assertTrue(u_ok)
        self.assertIn("Ballot Color", u_res.get('updated_items', []))
        self.assertIn("Voting Method", u_res.get('updated_items', []))

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg2 = json.load(f)
        self.assertEqual(cfg2["party"]["ballot_color"], "yellow")
        self.assertEqual(cfg2["election"]["voting_method"], "EVM")

    def test_13_tenant_branding_ballot_attributes(self):
        """Verify tenant_branding exposes voting_method, is_ballot_election, and ballot_color."""
        slug = "test_ballot_branding_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "महेन्द्र सिंह",
            "candidate_name_en": "Mahendra Singh",
            "ballot_serial": "5",
            "symbol_name_hi": "कार",
            "ballot_color": "green",
            "voting_method": "BALLOT_PAPER",
            "auto_ingest_rolls": "false"
        }
        campaign_manager.create_campaign(data)
        tenant_branding.invalidate_tenant_branding(slug)

        branding = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(branding.get("voting_method"), "BALLOT_PAPER")
        self.assertTrue(branding.get("is_ballot_election"))
        self.assertEqual(branding.get("ballot_color"), "green")
        self.assertEqual(branding.get("party_line"), "सरपंच प्रत्याशी (क्रम संख्या 5 पर कार का निशान)")

    def test_14_api_slip_text_ballot_instructions(self):
        """Verify api_slip_text appends ballot voting guidance footer when is_ballot_election is true, and omits it for EVM."""
        slug = "test_ballot_slip_unit"
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "candidate_name_hi": "महेन्द्र सिंह",
            "candidate_name_en": "Mahendra Singh",
            "auto_ingest_rolls": "false"
        }
        campaign_manager.create_campaign(data)

        # Mock resolve_tenant and repo.get_voter to test /api/slip_text
        from unittest.mock import patch, MagicMock
        mock_repo = MagicMock()
        mock_voter = {
            'id': 101,
            'name_hi': 'सुनील शर्मा',
            'name_en': 'Sunil Sharma',
            'rel_name_hi': 'राम अवतार',
            'rel_name_en': 'Ram Avtar',
            'rel_type': 'पिता',
            'part_no': 1,
            'sl_no': 25,
            'house_no': '42',
            'gender': 'पुरुष',
            'age': 30,
            'epic_no': 'ABC1234567',
            'booth_name': 'राजकीय उच्च प्राथमिक विद्यालय',
            'is_deleted': 0
        }
        mock_repo.get_voter.return_value = mock_voter
        mock_repo.get_booths_dict.return_value = {'1': {'name': 'राजकीय उच्च प्राथमिक विद्यालय'}}

        with patch('electionapp.blueprints.slip._get_tenant', return_value=(slug, mock_repo)):
            res = self.client.get('/api/slip_text?id=101')
            self.assertEqual(res.status_code, 200)
            slip_text = res.get_json().get('slip_text', '')
            self.assertIn("💡 *मतदान निर्देश:*", slip_text)
            self.assertIn("मतदान केंद्र पर दी गई मुहर (ठप्पा) का उपयोग करें", slip_text)
            self.assertIn("मतपत्र को पहले खड़े में मोड़ें", slip_text)

        # Verify EVM campaign slip does NOT contain ballot instructions
        with patch('electionapp.blueprints.slip._get_tenant', return_value=('ward82', mock_repo)):
            res_evm = self.client.get('/api/slip_text?id=101')
            self.assertEqual(res_evm.status_code, 200)
            slip_text_evm = res_evm.get_json().get('slip_text', '')
            self.assertNotIn("मुहर (ठप्पा)", slip_text_evm)

    def test_15_card_templates_ballot_and_evm_banner_and_footer(self):
        """Verify dynamic banner title, ballot color pill, and voter guidance note in single & family cards."""
        voter = {
            "part_no": 1,
            "sl_no": 10,
            "name_hi": "सुरेश कुमार",
            "name_en": "Suresh Kumar",
            "rel_name_hi": "मोहन लाल",
            "rel_name_en": "Mohan Lal",
            "gender": "पुरुष",
            "age": 40,
            "epic_no": "ABC1234567",
            "house_no": "12"
        }
        booth = {"name": "राजकीय विद्यालय", "area": "वार्ड 1"}

        # 1. Ballot Paper Election with color
        branding_ballot = {
            "tenant_slug": "test_slug",
            "candidate_name_hi": "सुरेश कुमार",
            "candidate_name_en": "Suresh Kumar",
            "candidate_disp": "सुरेश कुमार",
            "candidate_src": "data:image/png;base64,abc",
            "has_candidate": True,
            "party_name_hi": "",
            "party_name_en": "",
            "party_line": "सरपंच प्रत्याशी",
            "symbol_name_hi": "हैंडपंप",
            "symbol_name_en": "Handpump",
            "symbol_file": "symbol.png",
            "lotus_src": "data:image/png;base64,def",
            "has_symbol": True,
            "ballot_no": "3",
            "has_ballot": True,
            "voting_method": "BALLOT_PAPER",
            "is_ballot_election": True,
            "ballot_color": "गुलाबी",
            "slogan": "विकास की राह पर",
            "theme_color": "#15803d",
            "place_hi": "ग्राम पंचायत रामपुरा",
            "ward_name_hi": "ग्राम पंचायत रामपुरा",
            "ward_no": "1-9",
            "district": "जयपुर",
            "state": "Rajasthan",
            "appeal_msg": "आधिकारिक मुहर लगाकर विजयी बनाएं",
            "booths": {},
            "get_booth": lambda p: booth,
            "custom_bg": None
        }

        single_ballot = card_templates.build_standard_single_html(voter, booth, branding_ballot)
        self.assertIn("मतपत्र क्रम संख्या: 3", single_ballot)
        self.assertNotIn("ईवीएम क्रम संख्या", single_ballot)
        self.assertIn("मतपत्र रंग: गुलाबी", single_ballot)
        self.assertIn("ballot-color-pill", single_ballot)
        self.assertIn("मतदान केंद्र पर दी गई मुहर का उपयोग करें · मतपत्र को पहले खड़े में मोड़ें", single_ballot)

        family_ballot = card_templates.build_standard_family_html([voter], branding_ballot, 1, 1)
        self.assertIn("मतपत्र क्रम संख्या: 3", family_ballot)
        self.assertNotIn("ईवीएम क्रम संख्या", family_ballot)
        self.assertIn("मतपत्र रंग: गुलाबी", family_ballot)
        self.assertIn("ballot-color-pill", family_ballot)
        self.assertIn("मतदान केंद्र पर दी गई मुहर का उपयोग करें · मतपत्र को पहले खड़े में मोड़ें", family_ballot)

        # 2. EVM Election without color
        branding_evm = dict(branding_ballot)
        branding_evm["voting_method"] = "EVM"
        branding_evm["is_ballot_election"] = False
        branding_evm["ballot_color"] = ""

        single_evm = card_templates.build_standard_single_html(voter, booth, branding_evm)
        self.assertIn("ईवीएम क्रम संख्या: 3", single_evm)
        self.assertNotIn("मतपत्र क्रम संख्या", single_evm)
        self.assertNotIn("ballot-color-pill", single_evm)
        self.assertIn("कृपया मतदान के दिन अपना पहचान पत्र", single_evm)

        family_evm = card_templates.build_standard_family_html([voter], branding_evm, 1, 1)
        self.assertIn("ईवीएम क्रम संख्या: 3", family_evm)
        self.assertNotIn("मतपत्र क्रम संख्या", family_evm)
        self.assertNotIn("ballot-color-pill", family_evm)
        self.assertIn("कृपया मतदान के दिन अपना पहचान पत्र", family_evm)


if __name__ == '__main__':
    unittest.main()
