"""
tests/test_voice_agent.py
Unit tests for SmartBooth Voice Sales Agent Endpoints (electionapp/blueprints/voice_agent.py)
Interfacing with Sarvam Voice Agents (Samvaad) & Genie copilot.
"""

import json
import unittest
from unittest.mock import patch, MagicMock

from electionapp import create_app


class TestVoiceAgentEndpoints(unittest.TestCase):
    """Test suite for SmartBooth voice sales agent endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_coverage_missing_params_fails(self):
        """GET /api/voice_agent/coverage returns 400 when district or gp is absent."""
        resp = self.client.get('/api/voice_agent/coverage')
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['ok'])
        self.assertIn("required", data['error'])

    def test_coverage_success(self):
        """GET /api/voice_agent/coverage returns structured voter stats and Hindi speech summary."""
        resp = self.client.get('/api/voice_agent/coverage?district=SANCHORE&gp=CHHOTI+VIROL')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertIn('total_voters', data)
        self.assertIn('verified_mobiles', data)
        self.assertIn('reach_pct', data)
        self.assertIn('speech_summary', data)
        self.assertIn('मतदाता', data['speech_summary'])

    def test_send_demo_invalid_phone_fails(self):
        """POST /api/voice_agent/send_demo rejects invalid phone numbers."""
        resp = self.client.post('/api/voice_agent/send_demo', json={
            "phone": "12345",
            "candidate_name": "Test Candidate"
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['ok'])
        self.assertIn("Invalid", data['error'])

    def test_send_demo_success(self):
        """POST /api/voice_agent/send_demo successfully schedules WhatsApp demo slip dispatch."""
        resp = self.client.post('/api/voice_agent/send_demo', json={
            "phone": "9829012345",
            "candidate_name": "हरचंद राम",
            "election_type": "Sarpanch",
            "district": "SANCHORE",
            "gp": "CHHOTI VIROL"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['phone'], '9829012345')
        self.assertIn('speech_summary', data)
        self.assertIn('व्हाट्सएप', data['speech_summary'])

    def test_post_call_summary_webhook(self):
        """POST /api/voice_agent/call_summary logs Sarvam post-call webhook data."""
        payload = {
            "call_id": "sarvam_call_test_001",
            "phone": "9829012345",
            "duration": 145,
            "disposition": "INTERESTED",
            "transcript": "नमस्ते, मुझे डेमो पर्ची मिल गई है और बहुत अच्छी लगी।",
            "summary": "Candidate interested in full Panchayat digital campaign.",
            "context": {
                "candidate_name": "हरचंद राम",
                "district": "SANCHORE",
                "gram_panchayat": "CHHOTI VIROL"
            }
        }
        resp = self.client.post('/api/voice_agent/call_summary', json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['logged'])
        self.assertEqual(data['call_id'], "sarvam_call_test_001")

    def test_get_candidate_leads(self):
        """GET /api/voice_agent/leads returns lead cohorts for outbound dialing."""
        resp = self.client.get('/api/voice_agent/leads?limit=10')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertIn('leads', data)
        self.assertIn('count', data)


if __name__ == '__main__':
    unittest.main()
