import unittest
from electionapp import create_app

class TestCampaignCreatePage(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_super_admin_access(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
        res = self.client.get('/campaign/new')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('🏛️ नया चुनाव अभियान जोड़ें', html)
        self.assertIn('/admin', html)
        self.assertIn('एडमिन डैशबोर्ड (Back to Admin Dashboard)', html)
        self.assertIn('rajDistrictSelect', html)
        self.assertIn('wardNoContainer', html)
        self.assertIn('wizCandidatePhotoFile', html)
        self.assertIn('secSymbolChipsContainer', html)
        self.assertIn('wizCampaignMessage', html)
        self.assertIn('campaignCredentialsModal', html)
        self.assertIn('sectionWhatsAppQuota', html)
        self.assertIn('wizWhatsAppCredits', html)

    def test_reseller_access(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'reseller1'
            sess['role'] = 'reseller'
        res = self.client.get('/campaign/new')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('/my-campaigns', html)
        self.assertIn('रीसेलर कमान केंद्र (Back to My Campaigns)', html)
        self.assertIn('sectionWhatsAppQuota', html)
        self.assertIn('wizWhatsAppCredits', html)

    def test_unauthenticated_redirect(self):
        import os
        old = os.environ.get('FORCE_AUTH_CHECK')
        try:
            os.environ['FORCE_AUTH_CHECK'] = '1'
            res = self.client.get('/campaign/new')
            self.assertEqual(res.status_code, 302)
        finally:
            if old is None:
                os.environ.pop('FORCE_AUTH_CHECK', None)
            else:
                os.environ['FORCE_AUTH_CHECK'] = old

    def test_admin_dashboard_link_to_new_campaign(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
        res = self.client.get('/admin')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('href="/campaign/new"', html)
        self.assertNotIn('admin/campaign_wizard_modal.html', html)
        self.assertNotIn('id="createCampaignModal"', html)

    def test_my_campaigns_link_to_new_campaign(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 2
            sess['username'] = 'partner'
            sess['role'] = 'reseller'
        res = self.client.get('/my-campaigns')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('href="/campaign/new"', html)
        # Ensure modal include, popup container and old open function were removed
        self.assertNotIn('admin/campaign_wizard_modal.html', html)
        self.assertNotIn('id="createCampaignModal"', html)
        self.assertNotIn('openEditCampaignModal', html)

    def test_campaign_edit_page_super_admin(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
        res = self.client.get('/campaign/edit/ward82')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('अभियान संपादित करें', html)
        self.assertIn('id="wizStatus"', html)
        self.assertIn('id="wizSlogan"', html)
        self.assertIn('id="sectionWhatsAppQuota"', html)

    def test_base_mobile_header_optimizations(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        # Admin badge hidden on mobile
        self.assertIn('hidden sm:inline-flex text-[10px] bg-amber-950/60 text-amber-300', html)
        # Logout text hidden on mobile
        self.assertIn('<span class="hidden sm:inline">लॉगआउट</span>', html)
        # Switcher compact max-w on mobile
        self.assertIn('max-w-[120px] sm:max-w-[240px]', html)

    def test_hub_bottom_padding(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('pb-28 sm:pb-16', html)

    def test_candidate_cropper_and_sec_symbols(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
        res = self.client.get('/campaign/new')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Cropper Modal & Controls
        self.assertIn('id="cropModal"', html)
        self.assertIn('id="cropperImage"', html)
        self.assertIn('id="btnCropCandPhoto"', html)
        self.assertIn('cropper.min.css', html)
        self.assertIn('cropper.min.js', html)
        self.assertIn('heic2any.min.js', html)

        # 1-Tap SEC & Party Preset Symbols
        self.assertIn('selectSymbolPreset', html)
        self.assertIn('tier1PartySymbolsContainer', html)
        self.assertIn('secSymbolSearch', html)
        self.assertIn('filterSecSymbols', html)
        self.assertIn('tractor', html)
        self.assertIn('handpump', html)
        self.assertIn('almirah', html)
        self.assertIn('scissors', html)
        self.assertIn('ceiling_fan', html)
        self.assertIn('sewing_machine', html)
        self.assertIn('diya', html)
        self.assertIn('cot', html)
        self.assertIn('whistle', html)
        self.assertIn('congress_hand', html)
        self.assertIn('rlp_bottle', html)
        self.assertIn('bap_hockey', html)
        self.assertIn('gas_cylinder', html)
        self.assertIn('kite', html)
        self.assertIn('id="wizPresetSymbol"', html)

        # Backend preset resolver verification
        from electionapp.campaign_manager import _resolve_preset_symbol_path
        tractor_path = _resolve_preset_symbol_path('tractor')
        self.assertIsNotNone(tractor_path)
        self.assertTrue(tractor_path.endswith('tractor.png'))

        scissors_path = _resolve_preset_symbol_path('scissors')
        self.assertIsNotNone(scissors_path)
        self.assertTrue(scissors_path.endswith('scissors.png'))

        diya_path = _resolve_preset_symbol_path('दीपक')
        self.assertIsNotNone(diya_path)
        self.assertTrue(diya_path.endswith('diya.png'))

        sewing_path = _resolve_preset_symbol_path('सिलाई मशीन')
        self.assertIsNotNone(sewing_path)
        self.assertTrue(sewing_path.endswith('sewing_machine.png'))

        congress_path = _resolve_preset_symbol_path('congress_hand')
        self.assertIsNotNone(congress_path)
        self.assertTrue(congress_path.endswith('congress_hand.png'))

        lotus_path = _resolve_preset_symbol_path('bjp_lotus')
        self.assertIsNotNone(lotus_path)

if __name__ == '__main__':
    unittest.main()
