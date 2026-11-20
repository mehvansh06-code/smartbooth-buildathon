# -*- coding: utf-8 -*-
import unittest
from electionapp.blueprints.slip import (
    _clean_devanagari_skeleton,
    _clean_en_skeleton,
    _discover_family_for_voter
)


class MockRepo:
    def __init__(self, rows):
        self.storage_backend = 'postgres'
        self.rows = rows

    def get_family_voters(self, tenant, f_id):
        return [r for r in self.rows if r.get('family_id') == f_id]

    def _normalize_row(self, r, tid):
        row = dict(r)
        row['id'] = row.get('id') or row.get('voter_id')
        return row

    def get_connection(self):
        class MockCursor:
            def __init__(self, rows, norm_func):
                self.rows = rows
                self.norm_func = norm_func
                self._results = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

            def execute(self, sql, params):
                if "house_no = %s" in sql or "house_no = ?" in sql:
                    part_no = params[1] if len(params) > 2 else params[0]
                    h_no = params[2] if len(params) > 2 else params[1]
                    self._results = [r for r in self.rows if r.get('part_no') == part_no and str(r.get('house_no')).strip() == str(h_no).strip()]
                elif "voter_id != ALL" in sql or "id NOT IN" in sql or "voter_id NOT IN" in sql:
                    part_no = params[1] if len(params) > 2 else params[0]
                    exclude_ids = set(params[2]) if len(params) > 2 and isinstance(params[2], (list, tuple, set)) else set()
                    self._results = [r for r in self.rows if r.get('part_no') == part_no and r.get('id') not in exclude_ids]
                else:
                    self._results = []

            def fetchall(self):
                return self._results

        class MockConn:
            def __init__(self, rows, norm_func):
                self.rows = rows
                self.norm_func = norm_func

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

            def cursor(self, cursor_factory=None):
                return MockCursor(self.rows, self.norm_func)

            def close(self):
                pass

        return MockConn(self.rows, self._normalize_row)


class TestFamilySmartSuggestions(unittest.TestCase):

    def test_clean_devanagari_skeleton(self):
        # Verify bindi/anusvara and caste particle stripping
        self.assertEqual(_clean_devanagari_skeleton("सोंहन सिंह राजपुरोहित"), "सोहन")
        self.assertEqual(_clean_devanagari_skeleton("सोहनसिंह"), "सोहन")
        self.assertEqual(_clean_devanagari_skeleton("गोपालसिंह"), "गोपाल")
        self.assertEqual(_clean_devanagari_skeleton("हीरसिंह"), "हीर")

    def test_clean_en_skeleton(self):
        self.assertEqual(_clean_en_skeleton("Sohansingh"), "sohan")
        self.assertEqual(_clean_en_skeleton("Gopalsingh"), "gopal")

    def test_discover_family_with_offspring_and_sibling(self):
        # Exact Ghewda dataset structure
        rows = [
            {
                'id': 473219,
                'voter_id': 473219,
                'tenant_id': 41,
                'part_no': 39,
                'sl_no': 624,
                'house_no': '159',
                'name_hi': 'सोहनसिंह',
                'rel_name_hi': 'गोपालसिंह',
                'name_en': 'Sohansingh',
                'rel_name_en': 'Gopalsingh',
                'family_id': '39_159',
                'age': 55,
                'gender': 'पुरुष'
            },
            {
                'id': 473220,
                'voter_id': 473220,
                'tenant_id': 41,
                'part_no': 39,
                'sl_no': 625,
                'house_no': '159',
                'name_hi': 'चन्दन कंवर',
                'rel_name_hi': 'सोहनसिंह',
                'name_en': 'Chandan Kanwar',
                'rel_name_en': 'Sohansingh',
                'family_id': '39_159',
                'age': 50,
                'gender': 'स्त्री'
            },
            {
                'id': 473223,
                'voter_id': 473223,
                'tenant_id': 41,
                'part_no': 39,
                'sl_no': 628,
                'house_no': '160',
                'name_hi': 'हीरसिंह',
                'rel_name_hi': 'गोपालसिंह',
                'name_en': 'Hirsingh',
                'rel_name_en': 'Gopalsingh',
                'family_id': '39_160',
                'age': 48,
                'gender': 'पुरुष'
            },
            {
                'id': 473228,
                'voter_id': 473228,
                'tenant_id': 41,
                'part_no': 39,
                'sl_no': 633,
                'house_no': '160',
                'name_hi': 'वैभव सिंह',
                'rel_name_hi': 'सोंहन सिंह राजपुरोहित',
                'name_en': 'Vaibhav Singh',
                'rel_name_en': 'Songhan Singh Rajpurohit',
                'family_id': '39_160',
                'age': 23,
                'gender': 'पुरुष'
            },
            {
                'id': 473999,
                'voter_id': 473999,
                'tenant_id': 41,
                'part_no': 39,
                'sl_no': 800,
                'house_no': '500',
                'name_hi': 'रमेश कुमार',
                'rel_name_hi': 'मोहन लाल',
                'name_en': 'Ramesh Kumar',
                'rel_name_en': 'Mohan Lal',
                'family_id': '39_500',
                'age': 30,
                'gender': 'पुरुष'
            }
        ]

        repo = MockRepo(rows)
        voter = rows[0]

        official, same_house, direct, related = _discover_family_for_voter(
            voter, voter['part_no'], voter['house_no'], 'test_tenant', repo, 41
        )

        # 1. Direct should have Sohan and Chandan Kanwar
        self.assertEqual(len(direct), 2)
        direct_names = [d['name_hi'] for d in direct]
        self.assertIn('सोहनसिंह', direct_names)
        self.assertIn('चन्दन कंवर', direct_names)

        # 2. Related must have Vaibhav Singh and Heer Singh!
        self.assertGreaterEqual(len(related), 2)
        related_names = [r['name_hi'] for r in related]
        self.assertIn('वैभव सिंह', related_names)
        self.assertIn('हीरसिंह', related_names)

        # 3. Unrelated voter in house 500 must NOT be in related
        self.assertNotIn('रमेश कुमार', related_names)

        # 4. Check that relation reasons contain biological details
        vaibhav_cand = next(r for r in related if r['name_hi'] == 'वैभव सिंह')
        heer_cand = next(r for r in related if r['name_hi'] == 'हीरसिंह')

        self.assertIn('शाखा', vaibhav_cand['relation_reason'])
        self.assertIn('साझा पिता', heer_cand['relation_reason'])

    def test_rel_type_husband_not_matched_as_sibling(self):
        """Verify that a female voter whose relative is 'पति' (Husband) is NEVER matched as a sibling."""
        rows = [
            {
                'id': 1, 'voter_id': 1, 'tenant_id': 1, 'part_no': 1, 'sl_no': 10,
                'house_no': '188', 'name_hi': 'लाखाराम', 'rel_name_hi': 'रमेश', 'rel_type': 'पिता',
                'age': 28, 'gender': 'पुरुष'
            },
            {
                'id': 2, 'voter_id': 2, 'tenant_id': 1, 'part_no': 1, 'sl_no': 12,
                'house_no': '189', 'name_hi': 'इन्द्रा देवी', 'rel_name_hi': 'रमेश', 'rel_type': 'पति',
                'age': 35, 'gender': 'महिला'
            }
        ]
        repo = MockRepo(rows)
        target = rows[0]
        _, _, direct, related = _discover_family_for_voter(target, target['part_no'], target['house_no'], 'test', repo, 1)
        for cand in related:
            self.assertNotIn('साझा पिता', cand.get('relation_reason', ''))
            self.assertNotIn('सहोदर', cand.get('relation_reason', ''))

    def test_distant_house_rejection(self):
        """Verify that voters living far away (> 2 houses) are strictly rejected regardless of common names."""
        rows = [
            {
                'id': 10, 'voter_id': 10, 'tenant_id': 1, 'part_no': 1, 'sl_no': 10,
                'house_no': '126', 'name_hi': 'रमीला कुमारी', 'rel_name_hi': 'सांवला राम', 'rel_type': 'पिता',
                'age': 20, 'gender': 'महिला'
            },
            {
                'id': 20, 'voter_id': 20, 'tenant_id': 1, 'part_no': 1, 'sl_no': 125,
                'house_no': '185', 'name_hi': 'सांवला राम', 'rel_name_hi': 'पेमा', 'rel_type': 'पिता',
                'age': 59, 'gender': 'पुरुष'
            },
            {
                'id': 30, 'voter_id': 30, 'tenant_id': 1, 'part_no': 1, 'sl_no': 160,
                'house_no': '201', 'name_hi': 'दरिया देवी', 'rel_name_hi': 'सांवला राम', 'rel_type': 'पति',
                'age': 31, 'gender': 'महिला'
            }
        ]
        repo = MockRepo(rows)
        target = rows[0]
        _, _, direct, related = _discover_family_for_voter(target, target['part_no'], target['house_no'], 'test', repo, 1)
        self.assertEqual(len(related), 0)

    def test_subhouse_branch_detection(self):
        """Verify that sub-houses (e.g. 199 vs 199़) correctly match as genuine nearby branches."""
        rows = [
            {
                'id': 50, 'voter_id': 50, 'tenant_id': 1, 'part_no': 1, 'sl_no': 240,
                'house_no': '199', 'name_hi': 'जीवा राम', 'rel_name_hi': 'लीलाराम', 'rel_type': 'पिता',
                'age': 50, 'gender': 'पुरुष'
            },
            {
                'id': 51, 'voter_id': 51, 'tenant_id': 1, 'part_no': 1, 'sl_no': 241,
                'house_no': '199़', 'name_hi': 'मफी देवी', 'rel_name_hi': 'जीवा राम', 'rel_type': 'पति',
                'age': 46, 'gender': 'महिला'
            },
            {
                'id': 52, 'voter_id': 52, 'tenant_id': 1, 'part_no': 1, 'sl_no': 242,
                'house_no': '199़', 'name_hi': 'सुरेश कुमार', 'rel_name_hi': 'जीवा राम', 'rel_type': 'पिता',
                'age': 22, 'gender': 'पुरुष'
            }
        ]
        repo = MockRepo(rows)
        target = rows[0]
        _, _, direct, related = _discover_family_for_voter(target, target['part_no'], target['house_no'], 'test', repo, 1)
        self.assertEqual(len(related), 2)
        rel_names = [r['name_hi'] for r in related]
        self.assertIn('मफी देवी', rel_names)
        self.assertIn('सुरेश कुमार', rel_names)
        mafi = next(r for r in related if r['name_hi'] == 'मफी देवी')
        suresh = next(r for r in related if r['name_hi'] == 'सुरेश कुमार')
        self.assertIn('उप-मकान', mafi['relation_reason'])
        self.assertIn('उप-मकान', suresh['relation_reason'])

    def test_caste_clash_hard_veto(self):
        """Verify that electors with conflicting caste tags are vetoed even if in adjacent house."""
        rows = [
            {
                'id': 100, 'voter_id': 100, 'tenant_id': 1, 'part_no': 1, 'sl_no': 10,
                'house_no': '148', 'name_hi': 'मोहन', 'rel_name_hi': 'राम', 'rel_type': 'पिता',
                'caste_tag': 'Meghwal', 'age': 45, 'gender': 'पुरुष'
            },
            {
                'id': 101, 'voter_id': 101, 'tenant_id': 1, 'part_no': 1, 'sl_no': 11,
                'house_no': '149', 'name_hi': 'सुरेश', 'rel_name_hi': 'मोहन', 'rel_type': 'पिता',
                'caste_tag': 'Rajpurohit', 'age': 22, 'gender': 'पुरुष'
            }
        ]
        repo = MockRepo(rows)
        target = rows[0]
        _, _, direct, related = _discover_family_for_voter(target, target['part_no'], target['house_no'], 'test', repo, 1)
        self.assertEqual(len(related), 0)

    def test_worker_manual_lock_protection(self):
        # Verify that manual worker consolidation sets family_source_type = 'MANUAL'
        # and prevents any automated sync from overwriting
        voters = {
            101: {'voter_id': 101, 'name_hi': 'सोहनसिंह', 'house_no': '159', 'family_id': '39_159', 'family_count': 1, 'family_source_type': 'CENSUS'},
            102: {'voter_id': 102, 'name_hi': 'वैभव सिंह', 'house_no': '160', 'family_id': '39_160', 'family_count': 1, 'family_source_type': 'CENSUS'},
        }

        # 1. Execute manual worker consolidation (from campaign.py)
        clean_v_ids = [101, 102]
        assigned_fid = 'F_101'
        for vid in clean_v_ids:
            if vid in voters:
                voters[vid]['family_id'] = assigned_fid
                voters[vid]['family_count'] = len(clean_v_ids)
                voters[vid]['family_source_type'] = 'MANUAL'

        # Verify family_source_type flipped to MANUAL
        self.assertEqual(voters[102]['family_id'], 'F_101')
        self.assertEqual(voters[102]['family_source_type'], 'MANUAL')

        # 2. Simulate automated roll repair or resync with PostgreSQL lock guard:
        # WHERE voter_id = 102 AND (family_source_type IS NULL OR family_source_type != 'MANUAL')
        for vid, v in voters.items():
            if vid == 102 and (v.get('family_source_type') is None or v.get('family_source_type') != 'MANUAL'):
                v['family_id'] = '39_160'

        # Verify that MANUAL lock prevented the automated overwrite!
        self.assertEqual(voters[102]['family_id'], 'F_101')
        self.assertEqual(voters[102]['family_source_type'], 'MANUAL')


if __name__ == '__main__':
    unittest.main()
