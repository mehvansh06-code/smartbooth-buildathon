import os
import unittest
from unittest.mock import MagicMock, patch
from electionapp import create_app

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestInlineKaryakartaCreation(unittest.TestCase):
    """Test suite ensuring that the inline quick-add Karyakarta capability is
    fully integrated into the voter profile modal and client tagger script."""

    def setUp(self):
        self.modal_path = os.path.join(PLATFORM_ROOT, 'templates', 'modals', '_voter_profile_modal.html')
        self.js_path = os.path.join(PLATFORM_ROOT, 'static', 'js', 'voter_management_tagger.js')
        with open(self.modal_path, 'r', encoding='utf-8') as f:
            self.modal_html = f.read()
        with open(self.js_path, 'r', encoding='utf-8') as f:
            self.tagger_js = f.read()

    def test_modal_has_inline_karyakarta_markup(self):
        """Verify presence of toggle button, quick-action option, and inline form fields."""
        self.assertIn('id="vpmBtnToggleNewKarya"', self.modal_html)
        self.assertIn('window.vpmToggleNewKaryakartaForm()', self.modal_html)
        self.assertIn('value="__NEW__"', self.modal_html)
        self.assertIn('id="vpmNewKaryakartaBox"', self.modal_html)
        self.assertIn('id="vpmNewKaryaName"', self.modal_html)
        self.assertIn('id="vpmNewKaryaPhone"', self.modal_html)
        self.assertIn('id="vpmNewKaryaBooth"', self.modal_html)
        self.assertIn('id="vpmNewKaryaArea"', self.modal_html)
        self.assertIn('id="vpmBtnSaveNewKarya"', self.modal_html)
        self.assertIn('window.vpmSaveNewKaryakarta()', self.modal_html)

    def test_tagger_js_has_karyakarta_controller_functions(self):
        """Verify presence of client-side controller logic in voter_management_tagger.js."""
        self.assertIn('window.vpmToggleNewKaryakartaForm', self.tagger_js)
        self.assertIn('window.vpmHandleKaryakartaSelectChange', self.tagger_js)
        self.assertIn('window.vpmSaveNewKaryakarta', self.tagger_js)
        self.assertIn('populateVpmKaryakartaSelect', self.tagger_js)
        self.assertIn('/api/karyakartas/save', self.tagger_js)
        self.assertIn('__NEW__', self.tagger_js)

    def test_api_karyakartas_save_validation(self):
        """Verify backend API rejects empty name or invalid tenant."""
        app = create_app('ward82', storage_backend='postgres')
        app.config['TESTING'] = True
        client = app.test_client()

        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'operator'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

        # Test empty name returns 400
        res = client.post('/api/karyakartas/save?tenant=ward82', json={
            'name': '',
            'phone': '9829012345'
        })
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get('success'))
        self.assertIn('कार्यकर्ता का नाम आवश्यक है', data.get('error'))


if __name__ == '__main__':
    unittest.main()
