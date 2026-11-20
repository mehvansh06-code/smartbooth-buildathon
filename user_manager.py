"""
user_manager.py
PostgreSQL-backed Multi-Tenant User & Role Manager for SmartBooth.
Replaces legacy SQLite auth.db with first-class PostgreSQL 16 persistence.
Features:
- Threaded connection pooling via psycopg2
- PBKDF2/scrypt password hashing with legacy sha256 compatibility
- 5-Tier RBAC (1: super_admin, 2: admin/operator, 3: booth_agent, 4: karyakarta, 5: viewer)
- Strict tenant scoping (NULL or '*' = global super admin)
"""

import os
import json
import secrets
import string
import hashlib
import logging
from typing import Optional, Tuple, List, Dict, Any, Union
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg2
import psycopg2.pool
import psycopg2.extras

logger = logging.getLogger("smartbooth.user_manager")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

TIER_TO_ROLE = {
    1: 'super_admin',
    2: 'admin',
    3: 'booth_agent',
    4: 'karyakarta',
    5: 'viewer',
}

ROLE_TO_TIER = {
    'super_admin': 1,
    'candidate_admin': 2,
    'admin': 2,
    'operator': 2,
    'reseller': 2,
    'booth_agent': 3,
    'karyakarta': 4,
    'viewer': 5,
}

_pg_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pg_pool
    if _pg_pool is None or _pg_pool.closed:
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=int(os.environ.get("USER_DB_MAXCONN", 5)),
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", 5432)),
            dbname=os.environ.get("PGDATABASE", "voter_platform"),
            user=os.environ.get("PGUSER", "postgres"),
            password=os.environ.get("PGPASSWORD", "postgres"),
        )
    return _pg_pool


class PooledPostgresConn:
    def __init__(self, raw_conn, pool):
        self._raw = raw_conn
        self._pool = pool

    def cursor(self, *args, **kwargs):
        if 'cursor_factory' not in kwargs:
            kwargs['cursor_factory'] = psycopg2.extras.DictCursor
        return self._raw.cursor(*args, **kwargs)

    def execute(self, sql, params=None):
        if '?' in sql:
            sql = sql.replace('?', '%s')
        cur = self.cursor()
        cur.execute(sql, params or ())
        return cur

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, val):
        pass

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        if self._raw and self._pool:
            try:
                self._pool.putconn(self._raw)
            except Exception:
                pass
            self._raw = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                self.rollback()
            except Exception:
                pass
        else:
            try:
                self.commit()
            except Exception:
                pass
        self.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def get_db() -> PooledPostgresConn:
    pool = get_pool()
    raw = pool.getconn()
    raw.autocommit = False
    return PooledPostgresConn(raw, pool)


def _get_tenant_map() -> Tuple[Dict[str, int], Dict[int, str]]:
    slug_to_id: Dict[str, int] = {}
    id_to_slug: Dict[int, str] = {}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tenant_id, campaign_slug FROM tenants;")
                for tid, cslug in cur.fetchall():
                    s = str(cslug).strip().lower()
                    slug_to_id[s] = tid
                    id_to_slug[tid] = s
    except Exception:
        pass
    return slug_to_id, id_to_slug


def reseller_owns_tenant(user_id: int, slug: str) -> bool:
    """Checks whether the given reseller owns the campaign identified by slug."""
    if not user_id or not slug:
        return False
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM tenants WHERE LOWER(campaign_slug) = %s AND owner_user_id = %s",
                    (str(slug).strip().lower(), user_id)
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def get_owned_tenants(user_id: int) -> List[Dict[str, Any]]:
    """Returns the campaigns owned by the given reseller user."""
    if not user_id:
        return []
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id, campaign_slug, candidate_name, state, election_type, status "
                    "FROM tenants WHERE owner_user_id = %s ORDER BY created_at DESC",
                    (user_id,)
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def count_owned_tenants(user_id: int) -> int:
    """Counts how many campaigns the given reseller user currently owns."""
    if not user_id:
        return 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tenants WHERE owner_user_id = %s", (user_id,))
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return 0


def get_reseller_whatsapp_credits(user_id: int) -> int:
    """Returns the available WhatsApp credits for a reseller user."""
    if not user_id:
        return 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT whatsapp_credits FROM users WHERE user_id = %s;", (user_id,))
                row = cur.fetchone()
                return int(row[0] or 0) if row else 0
    except Exception as e:
        logger.error("Error reading whatsapp_credits for user_id %s: %s", user_id, e)
        return 0


def deduct_reseller_whatsapp_credits(user_id: int, amount: int) -> bool:
    """Atomically deducts credits from a reseller's prepaid balance."""
    if not user_id or amount <= 0:
        return True
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users
                    SET whatsapp_credits = whatsapp_credits - %s
                    WHERE user_id = %s AND whatsapp_credits >= %s
                    RETURNING whatsapp_credits;
                """, (amount, user_id, amount))
                row = cur.fetchone()
                return bool(row is not None)
    except Exception as e:
        logger.error("Error deducting %s whatsapp_credits from user_id %s: %s", amount, user_id, e)
        return False


def refund_reseller_whatsapp_credits(user_id: int, amount: int) -> None:
    """Refunds credits back to a reseller's prepaid balance."""
    if not user_id or amount <= 0:
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users
                    SET whatsapp_credits = COALESCE(whatsapp_credits, 0) + %s
                    WHERE user_id = %s;
                """, (amount, user_id))
    except Exception as e:
        logger.error("Error refunding %s whatsapp_credits to user_id %s: %s", amount, user_id, e)


def get_reseller_dashboard_summary(user_id: int) -> Dict[str, Any]:
    """Returns the full fleet portfolio and aggregated metrics for a reseller."""
    empty_result = {
        'campaigns': [],
        'fleet_stats': {
            'total_campaigns': 0,
            'max_campaigns': None,
            'whatsapp_credits': 0,
            'total_voters': 0,
            'total_voted': 0,
            'total_dispatches': 0,
            'overall_turnout_pct': 0.0,
        },
        'user': None
    }
    if not user_id:
        return empty_result

    user_row = get_user_by_id(user_id)
    if not user_row:
        return empty_result

    max_campaigns = user_row.get('max_campaigns')
    tenants_dir = os.path.join(BASE_DIR, 'tenants')
    campaigns = []

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1. Fetch owned tenants
                cur.execute(
                    "SELECT tenant_id, campaign_slug, candidate_name, state, election_type, status, max_sub_accounts, created_at "
                    "FROM tenants WHERE owner_user_id = %s ORDER BY tenant_id DESC",
                    (user_id,)
                )
                owned_rows = [dict(r) for r in cur.fetchall()]
                if not owned_rows:
                    empty_result['user'] = user_row
                    empty_result['fleet_stats']['max_campaigns'] = max_campaigns
                    return empty_result

                tenant_ids = [r['tenant_id'] for r in owned_rows]

                # 2. Count sub-accounts per tenant
                sub_counts = {}
                try:
                    cur.execute(
                        "SELECT tenant_id, COUNT(*) FROM users "
                        "WHERE tenant_id = ANY(%s) AND role NOT IN ('super_admin', 'reseller') "
                        "GROUP BY tenant_id",
                        (tenant_ids,)
                    )
                    for tid, cnt in cur.fetchall():
                        sub_counts[tid] = cnt
                except Exception:
                    conn.rollback()

                # 3. WhatsApp quotas
                wa_quotas = {}
                try:
                    cur.execute(
                        "SELECT tenant_id, granted, consumed, reserved, is_blocked "
                        "FROM whatsapp_quota WHERE tenant_id = ANY(%s)",
                        (tenant_ids,)
                    )
                    for tid, g, c, r, b in cur.fetchall():
                        wa_quotas[tid] = {
                            'granted': int(g or 0),
                            'consumed': int(c or 0),
                            'reserved': int(r or 0),
                            'available': int(g or 0) - int(c or 0) - int(r or 0),
                            'is_blocked': bool(b),
                            'configured': True,
                        }
                except Exception:
                    conn.rollback()

                # 3b. Voice quotas
                voice_quotas = {}
                try:
                    cur.execute(
                        "SELECT tenant_id, granted, consumed, reserved, is_blocked "
                        "FROM voice_quota WHERE tenant_id = ANY(%s)",
                        (tenant_ids,)
                    )
                    for tid, g, c, r, b in cur.fetchall():
                        voice_quotas[tid] = {
                            'granted': int(g or 0),
                            'consumed': int(c or 0),
                            'reserved': int(r or 0),
                            'available': int(g or 0) - int(c or 0) - int(r or 0),
                            'is_blocked': bool(b),
                            'configured': True,
                        }
                except Exception:
                    conn.rollback()

                # 4. Voter counts per partition
                for r in owned_rows:
                    tid = r['tenant_id']
                    slug = r['campaign_slug']
                    voters_count = 0
                    voted_count = 0
                    disp_count = 0

                    part_tbl = f"voters_t{tid}"
                    try:
                        cur.execute(
                            f"SELECT "
                            f"COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) <> 'deleted'), "
                            f"COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) <> 'deleted' AND is_voted = TRUE), "
                            f"COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) <> 'deleted' AND is_dispatched = TRUE) "
                            f"FROM {part_tbl}"
                        )
                        row = cur.fetchone()
                        if row:
                            voters_count, voted_count, disp_count = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
                    except Exception:
                        conn.rollback()
                        try:
                            cur.execute(
                                "SELECT "
                                "COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) <> 'deleted'), "
                                "COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) <> 'deleted' AND is_voted = TRUE), "
                                "COUNT(*) FILTER (WHERE LOWER(COALESCE(status, '')) <> 'deleted' AND is_dispatched = TRUE) "
                                "FROM voters WHERE tenant_id = %s",
                                (tid,)
                            )
                            row = cur.fetchone()
                            if row:
                                voters_count, voted_count, disp_count = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
                        except Exception:
                            conn.rollback()

                    # Load campaign.json metadata if available
                    cfg = {}
                    cfg_file = os.path.join(tenants_dir, slug, 'campaign.json')
                    if os.path.exists(cfg_file):
                        try:
                            with open(cfg_file, 'r', encoding='utf-8') as f:
                                cfg = json.load(f)
                        except Exception:
                            pass

                    cand = cfg.get('candidate', {})
                    elec = cfg.get('election', {})
                    party = cfg.get('party', {})

                    from electionapp import s3_storage
                    photo_url = None
                    if cand.get('photo_filename'):
                        photo_url = s3_storage.resolve_photo_url(slug, cand.get('photo_filename'), stored_url=cand.get('photo_url'), prefer_thumb=True)
                    symbol_url = None
                    if party.get('symbol_filename'):
                        symbol_url = s3_storage.resolve_symbol_url(slug, party.get('symbol_filename'), stored_url=party.get('symbol_url'))
                    if not symbol_url:
                        from electionapp.campaign_manager import _get_preset_symbol_key
                        preset_key = _get_preset_symbol_key(party.get('symbol_name_hi') or party.get('symbol_name_en') or party.get('preset_symbol'))
                        if preset_key:
                            symbol_url = f"/static/symbols/{preset_key}.png"

                    wa_info = wa_quotas.get(tid, {'available': 0, 'granted': 0, 'consumed': 0, 'configured': False, 'is_blocked': False})
                    voice_info = voice_quotas.get(tid, {'available': 0, 'granted': 0, 'consumed': 0, 'configured': True, 'is_blocked': False})
                    turnout_pct = (voted_count / voters_count * 100) if voters_count > 0 else 0.0

                    db_cand = str(r.get('candidate_name') or '').strip()
                    if '?' in db_cand:
                        db_cand = ''
                    file_cand_hi = str(cand.get('name_hi') or '').strip()
                    file_cand_en = str(cand.get('name_en') or '').strip()
                    cand_name = db_cand or file_cand_hi or file_cand_en or slug
                    cand_hi = db_cand or file_cand_hi

                    campaign_item = {
                        'tenant_id': tid,
                        'campaign_slug': slug,
                        'candidate_name': cand_name,
                        'candidate_name_hi': cand_hi,
                        'candidate_name_en': file_cand_en,
                        'state': r.get('state') or elec.get('state', 'Rajasthan'),
                        'election_type': r.get('election_type') or elec.get('election_type', 'ASSEMBLY'),
                        'status': (r.get('status') or 'ACTIVE').upper(),
                        'ward_name': elec.get('ward_name_hi') or elec.get('ward_name_en') or elec.get('constituency') or '',
                        'party_name': party.get('name_hi') or party.get('name_en') or '',
                        'symbol_name': party.get('symbol_name_hi') or party.get('symbol_name_en') or '',
                        'theme_color': party.get('theme_color', '#2563eb'),
                        'photo_url': photo_url,
                        'symbol_url': symbol_url,
                        'max_sub_accounts': r.get('max_sub_accounts') or 10,
                        'sub_accounts_count': sub_counts.get(tid, 0),
                        'total_voters': voters_count,
                        'voted_count': voted_count,
                        'dispatched_count': disp_count,
                        'turnout_pct': round(turnout_pct, 1),
                        'wa_quota': wa_info,
                        'voice_quota': voice_info,
                        'created_at': r.get('created_at'),
                    }
                    campaigns.append(campaign_item)

    except Exception as e:
        logger.error("Error gathering reseller dashboard summary: %s", e)

    total_voters = sum(c['total_voters'] for c in campaigns)
    total_voted = sum(c['voted_count'] for c in campaigns)
    total_disp = sum(c['dispatched_count'] for c in campaigns)
    overall_turnout = (total_voted / total_voters * 100) if total_voters > 0 else 0.0

    return {
        'campaigns': campaigns,
        'fleet_stats': {
            'total_campaigns': len(campaigns),
            'max_campaigns': max_campaigns,
            'whatsapp_credits': int(user_row.get('whatsapp_credits') or 0),
            'total_voters': total_voters,
            'total_voted': total_voted,
            'total_dispatches': total_disp,
            'overall_turnout_pct': round(overall_turnout, 2),
        },
        'user': user_row
    }


def list_owned_tenants(owner_user_id: int) -> List[Dict[str, Any]]:
    """
    Returns the list of campaigns/tenants owned by owner_user_id with voice_quota and wa_quota.
    Queries tenants and voice_quota table:
    SELECT tenant_id, granted, consumed, reserved, is_blocked FROM voice_quota WHERE tenant_id = ANY(%s)
    Attaches r['voice_quota'] = {'granted': ..., 'consumed': ..., 'available': ..., 'configured': True} to each row.
    """
    if not owner_user_id:
        return []
    summary = get_reseller_dashboard_summary(owner_user_id)
    return summary.get('campaigns', [])


def _format_user_dict(row: Any, id_to_slug: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    u = dict(row)
    uid = u.get('user_id') or u.get('id')
    u['id'] = uid
    u['user_id'] = uid
    u['whatsapp_credits'] = int(u.get('whatsapp_credits') or 0)
    u['plain_password'] = u.get('plain_password') or ''
    tok = u.get('access_token') or ''
    if not tok and uid:
        try:
            tok = get_or_create_user_magic_token(uid)
        except Exception:
            pass
    u['access_token'] = tok or ''
    u['magic_link'] = f"/k/{tok}" if tok else ''

    tid = u.get('tenant_id')
    tslug = u.get('tenant_slug')
    current_role = u.get('role')
    if current_role == 'reseller':
        tslug = ''
    elif not tslug or tslug == 'None':
        if tid and id_to_slug and tid in id_to_slug:
            tslug = id_to_slug[tid]
        elif tid is None or u.get('role_tier') == 1 or current_role == 'super_admin':
            tslug = '*'
        else:
            tslug = ''
    u['tenant_slug'] = tslug

    rtier = u.get('role_tier') or 2
    u['role_tier'] = rtier
    role = u.get('role')
    if not role:
        role = TIER_TO_ROLE.get(rtier, 'operator')
    u['role'] = role

    booths = u.get('assigned_booths') or []
    assigned_booth = u.get('assigned_booth')
    if assigned_booth is None and booths:
        try:
            assigned_booth = int(booths[0])
        except Exception:
            pass
    if not booths and assigned_booth is not None:
        booths = [assigned_booth]

    u['assigned_booths'] = booths
    u['assigned_booth'] = assigned_booth
    u['assigned_wards'] = list(u.get('assigned_wards') or [])
    u['assigned_villages'] = list(u.get('assigned_villages') or [])
    u['assigned_gps'] = list(u.get('assigned_gps') or [])
    scope = u.get('scope_type')
    if not scope or scope == 'all':
        if u['assigned_gps']:
            scope = 'gp'
        elif booths or assigned_booth:
            scope = 'booth'
        elif u['assigned_wards']:
            scope = 'ward'
        elif u['assigned_villages']:
            scope = 'village'
        else:
            scope = 'all'
    u['scope_type'] = scope
    u['parent_user_id'] = u.get('parent_user_id')
    u['max_sub_accounts'] = u.get('max_sub_accounts')
    raw_mods = u.get('allowed_modules')
    if not raw_mods or 'all' in raw_mods:
        u['allowed_modules'] = ['all']
    else:
        u['allowed_modules'] = [str(m).strip().lower() for m in raw_mods if str(m).strip()]
    raw_active = bool(u.get('is_active') if u.get('is_active') is not None else True)
    try:
        from electionapp.config import get_tenant_status
        c_status = get_tenant_status(tslug) if (tslug and tslug != '*') else 'ACTIVE'
    except Exception:
        c_status = 'ACTIVE'

    if c_status in ('PAUSED', 'ARCHIVED') and current_role not in ('super_admin', 'reseller'):
        u['status'] = c_status
        u['is_active'] = 0
    else:
        u['status'] = 'ACTIVE' if raw_active else 'INACTIVE'
        u['is_active'] = 1 if raw_active else 0
    u['campaign_status'] = c_status
    return u


def _verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash or not password:
        return False
    if stored_hash.startswith(('scrypt:', 'pbkdf2:')):
        try:
            return check_password_hash(stored_hash, password)
        except Exception:
            pass
    if hashlib.sha256(password.strip().encode('utf-8')).hexdigest().lower() == stored_hash.lower():
        return True
    return False


def ensure_schema():
    """Initializes PostgreSQL users table, columns, and seeds default super_admin."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Fast-path: if users table already has the latest columns and default super_admin, skip ALTER DDL locks
                cur.execute("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'users' AND column_name = 'assigned_gps'
                """)
                if cur.fetchone():
                    cur.execute("SELECT 1 FROM users WHERE LOWER(username) = 'admin'")
                    if cur.fetchone():
                        return

                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id SERIAL PRIMARY KEY,
                        tenant_id INTEGER REFERENCES tenants(tenant_id) ON DELETE SET NULL,
                        username VARCHAR(64) UNIQUE NOT NULL,
                        password_hash VARCHAR(255) NOT NULL,
                        role_tier INTEGER NOT NULL DEFAULT 2,
                        role VARCHAR(32) NOT NULL DEFAULT 'operator',
                        full_name VARCHAR(128) NOT NULL,
                        phone VARCHAR(32) DEFAULT '',
                        assigned_booths INTEGER[],
                        assigned_booth INTEGER,
                        assigned_wards INTEGER[],
                        assigned_villages TEXT[],
                        assigned_gps TEXT[],
                        scope_type VARCHAR(32) DEFAULT 'all',
                        karyakarta_id INTEGER,
                        tenant_slug VARCHAR(64),
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMPTZ
                    );
                """)
                # Ensure all columns exist and constraints are non-blocking
                cur.execute("ALTER TABLE users ALTER COLUMN tenant_id DROP NOT NULL;")
                cur.execute("ALTER TABLE users ALTER COLUMN phone DROP NOT NULL;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT 'operator';")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_booth INTEGER;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS karyakarta_id INTEGER;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tenant_slug VARCHAR(64);")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMPTZ;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_wards INTEGER[];")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_villages TEXT[];")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS assigned_gps TEXT[];")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS scope_type VARCHAR(32) DEFAULT 'all';")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS parent_user_id INTEGER;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS max_sub_accounts INTEGER;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_modules TEXT[];")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS max_campaigns INTEGER;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_credits INTEGER DEFAULT 0;")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS access_token VARCHAR(64);")
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS plain_password VARCHAR(255);")
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_access_token ON users (access_token) WHERE access_token IS NOT NULL;")
                cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS max_sub_accounts INTEGER DEFAULT 50;")
                cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL;")
                cur.execute("ALTER TABLE karyakartas ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;")

                # Seed default super admin 'admin' if not present
                cur.execute("SELECT user_id FROM users WHERE LOWER(username) = 'admin'")
                if not cur.fetchone():
                    pwd_hash = generate_password_hash("admin2026")
                    cur.execute("""
                        INSERT INTO users (username, password_hash, full_name, phone, role, role_tier, tenant_slug, is_active)
                        VALUES ('admin', %s, 'Super Administrator', '', 'super_admin', 1, '*', TRUE)
                    """, (pwd_hash,))
    except Exception as e:
        logger.error("Failed to initialize users table schema: %s", e)


try:
    ensure_schema()
except Exception:
    pass


def authenticate_user(username: str, password: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validates user credentials directly against PostgreSQL voter_platform.users."""
    if not username or not password:
        return None, "यूज़रनेम और पासवर्ड दोनों आवश्यक हैं (Username and password required)"

    clean_uname = username.strip().lower()
    raw_uname = username.strip()
    clean_no_underscore = clean_uname.replace('_', '').replace('-', '')
    _, id_to_slug = _get_tenant_map()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM users 
                    WHERE (LOWER(username) = %s 
                           OR REPLACE(REPLACE(LOWER(username), '_', ''), '-', '') = %s
                           OR phone = %s OR phone = %s) 
                      AND (is_active = TRUE OR is_active IS NULL)
                    ORDER BY 
                        CASE 
                            WHEN LOWER(username) = %s THEN 0 
                            WHEN REPLACE(REPLACE(LOWER(username), '_', ''), '-', '') = %s THEN 1 
                            ELSE 2 
                        END,
                        user_id ASC
                """, (clean_uname, clean_no_underscore, raw_uname, clean_uname, clean_uname, clean_no_underscore))
                rows = cur.fetchall()
                if not rows:
                    return None, "अमान्य यूज़रनेम या पासवर्ड (Invalid username or password)"

                matched_user = None
                for r in rows:
                    candidate = _format_user_dict(r, id_to_slug)
                    if _verify_password(candidate['password_hash'], password):
                        matched_user = candidate
                        break

                if not matched_user:
                    return None, "अमान्य यूज़रनेम या पासवर्ड (Invalid username or password)"

                cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE user_id = %s", (matched_user['user_id'],))
                return matched_user, None
    except Exception as e:
        logger.error("Authentication error for user '%s': %s", username, e, exc_info=True)
        return None, "प्रमाणीकरण सेवा अनुपलब्ध है (Authentication service temporarily unavailable)"


def generate_random_password(length: int = 8) -> str:
    chars = '23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ'
    return ''.join(secrets.choice(chars) for _ in range(length))


def get_sub_account_quota(tenant_slug: str) -> Dict[str, Any]:
    """Returns sub-account quota and usage for a campaign."""
    clean_tenant = str(tenant_slug).strip().lower() if tenant_slug else ''
    if not clean_tenant or clean_tenant == '*':
        return {'limit': 50, 'used': 0, 'remaining': 50, 'allowed': True}
    slug_to_id, _ = _get_tenant_map()
    tid = slug_to_id.get(clean_tenant)
    limit = 50
    count = 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if tid is not None:
                    cur.execute("SELECT max_sub_accounts FROM tenants WHERE tenant_id = %s", (tid,))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        limit = int(row[0])
                cur.execute("""
                    SELECT COUNT(*) FROM users
                    WHERE (tenant_id = %s OR LOWER(tenant_slug) = %s)
                      AND role_tier > 2
                """, (tid, clean_tenant))
                r = cur.fetchone()
                count = r[0] if r else 0
    except Exception:
        pass
    return {
        'limit': limit,
        'used': count,
        'remaining': max(0, limit - count),
        'can_create': count < limit
    }


def set_tenant_sub_account_limit(tenant_slug: str, limit: int) -> bool:
    """Sets the maximum number of sub-accounts allowed for a tenant."""
    slug_to_id, _ = _get_tenant_map()
    clean_tenant = str(tenant_slug).strip().lower()
    tid = slug_to_id.get(clean_tenant)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if tid is not None:
                    cur.execute("UPDATE tenants SET max_sub_accounts = %s WHERE tenant_id = %s", (int(limit), tid))
                else:
                    cur.execute("UPDATE tenants SET max_sub_accounts = %s WHERE LOWER(campaign_slug) = %s", (int(limit), clean_tenant))
        return True
    except Exception:
        return False


def list_users(tenant_slug: Optional[str] = None, exclude_super_admin: bool = False) -> List[Dict[str, Any]]:
    """Lists users from PostgreSQL, optionally filtered by tenant_slug."""
    slug_to_id, id_to_slug = _get_tenant_map()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if tenant_slug and tenant_slug != '*':
                    clean_slug = tenant_slug.strip().lower()
                    tid = slug_to_id.get(clean_slug)
                    if exclude_super_admin:
                        cur.execute("""
                            SELECT * FROM users
                            WHERE (tenant_id = %s OR LOWER(tenant_slug) = %s) AND (role_tier > 1 OR role_tier IS NULL) AND role != 'super_admin'
                            ORDER BY user_id DESC
                        """, (tid, clean_slug))
                    else:
                        cur.execute("""
                            SELECT * FROM users
                            WHERE (tenant_id = %s OR LOWER(tenant_slug) = %s) OR (tenant_slug = '*' AND (role = 'super_admin' OR role_tier = 1))
                            ORDER BY user_id DESC
                        """, (tid, clean_slug))
                else:
                    if exclude_super_admin:
                        cur.execute("SELECT * FROM users WHERE (role_tier > 1 OR role_tier IS NULL) AND role != 'super_admin' ORDER BY user_id DESC")
                    else:
                        cur.execute("SELECT * FROM users ORDER BY user_id DESC")

                rows = cur.fetchall()
                owned_counts = {}
                try:
                    cur.execute("SELECT owner_user_id, COUNT(*) FROM tenants WHERE owner_user_id IS NOT NULL GROUP BY owner_user_id")
                    for oid, cnt in cur.fetchall():
                        if oid is not None:
                            owned_counts[oid] = cnt
                except Exception:
                    pass

                users_list = []
                for r in rows:
                    u = _format_user_dict(r, id_to_slug)
                    if u.get('role') == 'reseller':
                        u['owned_campaigns_count'] = owned_counts.get(u.get('user_id'), 0)
                    users_list.append(u)
                return users_list
    except Exception:
        return []


def list_users_for_reseller(owner_user_id: int, tenant_slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists users belonging to campaigns owned by the given reseller."""
    if not owner_user_id:
        return []
    owned = get_owned_tenants(owner_user_id)
    if not owned:
        return []
    owned_map = {t['campaign_slug'].lower(): t['tenant_id'] for t in owned}
    id_to_slug = {t['tenant_id']: t['campaign_slug'] for t in owned}

    target_tids = []
    if tenant_slug and tenant_slug != '*':
        clean = tenant_slug.strip().lower()
        if clean not in owned_map:
            return []  # Not owned by this reseller
        target_tids = [owned_map[clean]]
    else:
        target_tids = list(id_to_slug.keys())

    if not target_tids:
        return []

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM users "
                    "WHERE (tenant_id = ANY(%s) OR LOWER(tenant_slug) = ANY(%s)) "
                    "  AND role NOT IN ('super_admin', 'reseller') "
                    "ORDER BY user_id DESC",
                    (target_tids, list(owned_map.keys()))
                )
                rows = cur.fetchall()
                return [_format_user_dict(r, id_to_slug) for r in rows]
    except Exception as e:
        logger.error("Error listing users for reseller: %s", e)
        return []



def create_user_account(
    username: str,
    password: str,
    full_name: str,
    phone: Optional[str] = None,
    role: str = 'operator',
    tenant_slug: Optional[str] = None,
    assigned_booth: Optional[int] = None,
    assigned_booths: Optional[List[int]] = None,
    assigned_wards: Optional[List[int]] = None,
    assigned_villages: Optional[List[str]] = None,
    assigned_gps: Optional[List[str]] = None,
    scope_type: str = 'all',
    allowed_modules: Optional[List[str]] = None,
    parent_user_id: Optional[int] = None,
    karyakarta_id: Optional[int] = None,
    max_campaigns: Optional[int] = None,
    whatsapp_credits: Optional[int] = None,
    enforce_quota: bool = True,
    overwrite_password: bool = False
) -> Tuple[bool, str, Optional[int]]:
    """Creates a new campaign user account in PostgreSQL."""
    if not username or not password or not full_name:
        return False, "सभी आवश्यक फ़ील्ड भरें (All required fields must be provided)", None

    clean_uname = username.strip().lower()
    pwd_hash = generate_password_hash(password.strip())

    slug_to_id, _ = _get_tenant_map()
    if role == 'reseller':
        # Unbound but ownership-scoped — never default to 'ward82' or '*'.
        clean_tenant = ''
        tid = None
    elif role == 'super_admin':
        clean_tenant = '*'
        tid = None
    else:
        clean_tenant = tenant_slug.strip().lower() if tenant_slug else ''
        if not clean_tenant:
            return False, "अभियान (Tenant) चुनना अनिवार्य है (Campaign is required)", None
        tid = slug_to_id.get(clean_tenant) if clean_tenant != '*' else None
    role_tier = ROLE_TO_TIER.get(role, 2)

    # Resolve booths list vs single booth
    booths = list(assigned_booths) if assigned_booths is not None else []
    if assigned_booth is not None and not booths:
        booths = [int(assigned_booth)]
    elif booths and assigned_booth is None:
        try:
            assigned_booth = int(booths[0])
        except Exception:
            pass

    wards = list(assigned_wards) if assigned_wards is not None else []
    villages = [str(v).strip() for v in (assigned_villages or []) if str(v).strip()]
    gps = [str(g).strip() for g in (assigned_gps or []) if str(g).strip()]

    # Infer scope_type if defaulted to 'all'
    if not scope_type or scope_type == 'all':
        if gps:
            scope_type = 'gp'
        elif booths or assigned_booth is not None:
            scope_type = 'booth'
        elif wards:
            scope_type = 'ward'
        elif villages:
            scope_type = 'village'
        else:
            scope_type = 'all'

    # Auto-resolve constituent polling booths for assigned GPs to guarantee backward compatibility
    if gps and not booths and tid:
        try:
            with _get_connection() as c_conn:
                with c_conn.cursor() as c_cur:
                    c_cur.execute("""
                        SELECT DISTINCT part_no
                        FROM voters
                        WHERE tenant_id = %s
                          AND gram_panchayat = ANY(%s)
                          AND part_no IS NOT NULL;
                    """, (tid, gps))
                    gp_parts = [r[0] for r in c_cur.fetchall() if r[0] is not None]
                    if gp_parts:
                        booths = sorted(list(set(gp_parts)))
                        if assigned_booth is None:
                            assigned_booth = booths[0]
        except Exception:
            pass

    clean_modules = [str(m).strip().lower() for m in (allowed_modules or []) if str(m).strip()]
    if not clean_modules or 'all' in clean_modules or '*' in clean_modules:
        clean_modules = ['all']

    # Quota check for sub-accounts created within a campaign
    if enforce_quota and role_tier > 2 and clean_tenant != '*':
        quota = get_sub_account_quota(clean_tenant)
        if not quota['can_create']:
            return False, f"उप-खाता निर्माण सीमा ({quota['limit']}) पूर्ण हो चुकी है। कृपया सुपर एडमिन से सीमा बढ़वाएं (Sub-account quota of {quota['limit']} reached).", None

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Check for existing user globally
                cur.execute("SELECT user_id, tenant_id, password_hash, tenant_slug, access_token FROM users WHERE LOWER(username) = %s", (clean_uname,))
                row = cur.fetchone()

                if row:
                    existing_uid, existing_tid, existing_hash, existing_tslug, existing_token = row[0], row[1], row[2], row[3], row[4]
                    # If the user exists for a different tenant, reject with clear error
                    if existing_tid is not None and tid is not None and existing_tid != tid:
                        return False, f"यूज़रनेम '{clean_uname}' पहले से किसी अन्य अभियान ({existing_tslug or 'अन्य'}) में उपयोग में है। कृपया भिन्न यूज़रनेम चुनें। (Username '{clean_uname}' is already taken in another campaign.)", None

                    user_id = existing_uid
                    # Preserve existing password on sync/re-provisioning unless overwrite explicitly requested
                    final_hash = pwd_hash if (overwrite_password or not existing_hash) else existing_hash
                    plain_pwd_to_store = password.strip() if (overwrite_password or not existing_hash) else None
                    token_to_set = existing_token or secrets.token_urlsafe(32)
                    cur.execute("""
                        UPDATE users SET
                            password_hash = %s,
                            plain_password = COALESCE(%s, plain_password),
                            role_tier = %s,
                            role = %s,
                            full_name = %s,
                            phone = %s,
                            assigned_booths = %s,
                            assigned_booth = %s,
                            assigned_wards = %s,
                            assigned_villages = %s,
                            assigned_gps = %s,
                            scope_type = %s,
                            allowed_modules = %s,
                            parent_user_id = COALESCE(%s, parent_user_id),
                            karyakarta_id = %s,
                            tenant_slug = %s,
                            tenant_id = COALESCE(%s, tenant_id),
                            max_campaigns = COALESCE(%s, max_campaigns),
                            whatsapp_credits = COALESCE(%s, whatsapp_credits),
                            access_token = COALESCE(access_token, %s),
                            is_active = TRUE
                        WHERE user_id = %s
                    """, (
                        final_hash, plain_pwd_to_store, role_tier, role, full_name.strip(),
                        phone.strip() if phone else '', booths, assigned_booth,
                        wards, villages, gps, scope_type, clean_modules, parent_user_id,
                        karyakarta_id, clean_tenant, tid, max_campaigns, whatsapp_credits, token_to_set, user_id
                    ))
                else:
                    new_token = secrets.token_urlsafe(32)
                    cur.execute("""
                        INSERT INTO users (
                            tenant_id, username, password_hash, plain_password, role_tier, role, full_name, phone,
                            assigned_booths, assigned_booth, assigned_wards, assigned_villages, assigned_gps,
                            scope_type, allowed_modules, parent_user_id, karyakarta_id, tenant_slug,
                            max_campaigns, whatsapp_credits, access_token, is_active
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                        RETURNING user_id
                    """, (
                        tid, clean_uname, pwd_hash, password.strip(), role_tier, role, full_name.strip(),
                        phone.strip() if phone else '', booths, assigned_booth,
                        wards, villages, gps, scope_type, clean_modules, parent_user_id,
                        karyakarta_id, clean_tenant, max_campaigns, (whatsapp_credits or 0), new_token
                    ))
                    user_id = cur.fetchone()[0]
                return True, "उपयोगकर्ता खाता सफलतापूर्वक बनाया गया (User created successfully)", user_id
    except Exception as e:
        return False, str(e), None


def reset_user_password(user_id: int, new_password: Optional[str] = None) -> Tuple[bool, str]:
    """Resets user password in PostgreSQL."""
    if not new_password:
        new_password = generate_random_password(8)
    pwd_hash = generate_password_hash(new_password)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                try:
                    uid_int = int(user_id)
                    cur.execute("UPDATE users SET password_hash = %s, plain_password = %s WHERE user_id = %s", (pwd_hash, new_password, uid_int))
                except (ValueError, TypeError):
                    cur.execute("UPDATE users SET password_hash = %s, plain_password = %s WHERE username = %s", (pwd_hash, new_password, str(user_id)))
        return True, new_password
    except Exception as e:
        return False, str(e)


def toggle_user_status(user_id: int) -> bool:
    """Toggles active/inactive flag in PostgreSQL."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET is_active = NOT COALESCE(is_active, TRUE) WHERE user_id = %s", (user_id,))
        return True
    except Exception:
        return False


def sync_users_with_campaign_status(campaign_slug: str, status: str) -> int:
    """
    Synchronizes users and karyakartas table is_active flag with campaign lifecycle status.
    - If status in ('PAUSED', 'ARCHIVED', 'CONCLUDED'): is_active = FALSE
    - If status == 'ACTIVE': is_active = TRUE
    Excludes super_admin and reseller roles.
    Returns the count of users updated.
    """
    if not campaign_slug:
        return 0
    clean = str(campaign_slug).strip().lower()
    st = str(status).strip().upper()
    is_active_val = (st == 'ACTIVE')

    slug_to_id, _ = _get_tenant_map()
    tid = slug_to_id.get(clean)

    updated_count = 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # 1. Update users table
                cur.execute("""
                    UPDATE users
                    SET is_active = %s
                    WHERE (LOWER(tenant_slug) = %s OR (tenant_id = %s AND %s IS NOT NULL))
                      AND role NOT IN ('super_admin', 'reseller')
                """, (is_active_val, clean, tid, tid))
                updated_count = cur.rowcount

                # 2. Update karyakartas table
                if tid is not None:
                    cur.execute("""
                        UPDATE karyakartas
                        SET is_active = %s
                        WHERE tenant_id = %s
                    """, (is_active_val, tid))
            conn.commit()
    except Exception as e:
        logger.error("Failed to sync users with campaign status for '%s': %s", clean, e)
    return updated_count


def sync_all_users_with_campaign_statuses() -> Dict[str, int]:
    """
    Batch synchronizes all users and karyakartas to match their parent tenant's current status.
    Ensures zero discrepancy across all campaigns in production.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Synchronize inactive/paused campaigns
                cur.execute("""
                    UPDATE users u
                    SET is_active = FALSE
                    FROM tenants t
                    WHERE (u.tenant_id = t.tenant_id OR LOWER(u.tenant_slug) = LOWER(t.campaign_slug))
                      AND UPPER(t.status) IN ('PAUSED', 'ARCHIVED')
                      AND u.role NOT IN ('super_admin', 'reseller')
                      AND (u.is_active = TRUE OR u.is_active IS NULL);
                """)
                paused_count = cur.rowcount

                cur.execute("""
                    UPDATE karyakartas k
                    SET is_active = FALSE
                    FROM tenants t
                    WHERE k.tenant_id = t.tenant_id
                      AND UPPER(t.status) IN ('PAUSED', 'ARCHIVED')
                      AND (k.is_active = TRUE OR k.is_active IS NULL);
                """)
                paused_karya = cur.rowcount

                # Synchronize active campaigns
                cur.execute("""
                    UPDATE users u
                    SET is_active = TRUE
                    FROM tenants t
                    WHERE (u.tenant_id = t.tenant_id OR LOWER(u.tenant_slug) = LOWER(t.campaign_slug))
                      AND UPPER(COALESCE(t.status, 'ACTIVE')) = 'ACTIVE'
                      AND u.role NOT IN ('super_admin', 'reseller')
                      AND u.is_active = FALSE;
                """)
                active_count = cur.rowcount

                cur.execute("""
                    UPDATE karyakartas k
                    SET is_active = TRUE
                    FROM tenants t
                    WHERE k.tenant_id = t.tenant_id
                      AND UPPER(COALESCE(t.status, 'ACTIVE')) = 'ACTIVE'
                      AND k.is_active = FALSE;
                """)
                active_karya = cur.rowcount

            conn.commit()
            return {
                'paused_users': paused_count,
                'paused_karyakartas': paused_karya,
                'active_users': active_count,
                'active_karyakartas': active_karya
            }
    except Exception as e:
        logger.error("Failed batch syncing users with campaign statuses: %s", e)
        return {}


def delete_user_account(user_id: int) -> bool:
    """Deletes user from PostgreSQL."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
                return cur.rowcount > 0
    except Exception:
        return False


def update_user_account(
    user_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    phone: Optional[str] = None,
    role: Optional[str] = None,
    role_tier: Optional[int] = None,
    tenant_slug: Optional[str] = None,
    password: Optional[str] = None,
    assigned_booths: Optional[List[int]] = None,
    assigned_booth: Optional[int] = None,
    assigned_wards: Optional[List[int]] = None,
    assigned_villages: Optional[List[str]] = None,
    assigned_gps: Optional[List[str]] = None,
    scope_type: Optional[str] = None,
    allowed_modules: Optional[List[str]] = None,
    max_sub_accounts: Optional[int] = None,
    max_campaigns: Optional[int] = None,
    whatsapp_credits: Optional[int] = None,
    is_active: Optional[Union[bool, int]] = None
) -> bool:
    """Updates user fields in PostgreSQL with validation and safety guards."""
    existing = get_user_by_id(user_id)
    if not existing:
        raise ValueError(f"उपयोगकर्ता ID {user_id} नहीं मिला (User not found)")

    # Safety Guard: Cannot disable or demote the last active super_admin
    is_current_super = existing.get('role') == 'super_admin'
    demoting_super = role is not None and role != 'super_admin'
    disabling_super = is_active is False or is_active == 0
    if is_current_super and (demoting_super or disabling_super):
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM users WHERE role = 'super_admin' AND is_active = TRUE AND user_id != %s", (user_id,))
                    other_supers = cur.fetchone()[0]
                    if other_supers == 0:
                        raise ValueError("कम से कम एक सक्रिय सुपर एडमिन खाता रहना अनिवार्य है (Cannot disable or demote the last super admin)")
        except ValueError:
            raise
        except Exception as e:
            logger.error("Error checking super admin count: %s", e)

    if role and role_tier is None:
        role_tier = ROLE_TO_TIER.get(role, 2)
    elif role_tier is not None and not role:
        if existing and existing.get('role') in ('reseller', 'candidate_admin', 'operator') and int(role_tier) == 2:
            role = existing.get('role')
        else:
            role = TIER_TO_ROLE.get(int(role_tier), 'admin')

    if assigned_booths and assigned_booth is None:
        try:
            assigned_booth = int(assigned_booths[0])
        except Exception:
            pass
    elif assigned_booth is not None and not assigned_booths:
        assigned_booths = [int(assigned_booth)]

    fields = []
    values = []

    # Username update with collision check
    if username is not None:
        clean_uname = username.strip().lower()
        if not clean_uname:
            raise ValueError("यूज़रनेम रिक्त नहीं हो सकता (Username cannot be empty)")
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM users WHERE LOWER(username) = %s AND user_id != %s", (clean_uname, user_id))
                if cur.fetchone():
                    raise ValueError(f"यूज़रनेम '{clean_uname}' पहले से उपयोग में है (Username already taken)")
        fields.append("username = %s")
        values.append(clean_uname)

    # Optional Password update
    if password is not None and str(password).strip():
        clean_pwd = str(password).strip()
        if len(clean_pwd) < 4:
            raise ValueError("पासवर्ड कम से कम 4 अक्षरों का होना चाहिए (Password must be at least 4 characters)")
        pwd_hash = generate_password_hash(clean_pwd)
        fields.append("password_hash = %s")
        values.append(pwd_hash)
        fields.append("plain_password = %s")
        values.append(clean_pwd)

    # Tenant Slug / Tenant ID update
    if tenant_slug is not None:
        slug_to_id, _ = _get_tenant_map()
        clean_ts = str(tenant_slug).strip().lower()
        effective_role = role or existing.get('role')
        if clean_ts in ('*', 'all', '') or effective_role in ('super_admin', 'reseller'):
            fields.append("tenant_id = %s")
            values.append(None)
            fields.append("tenant_slug = %s")
            values.append('*' if effective_role == 'super_admin' else None)
        else:
            tid = slug_to_id.get(clean_ts)
            fields.append("tenant_id = %s")
            values.append(tid)
            fields.append("tenant_slug = %s")
            values.append(clean_ts)

    if full_name is not None:
        fields.append("full_name = %s")
        values.append(full_name.strip())
    if phone is not None:
        fields.append("phone = %s")
        values.append(phone.strip())
    if role is not None:
        fields.append("role = %s")
        values.append(role.strip())
    if role_tier is not None:
        fields.append("role_tier = %s")
        values.append(int(role_tier))
    if assigned_booth is not None:
        fields.append("assigned_booth = %s")
        values.append(int(assigned_booth))
    if assigned_booths is not None:
        fields.append("assigned_booths = %s")
        values.append(assigned_booths)
    if assigned_wards is not None:
        fields.append("assigned_wards = %s")
        values.append(assigned_wards)
    if assigned_villages is not None:
        fields.append("assigned_villages = %s")
        values.append([str(v).strip() for v in assigned_villages if str(v).strip()])
    if assigned_gps is not None:
        fields.append("assigned_gps = %s")
        values.append([str(g).strip() for g in assigned_gps if str(g).strip()])
    if scope_type is not None:
        fields.append("scope_type = %s")
        values.append(scope_type.strip().lower())
    if allowed_modules is not None:
        fields.append("allowed_modules = %s")
        clean_mods = [str(m).strip().lower() for m in allowed_modules if str(m).strip()]
        values.append(clean_mods or ['all'])
    if max_sub_accounts is not None:
        fields.append("max_sub_accounts = %s")
        values.append(int(max_sub_accounts))
    if max_campaigns is not None:
        fields.append("max_campaigns = %s")
        values.append(int(max_campaigns))
    if whatsapp_credits is not None:
        fields.append("whatsapp_credits = %s")
        values.append(int(whatsapp_credits))
    if is_active is not None:
        fields.append("is_active = %s")
        values.append(bool(is_active))

    if not fields:
        return True

    values.append(user_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id = %s", values)
                return cur.rowcount > 0
    except Exception as e:
        logger.error("Failed to update user_id %s: %s", user_id, e)
        raise


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Gets single user by user_id from PostgreSQL."""
    _, id_to_slug = _get_tenant_map()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                return _format_user_dict(row, id_to_slug) if row else None
    except Exception:
        return None


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Gets single user by username from PostgreSQL."""
    _, id_to_slug = _get_tenant_map()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE LOWER(username) = %s", (username.strip().lower(),))
                row = cur.fetchone()
                return _format_user_dict(row, id_to_slug) if row else None
    except Exception:
        return None


def get_or_create_user_magic_token(user_id: int) -> Optional[str]:
    """Retrieves existing magic access_token for user or generates a cryptographically secure URL-safe token."""
    if not user_id:
        return None
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT access_token FROM users WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
                # Generate new token
                tok = secrets.token_urlsafe(32)
                cur.execute("UPDATE users SET access_token = %s WHERE user_id = %s", (tok, user_id))
                return tok
    except Exception as e:
        logger.error("Failed to get or create magic token for user %s: %s", user_id, e)
        return None


def get_user_by_magic_token(token: str) -> Optional[Dict[str, Any]]:
    """Retrieves an active user by their magic login access_token and updates last_login."""
    clean_tok = str(token or '').strip()
    if not clean_tok or len(clean_tok) < 16 or len(clean_tok) > 64:
        return None
    _, id_to_slug = _get_tenant_map()
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM users 
                    WHERE access_token = %s 
                      AND (is_active = TRUE OR is_active IS NULL)
                      AND (token_expires_at IS NULL OR token_expires_at > CURRENT_TIMESTAMP)
                """, (clean_tok,))
                row = cur.fetchone()
                if row:
                    user_dict = _format_user_dict(row, id_to_slug)
                    try:
                        cur.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE user_id = %s", (user_dict['user_id'],))
                    except Exception:
                        pass
                    return user_dict
                return None
    except Exception as e:
        logger.error("Error looking up user by magic token: %s", e)
        return None


def revoke_user_magic_token(user_id: int) -> bool:
    """Invalidates the magic access_token for a user account."""
    if not user_id:
        return False
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE users SET access_token = NULL WHERE user_id = %s", (user_id,))
                return cur.rowcount > 0
    except Exception as e:
        logger.error("Error revoking magic token for user %s: %s", user_id, e)
        return False



def validate_user_session_active(
    user_id: int,
    magic_token: Optional[str] = None,
    expected_hash_prefix: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """Fast server-side session validator returning (is_valid, error_code).

    Validates active state against PostgreSQL voter_platform.users:
    1. Checks if user_id exists in users table. If deleted -> (False, 'deleted')
    2. Checks if is_active is True. If deactivated or campaign paused -> (False, 'inactive')
    3. If magic_token was used, verifies access_token is still valid. If revoked -> (False, 'token_revoked')
    4. If expected_hash_prefix is provided: verifies password has not been reset/changed -> (False, 'password_changed')

    Returns:
        (True, None) if session is valid and active.
        (False, reason_str) if session must be immediately evicted.
    """
    if not user_id:
        return False, 'no_user_id'
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_active, password_hash, access_token FROM users WHERE user_id = %s",
                    (user_id,)
                )
                row = cur.fetchone()
                if not row:
                    return False, 'deleted'
                is_active, pw_hash, access_token = row
                if is_active is not None and not is_active:
                    return False, 'inactive'
                if magic_token:
                    if not access_token or str(access_token).strip() != str(magic_token).strip():
                        return False, 'token_revoked'
                if expected_hash_prefix and pw_hash:
                    curr_prefix = str(pw_hash)[:10]
                    if curr_prefix != str(expected_hash_prefix):
                        return False, 'password_changed'
                return True, None
    except Exception as e:
        logger.error("Session active validation error for user %s: %s", user_id, e)
        return True, None
