"""
Tests for per-campaign Voice Broadcast (OBD) quota management.

Guarantees:
1. ensure_voice_schema() idempotency across multiple runs.
2. get_quota(tenant_slug) handles configured vs unconfigured campaigns.
3. reserve(tenant_slug, want):
   - Fails closed with QuotaExhausted when available credits < want.
   - Moves credits atomically from available to reserved when credits exist.
4. spend(tenant_slug, count) and refund(tenant_slug, count) integrity.
5. grant(tenant_slug, count) updates balance and writes ledger record.
6. Reservation lifecycle (spend, refund, close) and reap_stale_reservations().
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import voice_quota


def postgres_available():
    try:
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        finally:
            pool.putconn(conn)
    except Exception:
        return False


class TestVoiceReservationInMemory(unittest.TestCase):
    """In-memory testing of Reservation logic without requiring database connection."""

    def test_reservation_spend_and_refund(self):
        resv = voice_quota.Reservation(reservation_id="test-res-1", tenant_id=1, granted=10)
        self.assertEqual(resv.remaining, 10)
        self.assertEqual(resv.spent, 0)

        # Spend 4 credits
        self.assertTrue(resv.spend(4))
        self.assertEqual(resv.remaining, 6)
        self.assertEqual(resv.spent, 4)

        # Spend beyond remaining fails
        self.assertFalse(resv.spend(7))
        self.assertEqual(resv.remaining, 6)
        self.assertEqual(resv.spent, 4)

        # Refund 2 credits
        resv.refund(2)
        self.assertEqual(resv.remaining, 8)
        self.assertEqual(resv.spent, 2)

        # Cannot refund more than spent
        resv.refund(10)
        self.assertEqual(resv.remaining, 8)
        self.assertEqual(resv.spent, 2)

    def test_reservation_close_idempotent(self):
        resv = voice_quota.Reservation(reservation_id="test-res-2", tenant_id=1, granted=5)
        resv._closed = True
        # Once closed, spending fails
        self.assertFalse(resv.spend(1))


class TestVoiceQuotaFailClosed(unittest.TestCase):
    """Test fail-closed behavior on missing or invalid tenant/db states."""

    def test_invalid_tenants_raise_quota_unavailable(self):
        for bad in (None, '', '   ', True, False):
            with self.assertRaises(voice_quota.QuotaUnavailable):
                voice_quota.resolve_tenant_id(bad)

    def test_unknown_slug_raises_quota_unavailable(self):
        with patch.object(voice_quota, '_txn') as mock_txn:
            cur = MagicMock()
            cur.fetchone.return_value = None
            mock_txn.return_value.__enter__.return_value = cur
            with self.assertRaises(voice_quota.QuotaUnavailable):
                voice_quota.resolve_tenant_id('nonexistent_slug_12345')

    def test_zero_or_negative_reserve_raises_quota_exhausted(self):
        with patch.object(voice_quota, 'resolve_tenant_id', return_value=1):
            with self.assertRaises(voice_quota.QuotaExhausted):
                voice_quota.reserve('any_slug', 0)
            with self.assertRaises(voice_quota.QuotaExhausted):
                voice_quota.reserve('any_slug', -10)


@unittest.skipUnless(postgres_available(), "PostgreSQL voter_platform not reachable")
class TestVoiceQuotaPostgres(unittest.TestCase):
    """Live PostgreSQL tests for schema, quota reservation, spend/refund, and grant ledger."""

    TEST_TID = None
    TEST_SLUG = None

    @classmethod
    def setUpClass(cls):
        voice_quota.ensure_voice_schema()
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                # Find or ensure a test tenant exists
                cur.execute("SELECT tenant_id, campaign_slug FROM tenants ORDER BY tenant_id LIMIT 1")
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        "INSERT INTO tenants (campaign_slug, title, client_name, is_active)"
                        " VALUES ('test_voice_tenant', 'Voice Test Campaign', 'Test Client', TRUE)"
                        " RETURNING tenant_id, campaign_slug"
                    )
                    row = cur.fetchone()
                cls.TEST_TID = row[0]
                cls.TEST_SLUG = row[1]
        finally:
            pool.putconn(conn)

    def setUp(self):
        # Reset voice quota state for this tenant before each test
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM voice_quota_ledger WHERE tenant_id = %s", (self.TEST_TID,))
                cur.execute("DELETE FROM voice_quota_reservation WHERE tenant_id = %s", (self.TEST_TID,))
                cur.execute("DELETE FROM voice_call_records WHERE tenant_id = %s", (self.TEST_TID,))
                cur.execute("DELETE FROM voice_campaigns WHERE tenant_id = %s", (self.TEST_TID,))
                cur.execute(
                    "INSERT INTO voice_quota (tenant_id, granted, consumed, reserved, is_blocked)"
                    " VALUES (%s, 0, 0, 0, FALSE)"
                    " ON CONFLICT (tenant_id) DO UPDATE"
                    "   SET granted = 0, consumed = 0, reserved = 0, is_blocked = FALSE",
                    (self.TEST_TID,)
                )
        finally:
            pool.putconn(conn)

    def tearDown(self):
        pass

    def test_ensure_voice_schema_idempotency(self):
        """Calling ensure_voice_schema() multiple times should succeed cleanly."""
        for _ in range(3):
            voice_quota.ensure_voice_schema()

        # Verify all tables exist
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                tables = [
                    'voice_quota', 'voice_quota_reservation', 'voice_quota_ledger',
                    'voice_audios', 'voice_campaigns', 'voice_call_records'
                ]
                for table in tables:
                    cur.execute(
                        "SELECT to_regclass(%s)", (f"public.{table}",)
                    )
                    res = cur.fetchone()[0]
                    self.assertIsNotNone(res, f"Table {table} must exist")
        finally:
            pool.putconn(conn)

    def test_get_quota_configured_vs_unconfigured(self):
        """get_quota returns configured=True for existing rows and configured=False otherwise."""
        q = voice_quota.get_quota(self.TEST_SLUG)
        self.assertTrue(q['configured'])
        self.assertEqual(q['granted'], 0)
        self.assertEqual(q['available'], 0)
        self.assertFalse(q['can_send'])

        # Query an unconfigured tenant ID (e.g., 999999)
        unconf = voice_quota.get_quota(999999)
        self.assertFalse(unconf['configured'])
        self.assertEqual(unconf['available'], 0)
        self.assertFalse(unconf['can_send'])

    def test_reserve_fails_closed_when_credits_exhausted(self):
        """reserve() fails closed with QuotaExhausted if available credits < want."""
        with self.assertRaises(voice_quota.QuotaExhausted) as ctx:
            voice_quota.reserve(self.TEST_SLUG, 5)
        self.assertIn("अपर्याप्त", str(ctx.exception))

    def test_reserve_successfully_moves_credits_to_reserved(self):
        """reserve() moves credits from available to reserved under row lock."""
        voice_quota.grant(self.TEST_SLUG, 50, note="Initial test grant")
        q_before = voice_quota.get_quota(self.TEST_SLUG)
        self.assertEqual(q_before['available'], 50)
        self.assertEqual(q_before['reserved'], 0)

        # Reserve 20 credits
        with voice_quota.reserve(self.TEST_SLUG, 20) as resv:
            self.assertEqual(resv.remaining, 20)
            q_during = voice_quota.get_quota(self.TEST_SLUG)
            self.assertEqual(q_during['reserved'], 20)
            self.assertEqual(q_during['available'], 30)

            # Spend 15 of them
            resv.spend(15)

        # After exiting context manager, unused 5 credits are released
        q_after = voice_quota.get_quota(self.TEST_SLUG)
        self.assertEqual(q_after['consumed'], 15)
        self.assertEqual(q_after['reserved'], 0)
        self.assertEqual(q_after['available'], 35)

    def test_spend_and_refund_integrity(self):
        """Direct spend() and refund() adjust consumed balance and maintain integrity."""
        voice_quota.grant(self.TEST_SLUG, 100)

        # Direct spend 30
        q = voice_quota.spend(self.TEST_SLUG, 30, note="Ad-hoc blast")
        self.assertEqual(q['consumed'], 30)
        self.assertEqual(q['available'], 70)

        # Cannot spend more than available
        with self.assertRaises(voice_quota.QuotaExhausted):
            voice_quota.spend(self.TEST_SLUG, 80)

        # Refund 10
        q_ref = voice_quota.refund(self.TEST_SLUG, 10, note="Carrier failure refund")
        self.assertEqual(q_ref['consumed'], 20)
        self.assertEqual(q_ref['available'], 80)

        # Zero spend / refund are no-ops
        q_zero = voice_quota.spend(self.TEST_SLUG, 0)
        self.assertEqual(q_zero['consumed'], 20)
        q_zero_ref = voice_quota.refund(self.TEST_SLUG, 0)
        self.assertEqual(q_zero_ref['consumed'], 20)

    def test_grant_updates_balance_and_writes_ledger_record(self):
        """grant() increases balance and inserts an immutable audit row into voice_quota_ledger."""
        voice_quota.grant(self.TEST_SLUG, 120, note="Test 120 voice credits grant")
        q = voice_quota.get_quota(self.TEST_SLUG)
        self.assertEqual(q['granted'], 120)
        self.assertEqual(q['available'], 120)

        # Verify ledger row exists
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT action, amount, balance_after, note FROM voice_quota_ledger"
                    " WHERE tenant_id = %s ORDER BY ledger_id DESC LIMIT 1",
                    (self.TEST_TID,)
                )
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], 'grant')
                self.assertEqual(row[1], 120)
                self.assertEqual(row[2], 120)
                self.assertIn("Test 120 voice credits grant", row[3])
        finally:
            pool.putconn(conn)

    def test_blocked_campaign_prevents_reserve_and_spend(self):
        """set_blocked(..., True) sets is_blocked and blocks reservations and sends."""
        voice_quota.grant(self.TEST_SLUG, 100)
        voice_quota.set_blocked(self.TEST_SLUG, True, note="Admin block")

        q = voice_quota.get_quota(self.TEST_SLUG)
        self.assertTrue(q['is_blocked'])
        self.assertFalse(q['can_send'])

        with self.assertRaises(voice_quota.QuotaExhausted) as ctx:
            voice_quota.reserve(self.TEST_SLUG, 10)
        self.assertIn("रोक दी गई है", str(ctx.exception))

        with self.assertRaises(voice_quota.QuotaExhausted):
            voice_quota.spend(self.TEST_SLUG, 5)

        # Unblock
        voice_quota.set_blocked(self.TEST_SLUG, False)
        q_unblocked = voice_quota.get_quota(self.TEST_SLUG)
        self.assertFalse(q_unblocked['is_blocked'])
        self.assertTrue(q_unblocked['can_send'])

    def test_reap_stale_reservations(self):
        """Expired open reservations have their held credits restored to available."""
        voice_quota.grant(self.TEST_SLUG, 50)
        resv = voice_quota.reserve(self.TEST_SLUG, 25)
        q1 = voice_quota.get_quota(self.TEST_SLUG)
        self.assertEqual(q1['reserved'], 25)
        self.assertEqual(q1['available'], 25)

        # Manually backdate the reservation in DB to make it expired
        pool = voice_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE voice_quota_reservation"
                    "   SET expires_at = NOW() - INTERVAL '1 hour'"
                    " WHERE reservation_id = %s",
                    (resv.reservation_id,)
                )
        finally:
            pool.putconn(conn)

        reclaimed = voice_quota.reap_stale_reservations()
        self.assertGreaterEqual(reclaimed, 1)

        q2 = voice_quota.get_quota(self.TEST_SLUG)
        self.assertEqual(q2['reserved'], 0)
        self.assertEqual(q2['available'], 50)


if __name__ == '__main__':
    unittest.main()
