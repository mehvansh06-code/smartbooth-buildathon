"""
tenancy.py
Authoritative multi-tenant resolution and isolation enforcement for SmartBooth.
Guarantees operators cannot access or modify records of unauthorized campaigns.
"""

from typing import Tuple, Any, Optional
from flask import g, session, current_app, request

# Tenants explicitly excluded from the "oldest male" mukhiya refinement
# (2026-10-11 decision) — these keep the legacy "oldest person" selection
# everywhere (new families included) until explicitly revisited.
MUKHIYA_MALE_PREFERENCE_EXCLUDED_TENANTS = {'sarana', 'gada', 'bondli'}


def mukhiya_prefers_male(tenant_slug: Optional[str]) -> bool:
    """True unless tenant_slug is one of the tenants excluded from the
    oldest-male mukhiya refinement (kept on legacy oldest-person logic)."""
    return str(tenant_slug or '').strip().lower() not in MUKHIYA_MALE_PREFERENCE_EXCLUDED_TENANTS


def resolve_tenant() -> Tuple[str, Any]:
    """
    Authoritatively returns (tenant_slug, repo) for the current request.
    The tenant_slug is validated and authorized against the user's session
    in before_request (resolve_tenant_context).
    """
    repo = current_app.extensions.get('repo')
    slug = getattr(g, 'tenant_slug', None)
    if not slug:
        sess_slug = session.get('tenant_slug')
        if sess_slug and sess_slug != '*':
            slug = sess_slug
        else:
            slug = current_app.config.get('TENANT_SLUG', '')
        g.tenant_slug = slug

    return str(slug).strip().lower(), repo
