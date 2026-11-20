import os
import json
import sqlite3
import datetime
import logging
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("smartbooth.dispatch.router")

from electionapp import whatsapp_quota
from .base import BaseDispatchChannel, DispatchResult
from .meta_cloud import MetaCloudChannel
from .wa_me import WaMeChannel
from .null import NullChannel


# Channels that incur a real per-message cost and therefore consume quota.
# wa_me only builds a deep link and null is in-memory, so neither is billable.
BILLABLE_CHANNELS = {'meta_cloud'}


DEFAULT_TENANT_CHANNEL_ORDER: Dict[str, List[str]] = {
    'ward82': ['meta_cloud', 'wa_me'],
    'jelatra': ['meta_cloud', 'wa_me'],
    'ward9': ['meta_cloud', 'wa_me'],
    'rajpura': ['meta_cloud', 'wa_me'],
    'default': ['meta_cloud', 'wa_me']
}


class DispatchRouter:
    """
    Central Dispatch Router.
    - Automated dispatch operates EXCLUSIVELY via official WhatsApp Cloud API (meta_cloud).
    - Manual 1-click fallback is available via volunteer deep links (wa_me).
    - Baileys socket gateways are retired per architectural directive.
    - Enforces voter consent gate (consent_status != 'revoked' / 'opted_out').
    - Provides single audit choke-point recording into dispatch_history.
    """

    def __init__(
        self,
        tenant_slug: str = "default",
        channels: Optional[Dict[str, BaseDispatchChannel]] = None,
        channel_order: Optional[List[str]] = None,
        db_path: Optional[str] = None,
        repo: Optional[Any] = None
    ):
        self.tenant_slug = tenant_slug
        self.db_path = db_path
        self.repo = repo

        # Initialize default channels if not provided
        if channels is not None:
            self.channels = channels
        else:
            self.channels = {
                'meta_cloud': MetaCloudChannel(),
                'wa_me': WaMeChannel(),
                'null': NullChannel()
            }

        # Resolve channel order
        if channel_order is not None:
            self.channel_order = channel_order
        else:
            self.channel_order = DEFAULT_TENANT_CHANNEL_ORDER.get(
                tenant_slug,
                DEFAULT_TENANT_CHANNEL_ORDER['default']
            )

    def _check_consent(self, voter_data: Optional[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        Consent Gate:
        Checks consent_status on voter record.
        Returns (is_allowed, reason).
        """
        if not voter_data:
            return True, None

        status = str(voter_data.get('consent_status', '')).strip().lower()
        if status in ['revoked', 'opt_out', 'opted_out', 'refused']:
            return False, f"Dispatch blocked: voter consent_status is '{status}'"
        return True, None

    def _log_audit_choke_point(
        self,
        voter_id: Optional[int],
        phone: str,
        channel: str,
        success: bool,
        message_id: Optional[str] = None,
        error: Optional[str] = None,
        action: str = "send"
    ) -> None:
        """
        Single audit choke-point: writes to PostgreSQL dispatch_history table (primary)
        or falls back to legacy SQLite if repo is not provided.
        """
        status_str = "SUCCESS" if success else "FAILED"

        # 1. Primary: PostgreSQL Multi-Tenant Repository
        if self.repo and hasattr(self.repo, 'get_connection'):
            try:
                tid, _ = self.repo._resolve_tenant(self.tenant_slug)
                with self.repo.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO dispatch_history (
                                tenant_id, voter_id, phone, channel, delivery_status, message_id, notes, sent_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        """, (
                            tid, voter_id, phone, channel, status_str, message_id, error
                        ))
                        if success and voter_id:
                            cur.execute("""
                                UPDATE voters
                                SET is_dispatched = TRUE, dispatched_at = NOW(), updated_at = NOW()
                                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                            """, (tid, voter_id, voter_id))
                    conn.commit()
                return
            except Exception as ex:
                logger.warning("Failed to write dispatch_history to PostgreSQL: %s", ex)

        # 2. Fallback: Legacy SQLite database if db_path is explicitly configured
        if not self.db_path or not os.path.exists(self.db_path):
            return

        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dispatch_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voter_id INTEGER,
                    phone TEXT,
                    channel TEXT,
                    delivery_status TEXT,
                    message_id TEXT,
                    message_ids TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    error_message TEXT
                );
            """)
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(dispatch_history)")
            cols = {r[1] for r in cur.fetchall()}
            if 'error_message' not in cols:
                try:
                    conn.execute("ALTER TABLE dispatch_history ADD COLUMN error_message TEXT;")
                    cols.add('error_message')
                except Exception:
                    pass

            if 'error_message' in cols:
                conn.execute("""
                    INSERT INTO dispatch_history (
                        voter_id, phone, channel, delivery_status, message_id, sent_at, error_message
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?);
                """, (
                    voter_id,
                    phone,
                    channel,
                    status_str,
                    message_id,
                    error
                ))
            else:
                conn.execute("""
                    INSERT INTO dispatch_history (
                        voter_id, phone, channel, delivery_status, message_id, sent_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
                """, (
                    voter_id,
                    phone,
                    channel,
                    status_str,
                    message_id
                ))
            conn.commit()
            conn.close()
        except Exception as ex:
            logger.warning("Failed to write dispatch_history: %s", ex)

    def dispatch_slip(
        self,
        phone: str,
        message: str,
        image_path: str,
        voter_data: Optional[Dict[str, Any]] = None,
        override_channel_order: Optional[List[str]] = None
    ) -> DispatchResult:
        """
        Dispatches a voter slip image using prioritized channel order with automatic fallback.
        """
        voter_id = voter_data.get('id') if voter_data else None

        # 0. Campaign Status Gate check
        from electionapp.config import get_tenant_status
        if get_tenant_status(self.tenant_slug) in ('PAUSED', 'ARCHIVED'):
            reason = f"Dispatch blocked: campaign '{self.tenant_slug}' is paused"
            result = DispatchResult(
                success=False,
                channel="campaign_lifecycle_gate",
                recipient_phone=phone,
                error=reason
            )
            self._log_audit_choke_point(voter_id, phone, "campaign_lifecycle_gate", False, error=reason)
            return result

        # 1. Consent Gate check
        allowed, reason = self._check_consent(voter_data)
        if not allowed:
            result = DispatchResult(
                success=False,
                channel="consent_gate",
                recipient_phone=phone,
                error=reason
            )
            self._log_audit_choke_point(voter_id, phone, "consent_gate", False, error=reason)
            return result

        # 2. WhatsApp quota gate. One credit covers this dispatch across all channel
        # attempts; a failed attempt refunds itself, so only a delivered message is
        # billed. Fails CLOSED: if the quota cannot be read, nothing is sent.
        order = override_channel_order or self.channel_order
        reservation = self._reserve_credit(order, voter_id, phone)
        if isinstance(reservation, DispatchResult):
            return reservation

        try:
            return self._dispatch_slip_inner(
                phone, message, image_path, voter_data, voter_id, order, reservation
            )
        finally:
            if reservation is not None:
                reservation.close()

    def _reserve_credit(self, order, voter_id, phone):
        """Reserves one WhatsApp credit when the channel order contains a billable channel.

        Returns a Reservation, or None when no billable channel is involved, or a
        failed DispatchResult when the quota refuses (which the caller must return).
        """
        if not any(c in BILLABLE_CHANNELS for c in order):
            return None
        try:
            return whatsapp_quota.reserve(self.tenant_slug, 1)
        except whatsapp_quota.QuotaError as ex:
            reason = str(ex)
            logger.warning("Dispatch refused by WhatsApp quota (tenant=%s): %s",
                           self.tenant_slug, reason)
            self._log_audit_choke_point(voter_id, phone, "quota_gate", False, error=reason)
            return DispatchResult(
                success=False,
                channel="quota_gate",
                recipient_phone=phone,
                error=reason
            )

    def _dispatch_slip_inner(
        self,
        phone: str,
        message: str,
        image_path: str,
        voter_data: Optional[Dict[str, Any]],
        voter_id: Optional[int],
        order: List[str],
        credit
    ) -> DispatchResult:
        last_result = None

        for ch_name in order:
            channel = self.channels.get(ch_name)
            if not channel:
                continue

            # Attempt dispatch
            try:
                res = channel.send_slip(phone, message, image_path, voter_data, credit=credit)
                last_result = res
                if res.success:
                    self._log_audit_choke_point(
                        voter_id, phone, res.channel, True, message_id=res.message_id
                    )
                    return res
                else:
                    self._log_audit_choke_point(
                        voter_id, phone, res.channel, False, error=res.error
                    )
                    # If recipient is confirmed not on WhatsApp, fallback might not help, but wa_me still can
                    if res.error and "Not on WhatsApp" in res.error and "wa_me" not in order:
                        return res
            except Exception as ex:
                err_str = f"Channel '{ch_name}' raised exception: {ex}"
                self._log_audit_choke_point(voter_id, phone, ch_name, False, error=err_str)
                last_result = DispatchResult(
                    success=False,
                    channel=ch_name,
                    recipient_phone=phone,
                    error=err_str
                )

        return last_result or DispatchResult(
            success=False,
            channel="router",
            recipient_phone=phone,
            error="No dispatch channel succeeded"
        )

    def dispatch_text(
        self,
        phone: str,
        message: str,
        voter_data: Optional[Dict[str, Any]] = None,
        override_channel_order: Optional[List[str]] = None
    ) -> DispatchResult:
        """
        Dispatches a text message using prioritized channel order with automatic fallback.
        """
        voter_id = voter_data.get('id') if voter_data else None

        # 1. Consent Gate check
        allowed, reason = self._check_consent(voter_data)
        if not allowed:
            result = DispatchResult(
                success=False,
                channel="consent_gate",
                recipient_phone=phone,
                error=reason
            )
            self._log_audit_choke_point(voter_id, phone, "consent_gate", False, error=reason)
            return result

        # 2. WhatsApp quota gate (see dispatch_slip). Fails CLOSED.
        order = override_channel_order or self.channel_order
        reservation = self._reserve_credit(order, voter_id, phone)
        if isinstance(reservation, DispatchResult):
            return reservation

        try:
            return self._dispatch_text_inner(
                phone, message, voter_data, voter_id, order, reservation
            )
        finally:
            if reservation is not None:
                reservation.close()

    def _dispatch_text_inner(
        self,
        phone: str,
        message: str,
        voter_data: Optional[Dict[str, Any]],
        voter_id: Optional[int],
        order: List[str],
        credit
    ) -> DispatchResult:
        last_result = None

        for ch_name in order:
            channel = self.channels.get(ch_name)
            if not channel:
                continue

            try:
                res = channel.send_message(phone, message, voter_data, credit=credit)
                last_result = res
                if res.success:
                    self._log_audit_choke_point(
                        voter_id, phone, res.channel, True, message_id=res.message_id
                    )
                    return res
                else:
                    self._log_audit_choke_point(
                        voter_id, phone, res.channel, False, error=res.error
                    )
            except Exception as ex:
                err_str = f"Channel '{ch_name}' raised exception: {ex}"
                self._log_audit_choke_point(voter_id, phone, ch_name, False, error=err_str)
                last_result = DispatchResult(
                    success=False,
                    channel=ch_name,
                    recipient_phone=phone,
                    error=err_str
                )

        return last_result or DispatchResult(
            success=False,
            channel="router",
            recipient_phone=phone,
            error="No dispatch channel succeeded"
        )
