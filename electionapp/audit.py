"""
audit.py
Generic "who did what" audit log: self-applying schema bootstrap + a
``@audit_action`` decorator that blueprint write routes can apply.

Distinct from the narrow ``voting_audit_log`` (vote-marking only) and
``dispatch_history`` (WhatsApp sends, no actor column) — this is the generic
action log that instrumented write routes opt into.

Design notes
------------
* Schema is self-applying at boot, mirroring ``whatsapp_quota.ensure_quota_schema``:
  nothing in CI or the deploy workflow runs the .sql migration files, so the DDL
  has to land itself on restart.
* Unlike WhatsApp quota, audit logging is intentionally NOT fail-closed: a
  schema-bootstrap or insert failure is logged and swallowed so a briefly
  unavailable audit trail never blocks the underlying request.
"""

import logging
from functools import wraps
from typing import Any, Optional

from flask import g, request

from electionapp.tenancy import resolve_tenant

logger = logging.getLogger("electionapp.audit")


def ensure_audit_schema(repo) -> None:
    """Idempotent DDL for the Postgres ``audit_log`` table. Safe to call on every boot.

    Reuses the repo's own pooled connection (same pattern as
    ``whatsapp_quota.ensure_quota_schema`` / ``user_manager.ensure_schema``) rather
    than opening a new connection pool.
    """
    with repo.get_connection() as conn:
        with conn:  # commits on clean exit, rolls back on exception
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS audit_log ("
                    "  id              BIGSERIAL PRIMARY KEY,"
                    "  tenant_id       INT NOT NULL,"
                    "  user_id         INT,"
                    "  username        VARCHAR(64),"
                    "  action          VARCHAR(64) NOT NULL,"
                    "  entity_type     VARCHAR(32),"
                    "  entity_id       VARCHAR(64),"
                    "  details         JSONB,"
                    "  ip_address      INET,"
                    "  user_agent      VARCHAR(256),"
                    "  created_at      TIMESTAMPTZ DEFAULT NOW()"
                    ");"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_created "
                    "ON audit_log (tenant_id, created_at DESC);"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_log_entity "
                    "ON audit_log (tenant_id, entity_type, entity_id);"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_log_user "
                    "ON audit_log (tenant_id, user_id);"
                )


def _unwrap_response(result: Any):
    """Normalize a Flask view's return value to (body, status_code).

    Handles a plain dict/Response, a ``(response, status_code)`` tuple, and the
    output of ``jsonify(...)``.
    """
    status_code = 200
    body = result
    if isinstance(result, tuple):
        body = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status_code = result[1]
    if hasattr(body, "status_code"):
        status_code = body.status_code
    return body, status_code


def _is_success(body: Any, status_code: int) -> bool:
    """Success if status < 400 AND, when the body is JSON, it isn't {"success": false}."""
    if status_code >= 400:
        return False
    payload = None
    if hasattr(body, "get_json"):
        try:
            payload = body.get_json(silent=True)
        except Exception:
            payload = None
    elif isinstance(body, dict):
        payload = body
    if isinstance(payload, dict) and payload.get("success") is False:
        return False
    return True


def _resolve_entity_id(entity_id_arg: Optional[str], kwargs: dict) -> Optional[Any]:
    """Resolve the entity id: prefer the named Flask URL converter kwarg,
    otherwise fall back to a sensible key in the JSON request body.
    """
    if entity_id_arg:
        return kwargs.get(entity_id_arg)
    body = request.get_json(silent=True) if request else None
    if isinstance(body, dict):
        for key in ("voter_id", "family_id", "user_id", "karyakarta_id", "campaign_id", "slug", "id"):
            if key in body:
                return body[key]
    return None


def _record(action, entity_type, entity_id_arg, entity_id_from_response, kwargs, result) -> None:
    """Build and persist one audit_log row for a completed (successful) view call."""
    if request and request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    body, status_code = _unwrap_response(result)
    if not _is_success(body, status_code):
        return

    tenant_slug, repo = resolve_tenant()
    if repo is None:
        return

    current_user = getattr(g, "current_user", None) or {}
    user_id = current_user.get("id")
    username = current_user.get("username")

    # Resolve tenant_slug -> numeric tenant_id using the repo's own existing
    # helper PostgresVoterRepository._resolve_tenant.
    tenant_id = tenant_slug
    resolver = getattr(repo, "_resolve_tenant", None)
    if callable(resolver):
        try:
            tenant_id, _ = resolver(tenant_slug)
        except Exception:
            tenant_id = tenant_slug

    entity_id = None
    if entity_id_from_response and isinstance(body, dict):
        entity_id = body.get("entity_id") or body.get("id")
    if entity_id is None:
        entity_id = _resolve_entity_id(entity_id_arg, kwargs)

    ip_address = request.remote_addr if request else None
    user_agent = (request.headers.get("User-Agent", "") if request else "")[:256]

    repo.log_audit_event(
        tenant_id=tenant_id,
        user_id=user_id,
        username=username,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=None,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def audit_action(action, entity_type=None, entity_id_arg=None, entity_id_from_response=False):
    """Decorator for blueprint write routes: records a generic ``audit_log`` row
    after a successful response. Never raises — a logging failure must never
    break the underlying request.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            result = fn(*args, **kwargs)
            try:
                _record(action, entity_type, entity_id_arg, entity_id_from_response, kwargs, result)
            except Exception:
                logger.exception("audit_action failed for %s", action)
            return result
        return wrapped
    return decorator
