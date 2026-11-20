"""
electionapp/photoparchi_codes.py
Access Code management and validation for PhotoParchi (smartbooth.in / /photoparchi).
Supports multi-worker Postgres connection pooling with sqlite fallback, code issuance,
scoping (District, Block, GP, Ward), usage limits, and toggle activation.
"""

import os
import re
import json
import random
import string
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("smartbooth.photoparchi_codes")


def _get_db_connection():
    """Returns a connection to PostgreSQL voter_platform or local DB."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(
            dbname=os.environ.get('PGDATABASE', 'voter_platform'),
            user=os.environ.get('PGUSER', 'postgres'),
            password=os.environ.get('PGPASSWORD', 'postgres'),
            host=os.environ.get('PGHOST', 'localhost'),
            port=int(os.environ.get('PGPORT', 5432)),
            connect_timeout=4
        )
        return conn
    except Exception as e:
        logger.warning("PostgreSQL connection failed in photoparchi_codes: %s", e)
        return None


def ensure_photoparchi_codes_schema() -> bool:
    """Ensures photoparchi_codes table and index exist with all required columns."""
    conn = _get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS photoparchi_codes (
                    id SERIAL PRIMARY KEY,
                    code VARCHAR(32) UNIQUE NOT NULL,
                    campaign_name VARCHAR(128) NOT NULL,
                    district VARCHAR(64),
                    block VARCHAR(64),
                    gram_panchayat VARCHAR(64),
                    ward_no VARCHAR(16),
                    max_uses INTEGER DEFAULT 500,
                    times_used INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_by VARCHAR(64) DEFAULT 'admin',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP WITH TIME ZONE,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_photoparchi_codes_code ON photoparchi_codes(UPPER(code));

                -- Schema migrations for single-campaign lock and format controls
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS campaign_type VARCHAR(32) DEFAULT 'PANCHAYAT';
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS allow_with_photo BOOLEAN DEFAULT TRUE;
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS allow_without_photo BOOLEAN DEFAULT TRUE;
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS is_single_campaign BOOLEAN DEFAULT TRUE;
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS locked_candidate_name VARCHAR(128);
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS locked_district VARCHAR(64);
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS locked_block VARCHAR(64);
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS locked_gp VARCHAR(64);
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS locked_gps TEXT;
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS locked_ward_no VARCHAR(16);
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS download_completed_at TIMESTAMP WITH TIME ZONE;

                -- Cashfree payment integration fields
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS price_amount DECIMAL(10,2) DEFAULT 0.00;
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS payment_status VARCHAR(32) DEFAULT 'FREE';
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS cashfree_order_id VARCHAR(64);
                ALTER TABLE photoparchi_codes ADD COLUMN IF NOT EXISTS paid_at TIMESTAMP WITH TIME ZONE;
            """)
            conn.commit()
            return True
    except Exception as e:
        logger.error("Failed to ensure photoparchi_codes schema: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def generate_code_string(prefix: str = "PP") -> str:
    """Generates an alphanumeric access code like PP-9X4K2."""
    chars = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # Omits 0, 1, I, O to prevent confusion
    rand_part = ''.join(random.choices(chars, k=5))
    return f"{prefix}-{rand_part}"


def create_access_code(
    campaign_name: str,
    campaign_type: str = "PANCHAYAT",
    allow_with_photo: bool = True,
    allow_without_photo: bool = True,
    is_single_campaign: bool = True,
    price_amount: float = 0.0,
    district: Optional[str] = None,
    block: Optional[str] = None,
    gram_panchayat: Optional[str] = None,
    ward_no: Optional[str] = None,
    max_uses: int = 1,
    expires_at: Optional[Any] = None,
    notes: Optional[str] = None,
    custom_code: Optional[str] = None,
    created_by: str = "admin"
) -> Dict[str, Any]:
    """Issues a new PhotoParchi access code with campaign type, format permissions, and pricing."""
    ensure_photoparchi_codes_schema()
    conn = _get_db_connection()
    if not conn:
        raise RuntimeError("Database connection unavailable")

    raw_code = (custom_code or generate_code_string()).strip().upper()
    raw_code = re.sub(r'[^A-Z0-9\-]', '', raw_code)
    if not raw_code:
        raw_code = generate_code_string()

    # If single campaign is enabled and max_uses was defaulted to 500, set to 1
    effective_max_uses = int(max_uses) if max_uses else (1 if is_single_campaign else 500)
    effective_price = round(float(price_amount or 0.0), 2)
    payment_status = "FREE" if effective_price <= 0 else "PENDING"

    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO photoparchi_codes (
                    code, campaign_name, campaign_type, allow_with_photo, allow_without_photo,
                    is_single_campaign, price_amount, payment_status, district, block, gram_panchayat,
                    ward_no, max_uses, expires_at, notes, created_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, code, campaign_name, campaign_type, allow_with_photo, allow_without_photo,
                          is_single_campaign, price_amount, payment_status, cashfree_order_id, paid_at,
                          district, block, gram_panchayat, ward_no, max_uses, times_used, is_active,
                          created_by, created_at, expires_at, notes, locked_candidate_name,
                          locked_district, locked_block, locked_gp, locked_gps,
                          locked_ward_no, download_completed_at;
            """, (
                raw_code,
                campaign_name.strip(),
                (campaign_type or "PANCHAYAT").strip().upper(),
                bool(allow_with_photo),
                bool(allow_without_photo),
                bool(is_single_campaign),
                effective_price,
                payment_status,
                district.strip().upper() if district else None,
                block.strip().upper() if block else None,
                gram_panchayat.strip() if gram_panchayat else None,
                str(ward_no).strip() if ward_no else None,
                effective_max_uses,
                expires_at,
                notes.strip() if notes else None,
                created_by
            ))
            row = cur.fetchone()
            conn.commit()
            return dict(row)
    except Exception as e:
        conn.rollback()
        logger.error("Error creating photoparchi code: %s", e)
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_access_code_by_code(code: str) -> Optional[Dict[str, Any]]:
    """Look up a code by exact uppercase string with all locking attributes and payment status."""
    if not code:
        return None
    ensure_photoparchi_codes_schema()
    code_clean = code.strip().upper()
    conn = _get_db_connection()
    if not conn:
        return None
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, code, campaign_name, campaign_type, allow_with_photo,
                       allow_without_photo, is_single_campaign, price_amount, payment_status,
                       cashfree_order_id, paid_at, district, block,
                       gram_panchayat, ward_no, max_uses, times_used, is_active,
                       created_by, created_at, expires_at, notes,
                       locked_candidate_name, locked_district, locked_block,
                       locked_gp, locked_gps, locked_ward_no, download_completed_at
                FROM photoparchi_codes
                WHERE UPPER(code) = %s;
            """, (code_clean,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("Error getting access code %s: %s", code, e)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def validate_access_code(code: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Validates code existence, activation, expiry, single-campaign usage, and limits.
    Returns (is_valid, message, code_dict).
    """
    if not code:
        return False, "एक्सेस कोड दर्ज करें (Please enter access code)", None

    code_info = get_access_code_by_code(code)
    if not code_info:
        return False, "अमान्य एक्सेस कोड (Invalid Access Code)", None

    payment_status = (code_info.get("payment_status") or "").upper()
    is_paid_campaign = (payment_status == "PAID")

    if not code_info.get("is_active"):
        if is_paid_campaign:
            # Paid campaigns must remain downloadable by the paying candidate
            pass
        elif code_info.get("download_completed_at") or (code_info.get("is_single_campaign") and (code_info.get("times_used") or 0) >= 1):
            return False, "यह एक्सेस कोड एकल अभियान हेतु मान्य था और इसका उपयोग पूर्ण हो चुका है (This access code has already been burned)", code_info
        else:
            return False, "यह एक्सेस कोड निष्क्रिय कर दिया गया है (This access code has been deactivated)", code_info

    # If code was already marked as downloaded for a single campaign (only burn unpaid/free codes!)
    if not is_paid_campaign and code_info.get("is_single_campaign") and code_info.get("download_completed_at"):
        return False, "इस एक्सेस कोड का उपयोग कर मतदाता पर्ची पहले ही डाउनलोड की जा चुकी है (Already downloaded)", code_info

    expires_at = code_info.get("expires_at")
    if expires_at:
        now = datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
        if now > expires_at:
            return False, "यह एक्सेस कोड समाप्त (Expired) हो चुका है", code_info

    max_uses = code_info.get("max_uses") or 500
    times_used = code_info.get("times_used") or 0
    if not is_paid_campaign and times_used >= max_uses:
        return False, f"इस कोड की उपयोग सीमा ({max_uses}) समाप्त हो चुकी है (Usage limit exceeded)", code_info

    return True, "सत्यापित (Access code verified)", code_info


def bind_code_to_campaign(
    code: str,
    candidate_name: str,
    district: str,
    block: Optional[str] = None,
    gp: Optional[str] = None,
    ward_no: Optional[str] = None,
    gps: Optional[List[str]] = None
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Permanently binds an access code to a specific candidate and geographic area.
    If already bound, verifies that the incoming request matches the locked area and candidate.
    Prevents using the same code for a different Gram Panchayat or another candidate.
    """
    code_info = get_access_code_by_code(code)
    if not code_info:
        return False, "कोड नहीं मिला (Code not found)", None

    # Multi-use / Multi-campaign code: do not lock to a single candidate or area
    if not code_info.get("is_single_campaign"):
        return True, "Multi-campaign code, binding not restricted", code_info

    c_name_clean = (candidate_name or "").strip()
    dist_clean = (district or "").strip().upper()
    block_clean = (block or "").strip().upper()
    gp_clean = (gp or "").strip()
    ward_clean = str(ward_no).strip() if ward_no else None
    gps_json = json.dumps(gps, ensure_ascii=False) if (gps and isinstance(gps, list)) else (json.dumps([gp_clean], ensure_ascii=False) if gp_clean else None)

    # Check if already locked to an area or candidate
    locked_dist = (code_info.get("locked_district") or "").strip().upper()
    locked_gp = (code_info.get("locked_gp") or "").strip()
    locked_cand = (code_info.get("locked_candidate_name") or "").strip()

    if locked_gp or locked_dist or locked_cand:
        # Verify candidate name match
        if locked_cand and locked_cand.lower() != c_name_clean.lower():
            return False, f"यह कोड केवल '{locked_cand}' प्रत्याशी के लिए लॉक है। अन्य नाम अनुमत नहीं है।", code_info

        # Verify district match
        if locked_dist and locked_dist != dist_clean:
            return False, f"यह कोड केवल जिला '{locked_dist}' के लिए लॉक है।", code_info

        # Verify GP match for single GP campaigns
        if locked_gp and gp_clean and locked_gp.lower() != gp_clean.lower():
            return False, f"यह कोड केवल ग्राम पंचायत '{locked_gp}' के लिए लॉक है। आप किसी अन्य ग्राम पंचायत की पर्ची नहीं बना सकते।", code_info

        return True, "Area and candidate verified", code_info

    # Not yet locked - bind it now!
    conn = _get_db_connection()
    if not conn:
        return False, "Database connection unavailable", None

    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE photoparchi_codes
                SET locked_candidate_name = %s,
                    locked_district = %s,
                    locked_block = %s,
                    locked_gp = %s,
                    locked_gps = %s,
                    locked_ward_no = %s
                WHERE UPPER(code) = %s AND (locked_gp IS NULL OR locked_gp = '')
                RETURNING id, code, locked_candidate_name, locked_district, locked_block, locked_gp;
            """, (
                c_name_clean,
                dist_clean,
                block_clean,
                gp_clean,
                gps_json,
                ward_clean,
                code.strip().upper()
            ))
            row = cur.fetchone()
            conn.commit()
            updated_info = get_access_code_by_code(code)
            return True, "अभियान एवं क्षेत्र सफलतापूर्वक कोड से लॉक कर दिया गया", updated_info
    except Exception as e:
        conn.rollback()
        logger.error("Error binding code %s to campaign: %s", code, e)
        return False, f"Lock error: {str(e)}", None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def finalize_code_download(code: str) -> bool:
    """
    Atomically marks the download as complete.
    For FREE/promotional codes, burns/deactivates the single-campaign code.
    For PAID campaigns, keeps code active so candidate can download both PDF slips and Excel list.
    """
    if not code:
        return False
    code_clean = code.strip().upper()
    conn = _get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE photoparchi_codes
                SET times_used = times_used + 1,
                    download_completed_at = CURRENT_TIMESTAMP,
                    is_active = CASE 
                        WHEN UPPER(payment_status) = 'PAID' THEN TRUE 
                        WHEN is_single_campaign THEN FALSE 
                        ELSE is_active 
                    END
                WHERE UPPER(code) = %s;
            """, (code_clean,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.error("Error finalizing download for code %s: %s", code, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def increment_code_usage(code: str) -> bool:
    """Atomically increments times_used by 1."""
    if not code:
        return False
    code_clean = code.strip().upper()
    conn = _get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE photoparchi_codes
                SET times_used = times_used + 1
                WHERE UPPER(code) = %s AND is_active = TRUE;
            """, (code_clean,))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.error("Error incrementing usage for code %s: %s", code, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def toggle_access_code(code_id: int, is_active: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """Toggles active state of a code."""
    conn = _get_db_connection()
    if not conn:
        return None
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if is_active is None:
                cur.execute("""
                    UPDATE photoparchi_codes
                    SET is_active = NOT is_active
                    WHERE id = %s
                    RETURNING id, code, is_active;
                """, (code_id,))
            else:
                cur.execute("""
                    UPDATE photoparchi_codes
                    SET is_active = %s
                    WHERE id = %s
                    RETURNING id, code, is_active;
                """, (is_active, code_id))
            row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None
    except Exception as e:
        logger.error("Error toggling code %s: %s", code_id, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def list_access_codes(limit: int = 200, offset: int = 0, search: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns list of issued access codes ordered by newest first."""
    ensure_photoparchi_codes_schema()
    conn = _get_db_connection()
    if not conn:
        return []
    try:
        import psycopg2.extras
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cols = """id, code, campaign_name, campaign_type, allow_with_photo,
                      allow_without_photo, is_single_campaign, price_amount, payment_status,
                      cashfree_order_id, paid_at, district, block,
                      gram_panchayat, ward_no, max_uses, times_used, is_active,
                      created_by, created_at, expires_at, notes,
                      locked_candidate_name, locked_district, locked_block,
                      locked_gp, locked_gps, locked_ward_no, download_completed_at"""
            if search:
                s_pat = f"%{search.strip()}%"
                cur.execute(f"""
                    SELECT {cols}
                    FROM photoparchi_codes
                    WHERE code ILIKE %s OR campaign_name ILIKE %s OR gram_panchayat ILIKE %s OR locked_gp ILIKE %s OR locked_candidate_name ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s;
                """, (s_pat, s_pat, s_pat, s_pat, s_pat, limit, offset))
            else:
                cur.execute(f"""
                    SELECT {cols}
                    FROM photoparchi_codes
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s;
                """, (limit, offset))
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get("created_at"):
                    r["created_at_iso"] = r["created_at"].isoformat()
                if r.get("expires_at"):
                    r["expires_at_iso"] = r["expires_at"].isoformat()
                if r.get("download_completed_at"):
                    r["download_completed_at_iso"] = r["download_completed_at"].isoformat()
                if r.get("paid_at"):
                    r["paid_at_iso"] = r["paid_at"].isoformat()
            return rows
    except Exception as e:
        logger.error("Error listing access codes: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_code_payment(code: str, payment_status: str, cashfree_order_id: Optional[str] = None) -> bool:
    """
    Updates the payment status of an access code.
    If payment_status == 'PAID', sets paid_at = CURRENT_TIMESTAMP.
    """
    if not code:
        return False
    code_clean = code.strip().upper()
    status_clean = payment_status.strip().upper()
    conn = _get_db_connection()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            if status_clean == "PAID":
                cur.execute("""
                    UPDATE photoparchi_codes
                    SET payment_status = 'PAID',
                        cashfree_order_id = COALESCE(%s, cashfree_order_id),
                        paid_at = CURRENT_TIMESTAMP,
                        is_active = TRUE
                    WHERE UPPER(code) = %s;
                """, (cashfree_order_id, code_clean))
            else:
                cur.execute("""
                    UPDATE photoparchi_codes
                    SET payment_status = %s,
                        cashfree_order_id = COALESCE(%s, cashfree_order_id)
                    WHERE UPPER(code) = %s;
                """, (status_clean, cashfree_order_id, code_clean))
            conn.commit()
            return cur.rowcount > 0
    except Exception as e:
        logger.error("Error updating payment for code %s: %s", code, e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

