"""
postgres_repo.py
PostgreSQL 17 implementation of BaseVoterRepository for Unified Multi-Tenant Voter Platform (Stage 2).
Uses partition pruning via strictly bound tenant_id and two-stage Indic search.
"""

import os
import re
import datetime
import json
import logging
from contextlib import contextmanager
from typing import Optional, Dict, Any, List, Union, Tuple

import psycopg2
import psycopg2.pool
import psycopg2.extras

from electionapp.repo.base import BaseVoterRepository
from electionapp.search import two_stage_search
from electionapp.tenancy import mukhiya_prefers_male
from electionapp.translit.normalizer import strip_fffd_artifacts

logger = logging.getLogger("smartbooth.postgres_repo")

SLUG_TO_ID: Dict[str, int] = {}
ID_TO_SLUG: Dict[int, str] = {}

KNOWN_GP_NAMES: Dict[str, str] = {
    'iras': 'ईरास (IRAS)',
    'karjaliya': 'करजालिया (Karjaliya)',
    'mod ka nimbaheda': 'मोड़ का निम्बाहेड़ा (Mod Ka Nimbaheda)',
    'mod ka nimbahera': 'मोड़ का निम्बाहेड़ा (Mod Ka Nimbaheda)',
    'rooppura': 'रूपपुरा (आसीन्द)',
    'rooppura (asind)': 'रूपपुरा (आसीन्द)',
    'rooppura (shahpura)': 'रूपपुरा (शाहपुरा)',
    'brahamano ki sareri': 'ब्राह्मणों की सरेरी (Brahamano ki sareri)',
    'brahmano ki sareri': 'ब्राह्मणों की सरेरी (Brahamano ki sareri)',
    'kaliyas': 'कालियास (KALIYAS)',
    'paladi': 'पालडी (Paladi)',
    'dulhepura': 'दूलहेपुरा (DULHEPURA)',
    'jodhras': 'जोधड़ास (JODHRAS)',
    'ganglas': 'गांगलास (GANGLAS)',
    'jivliya': 'जिवलिया (Jivliya)',
    'lilala': 'लीलाला',
    'mahingoni moodhon ki dhani': 'माहिगोणी मुढो की ढाणी',
    'mahingoni': 'माहिगोणी (Mahingoni)',
    'chhoti virol': 'छोटी विरोल (Chhoti Virol)',
    'hiradesar': 'हीरादेसर (Hiradesar)',
    'sopada': 'सोपड़ा (Sopada)',
    'ustra': 'उस्तरां (Ustra)',
    'dhoru': 'धोरू (Dhoru)',
    'kudi': 'कुड़ी (Kudi)',
    'bagoriya': 'बागोरिया (Bagoriya)',
    'kagal': 'कागल (Kagal)',
    'bhundana': 'भुण्डाणा (Bhundana)',
    'gaderi': 'गादेरी (Gaderi)',
    'satheen': 'साथीन (Satheen)',
    'didwadi': 'डिडवाडी (Didwadi)',
    'jatavati': 'जटावती (Jatavati)',
    'hathdoli': 'हथडोली (Hathdoli)',
    'badagav sarvar': 'बड़ागांव सरवर / हरसोता (Badagav Sarvar)',
    'badsam': 'बड़सम (Badsam)',
    'dhanta': 'धानता (Dhanta)',
    'hadetar': 'हाडेतर (Hadetar)',
    'jajusan': 'जाजूसन (Jajusan)',
    'karola': 'कारोला (Karola)',
    'medajageer': 'मेड़ाजागीर (Medajageer)',
    'paladar': 'पलादर (Paladar)',
    'pratappura': 'प्रतापपुरा (Pratappura)',
    'virol badi': 'विरोल बड़ी (Virol Badi)',
    'chhotu': 'छोटू (Chhotu)',
}

def derive_hi_gp_name(raw_gp: str) -> str:
    if not raw_gp or str(raw_gp).strip() == 'General':
        return ''
    cleaned = str(raw_gp).strip().lower()
    if cleaned in KNOWN_GP_NAMES:
        return KNOWN_GP_NAMES[cleaned]
    for k, v in KNOWN_GP_NAMES.items():
        if k == cleaned or f"({k})" in cleaned:
            return v
    if any('\u0900' <= c <= '\u097f' for c in str(raw_gp)):
        return str(raw_gp).strip()
    return str(raw_gp).strip()


class PostgresVoterRepository(BaseVoterRepository):
    """
    PostgreSQL-backed voter repository for multi-tenant deployment.
    Uses ThreadedConnectionPool and strictly enforces tenant_id in every SQL predicate.
    """

    def __init__(
        self,
        tenant_slug: str = "jelatra",
        host: str = "localhost",
        port: int = 5432,
        dbname: str = "voter_platform",
        user: str = "postgres",
        password: str = "postgres",
        dsn: Optional[str] = None,
        minconn: int = 1,
        maxconn: int = 10,
        connection_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None,
        **kwargs
    ):
        super().__init__(tenant_slug=tenant_slug, storage_backend="postgres")
        actual_maxconn = int(os.environ.get("POSTGRES_REPO_MAXCONN", maxconn))
        actual_minconn = int(os.environ.get("POSTGRES_REPO_MINCONN", minconn))
        db_config = kwargs.get("db_config")
        if connection_pool:
            self.pool = connection_pool
            self._owns_pool = False
        elif dsn:
            self.pool = psycopg2.pool.ThreadedConnectionPool(minconn=actual_minconn, maxconn=actual_maxconn, dsn=dsn)
            self._owns_pool = True
        elif db_config and isinstance(db_config, dict):
            self.pool = psycopg2.pool.ThreadedConnectionPool(minconn=actual_minconn, maxconn=actual_maxconn, **db_config)
            self._owns_pool = True
        else:
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=actual_minconn,
                maxconn=actual_maxconn,
                host=os.environ.get("PGHOST", host),
                port=int(os.environ.get("PGPORT", port)),
                dbname=os.environ.get("PGDATABASE", dbname),
                user=os.environ.get("PGUSER", user),
                password=os.environ.get("PGPASSWORD", password),
            )
            self._owns_pool = True
        pg_host = os.environ.get("PGHOST", host)
        pg_port = os.environ.get("PGPORT", port)
        pg_db = os.environ.get("PGDATABASE", dbname)
        self.db_path = f"postgresql://{pg_host}:{pg_port}/{pg_db}?tenant={self.tenant_slug}"
        self._refresh_tenant_map()

    def _refresh_tenant_map(self):
        """Populate in-memory cache mapping campaign_slug <-> tenant_id from database."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT tenant_id, campaign_slug FROM tenants;")
                    for tid, cslug in cur.fetchall():
                        s = str(cslug).strip().lower()
                        SLUG_TO_ID[s] = tid
                        ID_TO_SLUG[tid] = s
        except Exception as e:
            logger.warning("Could not refresh tenant map from database: %s", e)

    def close(self) -> None:
        """Closes all connections in the pool if owned."""
        if getattr(self, "_owns_pool", False) and self.pool:
            try:
                self.pool.closeall()
            except Exception:
                pass

    @contextmanager
    def get_connection(self):
        """Context manager for acquiring and releasing pooled connections."""
        conn = self.pool.getconn()
        try:
            yield conn
        finally:
            self.pool.putconn(conn)

    def _resolve_tenant(self, tenant_id: Optional[Union[int, str]] = None) -> Tuple[int, str]:
        """Resolves (tenant_id: int, tenant_slug: str)."""
        if tenant_id is None:
            tenant_id = self.tenant_slug

        if isinstance(tenant_id, int):
            if tenant_id not in ID_TO_SLUG:
                self._refresh_tenant_map()
            slug = ID_TO_SLUG.get(tenant_id, str(tenant_id))
            return tenant_id, slug
        elif isinstance(tenant_id, str):
            clean = tenant_id.strip()
            if clean.isdigit():
                tid = int(clean)
                if tid not in ID_TO_SLUG:
                    self._refresh_tenant_map()
                slug = ID_TO_SLUG.get(tid, clean)
                return tid, slug
            else:
                slug = clean.lower()
                if slug not in SLUG_TO_ID:
                    self._refresh_tenant_map()
                tid = SLUG_TO_ID.get(slug, 0)
                return tid, slug
        else:
            raise TypeError(f"Invalid tenant_id type: {type(tenant_id)}")

    def _normalize_row(self, row_dict: Dict[str, Any], tenant_id: int) -> Dict[str, Any]:
        """Normalizes PostgreSQL row dictionary matching canonical schema."""
        d = dict(row_dict)
        leg_id = d.get("legacy_id")
        if leg_id is not None:
            d["id"] = leg_id
            d["voter_id"] = leg_id
        else:
            vid = d.get("voter_id")
            d["id"] = vid
            d["voter_id"] = vid

        d["tenant_id"] = tenant_id

        # Normalize boolean columns
        for b in ["is_voted", "is_dispatched", "is_outside", "is_family_head", "is_on_whatsapp", "is_duplicate"]:
            if b in d and d[b] is not None:
                d[b] = bool(d[b])

        # Format datetime objects to match SQLite string format
        for k, v in d.items():
            if isinstance(v, datetime.datetime):
                d[k] = v.strftime("%Y-%m-%d %H:%M:%S")

        # Sanitize orphan combining marks, double matras, dangling/split halants, misplaced nuktas, and watermarks
        for col in ("name_hi", "rel_name_hi", "relative_name_hi", "house_no"):
            if d.get(col):
                val = str(d[col])
                if any('\u0900' <= c <= '\u097f' for c in val):
                    cleaned = re.sub(r'(^|\s+)[\u0901-\u0903\u093a-\u094f\u0962\u0963]+', r'\1', val)
                    cleaned = re.sub(r'\u0947\u093e', '\u094b', cleaned)
                    cleaned = re.sub(r'([\u093e-\u094c])\1+', r'\1', cleaned)
                    cleaned = re.sub(r'([\u093e-\u094c])[\u093e-\u094c]+', r'\1', cleaned)
                    cleaned = re.sub(r'([^\u0915-\u0939])\u093c', r'\1', cleaned)
                    cleaned = re.sub(r'(?i)\s*(Photo\s*Available|Photo\s*Not\s*Available|PhotoAvailable|Available|Photo)+$', '', cleaned)
                    cleaned = re.sub(r'\u094d+$', '', cleaned)
                    cleaned = re.sub(r'\u094d\s+', '्', cleaned)
        # Also strip watermark leakage from English names
        for col in ("name_en", "rel_name_en", "relative_name_en"):
            if d.get(col):
                d[col] = re.sub(r'(?i)\s*(Photo\s*Available|Photo\s*Not\s*Available|PhotoAvailable|Available|Photo)+$', '', str(d[col])).strip()

        # Drop heavy internal full-text indexing column to keep network payloads compact
        d.pop("search_doc", None)

        return d

    def get_voter(self, tenant_id: Union[int, str], voter_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Fetch single voter strictly scoped by tenant_id."""
        if voter_id is None:
            v_id = int(tenant_id)
            t_id = self.tenant_slug
        else:
            v_id = int(voter_id)
            t_id = tenant_id

        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM voters
                    WHERE tenant_id = %(tenant_id)s
                      AND (voter_id = %(voter_id)s OR legacy_id = %(voter_id)s)
                    LIMIT 1
                """, {"tenant_id": tid, "voter_id": v_id})
                row = cur.fetchone()
                if not row:
                    return None
                return self._normalize_row(dict(row), tid)

    def get_voters_by_ids(
        self, tenant_id: Union[int, str], voter_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch multiple voters strictly scoped by tenant_id."""
        if voter_ids is None:
            v_ids = tenant_id if isinstance(tenant_id, list) else []
            t_id = self.tenant_slug
        else:
            v_ids = voter_ids
            t_id = tenant_id

        clean_ids = [int(x) for x in v_ids if str(x).isdigit()]
        if not clean_ids:
            return []

        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM voters
                    WHERE tenant_id = %(tenant_id)s
                      AND (voter_id = ANY(%(voter_ids)s) OR legacy_id = ANY(%(voter_ids)s))
                    ORDER BY COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC
                """, {"tenant_id": tid, "voter_ids": clean_ids})
                return [self._normalize_row(dict(r), tid) for r in cur.fetchall()]

    def get_voters_by_part(
        self, tenant_id: Union[int, str], part_no: Optional[int] = None, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves voters within a given polling part strictly scoped by tenant_id."""
        if part_no is None:
            p_no = int(tenant_id)
            t_id = self.tenant_slug
        else:
            p_no = int(part_no)
            t_id = tenant_id

        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM voters
                    WHERE tenant_id = %(tenant_id)s AND part_no = %(part_no)s
                    ORDER BY sl_no ASC
                    LIMIT %(limit)s OFFSET %(offset)s
                """, {"tenant_id": tid, "part_no": p_no, "limit": limit, "offset": offset})
                return [self._normalize_row(dict(r), tid) for r in cur.fetchall()]

    def get_family_voters(
        self, tenant_id: Union[int, str], family_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves all voters in a family cluster strictly scoped by tenant_id."""
        if family_id is None:
            fam_id = str(tenant_id).strip()
            t_id = self.tenant_slug
        else:
            fam_id = str(family_id).strip()
            t_id = tenant_id

        if not fam_id:
            return []

        tid, t_slug = self._resolve_tenant(t_id)
        order_sql = "is_family_head DESC, (gender = 'M') DESC, age DESC, sl_no ASC" if mukhiya_prefers_male(t_slug) else "is_family_head DESC, age DESC, sl_no ASC"
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT * FROM voters
                    WHERE tenant_id = %(tenant_id)s AND family_id = %(family_id)s
                    ORDER BY {order_sql}
                """, {"tenant_id": tid, "family_id": fam_id})
                return [self._normalize_row(dict(r), tid) for r in cur.fetchall()]

    def mark_voted(
        self,
        tenant_id: Union[int, str],
        voter_id: Optional[int] = None,
        is_voted: Optional[bool] = None,
        user_id: int = 1,
        voter_name: str = "",
        device_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        **kwargs
    ) -> bool:
        """
        Updates voter turnout status and appends an immutable audit entry to voting_audit_log
        atomically in one transaction.
        Returns True on success, False otherwise.
        """
        if voter_id is None and "voter_id" in kwargs:
            voter_id = kwargs["voter_id"]
        if voter_id is None:
            voter_id = int(tenant_id)
            tenant_id = self.tenant_slug

        if is_voted is None:
            if "voted" in kwargs:
                is_voted = bool(kwargs["voted"])
            else:
                is_voted = True
        else:
            is_voted = bool(is_voted)

        tid, _ = self._resolve_tenant(tenant_id)
        with self.get_connection() as conn:
            with conn:  # Atomic transaction block
                with conn.cursor() as cur:
                    # 1. Lookup voter
                    cur.execute("""
                        SELECT voter_id, legacy_id, part_no, sl_no, name_en, name_hi
                        FROM voters
                        WHERE tenant_id = %(tenant_id)s
                          AND (voter_id = %(voter_id)s OR legacy_id = %(voter_id)s)
                        LIMIT 1
                    """, {"tenant_id": tid, "voter_id": voter_id})
                    row = cur.fetchone()
                    if not row:
                        return False

                    actual_voter_id = row[0]

                    # 2. Update voters table
                    cur.execute("""
                        UPDATE voters
                        SET is_voted = %(is_voted)s,
                            voted_at = (CASE WHEN %(is_voted)s THEN NOW() ELSE NULL END),
                            voted_marked_by = (CASE WHEN %(is_voted)s THEN %(user_id)s ELSE NULL END),
                            updated_at = NOW()
                        WHERE tenant_id = %(tenant_id)s AND voter_id = %(actual_voter_id)s
                    """, {
                        "tenant_id": tid,
                        "is_voted": is_voted,
                        "user_id": user_id,
                        "actual_voter_id": actual_voter_id,
                    })

                    # 3. Insert into voting_audit_log
                    action = "voted" if is_voted else "unvoted"
                    ip = ip_address.strip() if (ip_address and ip_address.strip()) else None

                    cur.execute("""
                        INSERT INTO voting_audit_log (
                            tenant_id, voter_id, action, performed_by,
                            device_id, client_timestamp, server_timestamp, ip_address
                        ) VALUES (
                            %(tenant_id)s, %(voter_id)s, %(action)s, %(performed_by)s,
                            %(device_id)s, NOW(), NOW(), %(ip_address)s
                        )
                    """, {
                        "tenant_id": tid,
                        "voter_id": actual_voter_id,
                        "action": action,
                        "performed_by": user_id,
                        "device_id": device_id,
                        "ip_address": ip,
                    })
                    return True

    def get_turnout_stats(
        self, tenant_id: Optional[Union[int, str]] = None, part_no: Optional[int] = None
    ) -> Dict[str, Any]:
        """Computes overall and booth/part-wise turnout metrics strictly scoped by tenant_id."""
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Overall stats. Struck-off electors (विलोपन) are not electors, so they
                # must not inflate the turnout denominator.
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN is_voted THEN 1 ELSE 0 END) as voted
                    FROM voters
                    WHERE tenant_id = %(tenant_id)s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                """, {"tenant_id": tid})
                row = cur.fetchone()
                total = row["total"] if row and row["total"] is not None else 0
                voted = row["voted"] if row and row["voted"] is not None else 0
                unvoted = total - voted
                pct = round((voted / total * 100), 2) if total > 0 else 0.0

                # Booth breakdown
                cur.execute("""
                    SELECT 
                        part_no,
                        COALESCE(booth_name, '') as booth_name,
                        COUNT(*) as total,
                        SUM(CASE WHEN is_voted THEN 1 ELSE 0 END) as voted
                    FROM voters
                    WHERE tenant_id = %(tenant_id)s AND part_no IS NOT NULL
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY part_no, booth_name
                    ORDER BY part_no ASC
                """, {"tenant_id": tid})
                booths = []
                for r in cur.fetchall():
                    b_tot = r["total"] if r["total"] is not None else 0
                    b_vod = r["voted"] if r["voted"] is not None else 0
                    b_unvod = b_tot - b_vod
                    b_pct = round((b_vod / b_tot * 100), 2) if b_tot > 0 else 0.0
                    booths.append({
                        "part_no": r["part_no"],
                        "booth_name": strip_fffd_artifacts(r["booth_name"]) or f"Booth {r['part_no']}",
                        "total_voters": b_tot,
                        "voted_count": b_vod,
                        "unvoted_count": b_unvod,
                        "turnout_percent": b_pct,
                        "total": b_tot,
                        "voted": b_vod,
                        "pending": b_unvod,
                        "turnout_pct": b_pct,
                    })

                booths_dict = {b["part_no"]: b for b in booths}
                return {
                    "success": True,
                    "total_voters": total,
                    "voted_count": voted,
                    "unvoted_count": unvoted,
                    "turnout_percent": pct,
                    "turnout_percentage": pct,
                    "booth_stats": booths,
                    "booths": booths_dict,
                }

    def get_gotv_voters(
        self,
        tenant_id: Optional[Union[int, str]] = None,
        min_category: Optional[int] = None,
        part_no: Optional[int] = None,
        category: Optional[str] = None,
        limit: int = 100,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """Returns favorable unvoted voters strictly scoped by tenant_id."""
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        limit_val = kwargs.get("limit", limit)

        conditions = ["tenant_id = %(tenant_id)s", "(is_voted = FALSE OR is_voted IS NULL)"]
        params: Dict[str, Any] = {"tenant_id": tid, "limit": limit_val}

        if part_no is not None:
            conditions.append("""(
                (booth_name ILIKE %(booth_pfx1)s OR booth_name ILIKE %(booth_pfx2)s)
                OR (part_no = %(part_no)s AND (booth_name IS NULL OR substring(booth_name from '^([0-9]+)\\\\s*[-:]') IS NULL))
            )""")
            params["part_no"] = part_no
            params["booth_pfx1"] = f"{part_no} - %"
            params["booth_pfx2"] = f"{part_no} %"

        cat_filter = category or kwargs.get("category")
        if cat_filter and str(cat_filter) != "all":
            conditions.append("voter_category = %(category)s")
            params["category"] = str(cat_filter)
        elif min_category is not None:
            conditions.append("voter_category >= %(min_category)s")
            params["min_category"] = int(min_category)

        where_clause = " AND ".join(conditions)
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT * FROM voters
                    WHERE {where_clause}
                    ORDER BY part_no ASC, COALESCE(NULLIF(ward_no, 0), 1) ASC, sl_no ASC NULLS LAST
                    LIMIT %(limit)s
                """, params)
                return [self._normalize_row(dict(r), tid) for r in cur.fetchall()]

    def get_parts(self, tenant_id: Optional[Union[int, str]] = None) -> List[Dict[str, Any]]:
        """Retrieves list of distinct polling parts with booth names, GP names, and voter counts."""
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT part_no, COALESCE(booth_name, '') as name, COUNT(*) as cnt,
                           COALESCE(MAX(NULLIF(TRIM(gram_panchayat), '')), '') as gp_name
                    FROM voters
                    WHERE tenant_id = %s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY part_no, booth_name
                    ORDER BY part_no ASC
                """, (tid,))
                parts = []
                for row in cur.fetchall():
                    clean_b_name = strip_fffd_artifacts(row[1]) or f"Booth {row[0]}"
                    gp = row[3] or ""
                    parts.append({
                        "part_no": row[0],
                        "name": clean_b_name,
                        "booth_name": clean_b_name,
                        "voter_count": row[2],
                        "total": row[2],
                        "gp_name": gp,
                        "gp_name_hi": derive_hi_gp_name(gp) if gp else ""
                    })
                return parts

    def get_booths_dict(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Dict[str, Any]]:
        """Returns a dictionary mapping part_no string to booth details."""
        parts = self.get_parts(tenant_id)
        return {str(p["part_no"]): p for p in parts}

    def get_wards(self, tenant_id: Optional[Union[int, str]] = None) -> List[Dict[str, Any]]:
        """
        Distinct wards with the booth each votes at and a live elector count.

        A panchayat elects one panch per ward, so the ward is the unit campaigners
        actually think in; several wards usually share one polling booth. Struck-off
        electors are excluded from the count so the dropdown agrees with the War Room.
        """
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ward_no,
                           part_no,
                           COALESCE(booth_name, '') AS booth_name,
                           COUNT(*) AS cnt
                    FROM voters
                    WHERE tenant_id = %s
                      AND ward_no IS NOT NULL AND ward_no > 0
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY ward_no, part_no, booth_name
                    ORDER BY ward_no ASC, part_no ASC
                """, (tid,))
                rows = cur.fetchall()
                if not rows:
                    return []
                wards_map: Dict[Any, Dict[str, Any]] = {}
                for row in rows:
                    w_no = row[0]
                    p_no = row[1]
                    raw_b_name = row[2] or f"Booth {p_no}"
                    b_name = strip_fffd_artifacts(raw_b_name) or f"Booth {p_no}"
                    cnt = row[3]
                    m = re.match(r'^(\d+)\s*[-:]', str(b_name))
                    b_no = m.group(1) if m else str(p_no)
                    if w_no not in wards_map:
                        wards_map[w_no] = {
                            "ward_no": w_no,
                            "part_no": p_no,
                            "booth_no": b_no,
                            "booth_name": b_name,
                            "voter_count": 0,
                            "total": 0,
                            "booths": []
                        }
                    existing_booth = next((b for b in wards_map[w_no]["booths"] if str(b["booth_no"]) == str(b_no)), None)
                    if existing_booth:
                        existing_booth["voter_count"] += cnt
                    else:
                        wards_map[w_no]["booths"].append({
                            "part_no": p_no,
                            "booth_no": b_no,
                            "booth_name": b_name,
                            "voter_count": cnt
                        })
                    wards_map[w_no]["voter_count"] += cnt
                    wards_map[w_no]["total"] += cnt

                wards_list = list(wards_map.values())
                for w in wards_list:
                    distinct_booth_nos = list(dict.fromkeys(str(b["booth_no"]) for b in w["booths"]))
                    w["booth_no"] = ", ".join(distinct_booth_nos)
                    if len(w["booths"]) > 1:
                        w["booth_name"] = f"{len(w['booths'])} मतदान केंद्र (बूथ {w['booth_no']})"
                    elif w["booths"]:
                        w["booth_name"] = w["booths"][0]["booth_name"]
                        w["booth_no"] = str(w["booths"][0]["booth_no"])
                        w["part_no"] = w["booths"][0]["part_no"]
                return wards_list

    def get_wards_dict(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Dict[str, Any]]:
        """Returns a dictionary mapping ward_no string to ward details."""
        return {str(w["ward_no"]): w for w in self.get_wards(tenant_id)}

    def get_booths_with_wards(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Dict[str, Any]]:
        """
        Returns mapping of physical booths with their constituent wards:
        {
            "79": {
                "part_no": 79,
                "booth_no": "79",
                "booth_name": "79 - राजकीय बालिका विद्यालय सराना कमरा 1",
                "parts": [1, 2],
                "parts_str": "1,2",
                "gp_name": "Sarana",
                "gp_name_hi": "सराना",
                "wards": [
                    {"ward_no": 1, "part_no": 1, "voter_count": 327},
                    {"ward_no": 2, "part_no": 2, "voter_count": 351}
                ],
                "total_voters": 678
            }
        }
        """
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT part_no,
                           COALESCE(booth_name, '') AS booth_name,
                           ward_no,
                           COUNT(*) AS cnt,
                           COALESCE(NULLIF(TRIM(gram_panchayat), ''), 'General') AS gp_name
                    FROM voters
                    WHERE tenant_id = %s
                      AND ward_no IS NOT NULL AND ward_no > 0
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY part_no, booth_name, ward_no, gram_panchayat
                    ORDER BY part_no ASC, ward_no ASC
                """, (tid,))
                booths_map: Dict[str, Dict[str, Any]] = {}
                for row in cur.fetchall():
                    p_no = str(row[0])
                    raw_b_name = row[1] or f"Booth {p_no}"
                    b_name = strip_fffd_artifacts(raw_b_name) or f"Booth {p_no}"
                    w_no = row[2]
                    cnt = row[3]
                    gp_name = row[4] if len(row) > 4 else 'General'
                    m = re.match(r'^(\d+)\s*[-:]', b_name)
                    canonical_booth_no = m.group(1) if m else p_no
                    if canonical_booth_no not in booths_map:
                        booths_map[canonical_booth_no] = {
                            "part_no": int(canonical_booth_no) if canonical_booth_no.isdigit() else row[0],
                            "booth_no": canonical_booth_no,
                            "booth_name": b_name,
                            "gp_name": gp_name if gp_name != "General" else "",
                            "gp_name_hi": derive_hi_gp_name(gp_name) if gp_name != "General" else "",
                            "parts": [],
                            "wards": [],
                            "total_voters": 0
                        }
                    p_int = int(p_no) if p_no.isdigit() else row[0]
                    if p_int not in booths_map[canonical_booth_no]["parts"]:
                        booths_map[canonical_booth_no]["parts"].append(p_int)

                    target_ward = next((w for w in booths_map[canonical_booth_no]["wards"] if str(w["ward_no"]) == str(w_no)), None)
                    if target_ward:
                        target_ward["voter_count"] += cnt
                        if p_int not in target_ward.get("parts", []):
                            target_ward.setdefault("parts", []).append(p_int)
                    else:
                        booths_map[canonical_booth_no]["wards"].append({
                            "ward_no": w_no,
                            "part_no": p_int,
                            "parts": [p_int],
                            "voter_count": cnt,
                            "gp_name": gp_name if gp_name != "General" else ""
                        })
                    booths_map[canonical_booth_no]["total_voters"] += cnt
                for booth in booths_map.values():
                    booth["wards"].sort(key=lambda x: int(x["ward_no"]) if str(x["ward_no"]).isdigit() else str(x["ward_no"]))
                    booth["parts"].sort()
                    booth["parts_str"] = ",".join(str(p) for p in booth["parts"])
                return booths_map

    def get_panchayat_hierarchy(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        """
        Builds a structured hierarchy of Gram Panchayats and their constituent wards/booths.
        Used for multi-GP elections (e.g. Panchayat Samiti Wards, Zila Parishad) to prevent
        collapsing Ward 1 of GP A and Ward 1 of GP B. For single-GP elections (Sarpanch, Gram
        Panchayat, Ward Panch), guarantees is_multi_gp=False and attributes all electors
        cleanly to the primary Gram Panchayat without artificial 'General' buckets.
        """
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)

        with self.get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Inspect Tenant Metadata & Election Type
                cur.execute("SELECT election_type, campaign_slug, config FROM tenants WHERE tenant_id = %s;", (tid,))
                t_info = cur.fetchone()
                el_type = (t_info[0] or '').upper() if t_info else ''
                t_slug = (t_info[1] or '').lower() if t_info else ''
                t_cfg = t_info[2] if t_info and len(t_info) > 2 and isinstance(t_info[2], dict) else {}

                is_expected_multi = (
                    el_type in ('ZILA_PARISHAD', 'ZP', 'PANCHAYAT_SAMITI', 'PS', 'ZILA_PARISHAD_WARD', 'PANCHAYAT_SAMITI_WARD')
                    or any(k in t_slug for k in ['zp', 'panchayat_samiti', 'ps_'])
                )
                is_single_gp = (
                    el_type in ('PANCHAYAT', 'GRAM_PANCHAYAT', 'SARPANCH', 'PANCHAYAT_SARPANCH', 'WARD_PANCH', 'MUNICIPAL', 'NAGAR_PALIKA', 'NAGAR_PARISHAD', 'NAGAR_NIGAM', 'PARSHAD', 'WARD')
                    or t_slug.endswith('_sarpanch')
                    or 'sarpanch' in t_slug
                )

                cfg_elec = t_cfg.get('election', {}) if isinstance(t_cfg, dict) else {}
                primary_gp = (
                    cfg_elec.get('gram_panchayat') 
                    or cfg_elec.get('ward_name_en', '').replace('Gram Panchayat', '').strip()
                    or cfg_elec.get('ward_name_hi', '').replace('ग्राम पंचायत', '').strip()
                    or ''
                )
                primary_gp_hi = cfg_elec.get('ward_name_hi', '').strip()

                cur.execute("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'voters' AND column_name = 'gram_panchayat'
                """)
                if not cur.fetchone():
                    return {
                        "is_multi_gp": False,
                        "has_multiple_samitis": False,
                        "panchayats": [],
                        "total_voters": 0,
                    }
                cur.execute("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'voters' AND column_name = 'panchayat_samiti'
                """)
                has_ps_col = bool(cur.fetchone())

                # If primary_gp not found in config, find prominent GP from voters table
                if not primary_gp:
                    cur.execute("""
                        SELECT TRIM(gram_panchayat) 
                        FROM voters 
                        WHERE tenant_id = %s 
                          AND gram_panchayat IS NOT NULL 
                          AND TRIM(gram_panchayat) <> '' 
                          AND LOWER(TRIM(gram_panchayat)) <> 'general'
                        GROUP BY TRIM(gram_panchayat)
                        ORDER BY COUNT(*) DESC 
                        LIMIT 1;
                    """, (tid,))
                    top_gp_row = cur.fetchone()
                    if top_gp_row and top_gp_row[0]:
                        primary_gp = top_gp_row[0]
                if not primary_gp:
                    primary_gp = 'General'

                if is_single_gp:
                    # In a single Gram Panchayat campaign, all electors belong to the tenant's GP
                    cur.execute("""
                        SELECT 
                            %s AS gp_name,
                            ward_no,
                            COUNT(*) AS cnt,
                            ARRAY_AGG(DISTINCT part_no) AS parts,
                            ARRAY_AGG(DISTINCT COALESCE(booth_name, '')) AS booth_names,
                            MAX(COALESCE(NULLIF(TRIM(panchayat_samiti), ''), '')) AS ps_name
                        FROM voters
                        WHERE tenant_id = %s 
                          AND ward_no IS NOT NULL AND ward_no > 0 
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                        GROUP BY 1, 2
                        ORDER BY 1 ASC, 2 ASC
                    """, (primary_gp, tid))
                elif has_ps_col:
                    cur.execute("""
                        SELECT 
                            COALESCE(NULLIF(TRIM(gram_panchayat), ''), %s) AS gp_name,
                            ward_no,
                            COUNT(*) AS cnt,
                            ARRAY_AGG(DISTINCT part_no) AS parts,
                            ARRAY_AGG(DISTINCT COALESCE(booth_name, '')) AS booth_names,
                            MAX(COALESCE(NULLIF(TRIM(panchayat_samiti), ''), '')) AS ps_name
                        FROM voters
                        WHERE tenant_id = %s 
                          AND ward_no IS NOT NULL AND ward_no > 0 
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                        GROUP BY 1, 2
                        ORDER BY 1 ASC, 2 ASC
                    """, (primary_gp, tid))
                else:
                    cur.execute("""
                        SELECT 
                            COALESCE(NULLIF(TRIM(gram_panchayat), ''), %s) AS gp_name,
                            ward_no,
                            COUNT(*) AS cnt,
                            ARRAY_AGG(DISTINCT part_no) AS parts,
                            ARRAY_AGG(DISTINCT COALESCE(booth_name, '')) AS booth_names,
                            '' AS ps_name
                        FROM voters
                        WHERE tenant_id = %s 
                          AND ward_no IS NOT NULL AND ward_no > 0 
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                        GROUP BY 1, 2
                        ORDER BY 1 ASC, 2 ASC
                    """, (primary_gp, tid))
                rows = cur.fetchall()
                if not rows:
                    return {
                        "is_multi_gp": False,
                        "has_multiple_samitis": False,
                        "panchayats": [],
                        "total_voters": 0,
                    }

                # Adapt legacy/test mock tuple format: (part_no, booth_name, ward_no, gp_name, cnt)
                if rows and len(rows[0]) >= 5 and isinstance(rows[0][4], (int, float)):
                    standardized = []
                    for r in rows:
                        standardized.append((r[3], r[2], r[4], [r[0]], [r[1]], r[5] if len(r) > 5 else ''))
                    rows = standardized
                elif rows and len(rows[0]) == 5:
                    standardized = []
                    for r in rows:
                        standardized.append((r[0], r[1], r[2], r[3], r[4], ''))
                    rows = standardized

                # Helper to derive clean Hindi/English GP name
                def _derive_hi_name(raw_gp: str, booth_names: List[str]) -> str:
                    if not raw_gp or raw_gp.strip() == 'General':
                        return 'General'
                    cleaned_key = raw_gp.strip().lower()
                    if cleaned_key in KNOWN_GP_NAMES:
                        return KNOWN_GP_NAMES[cleaned_key]
                    for k, v in KNOWN_GP_NAMES.items():
                        if k == cleaned_key or f"({k})" in cleaned_key:
                            return v
                    if any('\u0900' <= c <= '\u097f' for c in raw_gp):
                        return raw_gp.strip()
                    if primary_gp_hi and any('\u0900' <= c <= '\u097f' for c in primary_gp_hi) and (cleaned_key in primary_gp_hi.lower() or primary_gp.lower() == cleaned_key):
                        return primary_gp_hi

                    forbidden_tokens = {'कमरा', 'कक्ष', 'हॉल', 'होल', 'नंबर', 'भाग', 'बूथ', 'वार्ड', 'न.'}
                    school_prefixes = [
                        r'^\d+\s*[-:]\s*',
                        r'^राजकीय\s+.*विद्यालय\s+',
                        r'^रा[०\.]?\s*.*वि[०\.]?\s*',
                        r'^ग्राम\s+पंचायत\s+भवन\s+',
                        r'^पंचायत\s+भवन\s+',
                        r'^सामुदायिक\s+भवन\s+',
                        r'^अटल\s+सेवा\s+केंद्र\s+',
                        r'^विद्यालय\s+',
                        r'^स्कूल\s+',
                    ]

                    for b_name in booth_names:
                        bn = str(b_name).replace('\ufffd', '').strip()
                        # Pattern 1: Room/section info is in the middle: e.g. '... विद्यालय कमरा नं 1 बड़सम'
                        m_mid = re.search(r'(?:कमरा|कक्ष|हॉल|होल|भवन)[^ऀ-ॿ\w]*.*?(?:\d+|दांया|बांया|दायां|बायां|दाया|बाया|उत्तरी|दक्षिणी|पूर्वी|पश्चिमी)[^ऀ-ॿ\w]*(.+)$', bn, flags=re.IGNORECASE)
                        if m_mid and len(m_mid.group(1).strip()) >= 2:
                            candidate = m_mid.group(1).strip()
                            candidate = re.sub(r'^(?:में|स्थित|का|की|के)\s+', '', candidate).strip()
                            if any('\u0900' <= c <= '\u097f' for c in candidate) and not any(tok in candidate for tok in forbidden_tokens):
                                return f"{candidate} ({raw_gp.strip()})" if raw_gp and raw_gp.isascii() else candidate

                        # Pattern 2: Room/section info is at the end: e.g. '... विद्यालय सराना कमरा 1'
                        bn_clean = re.sub(r'(?:[,\s]+)?(?:कमरा|कक्ष|हॉल|होल|भाग\s*\d+|दांया|बांया|दायां|बायां|दाया|बाया|उत्तरी|दक्षिणी|पूर्वी|पश्चिमी|नवीन\s*भवन|पुराना\s*भवन|भवन).*$', '', bn, flags=re.IGNORECASE).strip()
                        for pfx in school_prefixes:
                            bn_clean = re.sub(pfx, '', bn_clean, flags=re.IGNORECASE).strip()
                        bn_clean = re.sub(r'(?:[,\s]+)?(?:कमरा|कक्ष|हॉल|होल|नवीन|पुराना|भवन|न[\.०]|नं[\.०]|नंबर|\d+).*$', '', bn_clean).strip()

                        if not bn_clean or len(bn_clean) < 2:
                            continue
                        if any(tok in bn_clean for tok in forbidden_tokens):
                            continue
                        if any('\u0900' <= c <= '\u097f' for c in bn_clean):
                            return f"{bn_clean} ({raw_gp.strip()})" if raw_gp and raw_gp.isascii() else bn_clean

                    return raw_gp.strip()

                distinct_gps_in_rows = {r[0] for r in rows if r[0] != 'General'}
                if is_expected_multi and not distinct_gps_in_rows:
                    # 1. On-the-fly heal from voters_master if available
                    try:
                        cur.execute("""
                            UPDATE voters v
                            SET gram_panchayat = m.gram_panchayat
                            FROM voters_master m
                            WHERE v.epic_no = m.epic_no
                              AND length(v.epic_no) > 5
                              AND m.gram_panchayat IS NOT NULL
                              AND v.tenant_id = %s
                              AND (v.gram_panchayat IS NULL OR trim(v.gram_panchayat) = '');
                        """, (tid,))
                        conn.commit()
                        cur.execute("""
                            SELECT 
                                COALESCE(NULLIF(TRIM(gram_panchayat), ''), 'General') AS gp_name,
                                ward_no,
                                COUNT(*) AS cnt,
                                ARRAY_AGG(DISTINCT part_no) AS parts,
                                ARRAY_AGG(DISTINCT COALESCE(booth_name, '')) AS booth_names
                            FROM voters
                            WHERE tenant_id = %s 
                              AND ward_no IS NOT NULL AND ward_no > 0 
                              AND LOWER(COALESCE(status, '')) <> 'deleted'
                            GROUP BY 1, 2
                            ORDER BY 1 ASC, 2 ASC
                        """, (tid,))
                        rows = cur.fetchall()
                        distinct_gps_in_rows = {r[0] for r in rows if r[0] != 'General'}
                    except Exception:
                        pass

                    # 2. Resilient fallback: derive location from booth names so UI NEVER collapses
                    if not distinct_gps_in_rows:
                        reconstructed = []
                        for r in rows:
                            derived_gp = _derive_hi_name('', r[4] or [])
                            if not derived_gp or derived_gp == 'General':
                                derived_gp = f"Booth {r[3][0] if r[3] else r[1]}"
                            reconstructed.append((derived_gp, r[1], r[2], r[3], r[4]))
                        rows = reconstructed

                # Group by gram_panchayat
                gp_groups: Dict[str, List[Any]] = {}
                for row in rows:
                    gp_key = row[0]
                    if gp_key not in gp_groups:
                        gp_groups[gp_key] = []
                    gp_groups[gp_key].append(row)

                panchayats = []
                grand_total = 0

                for gp_name, gp_rows in gp_groups.items():
                    all_booth_names = []
                    gp_ps_name = ""
                    for r in gp_rows:
                        if r[4]:
                            all_booth_names.extend(r[4])
                        if len(r) > 5 and r[5] and not gp_ps_name:
                            gp_ps_name = str(r[5]).strip()
                    gp_hi = _derive_hi_name(gp_name, all_booth_names) if gp_name != "General" else "General"

                    gp_parts_set = set()
                    gp_voter_count = 0
                    wards_list = []

                    for row in gp_rows:
                        w_no = row[1]
                        cnt = row[2]
                        row_parts = [p for p in row[3] if p is not None]
                        gp_parts_set.update(row_parts)
                        gp_voter_count += cnt

                        # Find primary booth number from booth names if available
                        b_names = row[4] or []
                        b_no = str(row_parts[0]) if row_parts else str(w_no)
                        for bn in b_names:
                            m = re.match(r'^(\d+)\s*[-:]', str(bn).replace('\ufffd', ''))
                            if m:
                                b_no = m.group(1)
                                break

                        display_name = f"वार्ड {w_no}"
                        filter_val = f"part:{row_parts[0]}_ward:{w_no}" if row_parts else (f"gp:{gp_name}_ward:{w_no}" if gp_name != "General" else f"ward:{w_no}")

                        wards_list.append({
                            "ward_no": w_no,
                            "part_no": row_parts[0] if row_parts else w_no,
                            "parts": sorted(row_parts),
                            "booth_no": b_no,
                            "voter_count": cnt,
                            "filter_value": filter_val,
                            "display_name": display_name,
                        })

                    wards_list.sort(key=lambda x: int(x["ward_no"]) if str(x["ward_no"]).isdigit() else str(x["ward_no"]))
                    grand_total += gp_voter_count
                    sorted_gp_parts = sorted(list(gp_parts_set))

                    gp_filter = f"parts:{','.join(str(p) for p in sorted_gp_parts)}" if sorted_gp_parts else (f"gp:{gp_name}" if gp_name != "General" else "")

                    panchayats.append({
                        "gp_name": gp_name,
                        "gp_name_hi": gp_hi,
                        "panchayat_samiti": gp_ps_name,
                        "total_voters": gp_voter_count,
                        "parts": sorted_gp_parts,
                        "filter_value": gp_filter,
                        "wards": wards_list,
                    })

                real_gps = [p for p in panchayats if p.get("gp_name") != "General"]
                if is_single_gp:
                    is_multi = False
                else:
                    is_multi = len(real_gps) > 1 or (is_expected_multi and len(real_gps) >= 1)

                distinct_ps = {p.get("panchayat_samiti") for p in panchayats if p.get("panchayat_samiti")}
                has_multiple_samitis = len(distinct_ps) > 1

                return {
                    "is_multi_gp": is_multi,
                    "has_multiple_samitis": has_multiple_samitis,
                    "panchayats": panchayats,
                    "total_voters": grand_total,
                }

    def get_villages(self, tenant_id: Optional[Union[int, str]] = None) -> List[str]:
        """Retrieves list of distinct villages / booth areas for the tenant."""
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT TRIM(booth_area)
                    FROM voters
                    WHERE tenant_id = %s
                      AND booth_area IS NOT NULL
                      AND LENGTH(TRIM(booth_area)) > 1
                      AND TRIM(booth_area) !~ '^[0-9]+$'
                    ORDER BY TRIM(booth_area) ASC
                """, (tid,))
                return [r[0] for r in cur.fetchall() if r[0]]

    def log_dispatch(
        self,
        tenant_id: Union[int, str],
        voter_id: Optional[int] = None,
        phone: Optional[str] = None,
        channel: str = "wa_me",
        message_id: Optional[str] = None,
        delivery_status: str = "SENT",
        payload: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        **kwargs
    ) -> int:
        """
        Records a slip dispatch entry in the dispatch audit ledger and marks voter is_dispatched.
        Returns generated dispatch ID.
        """
        if voter_id is None and "voter_id" in kwargs:
            voter_id = kwargs["voter_id"]
        if voter_id is None:
            voter_id = int(tenant_id)
            tenant_id = self.tenant_slug

        tid, _ = self._resolve_tenant(tenant_id)
        with self.get_connection() as conn:
            with conn:  # Atomic transaction block
                with conn.cursor() as cur:
                    # 1. Lookup voter
                    cur.execute("""
                        SELECT voter_id, legacy_id
                        FROM voters
                        WHERE tenant_id = %(tenant_id)s
                          AND (voter_id = %(voter_id)s OR legacy_id = %(voter_id)s)
                        LIMIT 1
                    """, {"tenant_id": tid, "voter_id": voter_id})
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"Voter ID {voter_id} not found for tenant {tenant_id}")

                    actual_voter_id = row[0]

                    # 2. Update voters table
                    cur.execute("""
                        UPDATE voters
                        SET is_dispatched = TRUE,
                            dispatched_at = NOW(),
                            contact_phone = COALESCE(NULLIF(%(phone)s, ''), contact_phone),
                            updated_at = NOW()
                        WHERE tenant_id = %(tenant_id)s AND voter_id = %(actual_voter_id)s
                    """, {
                        "tenant_id": tid,
                        "phone": phone,
                        "actual_voter_id": actual_voter_id,
                    })

                    # 3. Insert into dispatch_log
                    payload_json = json.dumps(payload) if payload else None
                    cur.execute("""
                        INSERT INTO dispatch_log (
                            tenant_id, voter_id, channel, template_name,
                            destination_phone, payload_summary, dispatched_by,
                            meta_message_id, delivery_status, dispatched_at
                        ) VALUES (
                            %(tenant_id)s, %(voter_id)s, %(channel)s, %(template_name)s,
                            %(destination_phone)s, %(payload_summary)s, %(dispatched_by)s,
                            %(meta_message_id)s, %(delivery_status)s, NOW()
                        ) RETURNING dispatch_id
                    """, {
                        "tenant_id": tid,
                        "voter_id": actual_voter_id,
                        "channel": channel,
                        "template_name": "voter_slip",
                        "destination_phone": phone or "",
                        "payload_summary": payload_json,
                        "dispatched_by": user_id,
                        "meta_message_id": message_id,
                        "delivery_status": delivery_status,
                    })
                    dispatch_id = cur.fetchone()[0]
                    return dispatch_id

    def log_audit_event(
        self,
        tenant_id: Union[int, str],
        user_id: Optional[int],
        username: Optional[str],
        action: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """
        Records a generic "who did what" entry in the audit_log ledger.
        Parameter-bound INSERT; details is serialized to JSON text and cast
        implicitly to the JSONB column, matching the existing payload_summary
        convention used by log_dispatch above.
        """
        tid, _ = self._resolve_tenant(tenant_id)
        details_json = json.dumps(details) if details else None
        with self.get_connection() as conn:
            with conn:  # Atomic transaction block
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO audit_log (
                            tenant_id, user_id, username, action,
                            entity_type, entity_id, details,
                            ip_address, user_agent
                        ) VALUES (
                            %(tenant_id)s, %(user_id)s, %(username)s, %(action)s,
                            %(entity_type)s, %(entity_id)s, %(details)s,
                            %(ip_address)s, %(user_agent)s
                        )
                    """, {
                        "tenant_id": tid,
                        "user_id": user_id,
                        "username": username,
                        "action": action,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "details": details_json,
                        "ip_address": ip_address,
                        "user_agent": user_agent,
                    })

    def search_voters(
        self, tenant_id: Union[int, str], query: Optional[str] = "", limit: int = 50, offset: int = 0, **kwargs
    ) -> List[Dict[str, Any]]:
        """Two-stage Indic search: GIN trigram candidate retrieval + SIMD rapidfuzz ranking."""
        if query is None:
            q_str = str(tenant_id).strip()
            t_id = self.tenant_slug
        else:
            q_str = query.strip()
            t_id = tenant_id

        tid, _ = self._resolve_tenant(t_id)
        name = kwargs.get("name")
        relative = kwargs.get("relative")
        part_no = kwargs.get("part_no")
        if part_no is not None:
            try:
                part_no = int(part_no)
            except (ValueError, TypeError):
                part_no = None

        ward_no = kwargs.get("ward_no")
        if ward_no is not None:
            try:
                ward_no = int(ward_no)
            except (ValueError, TypeError):
                ward_no = None

        gender = kwargs.get("gender")
        age_min = kwargs.get("age_min")
        if age_min is not None:
            try:
                age_min = int(age_min)
            except (ValueError, TypeError):
                age_min = None

        age_max = kwargs.get("age_max")
        if age_max is not None:
            try:
                age_max = int(age_max)
            except (ValueError, TypeError):
                age_max = None

        caste = kwargs.get("caste")
        category = kwargs.get("category")
        if category is not None:
            try:
                category = int(category)
            except (ValueError, TypeError):
                category = None

        part_nos = kwargs.get("part_nos")
        ward_nos = kwargs.get("ward_nos")
        villages = kwargs.get("villages")
        gp = kwargs.get("gp") or kwargs.get("gram_panchayat")
        sort = kwargs.get("sort") or kwargs.get("order_by")

        with self.get_connection() as conn:
            results = two_stage_search.search_voters(
                conn,
                tid,
                q_str,
                limit=limit + offset,
                candidate_limit=max(two_stage_search.STAGE1_CANDIDATE_LIMIT, limit + offset),
                part_no=part_no,
                ward_no=ward_no,
                gender=gender,
                age_min=age_min,
                age_max=age_max,
                caste=caste,
                category=category,
                name=name,
                relative=relative,
                part_nos=part_nos,
                ward_nos=ward_nos,
                villages=villages,
                gp=gp,
                gram_panchayat=gp,
                sort=sort,
            )
            sliced = results[offset:offset + limit]
            return [self._normalize_row(r, tid) for r in sliced]

    def count_voters(
        self, tenant_id: Union[int, str], query: Optional[str] = "", **kwargs
    ) -> int:
        """Returns the total number of voters matching the search/filter criteria."""
        if query is None:
            q_str = str(tenant_id).strip()
            t_id = self.tenant_slug
        else:
            q_str = query.strip()
            t_id = tenant_id

        tid, _ = self._resolve_tenant(t_id)
        name = kwargs.get("name")
        relative = kwargs.get("relative")
        part_no = kwargs.get("part_no")
        if part_no is not None:
            try:
                part_no = int(part_no)
            except (ValueError, TypeError):
                part_no = None

        ward_no = kwargs.get("ward_no")
        if ward_no is not None:
            try:
                ward_no = int(ward_no)
            except (ValueError, TypeError):
                ward_no = None

        gender = kwargs.get("gender")
        age_min = kwargs.get("age_min")
        age_max = kwargs.get("age_max")
        caste = kwargs.get("caste")
        category = kwargs.get("category")
        part_nos = kwargs.get("part_nos")
        ward_nos = kwargs.get("ward_nos")
        villages = kwargs.get("villages")
        gp = kwargs.get("gp") or kwargs.get("gram_panchayat")

        with self.get_connection() as conn:
            return two_stage_search.count_stage1_voters(
                conn,
                tid,
                q_str,
                part_no=part_no,
                ward_no=ward_no,
                gender=gender,
                age_min=age_min,
                age_max=age_max,
                caste=caste,
                category=category,
                name=name,
                relative=relative,
                part_nos=part_nos,
                ward_nos=ward_nos,
                villages=villages,
                gp=gp,
                gram_panchayat=gp,
            )

    def get_dispatch_history(
        self, tenant_id: Optional[Union[int, str]] = None, voter_id: Optional[int] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch recent dispatch logs, optionally filtered by voter_id."""
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if voter_id:
                    cur.execute("""
                        SELECT * FROM dispatch_log
                        WHERE tenant_id = %(tenant_id)s AND voter_id = %(voter_id)s
                        ORDER BY dispatched_at DESC
                        LIMIT %(limit)s
                    """, {"tenant_id": tid, "voter_id": voter_id, "limit": limit})
                else:
                    cur.execute("""
                        SELECT * FROM dispatch_log
                        WHERE tenant_id = %(tenant_id)s
                        ORDER BY dispatched_at DESC
                        LIMIT %(limit)s
                    """, {"tenant_id": tid, "limit": limit})
                return [dict(r) for r in cur.fetchall()]

    def get_row_counts(self, tenant_id: Optional[Union[int, str]] = None) -> Dict[str, int]:
        """Returns structured row counts for voters, dispatch_log, voting_audit_log, etc."""
        counts = {"voters": 0, "dispatch_history": 0, "voting_audit_log": 0, "voted": 0, "phones": 0}
        t_id = self.tenant_slug if tenant_id is None else tenant_id
        tid, _ = self._resolve_tenant(t_id)
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM voters WHERE tenant_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'", (tid,))
                    counts["voters"] = cur.fetchone()[0] or 0

                    cur.execute("SELECT COUNT(*) FROM voters WHERE tenant_id = %s AND is_voted = TRUE AND LOWER(COALESCE(status, '')) <> 'deleted'", (tid,))
                    counts["voted"] = cur.fetchone()[0] or 0

                    cur.execute(
                        "SELECT COUNT(*) FROM voters WHERE tenant_id = %s AND contact_phone IS NOT NULL AND TRIM(contact_phone) != '' AND LOWER(COALESCE(status, '')) <> 'deleted'",
                        (tid,),
                    )
                    counts["phones"] = cur.fetchone()[0] or 0

                    cur.execute("SELECT COUNT(*) FROM dispatch_log WHERE tenant_id = %s", (tid,))
                    counts["dispatch_history"] = cur.fetchone()[0] or 0

                    cur.execute("SELECT COUNT(*) FROM voting_audit_log WHERE tenant_id = %s", (tid,))
                    counts["voting_audit_log"] = cur.fetchone()[0] or 0
        except Exception:
            pass
        return counts
