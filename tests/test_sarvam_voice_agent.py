"""
tests/test_sarvam_voice_agent.py
Tests for Sarvam Voice Agents (Samvaad) Platform Integration Service.
"""

import unittest
from unittest.mock import patch, MagicMock
from electionapp.services.sarvam_voice_agent import (
    get_interactions,
    get_transcript,
    get_recording_url,
    trigger_instant_outbound_call
)


class TestSarvamVoiceAgentService(unittest.TestCase):

    @patch("electionapp.services.sarvam_voice_agent.requests.get")
    def test_get_interactions_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [{"interaction_id": "test_id_123", "duration_in_seconds": 15.0}]
        }
        mock_get.return_value = mock_resp

        results = get_interactions()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["interaction_id"], "test_id_123")

    @patch("electionapp.services.sarvam_voice_agent.requests.get")
    def test_get_transcript_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "messages": [
                {"turn_id": 1, "role": "assistant", "content": "नमस्ते"},
                {"turn_id": 2, "role": "user", "content": "हाँजी"}
            ]
        }
        mock_get.return_value = mock_resp

        msgs = get_transcript("test_id_123")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "नमस्ते")

    @patch("electionapp.services.sarvam_voice_agent.requests.post")
    def test_trigger_outbound_call(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"attempt_id": "attempt_999"}
        mock_post.return_value = mock_resp

        res = trigger_instant_outbound_call(
            user_phone_number="+919829012345",
            connection_id="conn_123",
            agent_phone_number="+919876543210"
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["attempt_id"], "attempt_999")


if __name__ == "__main__":
    unittest.main()
