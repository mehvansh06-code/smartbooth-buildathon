import unittest
import os
import json
import shutil
import tempfile
from unittest.mock import patch, MagicMock

from electionapp import create_app
from electionapp.campaign_manager import _get_election_type_display, create_campaign
from electionapp.tenant_branding import get_tenant_branding, invalidate_tenant_branding
from electionapp import rajasthan_catalog


class TestMultiGpCampaign(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_campaign_new_html_has_multi_gp_elements(self):
        """Verify election type cards, multi-GP container, and hidden fields in campaign create page."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        res = self.client.get('/campaign/new')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Check election type options
        self.assertIn('PANCHAYAT_SAMITI', html)
        self.assertIn('ZILA_PARISHAD', html)
        self.assertIn('पंचायत समिति सदस्य', html)
        self.assertIn('जिला परिषद सदस्य', html)

        # Check hidden field for multiple GPs
        self.assertIn('id="wizRajGps"', html)
        self.assertIn('name="rajasthan_gps"', html)

        # Check multi-GP UI container and controls
        self.assertIn('id="multiGpContainer"', html)
        self.assertIn('id="multiGpSearch"', html)
        self.assertIn('id="selectedGpCountBadge"', html)
        self.assertIn('id="selectedGpTags"', html)
        self.assertIn('id="multiGpCheckboxList"', html)
        self.assertIn('selectAllGps()', html)
        self.assertIn('clearAllGps()', html)

        # Check JS functions and template messages
        self.assertIn('PANCHAYAT_SAMITI:', html)
        self.assertIn('ZILA_PARISHAD:', html)
        self.assertIn('renderMultiGpCheckboxes', html)
        self.assertIn('onSelectedGpsUpdated', html)

    def test_election_type_display(self):
        """Verify _get_election_type_display for new multi-GP election types."""
        self.assertEqual(_get_election_type_display('PANCHAYAT_SAMITI'), 'पंचायत समिति चुनाव')
        self.assertEqual(_get_election_type_display('ZILA_PARISHAD'), 'जिला परिषद चुनाव')
        self.assertEqual(_get_election_type_display('PANCHAYAT'), 'पंचायती राज (सरपंच) चुनाव')
        self.assertEqual(_get_election_type_display('MUNICIPAL'), 'नगर पालिका / निगम चुनाव')
        self.assertEqual(_get_election_type_display('ASSEMBLY'), 'विधान सभा चुनाव')

    def test_tenant_branding_candidate_title(self):
        """Verify candidate titles for Panchayat Samiti and Zila Parishad."""
        invalidate_tenant_branding()
        # Non-party candidate
        ps_cfg = {
            'tenant_slug': 'test_ps_np',
            'candidate': {'name_hi': 'रमेश कुमार', 'name_en': 'Ramesh Kumar'},
            'election': {'election_type': 'PANCHAYAT_SAMITI', 'ward_name_hi': 'वार्ड 12'},
            'party': {},
            'booths': {}
        }
        with patch('electionapp.tenant_branding.load_tenant_config', return_value=ps_cfg):
            branding = get_tenant_branding('test_ps_np')
            self.assertIn('पंचायत समिति सदस्य प्रत्याशी', branding.get('party_line', ''))

        invalidate_tenant_branding()
        # Party candidate
        ps_party_cfg = {
            'tenant_slug': 'test_ps_p',
            'candidate': {'name_hi': 'रमेश कुमार'},
            'election': {'election_type': 'PANCHAYAT_SAMITI', 'ward_name_hi': 'वार्ड 12'},
            'party': {'name_hi': 'भाजपा'},
            'booths': {}
        }
        with patch('electionapp.tenant_branding.load_tenant_config', return_value=ps_party_cfg):
            branding_party = get_tenant_branding('test_ps_p')
            self.assertIn('भाजपा प्रत्याशी', branding_party.get('party_line', ''))

        invalidate_tenant_branding()
        # Zila Parishad candidate
        zp_cfg = {
            'tenant_slug': 'test_zp_np',
            'candidate': {'name_hi': 'सुनीता देवी'},
            'election': {'election_type': 'ZILA_PARISHAD', 'ward_name_hi': 'वार्ड 5'},
            'party': {},
            'booths': {}
        }
        with patch('electionapp.tenant_branding.load_tenant_config', return_value=zp_cfg):
            branding_zp = get_tenant_branding('test_zp_np')
            self.assertIn('जिला परिषद सदस्य प्रत्याशी', branding_zp.get('party_line', ''))

    def test_area_preview_multi_gp_aggregation(self):
        """Test get_area_preview with multiple GPs aggregation."""
        # Mock get_master_db_connection to return None so it falls back to scanning or empty preview safely
        with patch('electionapp.rajasthan_catalog.get_master_db_connection', return_value=None):
            preview = rajasthan_catalog.get_area_preview(
                district='Jalore',
                block='Sanchore',
                gps=['Chhoti Virol', 'Dantiwas']
            )
            self.assertTrue(preview.get('success'))
            self.assertEqual(preview.get('gps'), ['Chhoti Virol', 'Dantiwas'])
            self.assertIn('Chhoti Virol', preview.get('gp'))
            self.assertIn('Dantiwas', preview.get('gp'))

    def test_create_campaign_multi_gp_config(self):
        """Test create_campaign parses rajasthan_gps and writes gps into campaign.json."""
        test_slug = f"test_ps_{os.getpid()}"
        from electionapp.config import TENANTS_DIR
        tenant_dir = os.path.join(TENANTS_DIR, test_slug)

        payload = {
            'wizard_mode': 'create',
            'campaign_slug': test_slug,
            'candidate_name_hi': 'हनुमान राम',
            'candidate_name_en': 'Hanuman Ram',
            'election_type': 'PANCHAYAT_SAMITI',
            'ward_no': '14',
            'ward_name_hi': 'पंचायत समिति वार्ड 14',
            'district': 'जालोर',
            'rajasthan_district': 'Jalore',
            'rajasthan_block': 'Sanchore',
            'rajasthan_gps': 'Chhoti Virol,Dantiwas,Sarana',
            'auto_ingest_rolls': 'false'
        }

        try:
            # We mock postgres connections to avoid real DB timeouts during test
            with patch('electionapp.campaign_manager.get_pg_connection', return_value=None), \
                 patch('electionapp.rajasthan_catalog.get_master_db_connection', return_value=None):
                success, result, status_code = create_campaign(payload, files={}, owner_user_id=1)
                self.assertTrue(success)
                self.assertEqual(status_code, 201)
                self.assertEqual(result.get('campaign_slug'), test_slug)

                # Verify campaign.json was created with gps array
                cfg_path = os.path.join(tenant_dir, 'campaign.json')
                self.assertTrue(os.path.exists(cfg_path))
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)

                self.assertIn('gps', cfg)
                self.assertEqual(cfg['gps'], ['Chhoti Virol', 'Dantiwas', 'Sarana'])
                self.assertEqual(cfg['election']['election_type'], 'PANCHAYAT_SAMITI')
                self.assertEqual(cfg['election']['ward_no'], '14')
                self.assertEqual(cfg['election']['gps'], ['Chhoti Virol', 'Dantiwas', 'Sarana'])
        finally:
            if os.path.exists(tenant_dir):
                shutil.rmtree(tenant_dir, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
