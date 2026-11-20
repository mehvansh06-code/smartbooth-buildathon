"""
Per-campaign Voice Broadcast (OBD) quota and call tracking.

Modelled with the same fail-closed precision as electionapp/whatsapp_quota.py.
Outbound Voice Broadcast (OBD) calls are a metered, billable cost. This module is the
single source of truth for voice credits and call reservations, and it is the ONLY place
that mutates voice counters.

Design notes
------------
* available = granted - consumed - reserved.
* A dispatch must first reserve(tenant, want), which atomically moves credits
  from available into reserved under a row lock. Credits are taken out of circulation
  BEFORE any HTTP call to carrier gateways happens, so concurrent blast requests
  can never collectively overspend.
* spend(tenant_slug, amount) and refund(tenant_slug, amount) or Reservation.spend()/refund()
  ensure exact tracking.
* reconcile_call(...) records individual call outcome and logs to voice_call_records.
* If the quota check cannot be completed for ANY reason, QuotaUnavailable is raised
  and callers must not dispatch. Fail-closed by design.
* reap_stale_reservations() returns credits held by dead processes after expiration.
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

logger = logging.getLogger("smartbooth.voice_quota")

# How long a voice reservation may stay open before the reaper may reclaim it.
VOICE_RESERVATION_TTL_HOURS = int(os.environ.get("VOICE_QUOTA_RESERVATION_TTL_HOURS", 24))

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


_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()

_DEFAULT_MAXCONN = 20
_GETCONN_ATTEMPTS = 20
_GETCONN_BACKOFF = 0.15


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None or _pool.closed:
            _pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=int(os.environ.get("VOICE_QUOTA_DB_MAXCONN", os.environ.get("QUOTA_DB_MAXCONN", _DEFAULT_MAXCONN))),
                host=os.environ.get("PGHOST", "localhost"),
                port=int(os.environ.get("PGPORT", 5432)),
                dbname=os.environ.get("PGDATABASE", "voter_platform"),
                user=os.environ.get("PGUSER", "postgres"),
                password=os.environ.get("PGPASSWORD", "postgres"),
            )
    return _pool


def _getconn(pool):
    last_err = None
    for attempt in range(_GETCONN_ATTEMPTS):
        try:
            return pool.getconn()
        except psycopg2.pool.PoolError as e:
            last_err = e
            time.sleep(_GETCONN_BACKOFF * (1 + attempt * 0.1))
    raise QuotaUnavailable(
        f"Voice quota database connection pool exhausted after {_GETCONN_ATTEMPTS} attempts: {last_err}"
    )


@contextmanager
def _txn():
    """Yields a cursor inside an explicit transaction.
    Commits on clean exit, rolls back on any exception, and always returns
    connection to pool. Any failure surfaces as QuotaUnavailable (fail closed).
    """
    pool = get_pool()
    conn = None
    try:
        conn = _getconn(pool)
        conn.autocommit = False
    except QuotaUnavailable:
        raise
    except Exception as e:
        logger.error("Voice quota DB unreachable: %s", e)
        raise QuotaUnavailable(f"Voice quota database unavailable: {e}")

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
        logger.error("Voice quota transaction failed: %s", e)
        raise QuotaUnavailable(f"Voice quota check failed: {e}")
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


def ensure_voice_schema() -> None:
    """Idempotent DDL creating tables:
    - voice_quota
    - voice_quota_reservation
    - voice_quota_ledger
    - voice_audios
    - voice_campaigns
    - voice_call_records
    """
    with _txn() as cur:
        # 1. voice_quota
        cur.execute(
            "CREATE TABLE IF NOT EXISTS voice_quota ("
            "  tenant_id   INT PRIMARY KEY REFERENCES tenants(tenant_id) ON DELETE CASCADE,"
            "  granted     BIGINT  NOT NULL DEFAULT 0,"
            "  consumed    BIGINT  NOT NULL DEFAULT 0,"
            "  reserved    BIGINT  NOT NULL DEFAULT 0,"
            "  is_blocked  BOOLEAN NOT NULL DEFAULT FALSE,"
            "  updated_at  TIMESTAMPTZ DEFAULT NOW(),"
            "  updated_by  INT"
            ");"
        )

        # 2. voice_quota_reservation
        cur.execute(
            "CREATE TABLE IF NOT EXISTS voice_quota_reservation ("
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
            "CREATE INDEX IF NOT EXISTS idx_voice_quota_resv_stale"
            "  ON voice_quota_reservation (state, expires_at);"
        )

        # 3. voice_quota_ledger
        cur.execute(
            "CREATE TABLE IF NOT EXISTS voice_quota_ledger ("
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
            "CREATE INDEX IF NOT EXISTS idx_voice_quota_ledger_tenant"
            "  ON voice_quota_ledger (tenant_id, server_timestamp DESC);"
        )

        # 4. voice_audios (Recorded audio prompts uploaded & approved by carrier)
        cur.execute(
            "CREATE TABLE IF NOT EXISTS voice_audios ("
            "  audio_id         BIGSERIAL PRIMARY KEY,"
            "  tenant_id        INT NOT NULL,"
            "  sound_name       VARCHAR(128) NOT NULL,"
            "  carrier_sound_name VARCHAR(128),"
            "  display_label    VARCHAR(255),"
            "  file_path        TEXT,"
            "  original_filename VARCHAR(255),"
            "  duration_seconds NUMERIC(8,2) DEFAULT 0,"
            "  status           VARCHAR(32) NOT NULL DEFAULT 'active',"
            "  created_by       INT,"
            "  created_at       TIMESTAMPTZ DEFAULT NOW()"
            ");"
        )
        cur.execute("ALTER TABLE voice_audios ADD COLUMN IF NOT EXISTS carrier_sound_name VARCHAR(128);")
        cur.execute("ALTER TABLE voice_audios ADD COLUMN IF NOT EXISTS display_label VARCHAR(255);")
        cur.execute("ALTER TABLE voice_audios ADD COLUMN IF NOT EXISTS file_path TEXT;")
        cur.execute("ALTER TABLE voice_audios ADD COLUMN IF NOT EXISTS duration_seconds NUMERIC(8,2) DEFAULT 0;")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_audios_tenant"
            "  ON voice_audios (tenant_id, status);"
        )

        # 5. voice_campaigns (Broadcast batches)
        cur.execute(
            "CREATE TABLE IF NOT EXISTS voice_campaigns ("
            "  campaign_id      BIGSERIAL PRIMARY KEY,"
            "  tenant_id        INT NOT NULL,"
            "  gateway_campaign_id VARCHAR(128),"
            "  campaign_name    VARCHAR(255) NOT NULL,"
            "  sound_name       VARCHAR(128) NOT NULL,"
            "  total_target     INT NOT NULL DEFAULT 0,"
            "  dispatched_count INT NOT NULL DEFAULT 0,"
            "  status           VARCHAR(32) NOT NULL DEFAULT 'dispatched',"
            "  reservation_id   UUID,"
            "  created_by       INT,"
            "  created_at       TIMESTAMPTZ DEFAULT NOW()"
            ");"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_campaigns_tenant"
            "  ON voice_campaigns (tenant_id, created_at DESC);"
        )

        # 6. voice_call_records (Individual call logs / delivery reconciliation)
        cur.execute(
            "CREATE TABLE IF NOT EXISTS voice_call_records ("
            "  call_id          BIGSERIAL PRIMARY KEY,"
            "  campaign_id      BIGINT,"
            "  tenant_id        INT NOT NULL,"
            "  mobile           VARCHAR(20) NOT NULL,"
            "  voter_id         BIGINT,"
            "  status           VARCHAR(32) NOT NULL DEFAULT 'submitted',"
            "  duration_seconds INT DEFAULT 0,"
            "  gateway_call_id  VARCHAR(128),"
            "  created_at       TIMESTAMPTZ DEFAULT NOW(),"
            "  updated_at       TIMESTAMPTZ DEFAULT NOW()"
            ");"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_calls_tenant_mobile"
            "  ON voice_call_records (tenant_id, mobile);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_voice_calls_campaign"
            "  ON voice_call_records (campaign_id);"
        )

    logger.info("voice_quota schema ensured")


def _log(cur, tenant_id: int, action: str, amount: int,
         balance_after: Optional[int] = None,
         reservation_id: Optional[str] = None,
         performed_by: Optional[int] = None,
         note: Optional[str] = None) -> None:
    """Appends an immutable ledger row. Always called inside caller transaction."""
    cur.execute(
        "INSERT INTO voice_quota_ledger"
        "  (tenant_id, action, amount, balance_after, reservation_id, performed_by, note)"
        " VALUES (%(tid)s, %(action)s, %(amount)s, %(bal)s, %(rid)s, %(by)s, %(note)s)",
        {
            'tid': tenant_id, 'action': action, 'amount': amount,
            'bal': balance_after, 'rid': reservation_id,
            'by': performed_by, 'note': note,
        }
    )


def resolve_tenant_id(tenant: Union[int, str]) -> int:
    """Resolves a tenant_id or campaign_slug to numeric tenant_id.
    Fails closed on any unknown value.
    """
    if tenant is None or isinstance(tenant, bool):
        raise QuotaUnavailable(f"Invalid tenant for voice quota: {tenant!r}")
    if isinstance(tenant, int):
        return tenant
    clean = str(tenant).strip()
    if not clean:
        raise QuotaUnavailable("Empty tenant for voice quota")
    if clean.isdigit():
        return int(clean)
    with _txn() as cur:
        cur.execute(
            "SELECT tenant_id FROM tenants WHERE LOWER(campaign_slug) = %(slug)s",
            {'slug': clean.lower()}
        )
        row = cur.fetchone()
    if not row:
        raise QuotaUnavailable(f"Unknown campaign for voice quota: {clean}")
    return int(row['tenant_id'])


def _ensure_row(cur, tenant_id: int) -> None:
    cur.execute(
        "INSERT INTO voice_quota (tenant_id, granted, consumed, reserved)"
        " VALUES (%(tid)s, 0, 0, 0)"
        " ON CONFLICT (tenant_id) DO NOTHING",
        {'tid': tenant_id}
    )


class Reservation:
    """A block of voice credits held for one broadcast blast."""

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

    def spend(self, n: int = 1) -> bool:
        """Claims n credits from this reservation in-memory."""
        with self._lock:
            if self._closed or self._remaining < n:
                return False
            self._remaining -= n
            self._spent += n
            return True

    def refund(self, n: int = 1) -> None:
        """Refunds n credits back into this reservation."""
        with self._lock:
            if self._closed or self._spent < n:
                return
            self._spent -= n
            self._remaining += n

    def close(self) -> None:
        """Reconciles the DB: bank what was spent, release what was not."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            spent = self._spent
            held = self.granted

        release = held - spent
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                with _txn() as cur:
                    cur.execute(
                        "UPDATE voice_quota"
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
                        "UPDATE voice_quota_reservation"
                        "   SET held = 0, spent = %(spent)s, state = 'closed'"
                        " WHERE reservation_id = %(rid)s",
                        {'spent': spent, 'rid': self.reservation_id}
                    )

                    if spent:
                        _log(cur, self.tenant_id, 'consume', spent, balance,
                             self.reservation_id, note='voice broadcast completed')
                    if release:
                        _log(cur, self.tenant_id, 'release', release, balance,
                             self.reservation_id, note='unspent voice credits returned')
                    return
            except Exception as e:
                logger.error("Failed to close voice quota reservation %s (attempt %d/%d): %s",
                             self.reservation_id, attempt + 1, max_attempts, e)
                if attempt < max_attempts - 1:
                    time.sleep(0.15 * (attempt + 1))

        # Preserve spent count if full close failed so reaper settles carrier dispatches
        if spent > 0:
            try:
                with _txn() as cur:
                    cur.execute(
                        "UPDATE voice_quota_reservation"
                        "   SET spent = %(spent)s"
                        " WHERE reservation_id = %(rid)s",
                        {'spent': spent, 'rid': self.reservation_id}
                    )
            except Exception as preserve_err:
                logger.critical("CRITICAL: Failed to preserve spent count on voice reservation %s: %s",
                                self.reservation_id, preserve_err)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        return (f"<VoiceReservation {self.reservation_id} tenant={self.tenant_id} "
                f"granted={self.granted} spent={self._spent} remaining={self._remaining}>")


def reserve(tenant: Union[int, str], want: int,
            performed_by: Optional[int] = None) -> Reservation:
    """Atomically holds up to `want` voice credits for this campaign.
    Locks row SELECT ... FOR UPDATE, checks available = granted - consumed - reserved.
    If insufficient, raises QuotaExhausted.
    Moves credits to reserved.
    """
    tenant_id = resolve_tenant_id(tenant)
    want = int(want)
    if want <= 0:
        raise QuotaExhausted("Nothing to broadcast")

    try:
        return _try_reserve(tenant_id, want, performed_by)
    except QuotaExhausted:
        if reap_stale_reservations():
            return _try_reserve(tenant_id, want, performed_by)
        raise


def _try_reserve(tenant_id: int, want: int,
                 performed_by: Optional[int] = None) -> Reservation:
    rid = str(uuid.uuid4())
    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "SELECT granted, consumed, reserved, is_blocked"
            "  FROM voice_quota"
            " WHERE tenant_id = %(tid)s"
            " FOR UPDATE",
            {'tid': tenant_id}
        )
        row = cur.fetchone()

        if not row:
            raise QuotaExhausted("वॉइस कॉल सीमा निर्धारित नहीं है (No voice quota configured for this campaign).")

        if row['is_blocked']:
            raise QuotaExhausted("इस अभियान की वॉइस कॉल सेवा रोक दी गई है (Voice broadcast blocked by administrator).")

        available = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        if available < want:
            raise QuotaExhausted(
                f"वॉइस कॉल कोटा अपर्याप्त है। उपलब्ध: {max(0, available)}, आवश्यक: {want} "
                f"(Insufficient voice call quota. Available: {max(0, available)}, Wanted: {want})"
            )

        grant_n = want

        cur.execute(
            "UPDATE voice_quota"
            "   SET reserved = reserved + %(n)s, updated_at = NOW()"
            " WHERE tenant_id = %(tid)s",
            {'n': grant_n, 'tid': tenant_id}
        )

        cur.execute(
            "INSERT INTO voice_quota_reservation"
            "  (reservation_id, tenant_id, held, spent, state, expires_at)"
            " VALUES (%(rid)s, %(tid)s, %(n)s, 0, 'open',"
            "         NOW() + (%(ttl)s || ' hours')::interval)",
            {'rid': rid, 'tid': tenant_id, 'n': grant_n,
             'ttl': str(VOICE_RESERVATION_TTL_HOURS)}
        )

        _log(cur, tenant_id, 'reserve', grant_n, available - grant_n, rid, performed_by,
             note=f"reserved {want} voice credits")

    logger.info("Voice quota reserved tenant=%s want=%s granted=%s rid=%s",
                tenant_id, want, grant_n, rid)
    return Reservation(rid, tenant_id, grant_n)


def spend(tenant: Union[int, str], amount: int, performed_by: Optional[int] = None, note: str = "direct spend") -> Dict[str, Any]:
    """Spends voice credits directly from balance (e.g. ad-hoc single calls)."""
    tenant_id = resolve_tenant_id(tenant)
    amount = int(amount)
    if amount <= 0:
        return get_quota(tenant_id)

    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "SELECT granted, consumed, reserved, is_blocked FROM voice_quota WHERE tenant_id = %(tid)s FOR UPDATE",
            {'tid': tenant_id}
        )
        row = cur.fetchone()
        if not row or row['is_blocked']:
            raise QuotaExhausted("Voice broadcast is not configured or is blocked.")
        available = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        if available < amount:
            raise QuotaExhausted(f"Insufficient voice credits: {available} available, {amount} required.")

        cur.execute(
            "UPDATE voice_quota"
            "   SET consumed = consumed + %(amt)s, updated_at = NOW()"
            " WHERE tenant_id = %(tid)s"
            " RETURNING granted, consumed, reserved",
            {'amt': amount, 'tid': tenant_id}
        )
        row = cur.fetchone()
        balance = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        _log(cur, tenant_id, 'consume', amount, balance, performed_by=performed_by, note=note)

    return get_quota(tenant_id)


def refund(tenant: Union[int, str], amount: int, performed_by: Optional[int] = None, note: str = "carrier refund") -> Dict[str, Any]:
    """Refunds consumed credits (e.g. gateway rejected or failed delivery)."""
    tenant_id = resolve_tenant_id(tenant)
    amount = int(amount)
    if amount <= 0:
        return get_quota(tenant_id)

    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "UPDATE voice_quota"
            "   SET consumed = GREATEST(consumed - %(amt)s, 0), updated_at = NOW()"
            " WHERE tenant_id = %(tid)s"
            " RETURNING granted, consumed, reserved",
            {'amt': amount, 'tid': tenant_id}
        )
        row = cur.fetchone()
        balance = int(row['granted']) - int(row['consumed']) - int(row['reserved']) if row else 0
        _log(cur, tenant_id, 'refund', amount, balance, performed_by=performed_by, note=note)

    return get_quota(tenant_id)


def reconcile_call(tenant: Union[int, str], mobile: str, status: str,
                   duration_seconds: int = 0, campaign_id: Optional[int] = None,
                   voter_id: Optional[int] = None, gateway_call_id: Optional[str] = None) -> None:
    """Updates or logs an individual call record."""
    tenant_id = resolve_tenant_id(tenant)
    with _txn() as cur:
        cur.execute(
            "INSERT INTO voice_call_records"
            "  (tenant_id, campaign_id, mobile, voter_id, status, duration_seconds, gateway_call_id, updated_at)"
            " VALUES (%(tid)s, %(cid)s, %(mob)s, %(vid)s, %(st)s, %(dur)s, %(gwid)s, NOW())",
            {
                'tid': tenant_id, 'cid': campaign_id, 'mob': mobile,
                'vid': voter_id, 'st': status, 'dur': duration_seconds,
                'gwid': gateway_call_id
            }
        )


def grant(tenant: Union[int, str], amount: int,
          performed_by: Optional[int] = None,
          note: str = '') -> Dict[str, Any]:
    """Adds credits to a campaign voice balance and logs to ledger."""
    tenant_id = resolve_tenant_id(tenant)
    amount = int(amount)
    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "UPDATE voice_quota"
            "   SET granted = GREATEST(granted + %(amt)s, consumed + reserved),"
            "       updated_at = NOW(), updated_by = %(by)s"
            " WHERE tenant_id = %(tid)s"
            " RETURNING granted, consumed, reserved",
            {'amt': amount, 'by': performed_by, 'tid': tenant_id}
        )
        row = cur.fetchone()
        balance = int(row['granted']) - int(row['consumed']) - int(row['reserved'])
        _log(cur, tenant_id, 'grant' if amount >= 0 else 'revoke', amount, balance,
             performed_by=performed_by, note=note or 'Voice credit grant')
    return get_quota(tenant_id)


def get_quota(tenant: Union[int, str]) -> Dict[str, Any]:
    """Returns dictionary: {granted, consumed, reserved, available, is_blocked, can_send, configured}."""
    tenant_id = resolve_tenant_id(tenant)
    with _txn() as cur:
        cur.execute(
            "SELECT granted, consumed, reserved, is_blocked, updated_at"
            "  FROM voice_quota WHERE tenant_id = %(tid)s",
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


def set_blocked(tenant: Union[int, str], blocked: bool,
                performed_by: Optional[int] = None,
                note: Optional[str] = None) -> Dict[str, Any]:
    """Super-admin kill switch for voice broadcasts."""
    tenant_id = resolve_tenant_id(tenant)
    blocked = bool(blocked)
    with _txn() as cur:
        _ensure_row(cur, tenant_id)
        cur.execute(
            "UPDATE voice_quota"
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
    """Reclaims credits held by reservations that expired without being closed.
    Safeguards carrier bills: if spent > 0 was recorded, settles the spent calls
    rather than refunding them as unbilled credits.
    """
    reclaimed = 0
    with _txn() as cur:
        cur.execute(
            "SELECT reservation_id, tenant_id, held, spent"
            "  FROM voice_quota_reservation"
            " WHERE state = 'open' AND expires_at < NOW()"
            " FOR UPDATE SKIP LOCKED"
        )
        stale = cur.fetchall() or []
        for r in stale:
            held = int(r['held'])
            spent = int(r.get('spent') or 0)
            release = max(0, held - spent)
            tid = int(r['tenant_id'])
            rid = str(r['reservation_id'])

            cur.execute(
                "UPDATE voice_quota"
                "   SET consumed = consumed + %(spent)s,"
                "       reserved = GREATEST(reserved - %(held)s, 0),"
                "       updated_at = NOW()"
                " WHERE tenant_id = %(tid)s",
                {'spent': spent, 'held': held, 'tid': tid}
            )
            cur.execute(
                "UPDATE voice_quota_reservation"
                "   SET held = 0, state = 'closed'"
                " WHERE reservation_id = %(rid)s",
                {'rid': rid}
            )
            if spent:
                _log(cur, tid, 'consume', spent,
                     reservation_id=rid,
                     note='reaper settled uncommitted carrier voice calls')
            if release:
                _log(cur, tid, 'reap', release,
                     reservation_id=rid,
                     note='stale voice reservation reclaimed')
            reclaimed += 1
    if reclaimed:
        logger.warning("Reclaimed %s stale Voice quota reservation(s)", reclaimed)
    return reclaimed
