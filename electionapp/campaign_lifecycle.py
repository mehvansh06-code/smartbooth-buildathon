"""
campaign_lifecycle.py
Secure, modular Campaign Lifecycle Management Engine for SmartBooth:
- Safe Campaign Archival (Soft Delete / Deactivation)
- Campaign Restoration (Re-activation of archived campaigns)
- Permanent Campaign Purge (Atomic partition drop, user cascade, asset removal)

Security & Guard Rails:
- Enforces strict RBAC ownership: super_admin or verified reseller owner.
- Protects core system schemas and master tables.
- Requires explicit slug confirmation token for irreversible permanent deletion.
- Ensures all disk assets and memory/worker caches are thoroughly purged.
"""

import os
import shutil
import logging
from typing import Tuple, Dict, Any, Optional

from electionapp.config import PLATFORM_ROOT, TENANTS_DIR, DATA_DIR, get_tenant_dir, clear_tenant_config_cache
from electionapp.campaign_manager import get_pg_connection, sanitize_slug, purge_tenant_generated_cards
from electionapp.tenant_branding import invalidate_tenant_branding
import user_manager

logger = logging.getLogger("smartbooth.campaign_lifecycle")

PROTECTED_SLUGS = frozenset({"master", "admin", "system", "voter_platform", "public"})


def _invalidate_fleet_caches(slug: str) -> None:
    """Invalidates branding memory caches, config cache, and fleet version marker across workers."""
    try:
        clear_tenant_config_cache(slug)
    except Exception as e:
        logger.warning(f"Error clearing config cache for '{slug}': {e}")

    try:
        invalidate_tenant_branding(slug)
    except Exception as e:
        logger.warning(f"Error invalidating branding cache for '{slug}': {e}")

    try:
        fleet_ver = os.path.join(TENANTS_DIR, ".fleet_version")
        os.makedirs(TENANTS_DIR, exist_ok=True)
        with open(fleet_ver, "w", encoding="utf-8") as f:
            import time
            f.write(str(time.time()))
    except Exception as e:
        logger.warning(f"Error updating fleet version: {e}")


def _check_permission(slug: str, user_id: Optional[int], role: Optional[str]) -> bool:
    """Ownership check: super_admin has global access; reseller must own the tenant."""
    if role == "super_admin":
        return True
    if role == "reseller" and user_id:
        return user_manager.reseller_owns_tenant(user_id, slug)
    return False


def archive_campaign(slug: str, user_id: Optional[int] = None, role: Optional[str] = None) -> Tuple[bool, Dict[str, Any], int]:
    """
    Safely archives / deactivates a campaign without destroying voter roll tables.
    Preserves all voter records, metrics, and accounts while halting operations.
    """
    clean_slug = sanitize_slug(slug)
    if not clean_slug:
        return False, {"success": False, "error": "अमान्य अभियान स्लग (Invalid campaign slug)"}, 400

    if role and not _check_permission(clean_slug, user_id, role):
        return False, {"success": False, "error": "अनुमति अस्वीकृत (Access denied for this campaign)"}, 403

    # 1. Update PostgreSQL status
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE tenants SET status = 'ARCHIVED' WHERE campaign_slug = %s RETURNING tenant_id",
                    (clean_slug,)
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(f"Tenant slug '{clean_slug}' not found in PostgreSQL tenants table")
            pg_conn.commit()
        except Exception as e:
            logger.error(f"PostgreSQL archive error for '{clean_slug}': {e}")
            if pg_conn:
                pg_conn.rollback()
        finally:
            pg_conn.close()

    # 2. Update campaign.json
    cfg_file = os.path.join(TENANTS_DIR, clean_slug, "campaign.json")
    if os.path.exists(cfg_file):
        try:
            import json
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["status"] = "ARCHIVED"
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed updating campaign.json status to ARCHIVED for '{clean_slug}': {e}")

    try:
        user_manager.sync_users_with_campaign_status(clean_slug, "ARCHIVED")
    except Exception:
        pass

    _invalidate_fleet_caches(clean_slug)
    logger.info(f"Campaign '{clean_slug}' successfully archived by user {user_id} ({role})")

    return True, {
        "success": True,
        "campaign_slug": clean_slug,
        "status": "ARCHIVED",
        "message": f"अभियान '{clean_slug}' को सुरक्षित रूप से पुरालेख (Archive) में स्थानांतरित कर दिया गया है।"
    }, 200


def restore_campaign(slug: str, user_id: Optional[int] = None, role: Optional[str] = None) -> Tuple[bool, Dict[str, Any], int]:
    """
    Restores an archived campaign back to ACTIVE status.
    """
    clean_slug = sanitize_slug(slug)
    if not clean_slug:
        return False, {"success": False, "error": "अमान्य अभियान स्लग (Invalid campaign slug)"}, 400

    if role and not _check_permission(clean_slug, user_id, role):
        return False, {"success": False, "error": "अनुमति अस्वीकृत (Access denied for this campaign)"}, 403

    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE tenants SET status = 'ACTIVE' WHERE campaign_slug = %s RETURNING tenant_id",
                    (clean_slug,)
                )
            pg_conn.commit()
        except Exception as e:
            logger.error(f"PostgreSQL restore error for '{clean_slug}': {e}")
            if pg_conn:
                pg_conn.rollback()
        finally:
            pg_conn.close()

    cfg_file = os.path.join(TENANTS_DIR, clean_slug, "campaign.json")
    if os.path.exists(cfg_file):
        try:
            import json
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["status"] = "ACTIVE"
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed updating campaign.json status to ACTIVE for '{clean_slug}': {e}")

    try:
        user_manager.sync_users_with_campaign_status(clean_slug, "ACTIVE")
    except Exception:
        pass

    _invalidate_fleet_caches(clean_slug)
    logger.info(f"Campaign '{clean_slug}' successfully restored to ACTIVE by user {user_id} ({role})")

    return True, {
        "success": True,
        "campaign_slug": clean_slug,
        "status": "ACTIVE",
        "message": f"अभियान '{clean_slug}' पुनः सक्रिय (ACTIVE) कर दिया गया है।"
    }, 200


def purge_campaign_permanent(
    slug: str,
    confirmation_slug: str,
    user_id: Optional[int] = None,
    role: Optional[str] = None
) -> Tuple[bool, Dict[str, Any], int]:
    """
    Permanently purges a campaign:
    1. Validates strict slug confirmation match to prevent accidental triggers.
    2. Enforces RBAC permissions.
    3. Drops PostgreSQL partition table `voters_t{tenant_id}` CASCADE.
    4. Deletes whatsapp quota, quotas ledger, campaign karyakartas, and staff/operator accounts.
    5. Deletes tenant row from `tenants`.
    6. Deletes local directories `tenants/{slug}` and `data/{slug}`.
    7. Cleans up cached voter slips and invalidates in-memory caches.
    """
    clean_slug = sanitize_slug(slug)
    clean_confirm = sanitize_slug(confirmation_slug)

    if not clean_slug:
        return False, {"success": False, "error": "अभियान स्लग आवश्यक है (Campaign slug required)"}, 400

    if clean_slug in PROTECTED_SLUGS:
        return False, {"success": False, "error": f"संरक्षित सिस्टम अभियान '{clean_slug}' को हटाया नहीं जा सकता (Protected system campaign)"}, 403

    if clean_slug != clean_confirm:
        return False, {
            "success": False,
            "error": f"पुष्टि स्लग मेल नहीं खाता (Confirmation slug does not match '{clean_slug}')"
        }, 400

    # Strict Security Guard: Reseller or other non-super-admin roles can NEVER permanently delete a campaign
    if role != "super_admin":
        return False, {
            "success": False,
            "error": "केवल सुपर एडमिन ही अभियान को स्थायी रूप से हटा सकते हैं (Permanent campaign deletion is strictly restricted to Super Admin)"
        }, 403

    tenant_id: Optional[int] = None
    dropped_partition = False
    deleted_users_count = 0

    # 1. Database cleanup
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                # Find tenant_id
                cur.execute("SELECT tenant_id FROM tenants WHERE campaign_slug = %s", (clean_slug,))
                row = cur.fetchone()
                if row:
                    tenant_id = row[0]

                if tenant_id:
                    # Drop partition table safely
                    part_table = f"voters_t{tenant_id}"
                    cur.execute(f"DROP TABLE IF EXISTS {part_table} CASCADE;")
                    dropped_partition = True

                    try:
                        cur.execute("DELETE FROM whatsapp_quota_ledger WHERE tenant_id = %s", (tenant_id,))
                        cur.execute("DELETE FROM whatsapp_quota WHERE tenant_id = %s", (tenant_id,))
                    except Exception as qe:
                        logger.warning(f"Error purging quota for tenant {tenant_id}: {qe}")

                    try:
                        cur.execute("DELETE FROM karyakartas WHERE tenant_id = %s", (tenant_id,))
                    except Exception:
                        pass

                    try:
                        cur.execute("DELETE FROM dispatch_history WHERE tenant_id = %s", (tenant_id,))
                    except Exception:
                        pass

                # Delete campaign-specific users (never delete super_admin accounts!)
                try:
                    if tenant_id:
                        cur.execute(
                            "DELETE FROM users WHERE (tenant_slug = %s OR tenant_id = %s) AND role != 'super_admin'",
                            (clean_slug, tenant_id)
                        )
                    else:
                        cur.execute(
                            "DELETE FROM users WHERE tenant_slug = %s AND role != 'super_admin'",
                            (clean_slug,)
                        )
                    deleted_users_count = max(0, cur.rowcount)
                except Exception as ue:
                    logger.warning(f"Error deleting users for '{clean_slug}': {ue}")

                # Delete from tenants table
                cur.execute("DELETE FROM tenants WHERE campaign_slug = %s", (clean_slug,))

            pg_conn.commit()
        except Exception as e:
            logger.error(f"PostgreSQL purge failure for '{clean_slug}': {e}")
            if pg_conn:
                pg_conn.rollback()
            return False, {"success": False, "error": f"डेटाबेस विलोपन त्रुटि (Database deletion error): {str(e)}"}, 500
        finally:
            pg_conn.close()

    # 2. File system cleanup
    tenant_dir = os.path.join(TENANTS_DIR, clean_slug)
    if os.path.exists(tenant_dir):
        try:
            shutil.rmtree(tenant_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error removing tenant directory '{tenant_dir}': {e}")

    data_dir = os.path.join(DATA_DIR, clean_slug)
    if os.path.exists(data_dir):
        try:
            shutil.rmtree(data_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error removing data directory '{data_dir}': {e}")

    # SQLite fallback file cleanup
    sqlite_file = os.path.join(DATA_DIR, f"voters_{clean_slug}.db")
    if os.path.exists(sqlite_file):
        try:
            os.remove(sqlite_file)
        except Exception:
            pass

    # 3. Purge cached slip images & invalidate caches
    purged_cards = purge_tenant_generated_cards(clean_slug)
    _invalidate_fleet_caches(clean_slug)

    logger.info(
        f"Campaign '{clean_slug}' permanently deleted by user {user_id} ({role}). "
        f"Dropped partition: {dropped_partition}, Users removed: {deleted_users_count}, Slips purged: {purged_cards}"
    )

    return True, {
        "success": True,
        "campaign_slug": clean_slug,
        "tenant_id": tenant_id,
        "dropped_partition": dropped_partition,
        "deleted_users_count": deleted_users_count,
        "purged_cards": purged_cards,
        "message": f"अभियान '{clean_slug}' और उससे संबंधित सभी डेटाबेस रिकॉर्ड्स व फाइल्स को स्थायी रूप से हटा दिया गया है।"
    }, 200
