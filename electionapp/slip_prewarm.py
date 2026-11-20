"""
slip_prewarm.py
Background Slip Pre-generation & Meta Media Pre-upload Pipeline.

Pre-warms voter slip rendering and Meta Cloud API media upload as soon as an
operator opens the slip modal, hiding the ~1.3s rendering + upload latency
behind the natural 3-6s human pause while typing or verifying the recipient phone.

Architecture & Safety:
- Shared inter-worker cache backed by SQLite WAL in /dev/shm (or tempfile)
- Concurrency bounded via Semaphore to prevent Chrome daemon exhaustion
- Deterministic cache keys based on tenant, slip_type, sorted voter_ids, branding mtime
- Automatic TTL expiration (10 minutes)
- Non-blocking async API endpoint POST /api/prewarm_slip
- Graceful in-flight sync: if user clicks send while prewarm is mid-flight,
  the send handler waits up to 1.2s for completion before falling back.
"""

import os
import time
import json
import sqlite3
import hashlib
import logging
import threading
import tempfile
from typing import List, Dict, Optional, Tuple, Any

logger = logging.getLogger("smartbooth.slip_prewarm")

# Shared SQLite DB path across all Gunicorn workers
_SHM_DIR = "/dev/shm" if os.path.exists("/dev/shm") and os.path.isdir("/dev/shm") else tempfile.gettempdir()
PREWARM_DB_PATH = os.path.join(_SHM_DIR, "smartbooth_slip_prewarm.sqlite3")

# Bound concurrent prewarms across this process to never overload Chrome daemons
_MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_PREWARMS", "2"))
_PREWARM_SEMAPHORE = threading.BoundedSemaphore(_MAX_CONCURRENT)

# TTL for pre-warmed cards (10 minutes)
PREWARM_TTL_SECONDS = 600


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(PREWARM_DB_PATH, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn


def init_prewarm_db():
    """Initializes the shared SQLite table if not already created."""
    for attempt in range(5):
        try:
            with _get_db() as conn:
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                except Exception:
                    pass
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prewarm_cache (
                        cache_key TEXT PRIMARY KEY,
                        tenant TEXT,
                        slip_type TEXT,
                        voter_ids TEXT,
                        image_paths TEXT,
                        media_ids TEXT,
                        status TEXT,
                        created_at REAL,
                        expires_at REAL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_pw_expires ON prewarm_cache(expires_at);")
            break
        except sqlite3.OperationalError as e:
            if attempt == 4:
                logger.warning(f"Could not initialize prewarm db after 5 attempts: {e}")
            time.sleep(0.1)
        except Exception as e:
            logger.warning(f"Could not initialize prewarm db: {e}")
            break


# Initialize on module load
try:
    init_prewarm_db()
except Exception:
    pass


def resolve_slip_type(target_voters: list, requested_slip_type: str = 'Single') -> str:
    """Consistently resolves Single, Family, or Combined based on voter count and household."""
    if not target_voters or len(target_voters) <= 1:
        return 'Single'
    houses = set(str(v.get('house_no', '')).strip() for v in target_voters if str(v.get('house_no', '')).strip() not in ('', '0', '-'))
    parts = set(str(v.get('part_no', '')).strip() for v in target_voters if str(v.get('part_no', '')).strip())
    if len(houses) <= 1 and len(parts) <= 1 and requested_slip_type != 'Combined':
        return 'Family'
    return 'Combined'


def build_cache_key(tenant: str, slip_type: str, voter_ids: List[int]) -> str:
    """Builds a deterministic cache key for a tenant + slip_type + sorted voter_ids combination."""
    clean_tenant = str(tenant or 'ward82').strip().lower()
    clean_slip_type = str(slip_type or 'Single').strip()
    clean_ids = sorted([int(x) for x in voter_ids if str(x).isdigit()])
    ids_str = ",".join(str(x) for x in clean_ids)

    # Check tenant custom bg mtime for dynamic invalidation
    bg_mtime = 0
    try:
        from electionapp.tenant_branding import get_tenant_branding, get_tenant_custom_background
        branding = get_tenant_branding(clean_tenant)
        bg = branding.get('custom_bg') or get_tenant_custom_background(clean_tenant)
        if bg and os.path.exists(bg):
            bg_mtime = int(os.path.getmtime(bg))
    except Exception:
        pass

    raw = f"{clean_tenant}:{clean_slip_type}:{ids_str}:{bg_mtime}"
    h = hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]
    return f"pw_{clean_tenant}_{clean_slip_type}_{h}"


def get_prewarmed_slip(
    tenant: str,
    slip_type: str,
    voter_ids: List[int],
    wait_timeout: float = 1.2
) -> Tuple[Optional[List[str]], Optional[Dict[str, Dict[str, str]]]]:
    """
    Retrieves pre-warmed slip images and Meta media IDs if available.
    If prewarm is currently IN_FLIGHT, waits up to wait_timeout seconds.
    Returns (image_paths_list, media_ids_dict_by_img_path) or (None, None).
    """
    clean_ids = sorted([int(x) for x in voter_ids if str(x).isdigit()])
    if not clean_ids:
        return None, None

    clean_tenant = str(tenant or 'ward82').strip().lower()
    cache_key = build_cache_key(clean_tenant, slip_type, clean_ids)
    vids_json = json.dumps(clean_ids)
    start_wait = time.time()

    while True:
        try:
            with _get_db() as conn:
                cur = conn.execute(
                    "SELECT status, image_paths, media_ids, created_at, expires_at FROM prewarm_cache WHERE cache_key = ?",
                    (cache_key,)
                )
                row = cur.fetchone()
                if not row:
                    cur = conn.execute(
                        "SELECT status, image_paths, media_ids, created_at, expires_at FROM prewarm_cache WHERE voter_ids = ? AND tenant = ? ORDER BY created_at DESC LIMIT 1",
                        (vids_json, clean_tenant)
                    )
                    row = cur.fetchone()
        except Exception as e:
            logger.warning(f"Error reading prewarm cache: {e}")
            return None, None

        if not row:
            return None, None

        status, img_paths_json, media_ids_json, created_at, expires_at = row
        now = time.time()

        if status == 'READY':
            if expires_at and expires_at < now:
                return None, None  # Expired

            try:
                img_paths = json.loads(img_paths_json or '[]')
                media_ids = json.loads(media_ids_json or '{}')
            except Exception:
                return None, None

            # Verify files still exist on disk
            if img_paths and all(os.path.exists(p) and os.path.getsize(p) > 5000 for p in img_paths):
                return img_paths, media_ids
            return None, None

        elif status == 'IN_FLIGHT':
            # Another thread/process is currently generating. Wait briefly.
            if (now - created_at) > 10.0:
                # Stale in-flight task (>10s old)
                return None, None

            if (now - start_wait) >= wait_timeout:
                # User clicked send before prewarm finished; proceed on-demand
                return None, None

            time.sleep(0.06)
        else:
            # FAILED or unknown status
            return None, None


def _execute_prewarm_worker(tenant: str, slip_type: str, voter_ids: List[int], cache_key: str):
    """Worker thread that executes card rendering and Meta media upload."""
    try:
        from electionapp.repo import get_repository
        from electionapp.card_generator import generate_all_bjp_voter_card_pages
        import meta_whatsapp

        repo = get_repository()
        target_voters = repo.get_voters_by_ids(tenant, voter_ids)
        if not target_voters:
            with _get_db() as conn:
                conn.execute("UPDATE prewarm_cache SET status = 'FAILED' WHERE cache_key = ?", (cache_key,))
            return

        resolved_type = resolve_slip_type(target_voters, slip_type)
        norm_key = build_cache_key(tenant, resolved_type, voter_ids)

        t0 = time.time()
        # 1. Render all pages
        card_img_paths = generate_all_bjp_voter_card_pages(
            target_voters, slip_type=resolved_type, page_size=8, tenant=tenant
        )
        t_render = time.time() - t0

        if not card_img_paths:
            with _get_db() as conn:
                conn.execute("UPDATE prewarm_cache SET status = 'FAILED' WHERE cache_key = ?", (cache_key,))
            return

        # 2. Pre-upload all pages to Meta Cloud API
        media_map = {}  # {img_path: {provider_id: media_id}}
        t_up_start = time.time()
        for img_path in card_img_paths:
            try:
                # Upload to primary provider
                m_id = meta_whatsapp.upload_media(img_path)
                media_map[img_path] = {"primary": m_id}
            except Exception as up_err:
                logger.warning(f"[PREWARM_UPLOAD_WARN] {img_path}: {up_err}")

        t_up = time.time() - t_up_start

        # 3. Store ready result under both requested and resolved keys
        now = time.time()
        expires = now + PREWARM_TTL_SECONDS
        vids_str = json.dumps(voter_ids)
        with _get_db() as conn:
            for k in set([cache_key, norm_key]):
                conn.execute(
                    """
                    INSERT INTO prewarm_cache (cache_key, tenant, slip_type, voter_ids, status, image_paths, media_ids, created_at, expires_at)
                    VALUES (?, ?, ?, ?, 'READY', ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        status = 'READY',
                        image_paths = excluded.image_paths,
                        media_ids = excluded.media_ids,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (k, str(tenant).lower(), str(resolved_type), vids_str, json.dumps(card_img_paths), json.dumps(media_map), now, expires)
                )

        print(
            f"[PREWARM_DONE] key={cache_key} render={t_render*1000:.1f}ms upload={t_up*1000:.1f}ms "
            f"pages={len(card_img_paths)} media_ready={len(media_map)}",
            flush=True
        )

    except Exception as exc:
        logger.error(f"[PREWARM_ERROR] key={cache_key}: {exc}", exc_info=True)
        try:
            with _get_db() as conn:
                conn.execute("UPDATE prewarm_cache SET status = 'FAILED' WHERE cache_key = ?", (cache_key,))
        except Exception:
            pass
    finally:
        _PREWARM_SEMAPHORE.release()


def trigger_background_prewarm(tenant: str, slip_type: str, voter_ids: List[int]) -> Dict[str, Any]:
    """
    Triggers asynchronous background prewarm for the given voter IDs.
    Returns status dict: {'status': 'started' | 'already_cached' | 'busy_skipped' | 'invalid'}.
    Non-blocking: returns immediately to the HTTP caller.
    """
    clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
    if not clean_ids:
        return {'status': 'invalid', 'error': 'No valid voter IDs'}

    cache_key = build_cache_key(tenant, slip_type, clean_ids)
    now = time.time()

    # Check if already cached and fresh
    try:
        with _get_db() as conn:
            cur = conn.execute(
                "SELECT status, expires_at, created_at FROM prewarm_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cur.fetchone()
            if row:
                st, exp, cr = row
                if st == 'READY' and exp and exp > now:
                    return {'status': 'already_cached', 'cache_key': cache_key}
                if st == 'IN_FLIGHT' and (now - cr) < 8.0:
                    return {'status': 'already_in_flight', 'cache_key': cache_key}
    except Exception as e:
        logger.warning(f"Error checking prewarm cache: {e}")

    # Check concurrency semaphore
    acquired = _PREWARM_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        # High load: all prewarm slots busy. Drop gracefully. Real send will render on-demand.
        return {'status': 'busy_skipped', 'cache_key': cache_key}

    try:
        # Mark IN_FLIGHT in database
        with _get_db() as conn:
            conn.execute(
                """
                INSERT INTO prewarm_cache (cache_key, tenant, slip_type, voter_ids, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'IN_FLIGHT', ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    status = 'IN_FLIGHT',
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (cache_key, str(tenant), str(slip_type), json.dumps(clean_ids), now, now + 60.0)
            )

        # Launch background thread
        worker = threading.Thread(
            target=_execute_prewarm_worker,
            args=(tenant, slip_type, clean_ids, cache_key),
            daemon=True,
            name=f"prewarm_{cache_key[:12]}"
        )
        worker.start()
        return {'status': 'started', 'cache_key': cache_key}

    except Exception as e:
        _PREWARM_SEMAPHORE.release()
        logger.error(f"Failed to start prewarm thread: {e}")
        return {'status': 'error', 'error': str(e)}
