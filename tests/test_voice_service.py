"""
Tests for electionapp/voice_service.py & electionapp/services/cloudshope_voice.py:
Carrier gateway client for CloudShope Voice & Outbound Dialer (OBD) Platform.

Covers:
1. extract_clean_mobiles():
   - Indian mobile format normalization: +91, 91, leading 0, spaces, dashes, brackets.
   - Starting digit validation (6, 7, 8, 9).
   - Rejecting non-numeric, wrong length, or invalid starting digits.
2. deduplicate_voter_mobiles():
   - Groups voters sharing phone numbers into 1 call per household.
   - Attaches 'clean_mobile'.
   - Ignores voters with invalid or missing phones.
3. CloudShopeClient unit tests:
   - Token acquisition & automatic token caching.
   - Live wallet balance.
   - Outbound Caller IDs (CLIs).
   - Sound list parsing (JSON dict/list, durations, credits).
   - Audio file upload returning file_id.
   - Campaign dispatch & error handling.
4. dispatch_voice_blast():
   - 500-recipient chunking.
   - Two-phase quota reservation and settlement.
   - Multi-chunk dispatch via CloudShope.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import voice_service, voice_quota
from electionapp.services.cloudshope_voice import CloudShopeClient


class TestExtractCleanMobiles(unittest.TestCase):
    """Normalization of raw phone strings to canonical 10-digit Indian numbers."""

    def test_standard_ten_digit_numbers(self):
        for num in ('9876543210', '8123456789', '7000000001', '6999999999'):
            self.assertEqual(voice_service.extract_clean_mobiles(num), num)

    def test_plus_91_prefix(self):
        self.assertEqual(voice_service.extract_clean_mobiles('+919876543210'), '9876543210')
        self.assertEqual(voice_service.extract_clean_mobiles('+91 98765 43210'), '9876543210')
        self.assertEqual(voice_service.extract_clean_mobiles('+91-98765-43210'), '9876543210')

    def test_ninety_one_prefix(self):
        self.assertEqual(voice_service.extract_clean_mobiles('919876543210'), '9876543210')
        self.assertEqual(voice_service.extract_clean_mobiles('91 9876543210'), '9876543210')

    def test_leading_zero(self):
        self.assertEqual(voice_service.extract_clean_mobiles('09876543210'), '9876543210')
        self.assertEqual(voice_service.extract_clean_mobiles('0 9876543210'), '9876543210')

    def test_punctuation_and_whitespace(self):
        self.assertEqual(voice_service.extract_clean_mobiles('(9876) 543-210'), '9876543210')
        self.assertEqual(voice_service.extract_clean_mobiles('  98765 43210  '), '9876543210')
        self.assertEqual(voice_service.extract_clean_mobiles('98.76.54.32.10'), '9876543210')

    def test_invalid_starting_digits(self):
        for invalid in ('5123456789', '4123456789', '3123456789', '2123456789', '1123456789', '0123456789'):
            self.assertIsNone(voice_service.extract_clean_mobiles(invalid))

    def test_invalid_lengths_and_empty(self):
        for bad in ('', '   ', None, '123', '98765', '9876543210123', 'abcdefghij', 'N/A', 'nil'):
            self.assertIsNone(voice_service.extract_clean_mobiles(bad))


class TestDeduplicateVoterMobiles(unittest.TestCase):
    """Deduplication ensures only one broadcast call per household mobile number."""

    def test_family_household_deduplication(self):
        voters = [
            {'voter_id': 1, 'name': 'Ram Sharma', 'phone': '9876543210', 'house_no': '12'},
            {'voter_id': 2, 'name': 'Sita Sharma', 'phone': '+919876543210', 'house_no': '12'},
            {'voter_id': 3, 'name': 'Laxman Sharma', 'mobile': '09876543210', 'house_no': '12'},
            {'voter_id': 4, 'name': 'Bharat Singh', 'contact_phone': '9123456789', 'house_no': '14'},
        ]
        deduped = voice_service.deduplicate_voter_mobiles(voters)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]['voter_id'], 1)
        self.assertEqual(deduped[0]['clean_mobile'], '9876543210')
        self.assertEqual(deduped[1]['voter_id'], 4)
        self.assertEqual(deduped[1]['clean_mobile'], '9123456789')

    def test_filters_out_invalid_or_missing_phones(self):
        voters = [
            {'voter_id': 10, 'name': 'No Phone'},
            {'voter_id': 11, 'name': 'Invalid Phone', 'phone': '12345'},
            {'voter_id': 12, 'name': 'Valid Phone', 'mobile_no': '9876543210'},
        ]
        deduped = voice_service.deduplicate_voter_mobiles(voters)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]['voter_id'], 12)
        self.assertEqual(deduped[0]['clean_mobile'], '9876543210')

    def test_empty_list(self):
        self.assertEqual(voice_service.deduplicate_voter_mobiles([]), [])


class TestCloudShopeClient(unittest.TestCase):
    """Test REST API interactions for CloudShope voice client."""

    def setUp(self):
        self.client = CloudShopeClient(
            base_url="https://cloudshope.com/api/v1",
            username="test_user",
            password="test_password",
            api_token="",
            default_cli="6746780235"
        )

    @patch('requests.Session.post')
    def test_token_acquisition_and_caching(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "data": {"accessToken": "fake_jwt_token_123"}
        }
        mock_post.return_value = mock_resp

        # First call fetches token
        token1 = self.client.get_token()
        self.assertEqual(token1, "fake_jwt_token_123")
        self.assertEqual(mock_post.call_count, 1)

        # Second call uses cached token
        token2 = self.client.get_token()
        self.assertEqual(token2, "fake_jwt_token_123")
        self.assertEqual(mock_post.call_count, 1)

    @patch('requests.Session.request')
    def test_get_wallet_balance(self, mock_request):
        self.client._token = "valid_token"
        self.client._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"credit_type": "VOICE_CALL", "balance": 50}]
        }
        mock_request.return_value = mock_resp

        balance = self.client.get_wallet_balance()
        self.assertEqual(balance, 50)
        self.assertIn("/wallet/balance", mock_request.call_args[0][1])

    @patch('requests.Session.request')
    def test_get_caller_ids(self, mock_request):
        self.client._token = "valid_token"
        self.client._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"number": "6746782549", "default": "1", "truecaller": "0"},
                {"number": "6746780235", "default": "1", "truecaller": "1"}
            ]
        }
        mock_request.return_value = mock_resp

        clis = self.client.get_caller_ids()
        self.assertEqual(len(clis), 2)
        self.assertEqual(clis[0]["number"], "6746782549")
        self.assertTrue(clis[1]["truecaller"])

    @patch('requests.Session.request')
    def test_get_sound_list(self, mock_request):
        self.client._token = "valid_token"
        self.client._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "4": {
                    "id": 4,
                    "name": "WelcomeClip.wav",
                    "credit": 1,
                    "duration": 7,
                    "audio_voice": "<audio controls><source src=https://media.cloudshope.com/recordings/1/4.wav type=audio/mp3></audio>"
                }
            }
        }
        mock_request.return_value = mock_resp

        sounds = self.client.get_sound_list()
        self.assertEqual(len(sounds), 1)
        self.assertEqual(sounds[0]["id"], 4)
        self.assertEqual(sounds[0]["sound_name"], "WelcomeClip.wav")
        self.assertEqual(sounds[0]["duration_seconds"], 7)
        self.assertEqual(sounds[0]["url"], "https://media.cloudshope.com/recordings/1/4.wav")

    @patch('requests.Session.request')
    def test_upload_sound_success(self, mock_request):
        self.client._token = "valid_token"
        self.client._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "file_id": 42
        }
        mock_request.return_value = mock_resp

        ok, msg, file_id = self.client.upload_sound(b'RIFF_SAMPLE', "speech_clip")
        self.assertTrue(ok)
        self.assertEqual(file_id, 42)

    @patch('requests.Session.request')
    def test_upload_sound_from_file_path(self, mock_request):
        self.client._token = "valid_token"
        self.client._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"file_id": 99}
        mock_request.return_value = mock_resp

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tf:
            tf.write(b'RIFF_DATA')
            tf_path = tf.name

        try:
            ok, msg, file_id = self.client.upload_sound(tf_path, "local_file")
            self.assertTrue(ok)
            self.assertEqual(file_id, 99)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    @patch('requests.Session.request')
    def test_dispatch_campaign_success(self, mock_request):
        self.client._token = "valid_token"
        self.client._token_expires_at = 9999999999.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "campaignId": "camp_8892"
        }
        mock_request.return_value = mock_resp

        ok, msg, camp_id = self.client.dispatch_campaign(
            campaign_name="Test OBD",
            voice_file_id=4,
            numbers=["9876543210", "9123456789"]
        )
        self.assertTrue(ok)
        self.assertEqual(camp_id, "camp_8892")
        sent_json = mock_request.call_args[1]["json"]
        self.assertEqual(sent_json["voice_file_id"], "4")
        self.assertEqual(sent_json["number"], "9876543210,9123456789")


class TestDispatchVoiceBlast(unittest.TestCase):
    """Test chunking, quota integration, and gateway payload formatting."""

    def test_dispatch_fails_with_no_valid_numbers(self):
        ok, msg, stats = voice_service.dispatch_voice_blast('ward82', ['123', 'invalid'], 'audio_1')
        self.assertFalse(ok)
        self.assertIn('No valid mobile numbers', msg)
        self.assertEqual(stats['dispatched'], 0)

    @patch('electionapp.voice_quota.reserve')
    def test_dispatch_fails_closed_when_quota_exhausted(self, mock_reserve):
        mock_reserve.side_effect = voice_quota.QuotaExhausted("No quota left")
        ok, msg, stats = voice_service.dispatch_voice_blast('ward82', ['9876543210'], 'audio_1')
        self.assertFalse(ok)
        self.assertIn("No quota left", msg)
        self.assertEqual(stats.get('error'), 'quota_exhausted')

    @patch.object(CloudShopeClient, 'dispatch_campaign')
    @patch('electionapp.voice_quota.reserve')
    def test_dispatch_success_chunking_and_settlement(self, mock_reserve, mock_dispatch):
        # Prepare 550 distinct valid mobile numbers
        numbers = [f"98765{i:05d}" for i in range(550)]

        fake_resv = MagicMock()
        fake_resv.spend = MagicMock(return_value=True)
        fake_resv.close = MagicMock()
        mock_reserve.return_value = fake_resv

        mock_dispatch.return_value = (True, "Campaign created", "cs_1001")

        ok, msg, stats = voice_service.dispatch_voice_blast(
            tenant_slug='ward82',
            numbers_list=numbers,
            sound_name='4',
            campaign_name='Ward82 Broadcast'
        )

        self.assertTrue(ok)
        self.assertEqual(stats['dispatched'], 550)
        self.assertEqual(stats['total'], 550)

        # Quota reserved for 550 recipients
        mock_reserve.assert_called_once_with('ward82', 550)

        # Split into 2 chunks: chunk 1 (500) and chunk 2 (50)
        self.assertEqual(mock_dispatch.call_count, 2)

        # Verified chunk 1 call
        chunk1_call = mock_dispatch.call_args_list[0]
        self.assertEqual(len(chunk1_call[1]['numbers']), 500)
        self.assertEqual(chunk1_call[1]['voice_file_id'], 4)

        # Verified chunk 2 call
        chunk2_call = mock_dispatch.call_args_list[1]
        self.assertEqual(len(chunk2_call[1]['numbers']), 50)

        # Reservation closed / settled
        fake_resv.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
