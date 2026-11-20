"""
test_audit_log.py
Tests for the generic "who did what" audit-log feature:
  - electionapp/audit.py: ensure_audit_schema() bootstrap + @audit_action decorator
  - log_audit_event() on PostgresVoterRepository
  - the end-to-end wiring of @audit_action on a real blueprint route
  - the super-admin-only admin UI API (/api/admin/audit_log)

Follows the conventions established in test_repository_contract.py and
test_universal_campaign_switcher.py (FORCE_AUTH_CHECK=1 to exercise the REAL auth/role
gates, since FLASK_TESTING=1 deliberately bypasses them).
"""

import os
import sys
import json
import unittest

import psycopg2
import psycopg2.extras

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

from electionapp.repo import get_repository
from electionapp.audit import ensure_audit_schema
from electionapp import create_app


PG_CONN_KWARGS = dict(
    dbname=os.environ.get("PGDATABASE", "voter_platform_test"),
    user=os.environ.get("PGUSER", "postgres"),
    password=os.environ.get("PGPASSWORD", "postgres"),
    host=os.environ.get("PGHOST", "localhost"),
    port=int(os.environ.get("PGPORT", 5432)),
)


class TestAuditLogRepository(unittest.TestCase):
    """Repo-layer tests for log_audit_event on PostgreSQL backend,
    and for ensure_audit_schema()'s idempotency."""

    @classmethod
    def setUpClass(cls):
        cls.postgres_repo = get_repository("postgres")
        cls.pg_conn = psycopg2.connect(**PG_CONN_KWARGS)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "postgres_repo", None):
            cls.postgres_repo.close()
        if getattr(cls, "pg_conn", None):
            cls.pg_conn.close()

    def test_01_ensure_audit_schema_idempotent(self):
        """ensure_audit_schema(repo) is self-applying DDL run on every boot — must be
        safe to call repeatedly without raising."""
        ensure_audit_schema(self.postgres_repo)
        ensure_audit_schema(self.postgres_repo)  # second call must not raise

    def test_02_log_audit_event_postgres(self):
        """Verifies log_audit_event() inserts a correct row into the Postgres audit_log
        table, and that `details` round-trips as a dict (JSONB), not a raw JSON string."""
        action = "test.audit_pytest_postgres"
        username = "pytest_audit_user_pg"
        try:
            self.postgres_repo.log_audit_event(
                tenant_id=1,
                user_id=4242,
                username=username,
                action=action,
                entity_type="test",
                entity_id="123",
                details={"k": "v"},
                ip_address="127.0.0.1",
                user_agent="pytest",
            )

            with self.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT tenant_id, user_id, username, action, entity_type, entity_id,
                           details, ip_address, user_agent
                    FROM audit_log
                    WHERE username = %s AND action = %s
                    ORDER BY id DESC LIMIT 1
                    """,
                    (username, action),
                )
                row = cur.fetchone()

            self.assertIsNotNone(row, "Expected inserted audit_log row to be found")
            self.assertEqual(row["tenant_id"], 1)
            self.assertEqual(row["user_id"], 4242)
            self.assertEqual(row["username"], username)
            self.assertEqual(row["action"], action)
            self.assertEqual(row["entity_type"], "test")
            self.assertEqual(row["entity_id"], "123")
            # JSONB column: psycopg2 parses it back into a dict, not a raw JSON string
            self.assertIsInstance(row["details"], dict)
            self.assertEqual(row["details"], {"k": "v"})
            self.assertEqual(str(row["ip_address"]), "127.0.0.1")
            self.assertEqual(row["user_agent"], "pytest")
        finally:
            with self.pg_conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM audit_log WHERE username = %s AND action = %s",
                    (username, action),
                )
            self.pg_conn.commit()



class TestAuditActionBlueprintIntegration(unittest.TestCase):
    """End-to-end test: hitting a real @audit_action-decorated route
    (turnout.py's /api/turnout/mark_single, @audit_action('voter.mark_voted', ...))
    through the Flask test client and verifying a matching audit_log row lands
    in Postgres with the right action/entity_id/user_id."""

    TENANT_ID = 3  # ward82
    VOTER_ID = 3514  # confirmed present & initially unvoted in the local dev DB
    USERNAME = "pytest_mark_voted_user"

    @classmethod
    def setUpClass(cls):
        os.environ["FLASK_TESTING"] = "1"
        cls.app = create_app("ward82", storage_backend="postgres")
        cls.app.config["TESTING"] = True
        cls.pg_conn = psycopg2.connect(**PG_CONN_KWARGS)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "pg_conn", None):
            cls.pg_conn.close()

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 7171
            sess["username"] = self.USERNAME
            sess["role"] = "super_admin"
            sess["tenant_slug"] = "ward82"

        # Capture original voter state so the test can restore it exactly
        # (this is a shared local dev DB, not a throwaway fixture).
        with self.pg_conn.cursor() as cur:
            cur.execute(
                "SELECT is_voted, voted_at, voted_marked_by FROM voters "
                "WHERE tenant_id = %s AND voter_id = %s",
                (self.TENANT_ID, self.VOTER_ID),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row, "Fixture voter must exist in ward82 for this test")
        self._original_state = row

    def tearDown(self):
        is_voted, voted_at, voted_marked_by = self._original_state
        with self.pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE voters SET is_voted = %s, voted_at = %s, voted_marked_by = %s "
                "WHERE tenant_id = %s AND voter_id = %s",
                (is_voted, voted_at, voted_marked_by, self.TENANT_ID, self.VOTER_ID),
            )
            # Scoped to our own test username so we never remove unrelated audit history.
            cur.execute(
                "DELETE FROM audit_log WHERE tenant_id = %s AND entity_id = %s "
                "AND action = %s AND username = %s",
                (self.TENANT_ID, str(self.VOTER_ID), "voter.mark_voted", self.USERNAME),
            )
        self.pg_conn.commit()

    def test_mark_single_voter_route_writes_audit_log_row(self):
        original_is_voted = bool(self._original_state[0])
        new_is_voted = not original_is_voted

        resp = self.client.post(
            "/api/turnout/mark_single",
            json={"voter_id": self.VOTER_ID, "is_voted": new_is_voted},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("is_voted"), 1 if new_is_voted else 0)

        with self.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT tenant_id, user_id, username, action, entity_type, entity_id "
                "FROM audit_log WHERE tenant_id = %s AND entity_id = %s "
                "AND action = %s AND username = %s ORDER BY id DESC LIMIT 1",
                (self.TENANT_ID, str(self.VOTER_ID), "voter.mark_voted", self.USERNAME),
            )
            audit_row = cur.fetchone()

        self.assertIsNotNone(
            audit_row, "Expected @audit_action to write an audit_log row for the mark-voted route"
        )
        self.assertEqual(audit_row["tenant_id"], self.TENANT_ID)
        self.assertEqual(audit_row["user_id"], 7171)
        self.assertEqual(audit_row["username"], self.USERNAME)
        self.assertEqual(audit_row["action"], "voter.mark_voted")
        self.assertEqual(audit_row["entity_type"], "voter")
        self.assertEqual(audit_row["entity_id"], str(self.VOTER_ID))


class TestAuditLogAdminRoute(unittest.TestCase):
    """Minimal super-admin gating test for GET /api/admin/audit_log.

    Uses the established FORCE_AUTH_CHECK=1 pattern (see test_universal_campaign_switcher.py):
    FLASK_TESTING=1 deliberately bypasses the real role gate (defaults the session to
    super_admin for every request), so without forcing the real check this test would
    trivially pass a 200 for every role and never exercise @super_admin_required at all.
    """

    @classmethod
    def setUpClass(cls):
        cls._prev_force_auth = os.environ.get("FORCE_AUTH_CHECK")
        os.environ["FORCE_AUTH_CHECK"] = "1"
        cls.app = create_app("ward82", storage_backend="postgres")
        cls.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        if cls._prev_force_auth is None:
            os.environ.pop("FORCE_AUTH_CHECK", None)
        else:
            os.environ["FORCE_AUTH_CHECK"] = cls._prev_force_auth

    def setUp(self):
        self.client = self.app.test_client()

    def test_super_admin_gets_200_with_rows(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "admin"
            sess["role"] = "super_admin"
            sess["tenant_slug"] = "*"

        resp = self.client.get("/api/admin/audit_log")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertIsInstance(data.get("rows"), list)
        self.assertIn("total", data)

    def test_non_super_admin_gets_403(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 9999
            sess["username"] = "test_operator"
            sess["role"] = "operator"
            sess["tenant_slug"] = "ward82"

        resp = self.client.get("/api/admin/audit_log")
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data.get("success"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
