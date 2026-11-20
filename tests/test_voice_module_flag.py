import os
import unittest
from flask import Flask
from electionapp import create_app

class TestVoiceModuleFlag(unittest.TestCase):
    def setUp(self):
        # Default is False
        os.environ['ENABLE_VOICE_MODULE'] = '0'

    def tearDown(self):
        if 'ENABLE_VOICE_MODULE' in os.environ:
            del os.environ['ENABLE_VOICE_MODULE']

    def test_voice_disabled_by_default(self):
        app = create_app('ward82', storage_backend='postgres')
        app.config['TESTING'] = False  # Ensure guard triggers like production
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = ['all']

        # Check config flag
        self.assertFalse(app.config.get('ENABLE_VOICE_MODULE'))

        # Check API returns 403
        resp = client.get('/api/voice/balance')
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data.get('ok'))
        self.assertIn("disabled", data.get('error', '').lower())

        # Check page redirects to hub
        resp = client.get('/campaigns/voice?tenant=ward82')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/?tenant=ward82', resp.headers.get('Location', ''))

        # Check Hub template does not render voice broadcast card
        hub_resp = client.get('/?tenant=ward82')
        self.assertEqual(hub_resp.status_code, 200)
        self.assertNotIn(b'/campaigns/voice', hub_resp.data)

        # Check Voting Day desk does not render voice blast modal
        vd_resp = client.get('/voting-day?tenant=ward82')
        self.assertEqual(vd_resp.status_code, 200)
        self.assertNotIn(b'id="vdVoiceCallingModal"', vd_resp.data)
        self.assertNotIn(b'vdOpenVoiceCallingModal()', vd_resp.data)

    def test_voice_enabled_via_env(self):
        os.environ['ENABLE_VOICE_MODULE'] = '1'
        app = create_app('ward82', storage_backend='postgres')
        self.assertTrue(app.config.get('ENABLE_VOICE_MODULE'))

if __name__ == '__main__':
    unittest.main()
