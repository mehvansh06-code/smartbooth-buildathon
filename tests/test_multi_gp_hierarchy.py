"""
test_multi_gp_hierarchy.py
Tests for Multi-Gram Panchayat structured hierarchy, caching, blueprint filters,
and search handling across SmartBooth.
"""

import unittest
from unittest.mock import MagicMock, patch
from electionapp.config import (
    get_cached_ward_hierarchy,
    get_cached_panchayat_hierarchy,
    clear_ward_hierarchy_cache,
    is_ward_campaign,
)
from electionapp.repo.postgres_repo import PostgresVoterRepository
from electionapp.blueprints.caste import _parse_location_filter as parse_caste_loc
from electionapp.blueprints.family import _parse_location_filter as parse_family_loc
from electionapp import create_app


class TestMultiGpHierarchy(unittest.TestCase):
    def setUp(self):
        clear_ward_hierarchy_cache()

    def test_get_panchayat_hierarchy_single_gp(self):
        """Single GP should return is_multi_gp: False with one GP entry."""
        repo = PostgresVoterRepository('chhoti_virol')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Simulating Chhoti Virol (single GP)
        mock_cur.fetchall.return_value = [
            (1, "35 - राजकीय उच्च प्राथमिक विद्यालय कमरा नं. 1 छोटी विरोल", 1, "Chhoti Virol", 311),
            (2, "35 - राजकीय उच्च प्राथमिक विद्यालय कमरा नं. 1 छोटी विरोल", 2, "Chhoti Virol", 237),
            (4, "36 - राजकीय उच्च प्राथमिक विद्यालय कमरा नं. 2 छोटी विरोल", 4, "Chhoti Virol", 444),
        ]

        with patch.object(repo, 'get_connection', return_value=mock_conn), \
             patch.object(repo, '_resolve_tenant', return_value=(27, "chhoti_virol")):
            hierarchy = repo.get_panchayat_hierarchy('chhoti_virol')

        self.assertFalse(hierarchy["is_multi_gp"])
        self.assertEqual(len(hierarchy["panchayats"]), 1)
        gp = hierarchy["panchayats"][0]
        self.assertEqual(gp["gp_name"], "Chhoti Virol")
        self.assertEqual(gp["total_voters"], 992)
        self.assertEqual(len(gp["wards"]), 3)
        self.assertEqual(gp["filter_value"], "parts:1,2,4")
        self.assertEqual(gp["wards"][0]["filter_value"], "part:1_ward:1")
        self.assertIn("वार्ड 1", gp["wards"][0]["display_name"])

    def test_get_panchayat_hierarchy_multi_gp_baytoo(self):
        """Multi-GP campaign (Baytoo Ward 1 / Jaswant Choudhary) with Lilala and Mahingoni."""
        repo = PostgresVoterRepository('baytoo_panchayat_samiti_w1_jasaw')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Simulating exact DB rows for Tenant 104
        mock_cur.fetchall.return_value = [
            (9, "9 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, दांया भाग", 1, "Lilala", 344),
            (9, "9 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, दांया भाग", 2, "Lilala", 137),
            (9, "9 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, दांया भाग", 3, "Lilala", 248),
            (10, "10 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, बांया भाग", 4, "Lilala", 231),
            (10, "10 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, बांया भाग", 5, "Lilala", 284),
            (10, "10 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, बांया भाग", 6, "Lilala", 203),
            (10, "10 - राजकीय उच्च माध्यमिक विद्यालय लीलाला, बांया भाग", 7, "Lilala", 367),
            (11, "11 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी दांया", 1, "Mahingoni Moodhon Ki Dhani", 260),
            (11, "11 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी दांया", 2, "Mahingoni Moodhon Ki Dhani", 160),
            (11, "11 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी दांया", 3, "Mahingoni Moodhon Ki Dhani", 180),
            (12, "12 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी बांया", 4, "Mahingoni Moodhon Ki Dhani", 196),
            (12, "12 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी बांया", 5, "Mahingoni Moodhon Ki Dhani", 106),
            (12, "12 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी बांया", 6, "Mahingoni Moodhon Ki Dhani", 296),
            (12, "12 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी मुढो की ढाणी बांया", 7, "Mahingoni Moodhon Ki Dhani", 151),
        ]

        with patch.object(repo, 'get_connection', return_value=mock_conn), \
             patch.object(repo, '_resolve_tenant', return_value=(104, "baytoo_panchayat_samiti_w1_jasaw")):
            hierarchy = repo.get_panchayat_hierarchy('baytoo_panchayat_samiti_w1_jasaw')

        self.assertTrue(hierarchy["is_multi_gp"])
        self.assertEqual(len(hierarchy["panchayats"]), 2)
        self.assertEqual(hierarchy["total_voters"], 1814 + 1349)

        lilala = next(p for p in hierarchy["panchayats"] if p["gp_name"] == "Lilala")
        self.assertEqual(lilala["gp_name_hi"], "लीलाला")
        self.assertEqual(lilala["total_voters"], 1814)
        self.assertEqual(lilala["parts"], [9, 10])
        self.assertEqual(lilala["filter_value"], "parts:9,10")
        self.assertEqual(len(lilala["wards"]), 7)
        self.assertEqual(lilala["wards"][0]["ward_no"], 1)
        self.assertEqual(lilala["wards"][0]["filter_value"], "part:9_ward:1")
        self.assertEqual(lilala["wards"][0]["display_name"], "वार्ड 1")

        mahingoni = next(p for p in hierarchy["panchayats"] if p["gp_name"] == "Mahingoni Moodhon Ki Dhani")
        self.assertEqual(mahingoni["gp_name_hi"], "माहिगोणी मुढो की ढाणी")
        self.assertEqual(mahingoni["total_voters"], 1349)
        self.assertEqual(mahingoni["parts"], [11, 12])
        self.assertEqual(mahingoni["filter_value"], "parts:11,12")
        self.assertEqual(len(mahingoni["wards"]), 7)
        self.assertEqual(mahingoni["wards"][0]["ward_no"], 1)
        self.assertEqual(mahingoni["wards"][0]["filter_value"], "part:11_ward:1")
        self.assertEqual(mahingoni["wards"][0]["display_name"], "वार्ड 1")

    def test_caching_and_backward_compatibility(self):
        """Verify get_cached_panchayat_hierarchy and get_cached_ward_hierarchy coexistence and TTL caching."""
        mock_repo = MagicMock()
        mock_repo.get_wards_dict.return_value = {"1": {"ward_no": 1, "voter_count": 100}}
        mock_repo.get_booths_with_wards.return_value = {"9": {"booth_no": "9", "wards": []}}
        mock_repo.get_panchayat_hierarchy.return_value = {
            "is_multi_gp": True,
            "panchayats": [{"gp_name": "Lilala", "total_voters": 100}],
            "total_voters": 100,
        }

        cfg = {'election_type': 'PANCHAYAT_SAMITI'}

        # 1. get_cached_ward_hierarchy backward compatibility
        wards, booths_with_wards = get_cached_ward_hierarchy(mock_repo, 'test_tenant', cfg)
        self.assertEqual(len(wards), 1)
        self.assertIn("9", booths_with_wards)

        # 2. get_cached_panchayat_hierarchy
        h1 = get_cached_panchayat_hierarchy(mock_repo, 'test_tenant', cfg)
        self.assertTrue(h1["is_multi_gp"])
        self.assertEqual(mock_repo.get_panchayat_hierarchy.call_count, 1)

        # 3. Call again - must be served from cache
        h2 = get_cached_panchayat_hierarchy(mock_repo, 'test_tenant', cfg)
        self.assertEqual(mock_repo.get_panchayat_hierarchy.call_count, 1)
        self.assertEqual(h1, h2)

        # 4. Assembly election returns is_multi_gp: False without calling repo
        cfg_assembly = {'election_type': 'ASSEMBLY'}
        h_ass = get_cached_panchayat_hierarchy(mock_repo, 'sanchore', cfg_assembly)
        self.assertFalse(h_ass["is_multi_gp"])
        self.assertEqual(mock_repo.get_panchayat_hierarchy.call_count, 1)

    def test_location_filter_parsing_caste_and_family(self):
        """Verify parsing of part:9_ward:1 and parts:9,10 across caste and family helpers."""
        # Compound part:9_ward:1
        w, p, parts, *rest = parse_caste_loc(part_arg="part:9_ward:1")
        self.assertEqual(w, 1)
        self.assertEqual(p, 9)
        self.assertIsNone(parts)

        w, p, parts, *rest = parse_family_loc(part_arg="part:9_ward:1")
        self.assertEqual(w, 1)
        self.assertEqual(p, 9)
        self.assertIsNone(parts)

        # Multi-part parts:9,10
        w, p, parts, *rest = parse_caste_loc(part_arg="parts:9,10")
        self.assertIsNone(w)
        self.assertIsNone(p)
        self.assertEqual(parts, [9, 10])

        w, p, parts, *rest = parse_family_loc(part_arg="parts:9,10")
        self.assertIsNone(w)
        self.assertIsNone(p)
        self.assertEqual(parts, [9, 10])

        # Standard ward:3
        w, p, parts, *rest = parse_caste_loc(ward_arg="ward:3")
        self.assertEqual(w, 3)
        self.assertIsNone(p)
        self.assertIsNone(parts)

    def test_search_blueprint_compound_filter_parsing(self):
        """Test /api/search parses compound part:9_ward:1 and parts:9,10."""
        app = create_app('chhoti_virol', 'postgres')
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['username'] = 'admin'
                sess['role'] = 'super_admin'
                sess['tenant_slug'] = 'chhoti_virol'

            with patch('electionapp.repo.postgres_repo.PostgresVoterRepository.search_voters') as mock_search:
                mock_search.return_value = {'voters': [], 'total': 0, 'count': 0}

                # 1. Compound part:9_ward:1
                resp = client.get('/api/search?tenant=chhoti_virol&part=part:9_ward:1')
                self.assertEqual(resp.status_code, 200)
                mock_search.assert_called()
                call_kwargs = mock_search.call_args[1]
                self.assertEqual(call_kwargs.get('part_no'), 9)
                self.assertEqual(call_kwargs.get('ward_no'), 1)

                # 2. Multi-part parts:9,10
                mock_search.reset_mock()
                resp = client.get('/api/search?tenant=chhoti_virol&part=parts:9,10')
                self.assertEqual(resp.status_code, 200)
                mock_search.assert_called()
                call_kwargs = mock_search.call_args[1]
                self.assertEqual(call_kwargs.get('part_nos'), [9, 10])

    def test_config_parse_location_filter(self):
        """Test parse_location_filter in electionapp.config for all standard and compound cases."""
        from electionapp.config import parse_location_filter

        # 1. Compound part:9_ward:1
        w, p, parts = parse_location_filter(part_arg="part:9_ward:1")
        self.assertEqual((w, p, parts), (1, 9, None))

        # 2. Compound 9_ward:1 (without part: prefix)
        w, p, parts = parse_location_filter(part_arg="9_ward:1")
        self.assertEqual((w, p, parts), (1, 9, None))

        # 3. Multi-part parts:9,10
        w, p, parts = parse_location_filter(part_arg="parts:9,10")
        self.assertEqual((w, p, parts), (None, None, [9, 10]))

        # 4. Standard ward:5
        w, p, parts = parse_location_filter(ward_arg="ward:5")
        self.assertEqual((w, p, parts), (5, None, None))

        # 5. Pure numeric part 9
        w, p, parts = parse_location_filter(part_arg="9")
        self.assertEqual((w, p, parts), (None, 9, None))

        # 6. None or all
        w, p, parts = parse_location_filter(part_arg="all")
        self.assertEqual((w, p, parts), (None, None, None))

    def test_reports_query_builder_compound_filter(self):
        """Test _build_reports_query parses compound and multi-part location parameters."""
        from electionapp.blueprints.reports import _build_reports_query

        # 1. Compound part:9_ward:1
        where_sql, params, order = _build_reports_query(
            tid=104,
            args={'part_no': 'part:9_ward:1'},
            is_super=True,
            assigned_booths=[],
            assigned_wards=[]
        )
        self.assertIn("ward_no = %s AND part_no = %s", where_sql)
        self.assertIn(1, params)
        self.assertIn(9, params)

        # 2. Multi-part parts:9,10
        where_sql, params, order = _build_reports_query(
            tid=104,
            args={'part_no': 'parts:9,10'},
            is_super=True,
            assigned_booths=[],
            assigned_wards=[]
        )
        self.assertIn("part_no = ANY(%s)", where_sql)
        self.assertIn([9, 10], params)

    def test_turnout_stats_multi_gp(self):
        """Test /api/turnout/stats groups by GP and ward when campaign is multi-GP."""
        app = create_app('chhoti_virol', 'postgres')
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['user_id'] = 1
                sess['username'] = 'admin'
                sess['role'] = 'super_admin'
                sess['tenant_slug'] = 'baytoo_panchayat_samiti_w1_jasaw'

            mock_hierarchy = {
                'is_multi_gp': True,
                'panchayats': [
                    {'gp_name': 'Lilala', 'gp_name_hi': 'लीलाला'},
                    {'gp_name': 'Mahingoni', 'gp_name_hi': 'माहिगोणी'},
                ],
                'total_voters': 3163
            }

            mock_rows = [
                {'part_no': 9, 'ward_no': 1, 'gram_panchayat': 'Lilala', 'booth_name': '9 - लीलाला दांया', 'total': 344, 'voted': 120, 'last_voted_at': None},
                {'part_no': 11, 'ward_no': 1, 'gram_panchayat': 'Mahingoni', 'booth_name': '11 - माहिगोणी', 'total': 260, 'voted': 90, 'last_voted_at': None},
            ]

            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_conn.__enter__.return_value = mock_conn
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur
            # 1. overall stats fetchone, 2. multi-gp fetchall, 3. category fetchall, 4. hourly fetchall
            mock_cur.fetchone.return_value = {'total': 604, 'voted': 210, 'voted_male': 110, 'voted_female': 100, 'total_male': 310, 'total_female': 294}
            mock_cur.fetchall.side_effect = [mock_rows, [], []]

            with patch('electionapp.repo.postgres_repo.PostgresVoterRepository.get_panchayat_hierarchy', return_value=mock_hierarchy), \
                 patch('electionapp.repo.postgres_repo.PostgresVoterRepository.get_connection', return_value=mock_conn), \
                 patch('electionapp.repo.postgres_repo.PostgresVoterRepository._resolve_tenant', return_value=(104, 'baytoo_panchayat_samiti_w1_jasaw')):
                resp = client.get('/api/turnout/stats?tenant=baytoo_panchayat_samiti_w1_jasaw')
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data['success'])
                booths = data['booths']
                self.assertEqual(len(booths), 2)
                # Ensure Lilala Ward 1 and Mahingoni Ward 1 are NOT merged
                self.assertEqual(booths[0]['name'], 'लीलाला — वार्ड 1')
                self.assertEqual(booths[0]['total'], 344)
                self.assertEqual(booths[0]['filter_value'], 'part:9_ward:1')
                self.assertEqual(booths[1]['name'], 'माहिगोणी — वार्ड 1')
                self.assertEqual(booths[1]['total'], 260)
                self.assertEqual(booths[1]['filter_value'], 'part:11_ward:1')


if __name__ == '__main__':
    unittest.main()
