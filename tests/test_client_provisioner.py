"""
test_client_provisioner.py
Unit & Integration Tests for:
1. Fuzzy NLP & Phonetic Gram Panchayat Resolution (scripts/client_provisioner.py)
2. Auto-Discovery API (/api/admin/rajasthan/resolve)
3. End-to-End Client Provisioning Engine
"""

import os
import sys
import unittest
import json

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from scripts import client_provisioner
from electionapp import create_app
import user_manager


class ClientProvisionerTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ['VOTER_CORE_MODE'] = '0'
        cls.app = create_app(tenant_slug='ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def test_01_phonetic_skeleton_normalization(self):
        """Verify Hindi & Rajasthani dialect phonetic normalizations."""
        self.assertEqual(client_provisioner.to_phonetic_skeleton('आलड़ी'), 'ld')
        self.assertEqual(client_provisioner.to_phonetic_skeleton('Aladi'), 'ld')
        self.assertEqual(client_provisioner.to_phonetic_skeleton('Aldi'), 'ld')
        self.assertEqual(client_provisioner.to_phonetic_skeleton('जालौर'), 'jld')
        self.assertEqual(client_provisioner.to_phonetic_skeleton('Jalore'), 'jld')

    def test_02_resolve_aladi_from_conversational_hindi(self):
        """Verify natural Hindi query resolves to Gram Panchayat Aldi in Raniwara PS, Jalore."""
        query = "got a new client आलड़ी (Aladi / Alri) भारत के राजस्थान राज्य के जालौर जिले की रानीवाड़ा तहसील"
        res = client_provisioner.resolve_client_area(query)

        self.assertEqual(res['district'], 'JALORE')
        self.assertIn('RANIWARA', res['block'].upper())
        self.assertEqual(res['gp_name'], 'Aldi')
        self.assertEqual(res['suggested_slug'], 'aldi')
        self.assertGreaterEqual(res['preview']['total_voters'], 4700)
        self.assertEqual(res['preview']['ward_count'], 13)
        self.assertEqual(len(res['preview']['booths']), 5)

    def test_03_resolve_ajaysar_from_english(self):
        """Verify English input 'Ajaysar Ajmer' resolves correctly."""
        res = client_provisioner.resolve_client_area("Ajaysar Ajmer")
        self.assertEqual(res['district'], 'AJMER')
        self.assertEqual(res['gp_name'], 'Ajaysar')
        self.assertGreaterEqual(res['preview']['total_voters'], 4000)

    def test_04_api_resolve_endpoint(self):
        """Verify GET /api/admin/rajasthan/resolve returns structured resolution metadata."""
        resp = self.client.get('/api/admin/rajasthan/resolve?q=Aladi%20Jalore%20Raniwara')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['district'], 'JALORE')
        self.assertIn('RANIWARA', data['block'])
        self.assertEqual(data['gp_name'], 'Aldi')
        self.assertGreaterEqual(data['preview']['total_voters'], 4700)

    def test_05_resolve_aliased_gp(self):
        """Verify colloquial 'Adarsh Sondi' resolves to canonical 'sonadi' with electors."""
        res = client_provisioner.resolve_client_area('', district='BARMER', block='SEDWA PANCHAYAT SAMITI', gp='Adarsh Sondi')
        self.assertEqual(res['district'], 'BARMER')
        self.assertEqual(res['gp_name'], 'sonadi')
        self.assertGreaterEqual(res['preview']['total_voters'], 1400)
        self.assertTrue(res['preview']['has_records'])


if __name__ == '__main__':
    unittest.main()
