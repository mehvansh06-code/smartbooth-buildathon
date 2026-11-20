"""
Unit tests for db_qa_service SQL generation, validation, sanitization, and execution.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "electionapp", "services")))
from db_qa_service import (
    sanitize_and_validate_sql,
    _heuristic_sql_fallback,
    ask_database_question
)

class TestDbQaService(unittest.TestCase):
    def test_sql_sanitization_valid(self):
        valid_sql = "SELECT COUNT(*) FROM voters_t5 WHERE ward_no = 7 AND status != 'Deleted'"
        ok, clean = sanitize_and_validate_sql(valid_sql, "voters_t5")
        self.assertTrue(ok)
        self.assertIn("voters_t5", clean)

    def test_sql_sanitization_forbidden_insert(self):
        bad_sql = "INSERT INTO voters_t5 (name_en) VALUES ('Hacker')"
        ok, msg = sanitize_and_validate_sql(bad_sql, "voters_t5")
        self.assertFalse(ok)
        self.assertTrue("select" in msg.lower() or "forbidden" in msg.lower())

    def test_sql_sanitization_forbidden_drop(self):
        bad_sql = "DROP TABLE voters_t5"
        ok, msg = sanitize_and_validate_sql(bad_sql, "voters_t5")
        self.assertFalse(ok)
        self.assertTrue("select" in msg.lower() or "forbidden" in msg.lower())

    def test_sql_sanitization_cross_tenant_prevention(self):
        # Querying voters_t99 when target is voters_t5
        bad_sql = "SELECT * FROM voters_t99 WHERE ward_no = 1"
        ok, msg = sanitize_and_validate_sql(bad_sql, "voters_t5")
        self.assertFalse(ok)
        self.assertIn("cross-tenant", msg.lower())

    def test_sql_sanitization_enforces_limit(self):
        list_sql = "SELECT name_en, age FROM voters_t5 WHERE ward_no = 7"
        ok, clean = sanitize_and_validate_sql(list_sql, "voters_t5")
        self.assertTrue(ok)
        self.assertIn("LIMIT 50", clean)

    def test_heuristic_fallback_ward_count(self):
        res = _heuristic_sql_fallback("वार्ड 7 में कितने वोट हैं?", "voters_t5")
        self.assertIn("COUNT(*)", res["sql"])
        self.assertIn("ward_no = 7", res["sql"])

    def test_heuristic_fallback_female_count(self):
        res = _heuristic_sql_fallback("वार्ड 7 में कितने महिला मतदाता हैं?", "voters_t5")
        self.assertIn("COUNT(*)", res["sql"])
        self.assertTrue("gender ILIKE 'F" in res["sql"])

    def test_ask_database_question_pipeline(self):
        res = ask_database_question("वार्ड 7 में कितने महिला मतदाता हैं?", tenant_id=5, campaign_slug="sarana")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertTrue(len(res["answer"]) > 0)
        self.assertIn("sql_executed", res)

if __name__ == "__main__":
    unittest.main()
