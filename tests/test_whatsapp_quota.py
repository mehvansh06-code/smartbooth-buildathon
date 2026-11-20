"""
Tests for the per-campaign WhatsApp message quota.

The property under test is that no WhatsApp message can be sent without a quota
reservation, under any circumstances: missing argument, exhausted balance, blocked
campaign, database failure, or concurrent requests.

Tests that need a live PostgreSQL (PGDATABASE, default `voter_platform_test`) skip themselves
when one is not reachable, so the suite stays runnable anywhere.
"""

import ast
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import meta_whatsapp  # noqa: E402
from electionapp import whatsapp_quota  # noqa: E402
from electionapp.dispatch.meta_cloud import MetaCloudChannel  # noqa: E402


class FakeCredit:
    """In-memory stand-in for a Reservation, for tests that need no database."""

    def __init__(self, allowance=1):
        self.remaining = allowance
        self.spent = 0

    def spend(self):
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        self.spent += 1
        return True

    def refund_one(self):
        if self.spent > 0:
            self.spent -= 1
            self.remaining += 1


def postgres_available():
    try:
        pool = whatsapp_quota.get_pool()
        conn = pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        finally:
            pool.putconn(conn)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The gate itself. These need no database.
# ---------------------------------------------------------------------------

class TestSendGate(unittest.TestCase):
    """No credit, no send. This is the core guarantee."""

    def setUp(self):
        self._orig_token = os.environ.get('META_WA_ACCESS_TOKEN', '')
        os.environ['META_WA_ACCESS_TOKEN'] = 'test_mock_token'
        meta_whatsapp.provider_pool.load_providers()

    def tearDown(self):
        if self._orig_token:
            os.environ['META_WA_ACCESS_TOKEN'] = self._orig_token
        else:
            os.environ.pop('META_WA_ACCESS_TOKEN', None)
        meta_whatsapp.provider_pool.load_providers()

    def test_slip_card_without_credit_argument_raises_and_sends_nothing(self):
        with patch.object(meta_whatsapp._session, 'post') as mock_post:
            with self.assertRaises(TypeError):
                meta_whatsapp.send_voter_slip_card(
                    phone='9414127202', voter_name='Test', part_no=1, sl_no=1,
                    booth_name='Booth', image_path='nonexistent.png'
                )
            mock_post.assert_not_called()

    def test_text_message_without_credit_argument_raises_and_sends_nothing(self):
        with patch.object(meta_whatsapp._session, 'post') as mock_post:
            with self.assertRaises(TypeError):
                meta_whatsapp.send_text_message('9414127202', 'hello')
            mock_post.assert_not_called()

    def test_explicit_none_credit_is_refused(self):
        with patch.object(meta_whatsapp._session, 'post') as mock_post:
            with self.assertRaises(whatsapp_quota.QuotaRequired):
                meta_whatsapp.send_text_message('9414127202', 'hello', credit=None)
            mock_post.assert_not_called()

    def test_exhausted_credit_is_refused(self):
        spent = FakeCredit(allowance=0)
        with patch.object(meta_whatsapp._session, 'post') as mock_post:
            with self.assertRaises(whatsapp_quota.QuotaExhausted):
                meta_whatsapp.send_text_message('9414127202', 'hello', credit=spent)
            mock_post.assert_not_called()

    def test_meta_cloud_channel_also_requires_a_credit(self):
        """MetaCloudChannel is directly constructible, so it carries its own gate."""
        channel = MetaCloudChannel(phone_number_id='123', access_token='tok')
        with patch('requests.post') as mock_post:
            with self.assertRaises(TypeError):
                channel.send_message(phone='919414127202', message='hi')
            with self.assertRaises(whatsapp_quota.QuotaRequired):
                channel.send_message(phone='919414127202', message='hi', credit=None)
            mock_post.assert_not_called()

    def test_failed_send_refunds_its_credit(self):
        """Meta does not bill a rejected message, so a failure must cost nothing."""
        credit = FakeCredit(allowance=3)
        bad = MagicMock()
        bad.status_code = 400
        bad.json.return_value = {'error': {'code': 131026, 'message': 'Receiver incapable'}}
        bad.text = 'error'

        with patch.object(meta_whatsapp._session, 'post', return_value=bad):
            ok, _data, err = meta_whatsapp.send_text_message(
                '9414127202', 'hello', credit=credit)

        self.assertFalse(ok)
        self.assertEqual(credit.spent, 0, "a failed send must not consume a credit")
        self.assertEqual(credit.remaining, 3)

    def test_successful_send_consumes_exactly_one_credit(self):
        credit = FakeCredit(allowance=3)
        good = MagicMock()
        good.status_code = 200
        good.json.return_value = {'messages': [{'id': 'wamid.TEST'}]}

        with patch.object(meta_whatsapp._session, 'post', return_value=good):
            ok, _data, _err = meta_whatsapp.send_text_message(
                '9414127202', 'hello', credit=credit)

        self.assertTrue(ok)
        self.assertEqual(credit.spent, 1)
        self.assertEqual(credit.remaining, 2)


class TestGateCoverageIsComplete(unittest.TestCase):
    """Regression guard: a new billable POST must not slip in ungated."""

    GATE_CALLS = {'_assert_billable', 'assert_billable'}
    MESSAGING_HINTS = ('/messages', 'aoc-portal', 'AOC_WA_API_URL', 'graph.facebook.com')

    def _functions_posting_messages(self, path):
        """Yields (func_name, calls_gate) for functions that POST to a messaging URL."""
        with open(path, encoding='utf-8') as fh:
            source = fh.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.dump(node)
            posts = any(
                isinstance(n, ast.Call)
                and (getattr(n.func, 'attr', None) == 'post')
                for n in ast.walk(node)
            )
            if not posts:
                continue
            if not any(h in body_src or h in ast.unparse(node) for h in self.MESSAGING_HINTS):
                continue
            calls_gate = any(
                isinstance(n, ast.Call)
                and (getattr(n.func, 'attr', None) in self.GATE_CALLS
                     or getattr(n.func, 'id', None) in self.GATE_CALLS)
                for n in ast.walk(node)
            )
            yield node.name, calls_gate

    def test_every_message_posting_function_is_gated(self):
        files = [
            os.path.join(PLATFORM_ROOT, 'meta_whatsapp.py'),
            os.path.join(PLATFORM_ROOT, 'electionapp', 'dispatch', 'meta_cloud.py'),
        ]
        # upload_media, the AOC dashboard ping, and internal AOC helper dispatchers
        # are not top-level send primitives (the caller holds the billable gate):
        exempt = {
            'upload_media', 'check_health', '_notify_aoc_dashboard',
            '_dispatch_aoc_slip', '_dispatch_aoc_credentials', '_dispatch_aoc_magic_link'
        }
        ungated = []
        checked = 0
        for path in files:
            for name, gated in self._functions_posting_messages(path):
                if name in exempt:
                    continue
                checked += 1
                if not gated:
                    ungated.append(f"{os.path.basename(path)}::{name}")

        self.assertGreater(checked, 0, "gate audit found no send functions to check")
        self.assertEqual(
            ungated, [],
            "These functions POST a WhatsApp message without calling the quota gate: "
            + ", ".join(ungated)
        )

    def test_no_ungated_calls_to_send_primitives_in_app_code(self):
        """Every production call site must pass credit=..."""
        targets = {'send_voter_slip_card', 'send_text_message'}
        skip_dirs = {'.git', '__pycache__', '.venv', 'node_modules', 'data',
                     'legacy_campaigns', 'scratch', 'tests'}
        offenders = []
        for root, dirs, files in os.walk(PLATFORM_ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if not f.endswith('.py'):
                    continue
                fp = os.path.join(root, f)
                try:
                    with open(fp, encoding='utf-8') as fh:
                        tree = ast.parse(fh.read())
                except Exception:
                    continue
                for n in ast.walk(tree):
                    if not isinstance(n, ast.Call):
                        continue
                    name = getattr(n.func, 'attr', None) or getattr(n.func, 'id', None)
                    if name in targets and n.args is not None:
                        if 'credit' not in {k.arg for k in n.keywords}:
                            rel = os.path.relpath(fp, PLATFORM_ROOT)
                            offenders.append(f"{rel}:{n.lineno} {name}")
        self.assertEqual(offenders, [],
                         "Ungated WhatsApp send call sites: " + ", ".join(offenders))


class TestReservationAccounting(unittest.TestCase):
    """Reservation arithmetic, without touching the database."""

    def test_spend_and_refund_are_symmetric(self):
        r = whatsapp_quota.Reservation('rid', 1, 5)
        self.assertEqual(r.remaining, 5)
        for _ in range(3):
            self.assertTrue(r.spend())
        self.assertEqual((r.spent, r.remaining), (3, 2))
        r.refund_one()
        self.assertEqual((r.spent, r.remaining), (2, 3))

    def test_cannot_spend_past_the_grant(self):
        r = whatsapp_quota.Reservation('rid', 1, 2)
        self.assertTrue(r.spend())
        self.assertTrue(r.spend())
        self.assertFalse(r.spend(), "a reservation must never hand out more than it holds")
        self.assertEqual(r.remaining, 0)

    def test_refund_cannot_invent_credits(self):
        r = whatsapp_quota.Reservation('rid', 1, 1)
        r.refund_one()
        r.refund_one()
        self.assertEqual((r.spent, r.remaining), (0, 1))

    def test_zero_grant_reservation_spends_nothing(self):
        r = whatsapp_quota.Reservation('rid', 1, 0)
        self.assertFalse(r.spend())


class TestFailsClosed(unittest.TestCase):
    """A quota that cannot be read must block sending, never allow it."""

    def test_db_failure_raises_quota_unavailable(self):
        broken = MagicMock()
        broken.getconn.side_effect = Exception("connection refused")
        with patch.object(whatsapp_quota, 'get_pool', return_value=broken):
            with self.assertRaises(whatsapp_quota.QuotaUnavailable):
                whatsapp_quota.reserve(1, 10)

    def test_unknown_campaign_is_refused(self):
        with patch.object(whatsapp_quota, '_txn') as mock_txn:
            cur = MagicMock()
            cur.fetchone.return_value = None
            mock_txn.return_value.__enter__.return_value = cur
            with self.assertRaises(whatsapp_quota.QuotaUnavailable):
                whatsapp_quota.resolve_tenant_id('no_such_campaign')

    def test_empty_tenant_is_refused(self):
        for bad in (None, '', '   ', True):
            with self.assertRaises(whatsapp_quota.QuotaUnavailable):
                whatsapp_quota.resolve_tenant_id(bad)

    def test_zero_or_negative_request_is_refused(self):
        with self.assertRaises(whatsapp_quota.QuotaExhausted):
            whatsapp_quota.reserve(1, 0)
        with self.assertRaises(whatsapp_quota.QuotaExhausted):
            whatsapp_quota.reserve(1, -5)


class TestBulkBlasterRequiresTenant(unittest.TestCase):
    """The bulk blaster was tenant-blind; it must now refuse to run unmetered."""

    def test_start_campaign_without_tenant_is_refused(self):
        import bulk_campaign_service
        mgr = bulk_campaign_service.BulkCampaignManager()
        for bad in (None, '', '   '):
            ok, msg = mgr.start_campaign(tenant_slug=bad)
            self.assertFalse(ok)
            self.assertIn('tenant_slug', msg)
        self.assertEqual(mgr.state['status'], 'idle',
                         "a refused campaign must not start a worker thread")


# ---------------------------------------------------------------------------
# Live PostgreSQL. These prove the atomicity that the in-memory tests cannot.
# ---------------------------------------------------------------------------

@unittest.skipUnless(postgres_available(), "PostgreSQL voter_platform not reachable")
class TestQuotaAgainstPostgres(unittest.TestCase):

    TEST_SLUG = None
    TEST_TID = None

    @classmethod
    def setUpClass(cls):
        whatsapp_quota.ensure_quota_schema()
        pool = whatsapp_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT tenant_id, campaign_slug FROM tenants ORDER BY tenant_id LIMIT 1")
                row = cur.fetchone()
                if not row:
                    raise unittest.SkipTest("no tenants provisioned")
                cls.TEST_TID, cls.TEST_SLUG = row[0], row[1]
        finally:
            pool.putconn(conn)

    def _reset(self, granted, consumed=0, reserved=0, blocked=False):
        """Puts the test campaign's quota into a known state."""
        pool = whatsapp_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO whatsapp_quota (tenant_id, granted, consumed, reserved, is_blocked)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (tenant_id) DO UPDATE SET"
                    "   granted = EXCLUDED.granted, consumed = EXCLUDED.consumed,"
                    "   reserved = EXCLUDED.reserved, is_blocked = EXCLUDED.is_blocked",
                    (self.TEST_TID, granted, consumed, reserved, blocked)
                )
                cur.execute(
                    "DELETE FROM whatsapp_quota_reservation WHERE tenant_id = %s",
                    (self.TEST_TID,)
                )
        finally:
            pool.putconn(conn)

    def test_slug_resolves_to_tenant_id(self):
        self.assertEqual(
            whatsapp_quota.resolve_tenant_id(self.TEST_SLUG), self.TEST_TID)

    def test_partial_grant_when_fewer_credits_remain(self):
        self._reset(granted=500)
        with whatsapp_quota.reserve(self.TEST_TID, 2000) as credit:
            self.assertEqual(credit.granted, 500,
                             "should grant what is left, not refuse the whole batch")
            self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 0)
        self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 500,
                         "unspent credits must be released on close")

    def test_close_banks_only_what_was_spent(self):
        self._reset(granted=100)
        with whatsapp_quota.reserve(self.TEST_TID, 10) as credit:
            for _ in range(4):
                credit.spend()
        q = whatsapp_quota.get_quota(self.TEST_TID)
        self.assertEqual(q['consumed'], 4)
        self.assertEqual(q['reserved'], 0)
        self.assertEqual(q['available'], 96)

    def test_release_happens_even_when_the_dispatch_raises(self):
        self._reset(granted=100)
        with self.assertRaises(RuntimeError):
            with whatsapp_quota.reserve(self.TEST_TID, 50) as credit:
                credit.spend()
                raise RuntimeError("dispatch blew up mid-batch")
        q = whatsapp_quota.get_quota(self.TEST_TID)
        self.assertEqual(q['reserved'], 0, "a crash must not leak held credits")
        self.assertEqual(q['consumed'], 1)

    def test_exhausted_balance_refuses(self):
        self._reset(granted=10, consumed=10)
        with self.assertRaises(whatsapp_quota.QuotaExhausted):
            whatsapp_quota.reserve(self.TEST_TID, 1)

    def test_blocked_campaign_refuses_even_with_credits(self):
        self._reset(granted=1000, blocked=True)
        with self.assertRaises(whatsapp_quota.QuotaExhausted):
            whatsapp_quota.reserve(self.TEST_TID, 1)
        whatsapp_quota.set_blocked(self.TEST_TID, False)
        with whatsapp_quota.reserve(self.TEST_TID, 1) as credit:
            self.assertEqual(credit.granted, 1)

    def test_concurrent_holds_never_exceed_the_balance(self):
        """The real race: 8 threads holding at once can never hold more than exists.

        Reservations are deliberately NOT closed until every thread has finished,
        because closing returns unspent credits; holding them all simultaneously is
        what proves the FOR UPDATE lock actually serialises the arithmetic.
        """
        self._reset(granted=500)
        held = []
        lock = threading.Lock()
        errors = []

        def worker():
            try:
                res = whatsapp_quota.reserve(self.TEST_TID, 100)
                with lock:
                    held.append(res)
            except whatsapp_quota.QuotaExhausted:
                pass
            except Exception as e:      # pragma: no cover - surfaced below
                with lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        try:
            self.assertEqual(errors, [], f"unexpected errors: {errors}")
            total = sum(r.granted for r in held)
            self.assertEqual(
                total, 500,
                f"handed out {total} of 500 credits concurrently: "
                f"{[r.granted for r in held]}"
            )
            self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 0)
        finally:
            for r in held:
                r.close()

    def test_concurrent_spending_never_exceeds_the_balance(self):
        """Every thread spends everything it gets; total consumed must cap at granted."""
        self._reset(granted=500)
        lock = threading.Lock()
        errors = []

        def worker():
            try:
                with whatsapp_quota.reserve(self.TEST_TID, 100) as credit:
                    while credit.spend():
                        pass
            except whatsapp_quota.QuotaExhausted:
                pass
            except Exception as e:      # pragma: no cover
                with lock:
                    errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"unexpected errors: {errors}")
        q = whatsapp_quota.get_quota(self.TEST_TID)
        self.assertEqual(q['reserved'], 0, "no holds may be left dangling")
        self.assertLessEqual(
            q['consumed'], 500,
            f"spent {q['consumed']} credits against a 500 grant - overspend!"
        )
        self.assertEqual(q['consumed'], 500,
                         "all 500 credits should have been consumed, none wasted")

    def test_set_granted_cannot_drop_below_consumed(self):
        self._reset(granted=100, consumed=60)
        q = whatsapp_quota.set_granted(self.TEST_TID, 10)
        self.assertGreaterEqual(q['granted'], 60)
        self.assertGreaterEqual(q['available'], 0,
                                "counters must never be allowed to go negative")

    def test_grant_tops_up_the_balance(self):
        self._reset(granted=100, consumed=100)
        q = whatsapp_quota.grant(self.TEST_TID, 250, note='test top-up')
        self.assertEqual(q['granted'], 350)
        self.assertEqual(q['available'], 250)

    def test_reaper_reclaims_abandoned_holds(self):
        self._reset(granted=100)
        res = whatsapp_quota.reserve(self.TEST_TID, 40)
        self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 60)

        # Simulate a worker that died without closing its reservation.
        pool = whatsapp_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE whatsapp_quota_reservation"
                    "   SET expires_at = NOW() - interval '1 hour'"
                    " WHERE reservation_id = %s",
                    (res.reservation_id,)
                )
        finally:
            pool.putconn(conn)

        reclaimed = whatsapp_quota.reap_stale_reservations()
        self.assertGreaterEqual(reclaimed, 1)
        self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 100)

    def test_exhaustion_from_a_crashed_worker_self_heals(self):
        """A dead worker's hold must not permanently block a campaign."""
        self._reset(granted=100)
        abandoned = whatsapp_quota.reserve(self.TEST_TID, 100)
        self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 0)

        # Age the hold as if the worker died and its TTL has passed.
        pool = whatsapp_quota.get_pool()
        conn = pool.getconn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE whatsapp_quota_reservation"
                    "   SET expires_at = NOW() - interval '1 hour'"
                    " WHERE reservation_id = %s",
                    (abandoned.reservation_id,)
                )
        finally:
            pool.putconn(conn)

        # reserve() should reclaim the stale hold and succeed without any manual step.
        with whatsapp_quota.reserve(self.TEST_TID, 30) as credit:
            self.assertEqual(credit.granted, 30,
                             "stale credits should have been reclaimed automatically")

    def test_live_hold_is_not_stolen_by_the_reaper(self):
        """Only EXPIRED holds may be reclaimed; an in-flight dispatch keeps its credits."""
        self._reset(granted=100)
        with whatsapp_quota.reserve(self.TEST_TID, 100) as live:
            self.assertEqual(live.granted, 100)
            self.assertEqual(whatsapp_quota.reap_stale_reservations(), 0)
            self.assertEqual(whatsapp_quota.get_quota(self.TEST_TID)['available'], 0)
            with self.assertRaises(whatsapp_quota.QuotaExhausted):
                whatsapp_quota.reserve(self.TEST_TID, 1)

    def test_ledger_records_quota_events(self):
        self._reset(granted=0)
        whatsapp_quota.grant(self.TEST_TID, 100, note='ledger check')
        actions = [e['action'] for e in whatsapp_quota.get_ledger(self.TEST_TID, limit=5)]
        self.assertIn('grant', actions)


if __name__ == '__main__':
    unittest.main(verbosity=2)
