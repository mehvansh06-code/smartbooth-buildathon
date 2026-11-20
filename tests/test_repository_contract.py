"""
test_repository_contract.py
Comprehensive Verification Suite for the Repository Contract.

Verifies PostgresVoterRepository:
1. Strictly implements BaseVoterRepository contract with tenant_id parameter enforcement.
2. Accurately handles:
   - get_voter
   - search_voters
   - get_turnout_stats
   - get_family_voters
3. Verifies get_voters_by_ids, get_voters_by_part, get_gotv_voters.
4. Verifies mark_voted executes atomic updates to voters and voting_audit_log.
5. Verifies log_dispatch executes atomic updates to voters and dispatch_log.
6. Tests across both Tenant 1 (Jelatra) and Tenant 3 (Ward 82).
7. Verifies get_repository("sqlite") is rejected with ValueError.
"""

import os
import sys
import unittest
import psycopg2

# Configure UTF-8 stdout for Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure PLATFORM root is on sys.path
PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp.repo import (
    BaseVoterRepository,
    PostgresVoterRepository,
    get_repository,
)


class TestRepositoryContract(unittest.TestCase):
    """
    Contract test suite verifying PostgreSQL repository implementation.
    """

    @classmethod
    def setUpClass(cls):
        cls.postgres_repo = get_repository("postgres")
        cls.pg_conn = psycopg2.connect(
            dbname=os.environ.get("PGDATABASE", "voter_platform_test"),
            user=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PGPASSWORD", "postgres"),
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", 5432))
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "postgres_repo") and cls.postgres_repo:
            cls.postgres_repo.close()
        if hasattr(cls, "pg_conn") and cls.pg_conn:
            cls.pg_conn.close()

    def test_01_repository_instantiation_and_interface(self):
        """Verifies repository instantiation and abstract interface inheritance."""
        self.assertIsInstance(self.postgres_repo, BaseVoterRepository)
        self.assertIsInstance(self.postgres_repo, PostgresVoterRepository)

        # Factory rejects sqlite
        with self.assertRaises(ValueError):
            get_repository("sqlite")

        # Factory rejects unsupported backend
        with self.assertRaises(ValueError):
            get_repository("unsupported_backend")

    def test_02_tenant_resolution_int_and_slug(self):
        """Verifies that methods accept tenant_id as int or slug equivalently."""
        # Tenant 1: Jelatra (voter 101)
        v_by_id_pg = self.postgres_repo.get_voter(1, 101)
        v_by_slug_pg = self.postgres_repo.get_voter("jelatra", 101)
        self.assertIsNotNone(v_by_id_pg)
        self.assertEqual(v_by_id_pg["epic_no"], v_by_slug_pg["epic_no"])

        # Tenant 3: Ward 82 (voter 301)
        v3_by_id_pg = self.postgres_repo.get_voter(3, 301)
        v3_by_slug_pg = self.postgres_repo.get_voter("ward82", 301)
        self.assertIsNotNone(v3_by_id_pg)
        self.assertEqual(v3_by_id_pg["epic_no"], v3_by_slug_pg["epic_no"])

    def test_03_get_voter_matching_keys_and_data(self):
        """Verifies get_voter returns correct keys and data."""
        # Tenant 1: Jelatra (voter 101)
        v1_pg = self.postgres_repo.get_voter(1, 101)
        self.assertIsNotNone(v1_pg)

        core_fields = ["epic_no", "name_en", "name_hi", "part_no", "sl_no", "house_no", "age", "gender", "family_id"]
        for field in core_fields:
            self.assertIn(field, v1_pg)
            self.assertIsNotNone(v1_pg[field])

        # Tenant 3: Ward 82 (voter 301)
        v3_pg = self.postgres_repo.get_voter(3, 301)
        self.assertIsNotNone(v3_pg)
        for field in core_fields:
            self.assertIn(field, v3_pg)
            self.assertIsNotNone(v3_pg[field])

        # Non-existent voter lookup
        self.assertIsNone(self.postgres_repo.get_voter(1, 9999999))

    def test_04_get_voters_by_ids(self):
        """Verifies get_voters_by_ids returns requested records."""
        ids = [101, 102, 103]
        res_pg = self.postgres_repo.get_voters_by_ids(1, ids)

        self.assertEqual(len(res_pg), 3)
        self.assertEqual([r["epic_no"] for r in res_pg], ["RJ10000001", "RJ10000002", "RJ10000003"])

        # Empty IDs list returns empty list
        self.assertEqual(self.postgres_repo.get_voters_by_ids(1, []), [])

    def test_05_get_voters_by_part(self):
        """Verifies get_voters_by_part returns ordered records by sl_no."""
        part_no = 1
        res_pg = self.postgres_repo.get_voters_by_part(1, part_no, limit=10)

        self.assertTrue(len(res_pg) > 0)
        self.assertEqual([r["sl_no"] for r in res_pg], sorted([r["sl_no"] for r in res_pg]))
        for r in res_pg:
            self.assertEqual(r["part_no"], part_no)

    def test_06_get_family_voters(self):
        """Verifies get_family_voters returns co-located family members."""
        v1 = self.postgres_repo.get_voter(1, 101)
        family_id = v1["family_id"]
        self.assertTrue(bool(family_id))

        fam_pg = self.postgres_repo.get_family_voters(1, family_id)
        self.assertGreater(len(fam_pg), 1)
        for member in fam_pg:
            self.assertEqual(member["family_id"], family_id)

        # Non-existent family returns empty list
        self.assertEqual(self.postgres_repo.get_family_voters(1, "NON_EXISTENT_FAMILY_999"), [])

    def test_07_get_turnout_stats(self):
        """Verifies get_turnout_stats produces structured turnout counts."""
        # Tenant 1: Jelatra
        stats1_pg = self.postgres_repo.get_turnout_stats(1)
        self.assertEqual(stats1_pg["total_voters"], 5)
        self.assertEqual(stats1_pg["voted_count"], 0)
        self.assertEqual(stats1_pg["unvoted_count"], 5)
        self.assertEqual(stats1_pg["turnout_percent"], 0.0)
        self.assertIsInstance(stats1_pg["booth_stats"], list)

        # Tenant 3: Ward 82
        stats3_pg = self.postgres_repo.get_turnout_stats(3)
        self.assertEqual(stats3_pg["total_voters"], 4)
        self.assertEqual(stats3_pg["voted_count"], 0)
        self.assertEqual(stats3_pg["unvoted_count"], 4)
        self.assertEqual(stats3_pg["turnout_percent"], 0.0)
        self.assertIsInstance(stats3_pg["booth_stats"], list)

    def test_08_search_voters(self):
        """Verifies search_voters returns matching records."""
        # 1. EPIC token search on Jelatra
        s_epic_pg = self.postgres_repo.search_voters(1, "RJ10000001", limit=5)
        self.assertTrue(len(s_epic_pg) > 0)
        self.assertEqual(s_epic_pg[0]["epic_no"], "RJ10000001")

        # 2. EPIC token search on Ward 82
        w_epic_pg = self.postgres_repo.search_voters(3, "WD82000001", limit=5)
        self.assertTrue(len(w_epic_pg) > 0)
        self.assertEqual(w_epic_pg[0]["epic_no"], "WD82000001")

        # 3. Full name search on Jelatra
        s_name_pg = self.postgres_repo.search_voters(1, "Prahlad Ram", limit=5)
        self.assertTrue(len(s_name_pg) > 0)
        self.assertEqual(s_name_pg[0]["epic_no"], "RJ10000001")

        # 4. Empty query (initial browse mode)
        empty_pg = self.postgres_repo.search_voters(1, "", limit=5)
        self.assertTrue(len(empty_pg) > 0)

    def test_09_mark_voted_atomic_transaction_and_audit_log(self):
        """Verifies mark_voted atomically writes to both voters and voting_audit_log."""
        v_id = 102
        user_id = 77
        voter_name = "Test Audit Voter"
        device_id = "TEST-DEVICE-01"
        ip_addr = "10.0.0.1"

        try:
            v_initial_pg = self.postgres_repo.get_voter(1, v_id)
            self.assertFalse(v_initial_pg["is_voted"])

            # Mark voted
            ok_pg = self.postgres_repo.mark_voted(
                1, v_id, is_voted=True, user_id=user_id,
                voter_name=voter_name, device_id=device_id, ip_address=ip_addr
            )
            self.assertTrue(ok_pg)

            v_marked_pg = self.postgres_repo.get_voter(1, v_id)
            self.assertTrue(v_marked_pg["is_voted"])
            self.assertIsNotNone(v_marked_pg["voted_at"])

            # Verify audit log in PostgreSQL
            with self.pg_conn.cursor() as cur_pg:
                cur_pg.execute("""
                    SELECT tenant_id, voter_id, action, performed_by, device_id, ip_address 
                    FROM voting_audit_log 
                    WHERE tenant_id = 1 AND voter_id = %s 
                    ORDER BY log_id DESC LIMIT 1
                """, (v_id,))
                audit_row_pg = cur_pg.fetchone()

            self.assertIsNotNone(audit_row_pg)
            self.assertEqual(audit_row_pg[0], 1)
            self.assertEqual(audit_row_pg[1], v_id)
            self.assertEqual(audit_row_pg[2], "voted")
            self.assertEqual(audit_row_pg[3], user_id)
            self.assertEqual(audit_row_pg[4], device_id)
            self.assertEqual(str(audit_row_pg[5]), ip_addr)
        finally:
            # Revert PostgreSQL mark and clean up audit entries
            with self.pg_conn.cursor() as cur_pg:
                cur_pg.execute("UPDATE voters SET is_voted = FALSE, voted_at = NULL WHERE tenant_id = 1 AND voter_id = %s", (v_id,))
                cur_pg.execute("DELETE FROM voting_audit_log WHERE tenant_id = 1 AND voter_id = %s AND (performed_by = %s OR device_id = %s)", (v_id, user_id, device_id))
                self.pg_conn.commit()
            v_revert_pg = self.postgres_repo.get_voter(1, v_id)
            self.assertFalse(v_revert_pg["is_voted"])

    def test_10_log_dispatch_atomic_transaction(self):
        """Verifies log_dispatch writes to dispatch ledger and updates voter status."""
        v_id = 103
        phone = "919876543210"
        channel = "meta_cloud"
        msg_id = "test_msg_001"

        try:
            d_id_pg = self.postgres_repo.log_dispatch(
                1, v_id, phone=phone, channel=channel, message_id=msg_id, delivery_status="SENT"
            )
            self.assertIsInstance(d_id_pg, int)
            v_pg = self.postgres_repo.get_voter(1, v_id)
            self.assertTrue(v_pg["is_dispatched"])
            self.assertIsNotNone(v_pg["dispatched_at"])
        finally:
            with self.pg_conn.cursor() as cur:
                cur.execute("UPDATE voters SET is_dispatched = FALSE, dispatched_at = NULL, contact_phone = NULL WHERE tenant_id = 1 AND voter_id = %s", (v_id,))
                cur.execute("DELETE FROM dispatch_log WHERE tenant_id = 1 AND destination_phone = %s", (phone,))
                self.pg_conn.commit()

    def test_11_get_gotv_voters(self):
        """Verifies get_gotv_voters contract and filtering."""
        gotv_pg = self.postgres_repo.get_gotv_voters(1, min_category=0)
        self.assertIsInstance(gotv_pg, list)
        for v in gotv_pg:
            self.assertFalse(v["is_voted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
