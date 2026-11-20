"""
Per-campaign WhatsApp message quota (lifetime credit balance).

Every outbound WhatsApp message is a metered, billable cost. This module is the
single source of truth for how many messages a campaign is still allowed to send,
and it is the ONLY place that mutates the counters.

Design notes
------------
* ``available = granted - consumed - reserved``.
* A dispatch must first ``reserve(tenant, want)``, which atomically moves credits
  from *available* into *reserved* under a row lock. Credits are therefore taken
  out of circulation BEFORE any HTTP call happens, so concurrent requests across
  all Gunicorn workers can never collectively overspend.
* ``Reservation.spend()`` is an in-memory decrement of already-reserved credits
  (no DB round-trip), so a 2000-recipient blast costs one reservation query, not
  2000. The DB is reconciled once in ``Reservation.close()``.
* If the quota check cannot be completed for ANY reason, ``QuotaUnavailable`` is
  raised and the caller must not send. We fail CLOSED on purpose: the pre-existing
  ``user_manager.get_sub_account_quota`` fails open (silently returns used=0) and
  that is exactly the bug this module must not reproduce.
* ``reap_stale_reservations()`` returns credits held by crashed workers. It can
  only ever *increase* availability, so it can never cause an overspend.

Counters are deliberately NOT derived from ``dispatch_history``: ``/api/history/log``
lets a client self-report a "sent" row with no send at all, and the wa.me channel
writes success rows for link-only shares. Those rows are not trustworthy for billing.
"""

import os
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Optional, Union

import psycopg2
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger("smartbooth.whatsapp_quota")

# How long a reservation may stay open before the reaper may reclaim it.
RESERVATION_TTL_HOURS = int(os.environ.get("QUOTA_RESERVATION_TTL_HOURS", 24))

# Grant applied to campaigns seeded by scripts/backfill_whatsapp_quota.py
DEFAULT_BACKFILL_GRANT = int(os.environ.get("QUOTA_BACKFILL_GRANT", 100))


class QuotaError(RuntimeError):
    """Base class for all quota failures."""


class QuotaUnavailable(QuotaError):
    """The quota could not be determined (DB down, schema missing, unknown tenant).

    Callers MUST treat this as 'do not send'.
    """


class QuotaExhausted(QuotaError):
    """The campaign has no credits left (or is blocked by the super admin)."""


class QuotaRequired(QuotaError):
    """A send was attempted without presenting a quota reservation."""


# ---------------------------------------------------------------------------
# Connection handling
#
# This module owns its own small pool rather than borrowing the request-scoped
# repository, because quota must also work outside a Flask request context:
# the bulk campaign worker thread, the reaper, and the CLI backfill script.
# ---------------------------------------------------------------------------

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


# A reserving thread holds its connection for the duration of the row lock, so the
# pool must comfortably exceed the per-process thread count. Production runs Gunicorn
# with 4 workers x 4 threads, i.e. 16 threads per process; undersizing this turns a
# traffic spike into refused sends.
_DEFAULT_MAXCONN = 20

# How long to wait for a free connection before giving up (and refusing to send).
_GETCONN_ATTEMPTS = 20
_GETCONN_BACKOFF = 0.15


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None or _pool.closed:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=int(os.environ.get("QUOTA_DB_MAXCONN", _DEFAULT_MAXCONN)),
                host=os.environ.get("PGHOST", "localhost"),
                port=int(os.environ.get("PGPORT", 5432)),
                dbname=os.environ.get("PGDATABASE", "voter_platform"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ.get("PGPASSWORD", "postgres"),
            )
    return _pool


def _getconn(pool):
    """Borrows a connection, waiting briefly if the pool is momentarily exhausted.

    psycopg2's pool raises immediately rather than blocking, and under lock contention
    several threads legitimately hold connections at once. Waiting a moment is far
    better than refusing a send; if the wait runs out we still fail closed.
    """
    last_err = None
    for attempt in range(_GETCONN_ATTEMPTS):
        try:
            return pool.getconn()
        except psycopg2.pool.PoolError as e:
            last_err = e
            time.sleep(_GETCONN_BACKOFF * (1 + attempt * 0.1))
    raise QuotaUnavailable(
        "Quota database connection pool exhausted after "
        "{} attempts: {}".format(_GETCONN_ATTEMPTS, last_err)
    )


@contextmanager
def _txn():
    """Yields a cursor inside an explicit transaction.

    Commits on clean exit, rolls back on any exception, and always returns the
    connection to the pool. Any psycopg2 failure surfaces as QuotaUnavailable so
    that every caller fails closed.
    """
    pool = get_pool()
    conn = None
    try:
        conn = _getconn(pool)
        conn.autocommit = False
    except QuotaUnavailable:
        raise
    except Exception as e:
        logger.error("Quota DB unreachable: %s", e)
        raise QuotaUnavailable("Quota database unavailable: {}".format(e))

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except QuotaError:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error("Quota transaction failed: %s", e)
        raise QuotaUnavailable("Quota check failed: {}".format(e))
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


def ensure_quota_schema() -> None:
    """Idempotent DDL. Safe to call on every boot.

    Mirrors the project's shipping pattern (user_manager.ensure_schema): nothing in
    CI or the deploy workflow runs the .sql migration files, so the schema has to be
    self-applying on restart.
    """
    with _txn() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_quota ("
            "  tenant_id   INT PRIMARY KEY REFERENCES tenants(tenant_id) ON DELETE CASCADE,"
            "  granted     BIGINT  NOT NULL DEFAULT 0,"
            "  consumed    BIGINT  NOT NULL DEFAULT 0,"
            "  reserved    BIGINT  NOT NULL DEFAULT 0,"
            "  is_blocked  BOOLEAN NOT NULL DEFAULT FALSE,"
            "  updated_at  TIMESTAMPTZ DEFAULT NOW(),"
            "  updated_by  INT"
            ");"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_quota_reservation ("
            "  reservation_id UUID PRIMARY KEY,"
            "  tenant_id      INT NOT NULL,"
            "  held           INT NOT NULL,"
            "  spent          INT NOT NULL DEFAULT 0,"
            "  state          VARCHAR(16) NOT NULL DEFAULT 'open',"
            "  created_at     TIMESTAMPTZ DEFAULT NOW(),"
            "  expires_at     TIMESTAMPTZ NOT NULL"
            ");"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_wa_quota_resv_stale"
            "  ON whatsapp_quota_reservation (state, expires_at);"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS whatsapp_quota_ledger ("
            "  ledger_id        BIGSERIAL PRIMARY KEY,"
            "  tenant_id        INT NOT NULL,"
            "  action           VARCHAR(16) NOT NULL,"
            "  amount           BIGINT NOT NULL,"
            "  balance_after    BIGINT,"
            "  reservation_id   UUID,"
            "  performed_by     INT,"
            "  note             TEXT,"
            "  server_timestamp TIMESTAMPTZ DEFAULT NOW()"
            ");"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_wa_quota_ledger_tenant"
            "  ON whatsapp_quota_ledger (tenant_id, server_timestamp DESC);"
        )
    logger.info("whatsapp_quota schema ensured")


def _log(cur, tenant_id: int, action: str, amount: int,
         balance_after: Optional[int] = None,
         reservation_id: Optional[str] = None,
         performed_by: Optional[int] = None,
         note: Optional[str] = None) -> None:
    """Appends an immutable ledger row. Always called inside the caller's transaction."""
    cur.execute(
        "INSERT INTO whatsapp_quota_ledger"
        "  (tenant_id, action, amount, balance_after, reservation_id, performed_by, note)"
        " VALUES (%(tid)s, %(action)s, %(amount)s, %(bal)s, %(rid)s, %(by)s, %(note)s)",
        {
            'tid': tenant_id, 'action': action, 'amount': amount,
            'bal': balance_after, 'rid': reservation_id,
            'by': performed_by, 'note': note,
        }
    )


def resolve_tenant_id(tenant: Union[int, str]) -> int:
    """Resolves a tenant_id or campaign_slug to a numeric tenant_id.

    Raises QuotaUnavailable for anything unrecognised, so an unknown campaign can
    never slip past the quota as 'no limit configured'.
    """
    if tenant is None or isinstance(tenant, bool):
        raise QuotaUnavailable("Invalid tenant for quota: {!r}".format(tenant))
    if isinstance(tenant, int):
        return tenant
    clean = str(tenant).strip()
    if not clean:
        raise QuotaUnavailable("Empty tenant for quota")
    if clean.isdigit():
        return int(clean)
    with _txn() as cur:
        cur.execute(
            "SELECT tenant_id FROM tenants WHERE LOWER(campaign_slug) = %(slug)s",
            {'slug': clean.lower()}
        )
        row = cur.fetchone()
    if not row:
        raise QuotaUnavailable("Unknown campaign for quota: {}".format(clean))
    return int(row['tenant_id'])


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------

class Reservation:
    """A block of credits held for one dispatch run.

    Obtained from ``reserve()``. Use as a context manager so unspent credits are
    always returned, including when the dispatch raises::

        with whatsapp_quota.reserve(tid, len(phones)) as credit:
            for p in phones:
                if credit.remaining == 0:
                    break
                meta_whatsapp.send_voter_slip_card(..., credit=credit)
    """

    __slots__ = ('reservation_id', 'tenant_id', 'granted', '_spent', '_remaining',
                 '_closed', '_lock')

    def __init__(self, reservation_id: str, tenant_id: int, granted: int):
        self.reservation_id = reservation_id
        self.tenant_id = tenant_id
        self.granted = int(granted)
        self._spent = 0
        self._remaining = int(granted)
        self._closed = False
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._remaining

    @property
    def spent(self) -> int:
        with self._lock:
            return self._spent

    def spend(self) -> bool:
        """Claims one credit from this reservation. False when exhausted.

        Called by meta_whatsapp._assert_billable immediately before the HTTP POST.
        """
        with self._lock:
            if self._closed or self._remaining <= 0:
                return False
            self._remaining -= 1
            self._spent += 1
            return True

    def refund_one(self) -> None:
        """Returns one credit after a confirmed provider failure.

        Meta does not bill a rejected/failed message, so a failed attempt must not
        cost the campaign. The credit stays inside this reservation, so it is still
        accounted for and can never leak.
        """
        with self._lock:
            if self._closed or self._spent <= 0:
                return
            self._spent -= 1
            self._remaining += 1

    def close(self) -> None:
        """Reconciles the DB: bank what was spent, release what was not."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            spent = self._spent
            held = self.granted

        release = held - spent
        try:
            with _txn() as cur:
                cur.execute(
                    "UPDATE whatsapp_quota"
                    "   SET consumed = consumed + %(spent)s,"
                    "       reserved = GREATEST(reserved - %(held)s, 0),"
                    "       updated_at = NOW()"
                    " WHERE tenant_id = %(tid)s"
                    " RETURNING granted, consumed, reserved",
                    {'spent': spent, 'held': held, 'tid': self.tenant_id}
                )
                row = cur.fetchone()
                balance = None
                if row:
                    balance = int(row['granted']) - int(row['consumed']) - int(row['reserved'])

                cur.execute(
                    "UPDATE whatsapp_quota_reservation"
                    "   SET held = 0, spent = %(spent)s, state = 'closed'"
                    " WHERE reservation_id = %(rid)s",
                    {'spent': spent, 'rid': self.reservation_id}
                )

                if spent:
                    _log(cur, self.tenant_id, 'consume', spent, balance,
                         self.reservation_id, note='dispatch completed')
                if release:
                    _log(cur, self.tenant_id, 'release', release, balance,
                         self.reservation_id, note='unspent credits returned')
        except Exception as e:
            # Nothing more we can do here; the reaper reclaims the hold after
            # expires_at, so credits are never permanently lost.
            logger.error("Failed to close quota reservation %s: %s", self.reservation_id, e)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        return ("<Reservation {} tenant={} granted={} spent={} remaining={}>".format(
            self.reservation_id, self.tenant_id, self.granted, self._spent, self._remaining))


def assert_billable(credit) -> None:
    """Claims one credit immediately before a billable provider call.

    This is THE gate. Every function that POSTs a WhatsApp message must call it
    first. Raises QuotaRequired when no reservation was presented and QuotaExhausted
    when the reservation is spent; either way the caller never reaches the POST.
    """
    if credit is None or not hasattr(credit, 'spend'):
        raise QuotaRequired(
            "WhatsApp send attempted without a quota reservation. "
            "Obtain one via electionapp.whatsapp_quota.reserve(tenant, n)."
        )
    if not credit.spend():
        raise QuotaExhausted(
            "WhatsApp संदेश सीमा समाप्त हो गई है। "
            "(Campaign WhatsApp credit balance exhausted.)"
        )


def refund_credit(credit) -> None:
    """Returns a credit after a confirmed provider failure (Meta does not bill those)."""
    try:
        credit.refund_one()
    except Exception:
        pass


def reserve(tenant: Union[int, str], want: int,
            performed_by: Optional[int] = None) -> Reservation:
    """Atomically holds up to ``want`` credits for this campaign.

    Grants a partial amount when fewer credits remain, so a bulk dispatch sends up
    to the limit and then stops rather than failing wholesale.

    Raises QuotaExhausted when nothing is available (or the campaign is blocked),
    and QuotaUnavailable if the quota cannot be read at all.

    Self-healing: an apparent exhaustion may be credits still held by a worker that
    died without closing its reservation. In that case stale holds are reclaimed and
    the reservation is retried once. The reap runs in its own transaction, after the
    first attempt has released its row lock, so it cannot deadlock against itself.
    """
    tenant_id = resolve_tenant_id(tenant)
    want = int(want)
    if want <= 0:
        raise QuotaExhausted("Nothing to send")

    try:
        return _try_reserve(tenant_id, want, performed_by)
    except QuotaExhausted:
        if reap_stale_reservations():
            return _try_reserve(tenant_id, want, performed_by)
        raise


def _try_reserve(tenant_id: int, want: int,
                 performed_by: Optional[int] = None) -> Reservation:
    """One reservation attempt. See reserve() for the retry wrapper."""
    rid = str(uuid.uuid4())
    grant_n = 0

    with _txn() as cur:
        # Row lock serialises concurrent reservations across every worker/thread.
        # Postgres re-reads the latest committed row here, so the arithmetic below
        # always runs against fresh values.
        cur.execute(
            "SELECT granted, consumed, reserved, is_blocked"
            "  FROM whatsapp_quota"
            " WHERE tenant_id = %(tid)s"
            " FOR UPDATE",
            {'tid': tenant_id}
        )
        row = cur.fetchone()

        if not row:
            raise QuotaExhausted(
                "इस अभियान के लिए WhatsApp संदेश सीमा निर्धारित नहीं है। "
                "कृपया व्यवस्थापक से संपर्क करें। "
                "(No WhatsApp message quota configured for this campaign.)"
            )

        if row['is_blocked']:
            raise QuotaExhausted(
                "इस अभियान के WhatsApp संदेश व्यवस्थापक द्वारा रोके गए हैं। "
                "(WhatsApp sending is blocked for this campaign by the administrator.)"
            )

        available = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        grant_n = max(0, min(want, available))

        if grant_n <= 0:
            raise QuotaExhausted(
                "WhatsApp संदेश सीमा समाप्त हो गई है। कृपया व्यवस्थापक से संपर्क करें। "
                "(WhatsApp message limit reached. Please contact the administrator.)"
            )

        cur.execute(
            "UPDATE whatsapp_quota"
            "   SET reserved = reserved + %(n)s, updated_at = NOW()"
            " WHERE tenant_id = %(tid)s",
            {'n': grant_n, 'tid': tenant_id}
        )

        cur.execute(
            "INSERT INTO whatsapp_quota_reservation"
            "  (reservation_id, tenant_id, held, spent, state, expires_at)"
            " VALUES (%(rid)s, %(tid)s, %(n)s, 0, 'open',"
            "         NOW() + (%(ttl)s || ' hours')::interval)",
            {'rid': rid, 'tid': tenant_id, 'n': grant_n,
             'ttl': str(RESERVATION_TTL_HOURS)}
        )

        _log(cur, tenant_id, 'reserve', grant_n, available - grant_n, rid, performed_by,
             note="requested {}".format(want))

    logger.info("Quota reserved tenant=%s want=%s granted=%s rid=%s",
                tenant_id, want, grant_n, rid)
    return Reservation(rid, tenant_id, grant_n)


# ---------------------------------------------------------------------------
# Read / administer
# ---------------------------------------------------------------------------

def get_quota(tenant: Union[int, str]) -> Dict[str, Any]:
    """Returns the current balance for a campaign. Never fails open."""
    tenant_id = resolve_tenant_id(tenant)
    with _txn() as cur:
        cur.execute(
            "SELECT granted, consumed, reserved, is_blocked, updated_at"
            "  FROM whatsapp_quota WHERE tenant_id = %(tid)s",
            {'tid': tenant_id}
        )
        row = cur.fetchone()

    if not row:
        return {
            'tenant_id': tenant_id, 'configured': False,
            'granted': 0, 'consumed': 0, 'reserved': 0, 'available': 0,
            'is_blocked': False, 'can_send': False, 'updated_at': None,
        }

    granted = int(row['granted'])
    consumed = int(row['consumed'])
    reserved = int(row['reserved'])
    available = granted - consumed - reserved
    return {
        'tenant_id': tenant_id,
        'configured': True,
        'granted': granted,
        'consumed': consumed,
        'reserved': reserved,
        'available': available,
        'is_blocked': bool(row['is_blocked']),
        'can_send': (not row['is_blocked']) and available > 0,
        'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
    }


def get_ledger(tenant: Union[int, str], limit: int = 25) -> list:
    """Recent quota events, for the super admin console."""
    tenant_id = resolve_tenant_id(tenant)
    with _txn() as cur:
        cur.execute(
            "SELECT action, amount, balance_after, performed_by, note, server_timestamp"
            "  FROM whatsapp_quota_ledger"
            " WHERE tenant_id = %(tid)s"
            " ORDER BY server_timestamp DESC, ledger_id DESC"
            " LIMIT %(lim)s",
            {'tid': tenant_id, 'lim': int(limit)}
        )
        rows = cur.fetchall() or []
    out = []
    for r in rows:
        out.append({
            'action': r['action'],
            'amount': int(r['amount']),
            'balance_after': r['balance_after'],
            'performed_by': r['performed_by'],
            'note': r['note'],
            'at': r['server_timestamp'].isoformat() if r['server_timestamp'] else None,
        })
    return out


def _ensure_row(cur, tenant_id: int) -> None:
    cur.execute(
        "INSERT INTO whatsapp_quota (tenant_id, granted, consumed, reserved)"
        " VALUES (%(tid)s, 0, 0, 0)"
        " ON CONFLICT (tenant_id) DO NOTHING",
        {'tid': tenant_id}
    )


def grant(tenant: Union[int, str], amount: int,
          performed_by: Optional[int] = None,
          note: Optional[str] = None) -> Dict[str, Any]:
    """Adds credits to a campaign (a top-up). A negative amount revokes."""
    tenant_id = resolve_tenant_id(tenant)
    amount = int(amount)
    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "UPDATE whatsapp_quota"
            "   SET granted = GREATEST(granted + %(amt)s, consumed + reserved),"
            "       updated_at = NOW(), updated_by = %(by)s"
            " WHERE tenant_id = %(tid)s"
            " RETURNING granted, consumed, reserved",
            {'amt': amount, 'by': performed_by, 'tid': tenant_id}
        )
        row = cur.fetchone()
        balance = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        _log(cur, tenant_id, 'grant' if amount >= 0 else 'revoke', amount, balance,
             performed_by=performed_by, note=note)
    return get_quota(tenant_id)


def set_granted(tenant: Union[int, str], total: int,
                performed_by: Optional[int] = None,
                note: Optional[str] = None) -> Dict[str, Any]:
    """Sets the lifetime grant to an absolute total.

    Clamped so it can never fall below credits already consumed or held, which keeps
    the counters non-negative.
    """
    tenant_id = resolve_tenant_id(tenant)
    total = max(0, int(total))
    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "UPDATE whatsapp_quota"
            "   SET granted = GREATEST(%(total)s, consumed + reserved),"
            "       updated_at = NOW(), updated_by = %(by)s"
            " WHERE tenant_id = %(tid)s"
            " RETURNING granted, consumed, reserved",
            {'total': total, 'by': performed_by, 'tid': tenant_id}
        )
        row = cur.fetchone()
        balance = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        _log(cur, tenant_id, 'set', int(row['granted']), balance,
             performed_by=performed_by, note=note)
    return get_quota(tenant_id)


def set_blocked(tenant: Union[int, str], blocked: bool,
                performed_by: Optional[int] = None,
                note: Optional[str] = None) -> Dict[str, Any]:
    """Super-admin kill switch. Blocks new reservations immediately."""
    tenant_id = resolve_tenant_id(tenant)
    blocked = bool(blocked)
    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "UPDATE whatsapp_quota"
            "   SET is_blocked = %(b)s, updated_at = NOW(), updated_by = %(by)s"
            " WHERE tenant_id = %(tid)s"
            " RETURNING granted, consumed, reserved",
            {'b': blocked, 'by': performed_by, 'tid': tenant_id}
        )
        row = cur.fetchone()
        balance = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        _log(cur, tenant_id, 'block' if blocked else 'unblock', 0, balance,
             performed_by=performed_by, note=note)
    return get_quota(tenant_id)


def reap_stale_reservations() -> int:
    """Releases credits held by reservations that were never closed (crashed worker).

    Only ever increases availability, so it cannot cause an overspend.
    Returns the number of reservations reclaimed.
    """
    reclaimed = 0
    with _txn() as cur:
        cur.execute(
            "SELECT reservation_id, tenant_id, held"
            "  FROM whatsapp_quota_reservation"
            " WHERE state = 'open' AND expires_at < NOW()"
            " FOR UPDATE SKIP LOCKED"
        )
        stale = cur.fetchall() or []
        for r in stale:
            cur.execute(
                "UPDATE whatsapp_quota"
                "   SET reserved = GREATEST(reserved - %(held)s, 0), updated_at = NOW()"
                " WHERE tenant_id = %(tid)s",
                {'held': int(r['held']), 'tid': int(r['tenant_id'])}
            )
            cur.execute(
                "UPDATE whatsapp_quota_reservation"
                "   SET held = 0, state = 'closed'"
                " WHERE reservation_id = %(rid)s",
                {'rid': r['reservation_id']}
            )
            _log(cur, int(r['tenant_id']), 'reap', int(r['held']),
                 reservation_id=str(r['reservation_id']),
                 note='stale reservation reclaimed')
            reclaimed += 1
    if reclaimed:
        logger.warning("Reclaimed %s stale WhatsApp quota reservation(s)", reclaimed)
    return reclaimed
