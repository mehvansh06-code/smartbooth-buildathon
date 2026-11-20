import unittest
from unittest.mock import MagicMock, patch
from electionapp.config import is_ward_campaign, is_panchayat_campaign, get_cached_ward_hierarchy, clear_ward_hierarchy_cache
from electionapp.repo.postgres_repo import PostgresVoterRepository
from electionapp import create_app

class TestWardHierarchy(unittest.TestCase):
    def setUp(self):
        clear_ward_hierarchy_cache()

    def test_is_ward_campaign_classification(self):
        # Local body / Ward elections -> True
        self.assertTrue(is_ward_campaign({'election_type': 'PANCHAYAT'}))
        self.assertTrue(is_ward_campaign({'election_type': 'GRAM_PANCHAYAT'}))
        self.assertTrue(is_ward_campaign({'election_type': 'SARPANCH'}))
        self.assertTrue(is_ward_campaign({'election_type': 'ZILA_PARISHAD'}))
        self.assertTrue(is_ward_campaign({'election_type': 'ZP'}))
        self.assertTrue(is_ward_campaign({'election_type': 'PANCHAYAT_SAMITI'}))
        self.assertTrue(is_ward_campaign({'election_type': 'PS'}))
        self.assertTrue(is_ward_campaign({'election_type': 'MUNICIPAL'}))
        self.assertTrue(is_ward_campaign({'election_type': 'NAGAR_PALIKA'}))
        self.assertTrue(is_ward_campaign({'election_type': 'NAGAR_PARISHAD'}))
        self.assertTrue(is_ward_campaign({'election_type': 'NAGAR_NIGAM'}))
        self.assertTrue(is_ward_campaign({'election_type': 'WARD'}))

        # Assembly / Lok Sabha elections -> False
        self.assertFalse(is_ward_campaign({'election_type': 'ASSEMBLY'}))
        self.assertFalse(is_ward_campaign({'election_type': 'VIDHAN_SABHA'}))
        self.assertFalse(is_ward_campaign({'election_type': 'PARLIAMENTARY'}))
        self.assertFalse(is_ward_campaign({'election_type': 'LOK_SABHA'}))

        # Hindi strings
        self.assertTrue(is_ward_campaign({'election': {'ward_name_hi': 'ग्राम पंचायत सारणा'}}))
        self.assertTrue(is_ward_campaign({'election': {'ward_name_hi': 'जिला परिषद वार्ड 23'}}))
        self.assertFalse(is_ward_campaign({'election': {'ward_name_hi': '144 - सांचौर विधानसभा क्षेत्र'}}))
        self.assertFalse(is_ward_campaign({'election': {'ward_name_hi': '144 - सांचौर विधान सभा क्षेत्र'}}))

        # is_panchayat_campaign delegates properly
        self.assertTrue(is_panchayat_campaign({'election_type': 'ZILA_PARISHAD'}))
        self.assertFalse(is_panchayat_campaign({'election_type': 'ASSEMBLY'}))

    def test_get_cached_ward_hierarchy(self):
        mock_repo = MagicMock()
        mock_repo.get_wards_dict.return_value = {
            "1": {"ward_no": "1", "voter_count": 150},
            "2": {"ward_no": "2", "voter_count": 200}
        }
        mock_repo.get_booths_with_wards.return_value = {
            "79": {
                "booth_no": "79",
                "booth_name": "राउमावि सारणा (पूर्वी भाग)",
                "wards": [
                    {"ward_no": "1", "voter_count": 150},
                    {"ward_no": "2", "voter_count": 200}
                ]
            }
        }

        cfg = {'election_type': 'PANCHAYAT'}
        wards, booths_with_wards = get_cached_ward_hierarchy(mock_repo, 'sarana', cfg)

        self.assertEqual(len(wards), 2)
        self.assertIn("79", booths_with_wards)
        self.assertEqual(len(booths_with_wards["79"]["wards"]), 2)
        self.assertEqual(mock_repo.get_wards_dict.call_count, 1)
        self.assertEqual(mock_repo.get_booths_with_wards.call_count, 1)

        # Call again - must be served from cache without calling repo again
        wards2, booths_with_wards2 = get_cached_ward_hierarchy(mock_repo, 'sarana', cfg)
        self.assertEqual(mock_repo.get_wards_dict.call_count, 1)
        self.assertEqual(mock_repo.get_booths_with_wards.call_count, 1)

    def test_get_cached_ward_hierarchy_assembly_campaign(self):
        mock_repo = MagicMock()
        cfg = {'election_type': 'ASSEMBLY'}
        wards, booths_with_wards = get_cached_ward_hierarchy(mock_repo, 'sanchore', cfg)
        self.assertEqual(wards, {})
        self.assertEqual(booths_with_wards, {})
        self.assertEqual(mock_repo.get_wards_dict.call_count, 0)
        self.assertEqual(mock_repo.get_booths_with_wards.call_count, 0)

    def test_template_rendering_with_ward_hierarchy(self):
        app = create_app('ward82', 'postgres')
        app.config['TESTING'] = True
        client = app.test_client()

        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant'] = 'ward82'

        # index page (voter search) renders cleanly
        res = client.get('/search?tenant=ward82')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('id="partFilter"', html)

        # caste page renders cleanly
        res_caste = client.get('/caste?tenant=ward82')
        self.assertEqual(res_caste.status_code, 200)
        html_caste = res_caste.get_data(as_text=True)
        self.assertIn('id="castePartFilter"', html_caste)

        # turnout page renders cleanly
        res_turnout = client.get('/turnout?tenant=ward82')
        self.assertEqual(res_turnout.status_code, 200)
        html_turnout = res_turnout.get_data(as_text=True)
        self.assertIn('id="turnoutStatPct"', html_turnout)

        # reports page renders cleanly with repWardFilter
        res_reports = client.get('/reports?tenant=ward82')
        self.assertEqual(res_reports.status_code, 200)
        html_reports = res_reports.get_data(as_text=True)
        self.assertIn('id="repWardFilter"', html_reports)

        # family page renders cleanly with familyWardFilter
        res_family = client.get('/family?tenant=ward82')
        self.assertEqual(res_family.status_code, 200)
        html_family = res_family.get_data(as_text=True)
        self.assertIn('id="familyWardFilter"', html_family)

        # voting-day page renders cleanly
        res_vd = client.get('/voting-day?tenant=ward82')
        self.assertEqual(res_vd.status_code, 200)
        html_vd = res_vd.get_data(as_text=True)
        self.assertIn('vd-booth-tab', html_vd)

        # warroom page renders cleanly
        res_warroom = client.get('/warroom?tenant=ward82')
        self.assertEqual(res_warroom.status_code, 200)
        html_warroom = res_warroom.get_data(as_text=True)
        self.assertIn('wrBoothTableBody', html_warroom)

    def test_sarana_multi_ward_in_booth_rendering(self):
        app = create_app('sarana', 'postgres')
        app.config['TESTING'] = True
        client = app.test_client()

        with client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant'] = 'sarana'

        mock_wards = {
            '1': {'ward_no': '1', 'voter_count': 327, 'booth_no': '79', 'booths': [{'part_no': 79, 'booth_no': '79', 'voter_count': 327}]},
            '2': {'ward_no': '2', 'voter_count': 351, 'booth_no': '79', 'booths': [{'part_no': 79, 'booth_no': '79', 'voter_count': 351}]}
        }
        mock_booths = {
            '79': {
                'booth_no': '79',
                'booth_name': 'राउमावि सारणा',
                'wards': [{'ward_no': '1', 'voter_count': 327}, {'ward_no': '2', 'voter_count': 351}],
                'total_voters': 678
            }
        }

        with patch('electionapp.blueprints.core.get_cached_ward_hierarchy', return_value=(mock_wards, mock_booths)):
            # Index page has optgroup for wards and booths
            res = client.get('/search?tenant=sarana')
            self.assertEqual(res.status_code, 200)
            html = res.get_data(as_text=True)
            self.assertIn('वार्डवार (Ward-Wise)', html)
            self.assertIn('मतदान केंद्रवार (Polling Station / Booth-Wise)', html)
            self.assertIn('ward:1', html)

            # Turnout page GOTV selector has optgroup for wards and booths
            res_turnout = client.get('/turnout?tenant=sarana')
            self.assertEqual(res_turnout.status_code, 200)
            html_turnout = res_turnout.get_data(as_text=True)
            self.assertIn('वार्डवार (Ward-Wise)', html_turnout)
            self.assertIn('ward:1', html_turnout)

            # Reports page filter has optgroup for wards and booths
            res_reports = client.get('/reports?tenant=sarana')
            self.assertEqual(res_reports.status_code, 200)
            html_reports = res_reports.get_data(as_text=True)
            self.assertIn('वार्डवार (Ward-Wise)', html_reports)

    def test_postgres_get_wards_deduplication(self):
        repo = PostgresVoterRepository('sarana')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            (1, 79, "79 - School\ufffd Room 1", 120),
            (1, 80, "79 - School Room 1", 80),
        ]
        with patch.object(repo, 'get_connection', return_value=mock_conn), \
             patch.object(repo, '_resolve_tenant', return_value=(4, "sarana")):
            wards = repo.get_wards('sarana')
        self.assertEqual(len(wards), 1)
        w1 = wards[0]
        self.assertEqual(w1["ward_no"], 1)
        self.assertEqual(w1["booth_no"], "79")  # Strictly single booth_no, not "79, 79"
        self.assertNotIn("\ufffd", w1["booth_name"])
        self.assertEqual(len(w1["booths"]), 1)
        self.assertEqual(w1["booths"][0]["booth_no"], "79")
        self.assertEqual(w1["booths"][0]["voter_count"], 200)
        self.assertEqual(w1["voter_count"], 200)

    def test_postgres_get_booths_with_wards_deduplication(self):
        repo = PostgresVoterRepository('sarana')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchall.return_value = [
            (4, "80 - Girls\ufffd School", 4, 100),
            (80, "80 - Girls School", 4, 50),
            (4, "80 - Girls School", 5, 120),
            (80, "80 - Girls School", 5, 80),
        ]
        with patch.object(repo, 'get_connection', return_value=mock_conn), \
             patch.object(repo, '_resolve_tenant', return_value=(4, "sarana")):
            booths = repo.get_booths_with_wards('sarana')
        self.assertIn("80", booths)
        self.assertEqual(len(booths), 1)
        b80 = booths["80"]
        self.assertEqual(b80["booth_no"], "80")
        self.assertNotIn("\ufffd", b80["booth_name"])
        self.assertEqual(len(b80["wards"]), 2)
        self.assertEqual(b80["wards"][0]["ward_no"], 4)
        self.assertEqual(b80["wards"][0]["voter_count"], 150)
        self.assertEqual(b80["wards"][1]["ward_no"], 5)
        self.assertEqual(b80["wards"][1]["voter_count"], 200)
        self.assertEqual(b80["total_voters"], 350)

    def test_get_cached_ward_hierarchy_fallback_booth_no_deduplication(self):
        mock_repo = MagicMock()
        mock_repo.get_wards_dict.return_value = {}  # Trigger single-ward fallback
        mock_repo.get_booths_dict.return_value = {
            "1": {"booth_no": "79", "name": "Booth 79 Part 1", "voter_count": 100},
            "2": {"booth_no": "79", "name": "Booth 79 Part 2", "voter_count": 120},
        }
        cfg = {
            'election_type': 'PANCHAYAT',
            'election': {'ward_no': '5'}
        }
        wards, booths_with_wards = get_cached_ward_hierarchy(mock_repo, 'test_fallback', cfg)
        self.assertIn('5', wards)
        self.assertEqual(wards['5']['booth_no'], '79')  # Deduplicated from "79, 79"

if __name__ == '__main__':
    unittest.main()
