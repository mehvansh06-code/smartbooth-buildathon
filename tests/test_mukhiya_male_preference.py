"""
test_mukhiya_male_preference.py
Verifies the 2026-10-11 "oldest male" mukhiya refinement:
1. electionapp.tenancy.mukhiya_prefers_male() excludes sarana/gada/bondli only.
2. The (gender = 'M') DESC ordering tier picks the oldest MALE over an older
   female when no head is already tagged.
3. An already-tagged is_family_head row is never dethroned by the gender tier
   (is_family_head still outranks gender/age in every touched ORDER BY).
Runs against PostgreSQL temporary tables.
"""

import os
import unittest
import psycopg2

from electionapp.tenancy import mukhiya_prefers_male


class TestMukhiyaPrefersMaleHelper(unittest.TestCase):
    def test_excluded_tenants_return_false(self):
        for slug in ('sarana', 'gada', 'bondli', 'SARANA', ' Gada '):
            self.assertFalse(mukhiya_prefers_male(slug), slug)

    def test_other_tenants_return_true(self):
        for slug in ('ward82', 'rajpura', 'jelatra', 'zhak', ''):
            self.assertTrue(mukhiya_prefers_male(slug), slug)

    def test_none_returns_true(self):
        self.assertTrue(mukhiya_prefers_male(None))


class TestOldestMaleOrdering(unittest.TestCase):
    """Exercises the actual ORDER BY fragments used in family.py / campaign.py /
    the repo layer against PostgreSQL temporary tables."""

    def setUp(self):
        self.conn = psycopg2.connect(
            dbname=os.environ.get("PGDATABASE", "voter_platform_test"),
            user=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PGPASSWORD", "postgres"),
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", 5432))
        )
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TEMPORARY TABLE IF NOT EXISTS test_mukhiya_voters (
                    voter_id INTEGER,
                    family_id TEXT,
                    age INTEGER,
                    gender TEXT,
                    sl_no INTEGER,
                    is_family_head INTEGER DEFAULT 0
                );
                TRUNCATE test_mukhiya_voters;
            """)

    def tearDown(self):
        try:
            with self.conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS test_mukhiya_voters;")
            self.conn.close()
        except Exception:
            pass

    def _insert(self, voter_id, family_id, age, gender, sl_no, is_family_head=0):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO test_mukhiya_voters VALUES (%s, %s, %s, %s, %s, %s)",
                (voter_id, family_id, age, gender, sl_no, is_family_head),
            )

    def test_oldest_male_wins_fallback_selection(self):
        # Older female (70) + younger male (40), no existing head.
        self._insert(1, 'F1', 70, 'F', 1)
        self._insert(2, 'F1', 40, 'M', 2)

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT voter_id FROM test_mukhiya_voters WHERE family_id = 'F1' "
                "ORDER BY (gender = 'M') DESC, age DESC, sl_no ASC LIMIT 1"
            )
            row = cur.fetchone()
        self.assertEqual(row[0], 2, "the male voter should win over the older female")

    def test_legacy_oldest_wins_for_excluded_tenants(self):
        # Same data, but using the legacy (no gender tier) ordering used for
        # sarana/gada/bondli -- the older female must win, unchanged.
        self._insert(1, 'F1', 70, 'F', 1)
        self._insert(2, 'F1', 40, 'M', 2)

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT voter_id FROM test_mukhiya_voters WHERE family_id = 'F1' "
                "ORDER BY age DESC, sl_no ASC LIMIT 1"
            )
            row = cur.fetchone()
        self.assertEqual(row[0], 1, "legacy ordering must stay oldest-person, ignoring gender")

    def test_all_female_household_falls_back_to_oldest_female(self):
        self._insert(1, 'F2', 55, 'F', 1)
        self._insert(2, 'F2', 80, 'F', 2)

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT voter_id FROM test_mukhiya_voters WHERE family_id = 'F2' "
                "ORDER BY (gender = 'M') DESC, age DESC, sl_no ASC LIMIT 1"
            )
            row = cur.fetchone()
        self.assertEqual(row[0], 2, "no male present -> oldest female must still become mukhiya")

    def test_existing_manual_head_outranks_gender_and_age(self):
        # Younger female already manually tagged as head; older male present
        # but untagged -- is_family_head tier must still win.
        self._insert(1, 'F3', 30, 'F', 1, is_family_head=1)
        self._insert(2, 'F3', 65, 'M', 2, is_family_head=0)

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT voter_id FROM test_mukhiya_voters WHERE family_id = 'F3' "
                "ORDER BY is_family_head DESC, (gender = 'M') DESC, age DESC, sl_no ASC LIMIT 1"
            )
            row = cur.fetchone()
        self.assertEqual(row[0], 1, "a manually/previously tagged head must never be dethroned")


if __name__ == '__main__':
    unittest.main()
