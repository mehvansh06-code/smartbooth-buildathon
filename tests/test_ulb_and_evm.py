# -*- coding: utf-8 -*-
"""
tests/test_ulb_and_evm.py
=========================
Verifies:
1. Strict voting method classification (Panchayat is BALLOT_PAPER; Panchayat Samiti, Zila Parishad, Municipal, Assembly are EVM).
2. Rajasthan SEC ULB Catalog indexer (Nagar Palika, Nagar Parishad, Nagar Nigam across Rajasthan).
3. Admin REST API endpoint for ULBs (/api/admin/rajasthan/ulbs).
"""

import unittest
from electionapp.config import get_voting_method, BALLOT_PAPER_ELECTION_TYPES, EVM_ELECTION_TYPES
from electionapp import rajasthan_catalog
from electionapp import create_app


class TestULBAndEVMClassification(unittest.TestCase):

    def test_voting_method_classification(self):
        # Only Gram Panchayat elections use paper ballots
        self.assertEqual(get_voting_method('PANCHAYAT'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('GRAM_PANCHAYAT'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('SARPANCH'), 'BALLOT_PAPER')
        self.assertEqual(get_voting_method('WARD_PANCH'), 'BALLOT_PAPER')

        # Panchayat Samiti and Zila Parishad elections are 100% EVM
        self.assertEqual(get_voting_method('PANCHAYAT_SAMITI'), 'EVM')
        self.assertEqual(get_voting_method('PS'), 'EVM')
        self.assertEqual(get_voting_method('ZILA_PARISHAD'), 'EVM')
        self.assertEqual(get_voting_method('ZP'), 'EVM')

        # Municipal and Assembly elections are EVM
        self.assertEqual(get_voting_method('MUNICIPAL'), 'EVM')
        self.assertEqual(get_voting_method('NAGAR_PALIKA'), 'EVM')
        self.assertEqual(get_voting_method('NAGAR_PARISHAD'), 'EVM')
        self.assertEqual(get_voting_method('NAGAR_NIGAM'), 'EVM')
        self.assertEqual(get_voting_method('ASSEMBLY'), 'EVM')
        self.assertEqual(get_voting_method('VIDHAN_SABHA'), 'EVM')

    def test_ballot_and_evm_constants(self):
        self.assertIn('PANCHAYAT', BALLOT_PAPER_ELECTION_TYPES)
        self.assertIn('SARPANCH', BALLOT_PAPER_ELECTION_TYPES)
        self.assertNotIn('PANCHAYAT_SAMITI', BALLOT_PAPER_ELECTION_TYPES)
        self.assertNotIn('PS', BALLOT_PAPER_ELECTION_TYPES)

        self.assertIn('PANCHAYAT_SAMITI', EVM_ELECTION_TYPES)
        self.assertIn('PS', EVM_ELECTION_TYPES)
        self.assertIn('ZILA_PARISHAD', EVM_ELECTION_TYPES)
        self.assertIn('MUNICIPAL', EVM_ELECTION_TYPES)

    def test_rajasthan_ulb_catalog(self):
        # Test Jalore ULBs
        jalore_ulbs = rajasthan_catalog.get_ulbs('JALORE')
        self.assertGreaterEqual(len(jalore_ulbs), 4)
        names = [u['body_name'] for u in jalore_ulbs]
        self.assertIn('SANCHORE NAGAR PALIKA', names)
        self.assertIn('BHINMAL NAGAR PALIKA', names)
        self.assertIn('JALORE NAGAR PARISHAD', names)

        # Test Jaipur ULBs
        jaipur_ulbs = rajasthan_catalog.get_ulbs('JAIPUR')
        self.assertGreaterEqual(len(jaipur_ulbs), 10)
        jaipur_names = [u['body_name'] for u in jaipur_ulbs]
        self.assertTrue(any('JAIPUR' in n and 'NIGAM' in n for n in jaipur_names))

        # Test Chittorgarh ULBs
        chittor_ulbs = rajasthan_catalog.get_ulbs('CHITTORGARH')
        self.assertGreaterEqual(len(chittor_ulbs), 5)
        chittor_names = [u['body_name'] for u in chittor_ulbs]
        self.assertIn('NIMBAHERA NAGAR PARISHAD', chittor_names)
        self.assertIn('CHITTORGARH NAGAR PARISHAD', chittor_names)

        # Test get_ulb_by_name lookup
        sanchore_ulb = rajasthan_catalog.get_ulb_by_name('JALORE', 'Sanchore Nagar Palika')
        self.assertIsNotNone(sanchore_ulb)
        self.assertEqual(sanchore_ulb['body_name'], 'SANCHORE NAGAR PALIKA')
        self.assertEqual(sanchore_ulb['type'], 'NAGAR PALIKA')

    def test_admin_api_ulbs_endpoint(self):
        app = create_app()
        app.config['TESTING'] = True
        client = app.test_client()

        # Simulate admin session
        with client.session_transaction() as sess:
            sess['logged_in'] = True
            sess['username'] = 'admin'
            sess['role'] = 'admin'

        res = client.get('/api/admin/rajasthan/ulbs?district=JALORE')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['district'], 'JALORE')
        self.assertGreaterEqual(len(data['ulbs']), 4)
        ulb_names = [u['body_name'] for u in data['ulbs']]
        self.assertIn('SANCHORE NAGAR PALIKA', ulb_names)


if __name__ == '__main__':
    unittest.main()
