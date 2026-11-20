# -*- coding: utf-8 -*-
"""
tests/test_ballot_paper_adaptation.py
Comprehensive automated test suite for Ballot Paper Adaptation across Chunav Saathi Platform:
1. get_voting_method() resolution for PRI vs Non-PRI election types and custom overrides.
2. tenant_branding.py resolution of voting_method, is_ballot_election, ballot_color, and party_line formatting.
3. card_templates.py single and family slips HTML generation:
   - Dynamic banner titles ("मतपत्र क्रम संख्या:" vs "ईवीएम क्रम संख्या:")
   - Ballot color pill rendering ("मतपत्र रंग: ...")
   - Guidance footer notes (stamping vs identity card)
4. blueprints/slip.py text slips (single & family) containing voting guidance note for ballot campaigns and omitting for EVM.
5. campaign_manager.py create_campaign() and update_campaign() persisting ballot_color and voting_method.
6. Regression test ensuring fixtures ('ward82', 'jelatra') render cleanly without error.
"""

import unittest
import os
import json
import shutil
from unittest.mock import patch, MagicMock

from electionapp.config import (
    get_voting_method,
    BALLOT_PAPER_ELECTION_TYPES,
    EVM_ELECTION_TYPES,
    TENANTS_DIR,
    PLATFORM_ROOT,
    load_tenant_config,
    clear_tenant_config_cache,
    get_pg_connection
)
from electionapp import (
    campaign_manager,
    tenant_branding,
    card_templates,
    create_app
)


class TestBallotPaperAdaptation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def setUp(self):
        self.test_slugs = [
            'test_ballot_pri_unit',
            'test_ballot_evm_unit',
            'test_ballot_brand_unit',
            'test_ballot_api_unit',
            'test_ballot_crud_unit'
        ]
        self._cleanup_slugs()

    def tearDown(self):
        self._cleanup_slugs()

    def _cleanup_slugs(self):
        for slug in self.test_slugs:
            p = os.path.join(TENANTS_DIR, slug)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)
            tenant_branding.invalidate_tenant_branding(slug)
            clear_tenant_config_cache(slug)

        pg_conn = get_pg_connection()
        if pg_conn:
            try:
                with pg_conn.cursor() as cur:
                    cur.execute("DELETE FROM users WHERE tenant_id IN (SELECT tenant_id FROM tenants WHERE campaign_slug LIKE 'test_ballot_%');")
                    cur.execute("DELETE FROM tenants WHERE campaign_slug LIKE 'test_ballot_%';")
                    pg_conn.commit()
                pg_conn.close()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # 1. get_voting_method() Tests
    # -------------------------------------------------------------------------

    def test_01_get_voting_method_pri_types(self):
        """Verify all PRI election types return 'BALLOT_PAPER' regardless of casing."""
        pri_types = [
            'PANCHAYAT', 'GRAM_PANCHAYAT', 'SARPANCH', 'WARD_PANCH',
            'PANCHAYAT_SAMITI', 'PS'
        ]
        for t in pri_types:
            self.assertEqual(get_voting_method(t), 'BALLOT_PAPER', f"Failed for {t}")
            self.assertEqual(get_voting_method(t.lower()), 'BALLOT_PAPER', f"Failed for lowercase {t}")
            self.assertEqual(get_voting_method(f" {t} "), 'BALLOT_PAPER', f"Failed for padded {t}")

        # Substring heuristics
        self.assertEqual(get_voting_method('GRAM_PANCHAYAT_ELECTION'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('DISTRICT_ZILA_PARISHAD'), 'EVM')
        self.assertEqual(get_voting_method('RAJASTHAN_PRI_2026'), 'BALLOT_PAPER')

    def test_02_get_voting_method_non_pri_types(self):
        """Verify all non-PRI (Assembly, Municipal, Parliamentary) types return 'EVM'."""
        non_pri_types = [
            'ASSEMBLY', 'VIDHAN_SABHA', 'PARLIAMENTARY', 'LOK_SABHA',
            'MUNICIPAL', 'NAGAR_PALIKA', 'NAGAR_PARISHAD', 'NAGAR_NIGAM',
            'ZILA_PARISHAD', 'ZP'
        ]
        for t in non_pri_types:
            self.assertEqual(get_voting_method(t), 'EVM', f"Failed for {t}")
            self.assertEqual(get_voting_method(t.lower()), 'EVM', f"Failed for lowercase {t}")
            self.assertEqual(get_voting_method(f" {t} "), 'EVM', f"Failed for padded {t}")

        # Substring heuristics
        self.assertEqual(get_voting_method('DELHI_ASSEMBLY_2025'), 'EVM')
        self.assertEqual(get_voting_method('JAIPUR_MUNICIPAL_CORP'), 'EVM')

    def test_03_get_voting_method_custom_overrides(self):
        """Verify custom_override parameter overrides election type defaults."""
        # Force EVM on Panchayat
        self.assertEqual(get_voting_method('PANCHAYAT', custom_override='EVM'), 'EVM')
        self.assertEqual(get_voting_method('PANCHAYAT', custom_override='evm'), 'EVM')

        # Force Ballot Paper on Assembly / Municipal
        self.assertEqual(get_voting_method('ASSEMBLY', custom_override='BALLOT_PAPER'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('MUNICIPAL', custom_override='ballot_paper'), 'BALLOT_PAPER')

        # Empty or None override falls back to election type logic
        self.assertEqual(get_voting_method('PANCHAYAT', custom_override=''), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('PANCHAYAT', custom_override=None), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('ASSEMBLY', custom_override='   '), 'EVM')

        # Default fallback if both are None or empty
        self.assertEqual(get_voting_method(None, custom_override=None), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('', custom_override=''), 'BALLOT_PAPER')

    # -------------------------------------------------------------------------
    # 2. tenant_branding.py Tests
    # -------------------------------------------------------------------------

    def test_04_tenant_branding_ballot_resolution(self):
        """Verify get_tenant_branding outputs voting_method, is_ballot_election, ballot_color."""
        slug = 'test_ballot_brand_unit'
        data = {
            'campaign_slug': slug,
            'election_type': 'PANCHAYAT',
            'candidate_name_hi': 'सुरेश कुमार',
            'candidate_name_en': 'Suresh Kumar',
            'ballot_serial': '4',
            'symbol_name_hi': 'हैंडपंप',
            'ballot_color': 'गुलाबी',
            'auto_ingest_rolls': 'false'
        }
        success, _, _ = campaign_manager.create_campaign(data)
        self.assertTrue(success)

        branding = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(branding.get('voting_method'), 'BALLOT_PAPER')
        self.assertTrue(branding.get('is_ballot_election'))
        self.assertEqual(branding.get('ballot_color'), 'गुलाबी')
        self.assertEqual(branding.get('party_line'), 'सरपंच प्रत्याशी (क्रम संख्या 4 पर हैंडपंप का निशान)')

    def test_05_tenant_branding_evm_resolution(self):
        """Verify get_tenant_branding outputs EVM configuration for Assembly."""
        slug = 'test_ballot_evm_unit'
        data = {
            'campaign_slug': slug,
            'election_type': 'ASSEMBLY',
            'candidate_name_hi': 'रमेश शर्मा',
            'candidate_name_en': 'Ramesh Sharma',
            'ballot_serial': '2',
            'party_name_hi': 'भाजपा',
            'symbol_name_hi': 'कमल',
            'auto_ingest_rolls': 'false'
        }
        success, _, _ = campaign_manager.create_campaign(data)
        self.assertTrue(success)

        branding = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(branding.get('voting_method'), 'EVM')
        self.assertFalse(branding.get('is_ballot_election'))
        self.assertEqual(branding.get('ballot_color'), '')
        self.assertEqual(branding.get('party_line'), 'भाजपा प्रत्याशी (क्रम संख्या 2 पर कमल का निशान)')

    def test_06_tenant_branding_party_line_permutations(self):
        """Verify party_line formatting for various PRI designations and ballot serial combinations."""
        slug = 'test_ballot_pri_unit'

        # Panchayat Samiti
        data_ps = {
            'campaign_slug': slug,
            'election_type': 'PANCHAYAT_SAMITI',
            'candidate_name_hi': 'मोहन लाल',
            'symbol_name_hi': 'तीर',
            'ballot_serial': '2',
            'auto_ingest_rolls': 'false'
        }
        campaign_manager.create_campaign(data_ps)
        b_ps = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(b_ps.get('party_line'), 'पंचायत समिति सदस्य प्रत्याशी (क्रम संख्या 2 पर तीर का निशान)')

        # Zila Parishad
        self._cleanup_slugs()
        data_zp = {
            'campaign_slug': slug,
            'election_type': 'ZILA_PARISHAD',
            'candidate_name_hi': 'राकेश बिश्नोई',
            'symbol_name_hi': 'ट्रैक्टर',
            'ballot_serial': '7',
            'auto_ingest_rolls': 'false'
        }
        campaign_manager.create_campaign(data_zp)
        b_zp = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(b_zp.get('party_line'), 'जिला परिषद सदस्य प्रत्याशी (क्रम संख्या 7 पर ट्रैक्टर का निशान)')

    # -------------------------------------------------------------------------
    # 3. card_templates.py Single and Family Slips Tests
    # -------------------------------------------------------------------------

    def test_07_card_templates_single_ballot_and_evm(self):
        """Verify build_standard_single_html contains 'मतपत्र क्रम संख्या' or 'ईवीएम क्रम संख्या' and color pills."""
        voter = {
            'part_no': 1,
            'sl_no': 15,
            'name_hi': 'किशन लाल',
            'name_en': 'Kishan Lal',
            'rel_name_hi': 'शंकर लाल',
            'rel_name_en': 'Shankar Lal',
            'gender': 'पुरुष',
            'age': 45,
            'epic_no': 'KSH1234567',
            'house_no': '18'
        }
        booth = {'name': 'राजकीय विद्यालय', 'area': 'वार्ड 1'}

        # Case A: Ballot Paper with color
        branding_ballot_color = {
            'tenant_slug': 'mock_slug',
            'candidate_name_hi': 'सुरेश कुमार',
            'candidate_name_en': 'Suresh Kumar',
            'candidate_disp': 'सुरेश कुमार',
            'candidate_src': 'data:image/png;base64,mock',
            'has_candidate': True,
            'party_name_hi': '',
            'party_name_en': '',
            'party_line': 'सरपंच प्रत्याशी',
            'symbol_name_hi': 'हैंडपंप',
            'symbol_name_en': 'Handpump',
            'symbol_file': 'symbol.png',
            'lotus_src': 'data:image/png;base64,mock',
            'has_symbol': True,
            'ballot_no': '4',
            'has_ballot': True,
            'voting_method': 'BALLOT_PAPER',
            'is_ballot_election': True,
            'ballot_color': 'गुलाबी',
            'slogan': 'विकास की नई राह',
            'theme_color': '#16a34a',
            'place_hi': 'ग्राम पंचायत रामपुरा',
            'ward_name_hi': 'ग्राम पंचायत रामपुरा',
            'ward_no': '1-9',
            'district': 'जयपुर',
            'state': 'Rajasthan',
            'appeal_msg': 'आधिकारिक मुहर लगाकर भारी मतों से विजयी बनाएं',
            'booths': {},
            'get_booth': lambda p: booth,
            'custom_bg': None
        }

        single_ballot = card_templates.build_standard_single_html(voter, booth, branding_ballot_color)
        self.assertIn('मतपत्र क्रम संख्या: 4', single_ballot)
        self.assertNotIn('ईवीएम क्रम संख्या', single_ballot)
        self.assertIn('मतपत्र रंग: गुलाबी', single_ballot)
        self.assertIn('ballot-color-pill', single_ballot)
        self.assertIn('मतदान केंद्र पर दी गई मुहर का उपयोग करें · मतपत्र को पहले खड़े में मोड़ें', single_ballot)

        # Case B: Ballot Paper without color
        branding_ballot_no_color = dict(branding_ballot_color)
        branding_ballot_no_color['ballot_color'] = ''
        single_ballot_no_col = card_templates.build_standard_single_html(voter, booth, branding_ballot_no_color)
        self.assertIn('मतपत्र क्रम संख्या: 4', single_ballot_no_col)
        self.assertNotIn('मतपत्र रंग:', single_ballot_no_col)
        self.assertNotIn('ballot-color-pill', single_ballot_no_col)

        # Case C: EVM Election
        branding_evm = dict(branding_ballot_color)
        branding_evm['voting_method'] = 'EVM'
        branding_evm['is_ballot_election'] = False
        branding_evm['ballot_color'] = ''

        single_evm = card_templates.build_standard_single_html(voter, booth, branding_evm)
        self.assertIn('ईवीएम क्रम संख्या: 4', single_evm)
        self.assertNotIn('मतपत्र क्रम संख्या', single_evm)
        self.assertNotIn('ballot-color-pill', single_evm)
        self.assertIn('कृपया मतदान के दिन अपना पहचान पत्र', single_evm)

    def test_08_card_templates_family_ballot_and_evm(self):
        """Verify build_standard_family_html contains 'मतपत्र क्रम संख्या' or 'ईवीएम क्रम संख्या' and color pills."""
        voters = [
            {
                'part_no': 1,
                'sl_no': 10,
                'name_hi': 'सुनीता देवी',
                'name_en': 'Sunita Devi',
                'rel_name_hi': 'राजेश कुमार',
                'rel_name_en': 'Rajesh Kumar',
                'gender': 'महिला',
                'age': 38,
                'epic_no': 'SNT1234567',
                'house_no': '25'
            },
            {
                'part_no': 1,
                'sl_no': 11,
                'name_hi': 'राजेश कुमार',
                'name_en': 'Rajesh Kumar',
                'rel_name_hi': 'रामरतन',
                'rel_name_en': 'Ramratan',
                'gender': 'पुरुष',
                'age': 42,
                'epic_no': 'RJK1234567',
                'house_no': '25'
            }
        ]
        booth = {'name': 'राजकीय विद्यालय', 'area': 'वार्ड 1'}

        branding_ballot = {
            'tenant_slug': 'mock_slug',
            'candidate_name_hi': 'सुरेश कुमार',
            'candidate_name_en': 'Suresh Kumar',
            'candidate_disp': 'सुरेश कुमार',
            'candidate_src': 'data:image/png;base64,mock',
            'has_candidate': True,
            'party_name_hi': '',
            'party_name_en': '',
            'party_line': 'सरपंच प्रत्याशी',
            'symbol_name_hi': 'हैंडपंप',
            'symbol_name_en': 'Handpump',
            'symbol_file': 'symbol.png',
            'lotus_src': 'data:image/png;base64,mock',
            'has_symbol': True,
            'ballot_no': '5',
            'has_ballot': True,
            'voting_method': 'BALLOT_PAPER',
            'is_ballot_election': True,
            'ballot_color': 'सफेद',
            'slogan': 'सबका साथ सबका विकास',
            'theme_color': '#16a34a',
            'place_hi': 'ग्राम पंचायत रामपुरा',
            'ward_name_hi': 'ग्राम पंचायत रामपुरा',
            'ward_no': '1-9',
            'district': 'जयपुर',
            'state': 'Rajasthan',
            'appeal_msg': 'आधिकारिक मुहर लगाकर भारी मतों से विजयी बनाएं',
            'booths': {},
            'get_booth': lambda p: booth,
            'custom_bg': None
        }

        family_ballot = card_templates.build_standard_family_html(voters, branding_ballot, 1, 1)
        self.assertIn('मतपत्र क्रम संख्या: 5', family_ballot)
        self.assertNotIn('ईवीएम क्रम संख्या', family_ballot)
        self.assertIn('मतपत्र रंग: सफेद', family_ballot)
        self.assertIn('ballot-color-pill', family_ballot)
        self.assertIn('मतदान केंद्र पर दी गई मुहर का उपयोग करें · मतपत्र को पहले खड़े में मोड़ें', family_ballot)

        # EVM Family Card
        branding_evm = dict(branding_ballot)
        branding_evm['voting_method'] = 'EVM'
        branding_evm['is_ballot_election'] = False
        branding_evm['ballot_color'] = ''

        family_evm = card_templates.build_standard_family_html(voters, branding_evm, 1, 1)
        self.assertIn('ईवीएम क्रम संख्या: 5', family_evm)
        self.assertNotIn('मतपत्र क्रम संख्या', family_evm)
        self.assertNotIn('ballot-color-pill', family_evm)
        self.assertIn('कृपया मतदान के दिन अपना पहचान पत्र', family_evm)

    # -------------------------------------------------------------------------
    # 4. blueprints/slip.py api_slip_text Tests
    # -------------------------------------------------------------------------

    def test_09_api_slip_text_single_and_family_ballot_guidance(self):
        """Verify /api/slip_text text slips include guidance note for ballot paper and omit for EVM."""
        slug_ballot = 'test_ballot_api_unit'
        data_ballot = {
            'campaign_slug': slug_ballot,
            'election_type': 'PANCHAYAT',
            'candidate_name_hi': 'महेन्द्र सिंह',
            'candidate_name_en': 'Mahendra Singh',
            'auto_ingest_rolls': 'false'
        }
        campaign_manager.create_campaign(data_ballot)

        mock_repo = MagicMock()
        mock_voter_1 = {
            'id': 201,
            'name_hi': 'कमलेश शर्मा',
            'name_en': 'Kamlesh Sharma',
            'rel_name_hi': 'मदन लाल',
            'rel_name_en': 'Madan Lal',
            'rel_type': 'पिता',
            'part_no': 1,
            'sl_no': 55,
            'house_no': '12',
            'gender': 'पुरुष',
            'age': 35,
            'epic_no': 'KML1234567',
            'booth_name': 'राजकीय विद्यालय',
            'is_deleted': 0
        }
        mock_voter_2 = {
            'id': 202,
            'name_hi': 'सुमन शर्मा',
            'name_en': 'Suman Sharma',
            'rel_name_hi': 'कमलेश शर्मा',
            'rel_name_en': 'Kamlesh Sharma',
            'rel_type': 'पति',
            'part_no': 1,
            'sl_no': 56,
            'house_no': '12',
            'gender': 'महिला',
            'age': 32,
            'epic_no': 'SMN1234567',
            'booth_name': 'राजकीय विद्यालय',
            'is_deleted': 0
        }
        mock_repo.get_voter.return_value = mock_voter_1
        mock_repo.get_voters_by_ids.return_value = [mock_voter_1, mock_voter_2]
        mock_repo.get_booths_dict.return_value = {'1': {'name': 'राजकीय विद्यालय'}}

        # 1. Single voter slip under Ballot Paper campaign
        with patch('electionapp.blueprints.slip._get_tenant', return_value=(slug_ballot, mock_repo)):
            res_single = self.client.get('/api/slip_text?id=201')
            self.assertEqual(res_single.status_code, 200)
            slip_text = res_single.get_json().get('slip_text', '')
            self.assertIn('💡 *मतदान निर्देश:*', slip_text)
            self.assertIn('मतदान केंद्र पर दी गई मुहर (ठप्पा) का उपयोग करें', slip_text)
            self.assertIn('मतपत्र को पहले खड़े में मोड़ें', slip_text)

        # 2. Family slip under Ballot Paper campaign
        with patch('electionapp.blueprints.slip._get_tenant', return_value=(slug_ballot, mock_repo)):
            res_family = self.client.get('/api/slip_text?family_ids=201,202')
            self.assertEqual(res_family.status_code, 200)
            slip_text_fam = res_family.get_json().get('slip_text', '')
            self.assertIn('💡 *मतदान निर्देश:*', slip_text_fam)
            self.assertIn('मतदान केंद्र पर दी गई मुहर (ठप्पा) का उपयोग करें', slip_text_fam)

        # 3. Single and family voter slip under EVM (ward82 fixture)
        with patch('electionapp.blueprints.slip._get_tenant', return_value=('ward82', mock_repo)):
            res_evm_single = self.client.get('/api/slip_text?id=201')
            self.assertEqual(res_evm_single.status_code, 200)
            slip_text_evm = res_evm_single.get_json().get('slip_text', '')
            self.assertNotIn('मुहर (ठप्पा)', slip_text_evm)
            self.assertNotIn('मतपत्र को पहले खड़े में मोड़ें', slip_text_evm)

            res_evm_fam = self.client.get('/api/slip_text?family_ids=201,202')
            self.assertEqual(res_evm_fam.status_code, 200)
            slip_text_evm_fam = res_evm_fam.get_json().get('slip_text', '')
            self.assertNotIn('मुहर (ठप्पा)', slip_text_evm_fam)

    # -------------------------------------------------------------------------
    # 5. campaign_manager.py Persistence & Update Tests
    # -------------------------------------------------------------------------

    def test_10_campaign_manager_create_and_update_ballot_persistence(self):
        """Verify creating and updating campaign with ballot_color and voting_method works and stores properly."""
        slug = 'test_ballot_crud_unit'
        data_create = {
            'campaign_slug': slug,
            'election_type': 'PANCHAYAT',
            'candidate_name_hi': 'अर्जुन राम',
            'candidate_name_en': 'Arjun Ram',
            'ballot_color': 'नीला (Blue)',
            'voting_method': 'BALLOT_PAPER',
            'auto_ingest_rolls': 'false'
        }
        success, _, status = campaign_manager.create_campaign(data_create)
        self.assertTrue(success)
        self.assertEqual(status, 201)

        cfg_path = os.path.join(TENANTS_DIR, slug, 'campaign.json')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)

        self.assertEqual(cfg['election']['voting_method'], 'BALLOT_PAPER')
        self.assertEqual(cfg['election']['ballot_color'], 'नीला (Blue)')
        self.assertEqual(cfg['party']['voting_method'], 'BALLOT_PAPER')
        self.assertEqual(cfg['party']['ballot_color'], 'नीला (Blue)')

        branding_init = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(branding_init.get('voting_method'), 'BALLOT_PAPER')
        self.assertTrue(branding_init.get('is_ballot_election'))
        self.assertEqual(branding_init.get('ballot_color'), 'नीला (Blue)')

        # Update to EVM with new color
        data_update = {
            'voting_method': 'EVM',
            'ballot_color': 'पीला (Yellow)'
        }
        success_up, _, status_up = campaign_manager.update_campaign(slug, data_update)
        self.assertTrue(success_up)
        self.assertEqual(status_up, 200)

        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg_up = json.load(f)

        self.assertEqual(cfg_up['election']['voting_method'], 'EVM')
        self.assertEqual(cfg_up['election']['ballot_color'], 'पीला (Yellow)')
        self.assertEqual(cfg_up['party']['voting_method'], 'EVM')
        self.assertEqual(cfg_up['party']['ballot_color'], 'पीला (Yellow)')

        # Invalidate and check refreshed branding
        tenant_branding.invalidate_tenant_branding(slug)
        branding_up = tenant_branding.get_tenant_branding(slug)
        self.assertEqual(branding_up.get('voting_method'), 'EVM')
        self.assertFalse(branding_up.get('is_ballot_election'))
        self.assertEqual(branding_up.get('ballot_color'), 'पीला (Yellow)')

    # -------------------------------------------------------------------------
    # 6. Fixtures Regression Tests ('ward82', 'jelatra')
    # -------------------------------------------------------------------------

    def test_11_fixtures_ward82_regression(self):
        """Verify ward82 fixture resolves as EVM and renders card templates without regression."""
        branding = tenant_branding.get_tenant_branding('ward82')
        self.assertEqual(branding.get('voting_method'), 'EVM')
        self.assertFalse(branding.get('is_ballot_election'))

        voter = {
            'part_no': 1,
            'sl_no': 100,
            'name_hi': 'रमेश चंद्र',
            'name_en': 'Ramesh Chandra',
            'rel_name_hi': 'मदन लाल',
            'rel_name_en': 'Madan Lal',
            'gender': 'पुरुष',
            'age': 50,
            'epic_no': 'RJ01123456',
            'house_no': '82'
        }
        booth = {'name': 'सामुदायिक भवन, जाटा बास', 'area': 'वार्ड 82'}

        single_html = card_templates.build_standard_single_html(voter, booth, branding, tenant='ward82')
        self.assertIn('ईवीएम क्रम संख्या:', single_html)
        self.assertNotIn('मतपत्र क्रम संख्या', single_html)
        self.assertNotIn('ballot-color-pill', single_html)

        family_html = card_templates.build_standard_family_html([voter], branding, 1, 1, tenant='ward82')
        self.assertIn('ईवीएम क्रम संख्या:', family_html)
        self.assertNotIn('मतपत्र क्रम संख्या', family_html)
        self.assertNotIn('ballot-color-pill', family_html)

    def test_12_fixtures_jelatra_regression(self):
        """Verify jelatra fixture renders without crashes and handles single and family card templates."""
        branding = tenant_branding.get_tenant_branding('jelatra')
        self.assertIsNotNone(branding)

        voter = {
            'part_no': 77,
            'sl_no': 50,
            'ward_no': 1,
            'name_hi': 'डूंगर राम',
            'name_en': 'Dungar Ram',
            'rel_name_hi': 'चेना राम',
            'rel_name_en': 'Chena Ram',
            'gender': 'पुरुष',
            'age': 40,
            'epic_no': 'RJ04567890',
            'house_no': '12'
        }
        booth = {'name': '77 - राजकीय उच्च माध्यमिक विद्यालय (कमरा नंबर 1)', 'area': 'वार्ड 1'}

        single_html = card_templates.build_standard_single_html(voter, booth, branding, tenant='jelatra')
        self.assertGreater(len(single_html), 500)
        self.assertIn('डूंगर राम', single_html)

        family_html = card_templates.build_standard_family_html([voter], branding, 1, 1, tenant='jelatra')
        self.assertGreater(len(family_html), 500)
        self.assertIn('डूंगर राम', family_html)

    def test_13_api_admin_campaigns_get_returns_ballot_metadata(self):
        """Verify GET /api/admin/campaigns/get returns ballot_color and voting_method for Edit Campaign page pre-filling."""
        slug = 'test_ballot_api_unit'
        data = {
            'campaign_slug': slug,
            'election_type': 'PANCHAYAT',
            'candidate_name_hi': 'कमला देवी',
            'candidate_name_en': 'Kamla Devi',
            'ballot_serial': '3',
            'ballot_color': 'गुलाबी / Pink',
            'auto_ingest_rolls': 'false'
        }
        success, _, _ = campaign_manager.create_campaign(data)
        self.assertTrue(success)

        with self.client.session_transaction() as sess:
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = '*'

        res = self.client.get(f'/api/admin/campaigns/get?slug={slug}')
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertTrue(json_data.get('success'))
        campaign = json_data.get('campaign', {})
        self.assertEqual(campaign.get('ballot_serial'), '3')
        self.assertEqual(campaign.get('ballot_color'), 'गुलाबी / Pink')
        self.assertEqual(campaign.get('voting_method'), 'BALLOT_PAPER')


if __name__ == '__main__':
    unittest.main()

