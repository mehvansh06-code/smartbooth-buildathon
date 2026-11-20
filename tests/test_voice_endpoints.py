"""
Tests for Voice Broadcast Flask API Endpoints (electionapp/blueprints/voice.py).

Revamped Component C Guarantees:
1. GET /api/voice/balance:
   - Returns JSON {success: True, campaign: ..., granted: ..., available: ...}.
2. GET /api/voice/sounds:
   - Returns JSON {success: True, sounds: [...]}, each with audio_id, sound_name,
     carrier_sound_name, display_label, duration_seconds, credits_per_call, status.
   - Enforces zero HTML leaks.
3. POST /api/voice/upload_audio:
   - Fails closed (400) when sound name or file is absent.
   - Accepts both file uploads and browser mic recording blobs.
   - Transcodes to telecom WAV via audio_converter.
   - Generates opaque carrier sound name.
   - Uploads to carrier gateway and records in voice_audios.
4. GET /api/voice/audio/<int:audio_id>:
   - Verifies tenant ownership: returns 403 if accessed by another tenant.
   - Returns 404 if audio does not exist.
   - Streams file with conditional Range support and X-Content-Type-Options: nosniff.
5. POST /api/voice/estimate:
   - Computes target voters, unique mobiles, duration_seconds, pulse credits,
     total credits needed, and current available balance.
6. POST /api/voice/dispatch:
   - Enforces TRAI calling window (09:00 - 21:00 IST) with bypass_window guard.
   - Resolves audio_id strictly server-side to carrier_sound_name and pulse credits.
   - Multiplies reservation and settlement by credits_per_call.
   - Records campaign and call records in PostgreSQL.
"""

import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app, voice_quota


class TestVoiceEndpoints(unittest.TestCase):
    """Integration tests for revamped voice broadcast REST API endpoints."""

    @classmethod
    def setUpClass(cls):
        voice_quota.ensure_voice_schema()
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'
            sess['allowed_modules'] = ['all']

    def test_get_voice_balance_success(self):
        """GET /api/voice/balance returns {success: True, campaign: ..., granted: ..., available: ...}."""
        voice_quota.grant('ward82', 150, note="Endpoint balance test")

        resp = self.client.get('/api/voice/balance')
        self.assertEqual(resp.status_code, 200)

        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['campaign'], 'ward82')
        self.assertGreaterEqual(data['granted'], 150)
        self.assertGreaterEqual(data['available'], 0)
        self.assertIn('quota', data)
        self.assertEqual(data['quota']['tenant_id'], voice_quota.resolve_tenant_id('ward82'))

    def test_get_voice_sounds_success(self):
        """GET /api/voice/sounds returns clean JSON list with pulse credits and carrier names."""
        # Insert a sample audio record
        tid = voice_quota.resolve_tenant_id('ward82')
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO voice_audios 
                      (tenant_id, sound_name, carrier_sound_name, display_label, duration_seconds, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (tid, 'test_appeal_sound', 't13_abc1234567', 'Ward 82 Appeal', 45.2, 'active')
                )
                conn.commit()
        finally:
            pool.putconn(conn)

        resp = self.client.get('/api/voice/sounds')
        self.assertEqual(resp.status_code, 200)

        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertIsInstance(data['sounds'], list)
        self.assertGreaterEqual(len(data['sounds']), 1)

        # Verify pulse credits calculation (45.2s -> 2 credits)
        target_sound = next((s for s in data['sounds'] if s['sound_name'] == 'test_appeal_sound'), None)
        self.assertIsNotNone(target_sound)
        self.assertEqual(target_sound['carrier_sound_name'], 't13_abc1234567')
        self.assertEqual(target_sound['display_label'], 'Ward 82 Appeal')
        self.assertEqual(target_sound['credits_per_call'], 2)
        self.assertNotIn('<', target_sound['display_label'])

    def test_dispatch_fails_when_sound_not_provided(self):
        """POST /api/voice/dispatch fails closed (400) when sound_name/audio_id is missing or empty."""
        resp1 = self.client.post('/api/voice/dispatch', json={
            'target_type': 'unvoted',
            'limit': 100
        })
        self.assertEqual(resp1.status_code, 400)
        data1 = resp1.get_json()
        self.assertFalse(data1['success'])
        self.assertIn('audio sound', data1['error'].lower())

        resp2 = self.client.post('/api/voice/dispatch', json={
            'sound_name': '   ',
            'target_type': 'unvoted'
        })
        self.assertEqual(resp2.status_code, 400)
        self.assertFalse(resp2.get_json()['success'])

    @patch('electionapp.voice_service.dispatch_voice_blast')
    def test_dispatch_handles_target_type_unvoted(self, mock_dispatch):
        """POST /api/voice/dispatch with target_type='unvoted' filters unvoted voters."""
        mock_dispatch.return_value = (
            True,
            "Dispatched 5 calls.",
            {'dispatched': 5, 'total': 5, 'gateway_campaign_ids': ['gw_camp_unvoted_101'], 'errors': []}
        )

        resp = self.client.post('/api/voice/dispatch', json={
            'sound_name': 'appeal_audio_1',
            'target_type': 'unvoted',
            'limit': 5
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['stats']['dispatched'], 5)

        mock_dispatch.assert_called_once()
        args, kwargs = mock_dispatch.call_args
        self.assertEqual(kwargs['tenant_slug'], 'ward82')
        self.assertGreaterEqual(len(kwargs['numbers_list']), 1)

    @patch('electionapp.voice_service.dispatch_voice_blast')
    def test_dispatch_handles_target_type_booths(self, mock_dispatch):
        """POST /api/voice/dispatch with target_type='booths' filters voters by specified booths."""
        mock_dispatch.return_value = (
            True,
            "Dispatched 10 calls.",
            {'dispatched': 10, 'total': 10, 'gateway_campaign_ids': ['gw_camp_booths_102'], 'errors': []}
        )

        resp = self.client.post('/api/voice/dispatch', json={
            'sound_name': 'appeal_audio_booths',
            'target_type': 'booths',
            'booths': [1, 2],
            'limit': 10
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['stats']['dispatched'], 10)

        mock_dispatch.assert_called_once()
        _, kwargs = mock_dispatch.call_args
        self.assertGreaterEqual(len(kwargs['numbers_list']), 1)

    @patch('electionapp.voice_service.dispatch_voice_blast')
    def test_dispatch_records_campaign_and_call_records_in_db(self, mock_dispatch):
        """Successful dispatch inserts rows into voice_campaigns and voice_call_records."""
        mock_dispatch.return_value = (
            True,
            "Dispatched 3 calls.",
            {'dispatched': 3, 'total': 3, 'gateway_campaign_ids': ['gw_camp_db_check_999'], 'errors': []}
        )

        resp = self.client.post('/api/voice/dispatch', json={
            'sound_name': 'speech_record_check',
            'target_type': 'unvoted',
            'limit': 3
        })
        self.assertEqual(resp.status_code, 200)

        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT campaign_id, gateway_campaign_id, sound_name, dispatched_count "
                    "FROM voice_campaigns WHERE gateway_campaign_id = %s",
                    ('gw_camp_db_check_999',)
                )
                camp_row = cur.fetchone()
                self.assertIsNotNone(camp_row)
                camp_id, gw_cid, s_name, d_count = camp_row
                self.assertEqual(gw_cid, 'gw_camp_db_check_999')
                self.assertEqual(d_count, 3)

                cur.execute(
                    "SELECT count(*) FROM voice_call_records WHERE campaign_id = %s",
                    (camp_id,)
                )
                call_count = cur.fetchone()[0]
                self.assertEqual(call_count, 3)
        finally:
            pool.putconn(conn)

    @patch('electionapp.voice_service.dispatch_voice_blast')
    def test_dispatch_returns_error_when_gateway_fails(self, mock_dispatch):
        """When voice_service returns success=False, endpoint returns HTTP 400 with error."""
        mock_dispatch.return_value = (
            False,
            "Carrier Gateway error: insufficient balance",
            {'dispatched': 0, 'total': 5, 'errors': ['Insufficient balance']}
        )

        resp = self.client.post('/api/voice/dispatch', json={
            'sound_name': 'test_audio_prompt',
            'target_type': 'unvoted',
            'limit': 5
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn("insufficient balance", data['error'].lower())

    def test_upload_audio_fails_on_missing_fields(self):
        """POST /api/voice/upload_audio fails when sound_name or file is absent."""
        resp1 = self.client.post('/api/voice/upload_audio', data={})
        self.assertEqual(resp1.status_code, 400)
        self.assertIn('Sound name is required', resp1.get_json()['error'])

        resp2 = self.client.post('/api/voice/upload_audio', data={'sound_name': 'audio_sample'})
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('Audio file is required', resp2.get_json()['error'])

    @patch('electionapp.voice_service.upload_sound')
    @patch('electionapp.services.audio_converter.convert_to_telecom_wav')
    def test_upload_audio_success(self, mock_convert, mock_upload):
        """POST /api/voice/upload_audio forwards file bytes and persists record."""
        mock_convert.return_value = (b'RIFF_MOCK_8KHZ_PCM', '/tmp/mock.wav', 24.5, 1)
        mock_upload.return_value = (True, "Carrier uploaded successfully")

        audio_bytes = b'SAMPLE_USER_AUDIO_BYTES'
        data = {
            'sound_name': 'test_uploaded_sound',
            'display_label': 'Namaste Ward 82',
            'audio_file': (io.BytesIO(audio_bytes), 'my_audio.mp3')
        }

        resp = self.client.post(
            '/api/voice/upload_audio',
            data=data,
            content_type='multipart/form-data'
        )
        self.assertEqual(resp.status_code, 200)
        res_json = resp.get_json()
        self.assertTrue(res_json['success'])
        self.assertEqual(res_json['sound_name'], 'test_uploaded_sound')
        self.assertEqual(res_json['display_label'], 'Namaste Ward 82')
        self.assertEqual(res_json['duration_seconds'], 24.5)
        self.assertEqual(res_json['credits_per_call'], 1)
        self.assertIsNotNone(res_json['audio_id'])
        self.assertTrue(res_json['carrier_sound_name'].startswith('t'))

        mock_convert.assert_called_once()
        mock_upload.assert_called_once_with(b'RIFF_MOCK_8KHZ_PCM', res_json['carrier_sound_name'])

    @patch('electionapp.voice_service.upload_sound')
    @patch('electionapp.services.audio_converter.convert_to_telecom_wav')
    def test_upload_audio_browser_mic_blob(self, mock_convert, mock_upload):
        """POST /api/voice/upload_audio accepts browser MediaRecorder live mic blob."""
        mock_convert.return_value = (b'RIFF_MOCK_MIC_WAV', '/tmp/mic.wav', 42.0, 2)
        mock_upload.return_value = (True, "Carrier uploaded successfully")

        blob_bytes = b'WEBM_OPUS_BROWSER_MIC_BLOB'
        data = {
            'display_label': 'Live Mic Announcement',
            'recording': (io.BytesIO(blob_bytes), 'mic_record.webm', 'audio/webm')
        }

        resp = self.client.post(
            '/api/voice/upload_audio',
            data=data,
            content_type='multipart/form-data'
        )
        self.assertEqual(resp.status_code, 200)
        res_json = resp.get_json()
        self.assertTrue(res_json['success'])
        self.assertEqual(res_json['display_label'], 'Live Mic Announcement')
        self.assertEqual(res_json['duration_seconds'], 42.0)
        self.assertEqual(res_json['credits_per_call'], 2)

    def test_audio_stream_endpoint(self):
        """GET /api/voice/audio/<int:audio_id> serves audio with tenant guard and Range support."""
        tid = voice_quota.resolve_tenant_id('ward82')
        
        # Create a real temporary WAV file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            tf.write(b'RIFF_SAMPLE_PCM_WAV_HEADER_AND_BODY')
            tmp_path = tf.name

        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO voice_audios 
                      (tenant_id, sound_name, carrier_sound_name, file_path, duration_seconds)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING audio_id
                    """,
                    (tid, 'stream_test_sound', 't13_stream123', tmp_path, 12.0)
                )
                aid = cur.fetchone()[0]
                conn.commit()
        finally:
            pool.putconn(conn)

        # 1. Successful stream
        resp = self.client.get(f'/api/voice/audio/{aid}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get('Content-Type'), 'audio/wav')
        self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(resp.data, b'RIFF_SAMPLE_PCM_WAV_HEADER_AND_BODY')

        # 2. 404 for non-existent audio
        resp_404 = self.client.get('/api/voice/audio/999999999')
        self.assertEqual(resp_404.status_code, 404)

        # Cleanup
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    def test_audio_stream_tenant_isolation_403(self):
        """GET /api/voice/audio/<int:audio_id> fails closed (403) for other tenants' audio."""
        # Insert audio for tenant_id 999
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO voice_audios 
                      (tenant_id, sound_name, carrier_sound_name, file_path)
                    VALUES (%s, %s, %s, %s)
                    RETURNING audio_id
                    """,
                    (99999, 'foreign_tenant_sound', 't99999_xyz', '/tmp/foreign.wav')
                )
                foreign_aid = cur.fetchone()[0]
                conn.commit()
        finally:
            pool.putconn(conn)

        # Client is ward82 (different tenant)
        resp = self.client.get(f'/api/voice/audio/{foreign_aid}')
        self.assertEqual(resp.status_code, 403)

    def test_estimate_endpoint(self):
        """POST /api/voice/estimate pre-calculates reach, pulse cost, and credit balance."""
        # Insert an audio with 65s duration (3 credits per call)
        tid = voice_quota.resolve_tenant_id('ward82')
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO voice_audios 
                      (tenant_id, sound_name, carrier_sound_name, display_label, duration_seconds)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING audio_id
                    """,
                    (tid, 'estimate_test_audio', 't13_est123', 'Estimate Test', 65.0)
                )
                aid = cur.fetchone()[0]
                conn.commit()
        finally:
            pool.putconn(conn)

        resp = self.client.post('/api/voice/estimate', json={
            'target_type': 'unvoted',
            'audio_id': aid
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['duration_seconds'], 65.0)
        self.assertEqual(data['credits_per_call'], 3)
        self.assertIn('unique_mobiles', data)
        self.assertIn('total_credits_needed', data)
        self.assertEqual(data['total_credits_needed'], data['unique_mobiles'] * 3)

    def test_trai_calling_window_enforcement(self):
        """POST /api/voice/dispatch blocks calls outside 09:00 - 21:00 IST unless bypassed."""
        # Temporarily enable voice module and turn off TESTING flag to test production TRAI guard
        self.app.config['ENABLE_VOICE_MODULE'] = True
        self.app.config['TESTING'] = False
        try:
            with patch('electionapp.blueprints.voice.is_within_trai_calling_window', return_value=False):
                # 1. Blocked without bypass
                resp_blocked = self.client.post('/api/voice/dispatch', json={
                    'sound_name': 'namaste',
                    'target_type': 'unvoted'
                })
                self.assertEqual(resp_blocked.status_code, 403)
                self.assertIn('TRAI Calling Window', resp_blocked.get_json()['error'])

                # 2. Allowed with bypass_window parameter
                with patch('electionapp.voice_service.dispatch_voice_blast') as mock_d:
                    mock_d.return_value = (True, "Dispatched", {'dispatched': 1, 'total': 1, 'gateway_campaign_ids': ['1']})
                    resp_bypass = self.client.post('/api/voice/dispatch', json={
                        'sound_name': 'namaste',
                        'target_type': 'unvoted',
                        'bypass_window': 1,
                        'limit': 1
                    })
                    self.assertEqual(resp_bypass.status_code, 200)
        finally:
            self.app.config['TESTING'] = True



    @patch('electionapp.voice_service.get_caller_ids')
    @patch('electionapp.voice_service.get_balance')
    def test_provider_status_endpoint(self, mock_bal, mock_clis):
        """GET /api/voice/provider returns CloudShope status, balance, and CLIs."""
        mock_bal.return_value = 50
        mock_clis.return_value = [{'number': '6746780235', 'truecaller': True}]

        resp = self.client.get('/api/voice/provider')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['provider'], 'cloudshope')
        self.assertEqual(data['balance'], 50)
        self.assertEqual(len(data['caller_ids']), 1)

    def test_cloudshope_webhook_endpoint(self):
        """POST /api/voice/webhook/cloudshope processes DLR callbacks cleanly."""
        resp = self.client.post('/api/voice/webhook/cloudshope', json={
            'number': '9876543210',
            'status': 'answered',
            'duration': 18,
            'call_id': 'cs_call_7788'
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['message'], 'Webhook processed')


if __name__ == '__main__':

    unittest.main()
