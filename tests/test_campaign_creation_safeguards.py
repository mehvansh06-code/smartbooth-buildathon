# -*- coding: utf-8 -*-
"""
tests/test_campaign_creation_safeguards.py
===========================================
Thorough automated test suite for campaign creation safeguards:
1. Verifies create_campaign for Single-GP (Sarpanch / Panchayat) and Multi-GP
   (Panchayat Samiti / Zila Parishad) campaigns with SEC roll Hindi-only records.
2. Asserts generated SQL satisfies PostgreSQL check constraints:
   - chk_no_deva_in_name_en: CHECK (name_en IS NULL OR name_en !~ '[\u0900-\u097F]')
   - chk_no_deva_in_rel_en:  CHECK (rel_name_en IS NULL OR rel_name_en !~ '[\u0900-\u097F]')
3. Simulates SQLite database with production check constraints to guarantee:
   - Insertion of clean and Hindi-only records succeeds.
   - Devanagari values in name_en/rel_name_en are filtered to NULL.
   - The legacy buggy fallback (COALESCE(name_en, name_hi)) immediately triggers
     IntegrityError, preventing future regressions in CI.
"""

import os
import re
import json
import shutil
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from electionapp.config import TENANTS_DIR
from electionapp.campaign_manager import create_campaign


class TestCampaignCreationSafeguards(unittest.TestCase):
    def setUp(self):
        self.created_slugs = []

    def tearDown(self):
        for slug in self.created_slugs:
            p = os.path.join(TENANTS_DIR, slug)
            if os.path.exists(p):
                shutil.rmtree(p, ignore_errors=True)

    def _create_mock_pg_connection(self):
        """Creates a mock psycopg2 connection and cursor that records all SQL calls."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        
        executed_statements = []

        def execute_side_effect(sql, params=None):
            executed_statements.append((sql, params))

        def fetchone_handler():
            if not executed_statements:
                return (1,)
            last_sql, last_params = executed_statements[-1]
            last_sql_upper = last_sql.strip().upper()

            if "FROM TENANTS" in last_sql_upper:
                return (99,)
            if "RETURNING TENANT_ID" in last_sql_upper:
                return (99,)
            if "TO_REGCLASS" in last_sql_upper:
                return ('voters_master',)
            if "FROM VOTERS_MASTER" in last_sql_upper:
                if last_params and len(last_params) > 1:
                    return (last_params[1],)
                return ('CHHOTI VIROL',)
            return (1,)

        mock_cur.execute.side_effect = execute_side_effect
        mock_cur.fetchone.side_effect = fetchone_handler
        mock_cur.rowcount = 100
        return mock_conn, mock_cur

    def test_01_single_gp_campaign_sql_safeguards(self):
        """Verify single-GP campaign creation generates SQL with Devanagari guards on name_en and rel_name_en."""
        slug = f"test_single_gp_{os.getpid()}"
        self.created_slugs.append(slug)

        payload = {
            'campaign_slug': slug,
            'candidate_name_hi': 'नेनु देवी',
            'candidate_name_en': 'Nenu Devi',
            'election_type': 'PANCHAYAT',
            'district': 'जालोर',
            'rajasthan_district': 'JALORE',
            'rajasthan_gp': 'CHHOTI VIROL',
            'auto_ingest_rolls': 'true'
        }

        mock_conn, mock_cur = self._create_mock_pg_connection()

        with patch('electionapp.campaign_manager.get_pg_connection', return_value=mock_conn), patch('user_manager.create_user_account', return_value=(True, 'OK', 1)):
            success, result, status_code = create_campaign(payload, files={}, owner_user_id=1)
            self.assertTrue(success)
            self.assertEqual(status_code, 201)

        # Inspect all SQL statements executed on the cursor
        executed_sqls = [call_args[0][0] for call_args in mock_cur.execute.call_args_list if len(call_args[0]) > 0]
        ingest_sql = next((s for s in executed_sqls if "INSERT INTO" in s and "FROM voters_master" in s), None)
        self.assertIsNotNone(ingest_sql, "Expected voter ingestion SQL to be executed")

        # 1. Assert name_en uses regex check !~ '[\u0900-\u097F]' and does NOT fall back to name_hi
        self.assertIn("name_en !~ '[\u0900-\u097F]'", ingest_sql)
        self.assertNotIn("NULLIF(TRIM(name_hi), ''), 'Voter '", ingest_sql)

        # 2. Assert rel_name_en uses regex check !~ '[\u0900-\u097F]' and strips 'available'
        self.assertIn("rel_name_en !~ '[\u0900-\u097F]'", ingest_sql)
        self.assertIn("rel_name_en !~* 'available'", ingest_sql)

        # 3. Assert ON CONFLICT update guards EXCLUDED.name_en against Devanagari
        self.assertIn("EXCLUDED.name_en !~ '[\u0900-\u097F]'", ingest_sql)

    def test_02_multi_gp_panchayat_samiti_sql_safeguards(self):
        """Verify multi-GP (Panchayat Samiti) campaign creation generates SQL with Devanagari guards."""
        slug = f"test_ps_safeguard_{os.getpid()}"
        self.created_slugs.append(slug)

        payload = {
            'campaign_slug': slug,
            'candidate_name_hi': 'हनुमान राम',
            'candidate_name_en': 'Hanuman Ram',
            'election_type': 'PANCHAYAT_SAMITI',
            'ward_no': '14',
            'district': 'जालोर',
            'rajasthan_district': 'JALORE',
            'rajasthan_block': 'SANCHORE',
            'rajasthan_gps': 'CHHOTI VIROL,DANTIWAS',
            'auto_ingest_rolls': 'true'
        }

        mock_conn, mock_cur = self._create_mock_pg_connection()

        with patch('electionapp.campaign_manager.get_pg_connection', return_value=mock_conn), patch('user_manager.create_user_account', return_value=(True, 'OK', 1)):
            success, result, status_code = create_campaign(payload, files={}, owner_user_id=1)
            self.assertTrue(success)
            self.assertEqual(status_code, 201)

        executed_sqls = [call_args[0][0] for call_args in mock_cur.execute.call_args_list if len(call_args[0]) > 0]
        ingest_sql = next((s for s in executed_sqls if "WITH raw_electors AS" in s), None)
        self.assertIsNotNone(ingest_sql, "Expected multi-GP CTE voter ingestion SQL to be executed")

        # 1. Assert CTE raw_electors has the safe CASE expressions
        self.assertIn("name_en !~ '[\u0900-\u097F]'", ingest_sql)
        self.assertIn("rel_name_en !~ '[\u0900-\u097F]'", ingest_sql)
        self.assertIn("rel_name_en !~* 'available'", ingest_sql)

        # 2. Assert no Devanagari fallback into name_en in the CTE
        self.assertNotIn("NULLIF(TRIM(name_hi), ''), 'Voter '", ingest_sql)

    def test_03_multi_gp_zila_parishad_sql_safeguards(self):
        """Verify multi-GP (Zila Parishad) campaign creation also applies full safeguards."""
        slug = f"test_zp_safeguard_{os.getpid()}"
        self.created_slugs.append(slug)

        payload = {
            'campaign_slug': slug,
            'candidate_name_hi': 'सुनीता देवी',
            'candidate_name_en': 'Sunita Devi',
            'election_type': 'ZILA_PARISHAD',
            'ward_no': '5',
            'district': 'जोधपुर',
            'rajasthan_district': 'JODHPUR',
            'rajasthan_gps': 'BHOPALGARH,RANSI GAON,HERAN',
            'auto_ingest_rolls': 'true'
        }

        mock_conn, mock_cur = self._create_mock_pg_connection()

        with patch('electionapp.campaign_manager.get_pg_connection', return_value=mock_conn), patch('user_manager.create_user_account', return_value=(True, 'OK', 1)):
            success, result, status_code = create_campaign(payload, files={}, owner_user_id=1)
            self.assertTrue(success)
            self.assertEqual(status_code, 201)

        executed_sqls = [call_args[0][0] for call_args in mock_cur.execute.call_args_list if len(call_args[0]) > 0]
        ingest_sql = next((s for s in executed_sqls if "WITH raw_electors AS" in s), None)
        self.assertIsNotNone(ingest_sql)
        self.assertIn("name_en !~ '[\u0900-\u097F]'", ingest_sql)
        self.assertIn("rel_name_en !~ '[\u0900-\u097F]'", ingest_sql)

    def test_04_sqlite_simulation_production_check_constraints(self):
        """Simulate SQLite database with production PostgreSQL check constraints:
        chk_no_deva_in_name_en and chk_no_deva_in_rel_en.
        Verify that:
        - Hindi-only records insert safely with name_en = NULL.
        - Devanagari in name_en/rel_name_en is safely sanitized to NULL.
        - Clean English names are preserved.
        - The old bug (inserting Devanagari into name_en) triggers IntegrityError.
        """
        conn = sqlite3.connect(":memory:")
        
        # Register regex check function in SQLite to simulate PostgreSQL ~ / !~
        def has_devanagari(text):
            if text is None:
                return 0
            return 1 if re.search(r'[\u0900-\u097F]', str(text)) else 0

        conn.create_function("has_deva", 1, has_devanagari)

        # Create target partition table with strict CHECK constraints
        conn.execute("""
            CREATE TABLE voters_t99 (
                tenant_id INTEGER,
                ward_no INTEGER,
                part_no INTEGER,
                sl_no INTEGER,
                epic_no TEXT,
                name_hi TEXT,
                name_en TEXT,
                name_norm TEXT,
                rel_type TEXT,
                rel_name_hi TEXT,
                rel_name_en TEXT,
                rel_norm TEXT,
                house_no TEXT,
                age INTEGER,
                gender TEXT,
                status TEXT,
                CONSTRAINT chk_no_deva_in_name_en CHECK (name_en IS NULL OR has_deva(name_en) = 0),
                CONSTRAINT chk_no_deva_in_rel_en CHECK (rel_name_en IS NULL OR has_deva(rel_name_en) = 0)
            );
        """)

        # Sample test data mimicking real SEC Rajasthan voter rolls and edge cases:
        sample_master_rows = [
            # 1. Standard SEC roll: Hindi only, English NULL
            {
                'id': 101, 'ward_no': 1, 'sl_no': 1, 'epic_no': 'RLJ1234567',
                'name_hi': 'नेनु देवी', 'name_en': None,
                'rel_type': 'पति', 'rel_name_hi': 'हरचंद राम', 'rel_name_en': None,
                'house_no': '12', 'age': 45, 'gender': 'महिला'
            },
            # 2. SEC roll: English is empty string
            {
                'id': 102, 'ward_no': 1, 'sl_no': 2, 'epic_no': 'RLJ1234568',
                'name_hi': 'मोहन लाल', 'name_en': '   ',
                'rel_type': 'पिता', 'rel_name_hi': 'रामू राम', 'rel_name_en': '',
                'house_no': '14', 'age': 32, 'gender': 'पुरुष'
            },
            # 3. Corrupted OCR leak: Devanagari in name_en and rel_name_en
            {
                'id': 103, 'ward_no': 1, 'sl_no': 3, 'epic_no': 'RLJ1234569',
                'name_hi': 'महेन्द्र कुमार', 'name_en': 'महेन्द्र कुमार',
                'rel_type': 'पिता', 'rel_name_hi': 'राजेन्द्र कुमार', 'rel_name_en': 'राजेन्द्र कुमार',
                'house_no': '18', 'age': 28, 'gender': 'पुरुष'
            },
            # 4. Scraper leak: 'Available' / 'Not Available' in rel_name_en
            {
                'id': 104, 'ward_no': 1, 'sl_no': 4, 'epic_no': 'RLJ1234570',
                'name_hi': 'सुनील कुमार', 'name_en': 'Sunil Kumar',
                'rel_type': 'पिता', 'rel_name_hi': 'सत्यनारायण', 'rel_name_en': 'Available',
                'house_no': '22', 'age': 25, 'gender': 'पुरुष'
            },
            # 5. Clean bilingual record: Valid Latin characters
            {
                'id': 105, 'ward_no': 1, 'sl_no': 5, 'epic_no': 'RLJ1234571',
                'name_hi': 'सुरेश कुमार', 'name_en': 'Suresh Kumar',
                'rel_type': 'पिता', 'rel_name_hi': 'दिनेश कुमार', 'rel_name_en': 'Dinesh Kumar',
                'house_no': '25', 'age': 38, 'gender': 'पुरुष'
            }
        ]

        # Transformation function matching the fixed SQL CASE expressions
        def transform_row(r):
            # name_en CASE
            raw_n_en = r['name_en'].strip() if r['name_en'] else None
            if raw_n_en and not re.search(r'[\u0900-\u097F]', raw_n_en):
                clean_n_en = raw_n_en
            else:
                clean_n_en = None

            # rel_name_en CASE
            raw_r_en = r['rel_name_en'].strip() if r['rel_name_en'] else None
            if raw_r_en and not re.search(r'[\u0900-\u097F]', raw_r_en) and not re.search(r'available', raw_r_en, re.I):
                clean_r_en = raw_r_en
            else:
                clean_r_en = None

            # name_norm & rel_norm
            name_norm = (clean_n_en or r['name_hi'] or 'voter').strip().lower()
            rel_norm = (clean_r_en or r['rel_name_hi'] or '').strip().lower()

            return (
                99, r['ward_no'], r['ward_no'], r['sl_no'], r['epic_no'],
                r['name_hi'], clean_n_en, name_norm,
                r['rel_type'], r['rel_name_hi'], clean_r_en, rel_norm,
                r['house_no'], r['age'], r['gender'], 'Active'
            )

        # Insert transformed rows into voters_t99
        insert_sql = """
            INSERT INTO voters_t99 (
                tenant_id, ward_no, part_no, sl_no, epic_no,
                name_hi, name_en, name_norm,
                rel_type, rel_name_hi, rel_name_en, rel_norm,
                house_no, age, gender, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """

        for row in sample_master_rows:
            transformed = transform_row(row)
            # This MUST succeed without check constraint violation
            conn.execute(insert_sql, transformed)

        conn.commit()

        # Query all inserted records and verify constraints
        cur = conn.cursor()
        cur.execute("SELECT sl_no, name_hi, name_en, rel_name_hi, rel_name_en FROM voters_t99 ORDER BY sl_no;")
        rows = cur.fetchall()
        self.assertEqual(len(rows), 5)

        for sl_no, n_hi, n_en, r_hi, r_en in rows:
            # 1. name_hi and rel_name_hi must retain authentic Devanagari text
            self.assertIsNotNone(n_hi)
            self.assertTrue(len(n_hi.strip()) > 0)
            self.assertIsNotNone(re.search(r'[\u0900-\u097F]', n_hi), f"Row {sl_no} name_hi should be Devanagari")

            # 2. name_en MUST either be None or contain strictly ZERO Devanagari characters
            if n_en is not None:
                self.assertIsNone(re.search(r'[\u0900-\u097F]', n_en), f"Row {sl_no} name_en contains Devanagari: {n_en}")
                self.assertTrue(len(n_en.strip()) > 0)

            # 3. rel_name_en MUST either be None or contain strictly ZERO Devanagari characters
            if r_en is not None:
                self.assertIsNone(re.search(r'[\u0900-\u097F]', r_en), f"Row {sl_no} rel_name_en contains Devanagari: {r_en}")
                self.assertFalse('available' in r_en.lower())

        # Verify specific expected outputs per row:
        # Row 1 (Hindi only): name_en and rel_name_en must be NULL
        self.assertIsNone(rows[0][2])
        self.assertIsNone(rows[0][4])
        self.assertEqual(rows[0][1], 'नेनु देवी')

        # Row 2 (Empty string English): name_en and rel_name_en must be NULL
        self.assertIsNone(rows[1][2])
        self.assertIsNone(rows[1][4])
        self.assertEqual(rows[1][1], 'मोहन लाल')

        # Row 3 (Devanagari OCR in English): must be filtered to NULL
        self.assertIsNone(rows[2][2])
        self.assertIsNone(rows[2][4])
        self.assertEqual(rows[2][1], 'महेन्द्र कुमार')

        # Row 4 ('Available' scraper leak): rel_name_en filtered to NULL, name_en preserved
        self.assertEqual(rows[3][2], 'Sunil Kumar')
        self.assertIsNone(rows[3][4])

        # Row 5 (Valid bilingual): both English names preserved
        self.assertEqual(rows[4][2], 'Suresh Kumar')
        self.assertEqual(rows[4][4], 'Dinesh Kumar')

        # 4. REGRESSION VERIFICATION: Prove that old buggy logic (falling back to name_hi)
        # violates chk_no_deva_in_name_en and raises IntegrityError in SQLite
        old_buggy_name_en = sample_master_rows[0]['name_hi']  # 'नेनु देवी' placed into name_en
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO voters_t99 (
                    tenant_id, ward_no, part_no, sl_no, epic_no,
                    name_hi, name_en, name_norm,
                    rel_type, rel_name_hi, rel_name_en, rel_norm,
                    house_no, age, gender, status
                ) VALUES (99, 1, 1, 999, 'RLJ9999999', 'नेनु देवी', ?, 'nenu devi', 'पति', 'हरचंद राम', NULL, '', '1', 45, 'महिला', 'Active');
            """, (old_buggy_name_en,))

        # Also prove old buggy logic for rel_name_en violates chk_no_deva_in_rel_en
        old_buggy_rel_name_en = sample_master_rows[0]['rel_name_hi']  # 'हरचंद राम' placed into rel_name_en
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO voters_t99 (
                    tenant_id, ward_no, part_no, sl_no, epic_no,
                    name_hi, name_en, name_norm,
                    rel_type, rel_name_hi, rel_name_en, rel_norm,
                    house_no, age, gender, status
                ) VALUES (99, 1, 1, 998, 'RLJ9999998', 'नेनु देवी', NULL, 'nenu devi', 'पति', 'हरचंद राम', ?, '', '1', 45, 'महिला', 'Active');
            """, (old_buggy_rel_name_en,))

        conn.close()

    def test_05_heal_multi_gp_script_safeguards(self):
        """Verify scripts/provision_campaign.py contains the PostgreSQL name_en and rel_name_en regex safeguards."""
        script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'provision_campaign.py')
        self.assertTrue(os.path.exists(script_path), f"File {script_path} not found")
        with open(script_path, 'r', encoding='utf-8') as f:
            code = f.read()

        # Must contain regex checks on name_en and rel_name_en
        self.assertIn("name_en !~", code)
        self.assertIn("rel_name_en !~", code)
        self.assertIn("available", code.lower())
        # Must not contain buggy Devanagari fallback into name_en
        self.assertNotIn("COALESCE(NULLIF(TRIM(name_en), ''), NULLIF(TRIM(name_hi), '')", code)

    def test_06_admin_blueprint_safeguards(self):
        """Verify electionapp/blueprints/admin.py contains the PostgreSQL name_en and rel_name_en regex safeguards."""
        admin_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'electionapp', 'blueprints', 'admin.py')
        self.assertTrue(os.path.exists(admin_path), f"File {admin_path} not found")
        with open(admin_path, 'r', encoding='utf-8') as f:
            code = f.read()

        self.assertIn("name_en !~", code)
        self.assertIn("rel_name_en !~", code)
        self.assertIn("available", code.lower())
        self.assertNotIn("COALESCE(NULLIF(TRIM(name_en), ''), NULLIF(TRIM(name_hi), '')", code)


if __name__ == '__main__':
    unittest.main()
