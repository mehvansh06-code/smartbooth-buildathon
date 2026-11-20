"""
Unified Multi-Tenant Election Campaign Platform.
Application Factory implementation following Section 3.2 & 3.3 of the Master Plan.
Zero module-level mutable globals.
Supports strict user-tenant isolation ("under one roof, but separated").
"""

import os
import time
import datetime
import uuid
import logging
from typing import Optional, Dict, Tuple, Any
from flask import Flask, send_from_directory, request, g, session, redirect, jsonify, make_response, render_template
import user_manager

logger = logging.getLogger("smartbooth")

from electionapp.config import (
    load_tenant_config,
    get_tenant_assets_dir,
    get_tenant_db_path,
    list_available_tenants,
    is_panchayat_campaign,
    is_ward_campaign,
    get_cached_ward_hierarchy,
    get_tenant_status,
    PLATFORM_ROOT
)
from electionapp.tenant_branding import get_tenant_branding

from electionapp.repo import get_repository
from electionapp.dispatch import DispatchRouter
from electionapp.blueprints import (
    auth_bp,
    core_bp,
    voters_bp,
    search_bp,
    warroom_bp,
    dispatch_bp,
    admin_bp,
    history_bp,
    slip_bp,
    campaign_bp,
    turnout_bp,
    karyakartas_bp,
    caste_bp,
    family_bp,
    reports_bp,
    free_slips_bp,
    voice_bp,
    voice_agent_bp,
    agent_bp
)

__version__ = "1.0.0"


def _resolve_app_version() -> str:
    """Computes a git-commit fingerprint for automated asset cache-busting."""
    env_ver = os.environ.get('APP_VERSION')
    if env_ver:
        return env_ver.strip()
    try:
        import subprocess
        out = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=PLATFORM_ROOT,
            stderr=subprocess.DEVNULL,
            timeout=2
        ).decode('utf-8').strip()
        if out:
            return out
    except Exception:
        pass
    try:
        mtime = int(os.path.getmtime(os.path.join(PLATFORM_ROOT, 'config.py')))
        return str(mtime)
    except Exception:
        return str(int(time.time()))


APP_VERSION = _resolve_app_version()


HOST_TENANT_MAP = {
    'punjab.eiofficial.com': 'rajpura',
    'sarpanch.eiofficial.com': 'jelatra',
    'vedmap.eiofficial.com': 'ward82',
    'warroom.eiofficial.com': 'ward82',
    'sarana.vedmap.com': 'sarana',
    'election.vedmap.com': 'ward82',
    'zhak.vedmap.com': 'zhak',
}


def format_campaign_for_display(slug: str, t_cfg: dict, fallback_candidate: str = '') -> dict:
    """Formats campaign names for clean, distinct display without repetitive Gram Panchayat prefixes."""
    import re
    t_elec = t_cfg.get('election', {}) if isinstance(t_cfg, dict) else {}
    t_cand = t_cfg.get('candidate', {}) if isinstance(t_cfg, dict) else {}

    raw_cand = (t_cand.get('name_hi') or t_cand.get('name_en') or fallback_candidate or '').strip()
    generic_candidates = {
        'सरपंच प्रत्याशी', 'sarpanch candidate', 'प्रत्याशी', 'candidate', 
        'सरपंच', 'sarpanch', 'वार्ड पार्षद', 'ward member', 'सदस्य', 'member',
        'जिला परिषद सदस्य', 'zila parishad member', 'पंचायत समिति सदस्य'
    }
    is_generic_cand = not raw_cand or raw_cand.lower() in generic_candidates or raw_cand.lower() == slug.lower()
    candidate_name = '' if is_generic_cand else raw_cand

    raw_area = (t_elec.get('ward_name_hi') or t_elec.get('ward_name_en') or slug).strip()
    clean_area = re.sub(r'^(?:ग्राम\s*पंचायत|Gram\s*Panchayat)\s+', '', raw_area, flags=re.IGNORECASE).strip()
    clean_area = re.sub(r'^(?:जिला\s*परिषद|Zila\s*Parishad)\s+', '', clean_area, flags=re.IGNORECASE).strip()
    clean_area = re.sub(r'^(?:पंचायत\s*समिति|Panchayat\s*Samiti)\s+', '', clean_area, flags=re.IGNORECASE).strip()
    clean_area = re.sub(r'^(?:नगर\s*(?:निगम|पालिका|परिषद)|Municipal\s*(?:Corporation|Council|Board)?)\s+', '', clean_area, flags=re.IGNORECASE).strip()
    if not clean_area:
        clean_area = raw_area

    district = (t_elec.get('district') or '').strip()
    ward_no = (t_elec.get('ward_no') or '').strip()
    election_type = (t_elec.get('election_type') or 'PANCHAYAT').upper()

    if candidate_name:
        display_label = f"{candidate_name} • {clean_area}"
    else:
        display_label = clean_area

    return {
        'slug': slug,
        'name': display_label,
        'clean_area': clean_area,
        'candidate': candidate_name,
        'district': district,
        'ward_no': ward_no,
        'election_type': election_type,
        'full_title': f"{display_label} ({slug})"
    }


def create_app(tenant_slug: str = "ward82", storage_backend: Optional[str] = None) -> Flask:
    """
    Application Factory for tenant-isolated Flask application.
    
    :param tenant_slug: Slug of the tenant (e.g., 'ward82', 'jelatra', 'ward9', 'rajpura')
    :param storage_backend: 'postgres' (primary production Cloud SaaS engine)
    :return: Configured Flask application instance with zero module-level mutable globals.
    """
    if storage_backend is None:
        storage_backend = os.environ.get('STORAGE_BACKEND', 'postgres')

    templates_dir = os.path.join(PLATFORM_ROOT, 'templates')
    static_dir = os.path.join(PLATFORM_ROOT, 'static')

    # Initialize Sentry error telemetry if configured
    from electionapp.telemetry import init_sentry
    init_sentry(app_name="smartbooth")

    app = Flask(
        f"electionapp_{tenant_slug}",
        template_folder=templates_dir,
        static_folder=static_dir,
        static_url_path='/static'
    )
    app.secret_key = os.environ.get('SECRET_KEY', 'voter_election_secret_platform_2026')
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(days=30)
    app.permanent_session_lifetime = datetime.timedelta(days=30)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    if os.environ.get('FLASK_ENV') == 'production' or os.environ.get('SESSION_COOKIE_SECURE') == '1':
        app.config['SESSION_COOKIE_SECURE'] = True

    # Core Mode: Default to 0 (show all War Room, Turnout, Voting Day, Caste, and Secondary modules)
    core_mode = os.environ.get('VOTER_CORE_MODE', '0') == '1'
    app.config['CORE_MODE'] = core_mode

    # Voice Module Feature Flag (Default False to hide across all user roles)
    enable_voice = os.environ.get('ENABLE_VOICE_MODULE', '0').lower() in ('1', 'true', 'yes')
    app.config['ENABLE_VOICE_MODULE'] = enable_voice

    # 1. Load tenant configuration
    campaign_config = load_tenant_config(tenant_slug)
    app.config['CAMPAIGN'] = campaign_config
    app.config['TENANT_SLUG'] = tenant_slug
    app.config['STORAGE_BACKEND'] = storage_backend
    app.config['APP_VERSION'] = APP_VERSION

    # 2. Initialize repository and dispatch router
    repo = get_repository(storage_backend, tenant_slug=tenant_slug)
    db_path = getattr(repo, 'db_path', None)
    router = DispatchRouter(tenant_slug=tenant_slug, repo=repo, db_path=db_path)

    app.extensions['repo'] = repo
    app.extensions['router'] = router

    # 2b. Self-applying WhatsApp quota schema. Nothing in CI or the deploy workflow
    # runs the .sql migration files, so the DDL has to land on restart (same pattern
    # as user_manager.ensure_schema). A failure here is logged but NOT swallowed into
    # a permissive state: whatsapp_quota.reserve() then raises QuotaUnavailable and
    # every send is refused, which is the intended fail-closed behaviour.
    if storage_backend == 'postgres':
        try:
            from electionapp.whatsapp_quota import (
                ensure_quota_schema,
                reap_stale_reservations,
            )
            ensure_quota_schema()
            # A restart after a crash may leave credits held by the dead process.
            # Reclaiming at boot only ever returns credits, never grants extra.
            reap_stale_reservations()
        except Exception as e:
            app.logger.error("WhatsApp quota schema could not be ensured: %s", e)

        try:
            from electionapp.voice_quota import (
                ensure_voice_schema,
                reap_stale_reservations as reap_voice_reservations,
            )
            ensure_voice_schema()
            reap_voice_reservations()
        except Exception as e:
            app.logger.error("Voice quota schema could not be ensured: %s", e)

        try:
            from electionapp.services.bulk_slip_worker import (
                ensure_bulk_dispatch_schema,
                reap_stale_bulk_jobs,
            )
            ensure_bulk_dispatch_schema()
            reap_stale_bulk_jobs()
        except Exception as e:
            app.logger.error("Bulk slip dispatch schema could not be ensured: %s", e)

    # 2c. Self-applying generic audit_log schema. Unlike the quota schema above,
    # audit logging must NOT fail closed: a briefly unavailable audit trail
    # should never block application boot or request handling.
    if storage_backend == 'postgres':
        try:
            from electionapp.audit import ensure_audit_schema
            ensure_audit_schema(repo)
        except Exception as e:
            app.logger.error("Audit log schema could not be ensured: %s", e)

    # 3. Dynamic multi-tenant resolution and strict user authentication
    @app.before_request
    def resolve_tenant_context():
        g.request_id = request.headers.get('X-Request-ID') or uuid.uuid4().hex[:8]
        g.request_start_time = time.time()
        path = request.path

        # Resolve host-based mapping first (applies to login & public assets too)
        host = request.host.split(':')[0].lower()
        mapped_tenant = HOST_TENANT_MAP.get(host)
        if mapped_tenant:
            g.tenant_slug = mapped_tenant

        if host == 'warroom.eiofficial.com' and path == '/':
            return redirect('/warroom')

        # Dedicated routing & 100% isolation for PhotoParchi domain (parchi.smartbooth.in)
        is_parchi_host = (
            host == 'parchi.smartbooth.in'
            or host.startswith('parchi.')
            or host == '15.252.229.132'
            or host == '65.0.132.235'
            or os.environ.get('IS_PARCHI_SERVER') == '1'
        )
        if is_parchi_host:
            if path == '/':
                qs = request.query_string.decode("utf-8", errors="ignore")
                target = "/photoparchi" + (f"?{qs}" if qs else "")
                return redirect(target, code=302)
            if path in ['/admin', '/admin/']:
                return redirect('/photoparchi/admin')
            # Standalone domain: never route to SmartBooth login or tenancy checks
            return None

        # Whitelist public assets & auth endpoints
        is_public = (
            path in ['/login', '/logout', '/healthz', '/favicon.ico', '/manifest.json', '/sw.js', '/free-slips', '/parchi', '/photoparchi', '/terms', '/privacy', '/refund', '/shipping', '/delivery', '/about', '/pricing', '/contact', '/demo', '/demo/sarana']
            or path.startswith('/demo')
            or path.startswith('/static/')
            or path.startswith('/tenant_assets/')
            or path.startswith('/k/')
            or path.startswith('/magic/')
            or path.startswith('/api/webhook/')
            or path.startswith('/api/free-slips/')
            or path.startswith('/api/photoparchi/')
            or path.startswith('/api/voice_agent/')
            or path.startswith('/api/agent/')
            or path.startswith('/cards/')
            or host == 'parchi.smartbooth.in'
            or host.startswith('parchi.')
        )

        if (
            path in ['/favicon.ico', '/login', '/logout', '/free-slips', '/parchi', '/photoparchi', '/terms', '/privacy', '/refund', '/shipping', '/delivery', '/about', '/pricing', '/contact', '/demo', '/demo/sarana']
            or path.startswith('/demo')
            or path.startswith('/logout/')
            or path.startswith('/login/')
            or path.startswith('/k/')
            or path.startswith('/magic/')
            or path.startswith('/api/free-slips/')
            or path.startswith('/api/photoparchi/')
            or path.startswith('/api/voice_agent/')
            or path.startswith('/api/agent/')
            or path.startswith('/cards/')
            or path.startswith('/static/')
            or path.startswith('/tenant_assets/')
        ):
            return None

        force_auth = os.environ.get('FORCE_AUTH_CHECK') == '1'
        is_testing = (
            not force_auth
            and (app.config.get('TESTING', False) or os.environ.get('FLASK_TESTING') == '1')
        )

        # Check authentication for protected paths
        if not is_public and not is_testing and not session.get('user_id'):
            if request.is_json or path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'लॉगिन आवश्यक है (Please login)'}), 401
            next_param = request.full_path if request.query_string else request.path
            return redirect(f"/login?next={next_param}")

        # Server-side instant session validation for all users (Prevents ghost sessions after account deletion)
        # Bypassed during test suites where mock user IDs are injected into session
        is_test_env = app.config.get('TESTING', False) or os.environ.get('FLASK_TESTING') == '1'
        uid = session.get('user_id')
        role = session.get('role')
        if uid and not is_public and not is_test_env:
            if role == 'karyakarta' and session.get('karyakarta_id'):
                try:
                    from electionapp.tenancy import resolve_tenant
                    _, check_repo = resolve_tenant()
                    if hasattr(check_repo, 'get_connection'):
                        with check_repo.get_connection() as check_conn:
                            with check_conn.cursor() as check_cur:
                                check_cur.execute(
                                    "SELECT is_active, access_token FROM karyakartas WHERE karyakarta_id = %s",
                                    (session.get('karyakarta_id'),)
                                )
                                k_check = check_cur.fetchone()
                                if not k_check or not k_check[0] or (session.get('karyakarta_token') and k_check[1] != session.get('karyakarta_token')):
                                    session.clear()
                                    if request.is_json or path.startswith('/api/'):
                                        resp = make_response(jsonify({'success': False, 'error': 'आपका खाता या लिंक निष्क्रिय कर दिया गया है (Access revoked)'}), 401)
                                    else:
                                        resp = make_response(redirect('/login?error=revoked'))
                                    resp.set_cookie('voter_tenant', '', expires=0, max_age=0, path='/', samesite='Lax')
                                    resp.set_cookie('session', '', expires=0, max_age=0, path='/', samesite='Lax')
                                    return resp
                except Exception as e:
                    logger.debug("Karyakarta instant revocation check bypassed on db error: %s", e)
            elif role != 'karyakarta':
                try:
                    import user_manager
                    is_valid, reason = user_manager.validate_user_session_active(
                        uid,
                        magic_token=session.get('magic_token'),
                        expected_hash_prefix=session.get('pw_hash')
                    )
                    if not is_valid:
                        session.clear()
                        err_msg = 'आपका खाता हटा दिया गया है या निष्क्रिय है (Account deleted or inactive)'
                        if request.is_json or path.startswith('/api/'):
                            resp = make_response(jsonify({'success': False, 'error': err_msg, 'code': reason}), 401)
                        else:
                            resp = make_response(redirect(f'/login?error={reason or "revoked"}'))
                        resp.set_cookie('voter_tenant', '', expires=0, max_age=0, path='/', samesite='Lax')
                        resp.set_cookie('session', '', expires=0, max_age=0, path='/', samesite='Lax')
                        return resp
                except Exception as e:
                    logger.debug("User session validation bypassed on db error: %s", e)

        # Resolve user role and assigned tenant
        user_role = session.get('role') or ('super_admin' if is_testing else 'operator')
        user_tenant = session.get('tenant_slug')

        # Detect any requested tenant override across args, headers, json body, and form
        req_tenant = request.args.get('tenant') or request.headers.get('X-Tenant')
        if not req_tenant and request.is_json:
            try:
                body = request.get_json(silent=True) or {}
                req_tenant = body.get('tenant') or body.get('tenant_slug')
            except Exception:
                pass
        if not req_tenant and request.form:
            req_tenant = request.form.get('tenant') or request.form.get('tenant_slug')

        # Reseller: owns a subset of campaigns, never unrestricted. Must be checked
        # BEFORE the super-admin branch below, because an unbound reseller's
        # user_tenant is '' (falsy) and would otherwise match `not user_tenant`
        # there and wrongly grant super-admin-style access to any tenant.
        if user_role == 'reseller':
            # 1. On /my-campaigns, reseller is viewing their own campaigns list (no tenant required)
            if path in ['/my-campaigns']:
                g.tenant_slug = ''
            elif path in ['/', '/hub'] and not req_tenant:
                # 2. Resellers have no global default campaign; root home redirects to their own dashboard
                return redirect('/my-campaigns')
            else:
                target = req_tenant or mapped_tenant
                # Only trust voter_tenant cookie if the reseller actually owns it
                cookie_tenant = request.cookies.get('voter_tenant')
                if not target and cookie_tenant:
                    if user_manager.reseller_owns_tenant(session.get('user_id'), cookie_tenant):
                        target = cookie_tenant
                if target:
                    target = str(target).strip().lower()
                    if not user_manager.reseller_owns_tenant(session.get('user_id'), target):
                        if request.is_json or path.startswith('/api/'):
                            return jsonify({
                                'success': False,
                                'error': f"Access denied. You do not manage campaign '{target}'."
                            }), 403
                        return redirect('/my-campaigns')
                    g.tenant_slug = target
                else:
                    g.tenant_slug = ''
        # Super Admin (or testing without tenant): Can access requested tenant or switch
        elif user_role == 'super_admin' or user_tenant == '*' or not user_tenant:
            is_admin_path = (path == '/admin' or path.startswith('/admin/'))
            if is_admin_path and not req_tenant:
                g.tenant_slug = ''
            else:
                target = req_tenant or mapped_tenant or request.cookies.get('voter_tenant') or app.config.get('TENANT_SLUG', 'ward82')
                g.tenant_slug = str(target).strip().lower()
        else:
            # Regular Operator: Strictly locked to their assigned campaign
            g.tenant_slug = str(user_tenant).strip().lower()
            if req_tenant and str(req_tenant).strip().lower() != g.tenant_slug:
                if request.is_json or path.startswith('/api/'):
                    return jsonify({
                        'success': False, 
                        'error': f"Access denied. You only have access to campaign '{g.tenant_slug}'."
                    }), 403
                from urllib.parse import urlencode, parse_qs, urlsplit, urlunsplit
                url_parts = list(urlsplit(request.full_path if request.query_string else request.path))
                query = parse_qs(url_parts[3])
                query['tenant'] = [g.tenant_slug]
                url_parts[3] = urlencode(query, doseq=True)
                correct_url = urlunsplit(url_parts)
                resp = redirect(correct_url)
                resp.set_cookie('voter_tenant', g.tenant_slug, max_age=30 * 86400, path='/', samesite='Lax')
                return resp

        # Contextual current user
        is_super = (user_role == 'super_admin' or session.get('role') == 'super_admin' or user_tenant == '*')
        assigned_booths = session.get('assigned_booths') or []
        assigned_booth = session.get('assigned_booth')
        assigned_wards = session.get('assigned_wards') or []
        assigned_villages = session.get('assigned_villages') or []
        scope_type = session.get('scope_type') or 'all'
        is_scoped = not is_super and bool(assigned_booths or assigned_booth is not None or assigned_wards or assigned_villages)

        user_allowed_modules = session.get('allowed_modules') or ['all']

        g.current_user = {
            'id': session.get('user_id', 1),
            'username': session.get('username', 'admin' if is_testing else 'guest'),
            'name': session.get('full_name', 'Administrator' if is_testing else 'User'),
            'full_name': session.get('full_name', 'Administrator' if is_testing else 'User'),
            'role': user_role,
            'role_tier': session.get('role_tier', 1 if is_super else 2),
            'tenant_slug': g.tenant_slug,
            'assigned_tenant': user_tenant,
            'assigned_booths': assigned_booths,
            'assigned_booth': assigned_booth,
            'assigned_wards': assigned_wards,
            'assigned_villages': assigned_villages,
            'scope_type': scope_type,
            'allowed_modules': user_allowed_modules,
            'is_scoped_user': is_scoped,
            'is_super_admin': is_super
        }

        # Campaign lifecycle enforcement (PAUSED / ARCHIVED / CONCLUDED)
        active_slug = getattr(g, 'tenant_slug', None)
        if active_slug and active_slug != '*':
            c_status = get_tenant_status(active_slug)
            if c_status in ('PAUSED', 'ARCHIVED'):
                is_admin_route = (
                    path in ['/admin', '/my-campaigns']
                    or path.startswith('/admin/')
                    or path.startswith('/campaign/')
                    or path.startswith('/api/admin/')
                )
                if not (is_super or (user_role == 'reseller' and is_admin_route)):
                    if request.is_json or path.startswith('/api/'):
                        return jsonify({
                            'success': False,
                            'error': f"यह चुनाव अभियान वर्तमान में रोक दिया गया है (This campaign is currently {c_status.lower()})",
                            'campaign_status': c_status
                        }), 403
                    from flask import render_template
                    branding = get_tenant_branding(active_slug)
                    return render_template(
                        'campaign_paused.html',
                        tenant_slug=active_slug,
                        branding=branding,
                        status=c_status,
                        user=g.current_user
                    ), 403

        # Check module-wise access for non-super admins
        if not is_super:
            # Explicit strict security boundary for field Karyakartas:
            # They only have access to view their assigned voters (/my-voters) and edit profile (/api/voter/profile/update).
            # No access to all voters, voting day, turnout, warroom, admin, or whatsapp dispatch.
            if user_role == 'karyakarta':
                allowed_karya_pages = ('/my-voters', '/logout')
                allowed_karya_apis = ('/api/karyakarta/my_voters', '/api/voter/profile/update', '/api/voter/offline_sync')

                if not path.startswith('/static/') and not path.startswith('/tenant_assets/'):
                    if path.startswith('/api/'):
                        if not any(path.startswith(a) for a in allowed_karya_apis):
                            return jsonify({
                                'success': False,
                                'error': 'कार्यकर्ता को केवल अपने आवंटित मतदाताओं को देखने व संपादित करने की अनुमति है (Restricted: Karyakartas can only view & edit their assigned voters)'
                            }), 403
                    else:
                        if path not in allowed_karya_pages:
                            return redirect(f"/my-voters?tenant={g.tenant_slug or ''}")

            if 'all' not in user_allowed_modules and '*' not in user_allowed_modules:
                PAGE_TO_MODULE = {
                    '/voters': 'search',
                    '/search': 'search',
                    '/my-voters': 'my_voters',
                    '/voting-day': 'voting_day',
                    '/turnout': 'voting_day',
                    '/warroom': 'warroom',
                    '/history': 'history',
                    '/caste': 'caste',
                    '/users': 'users',
                    '/karyakartas': 'karyakartas',
                    '/outside': 'outside',
                    '/campaigns/voice': 'voice',
                    '/voice': 'voice'
                }
                API_TO_MODULE = {
                    '/api/karyakarta/my_voters': 'my_voters',
                    '/api/voter/profile/update': ('search', 'my_voters'),
                    '/api/voter/offline_sync': ('search', 'my_voters'),
                    '/api/search': 'search',
                    '/api/voter': 'search',
                    '/api/voters': 'search',
                    '/api/family': 'search',
                    '/api/voters_by_part': 'search',
                    '/api/mark_voted': 'voting_day',
                    '/api/turnout_stats': 'voting_day',
                    '/api/turnout': 'voting_day',
                    '/api/vote/': 'voting_day',
                    '/api/gotv': 'warroom',
                    '/api/generate_unvoted_pdf': 'warroom',
                    '/api/history': 'history',
                    '/api/caste': 'caste',
                    '/api/karyakartas/options': 'search',
                    '/api/karyakartas': 'karyakartas',
                    '/api/karyakarta': 'karyakartas',
                    '/api/outside_voters': 'outside',
                    '/api/outside': 'outside',
                    '/api/voice': 'voice'
                }

                req_mod = PAGE_TO_MODULE.get(path)
                if not req_mod:
                    for api_prefix, mod_name in API_TO_MODULE.items():
                        if path.startswith(api_prefix):
                            req_mod = mod_name
                            break

                if req_mod:
                    if isinstance(req_mod, (list, tuple, set)):
                        has_access = any(m in user_allowed_modules for m in req_mod)
                    else:
                        has_access = (req_mod in user_allowed_modules)

                    # Karyakarta role always has full access to my_voters
                    if user_role == 'karyakarta' and req_mod == 'my_voters':
                        has_access = True

                    if not has_access:
                        if request.is_json or path.startswith('/api/') or path in ('/campaigns/voice', '/voice'):
                            return jsonify({
                                'success': False,
                                'error': f"इस मॉड्यूल का उपयोग करने की अनुमति नहीं है (Access to '{req_mod}' module is restricted)"
                            }), 403
                        if user_role == 'karyakarta':
                            return redirect(f"/my-voters?tenant={g.tenant_slug}&denied_module={req_mod}")
                        return redirect(f"/?tenant={g.tenant_slug}&denied_module={req_mod}")

    @app.after_request
    def set_tenant_cookie(response):
        path = request.path
        duration_ms = (time.time() - getattr(g, 'request_start_time', time.time())) * 1000
        response.headers['X-Request-ID'] = getattr(g, 'request_id', '')
        response.headers['X-Response-Time'] = f"{duration_ms:.1f}ms"
        if not path.startswith('/static/') and not path.startswith('/tenant_assets/'):
            logger.info("[%s] %s %s -> %s (%.1fms)", getattr(g, 'request_id', '-'), request.method, path, response.status_code, duration_ms)

        if not path.startswith('/static/') and not path.startswith('/tenant_assets/'):
            response.headers['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0, s-maxage=0'
            response.headers['CDN-Cache-Control'] = 'no-store'
            response.headers['Cloudflare-CDN-Cache-Control'] = 'no-store'
            response.headers['Surrogate-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'

        if path == '/logout' or path.startswith('/logout') or path == '/login' or path.startswith('/login') or path == '/admin' or path.startswith('/admin/'):
            return response
        target = getattr(g, 'tenant_slug', None)
        if target:
            if request.args.get('tenant') or request.cookies.get('voter_tenant') != target:
                response.set_cookie('voter_tenant', target, max_age=30 * 86400, path='/', samesite='Lax')
        return response

    _AVAILABLE_CAMPAIGNS_CACHE: Tuple[float, Tuple[list, list]] = (0.0, ([], []))
    _AVAILABLE_CAMPAIGNS_TTL = 300.0

    def get_cached_available_campaigns() -> Tuple[list, list]:
        nonlocal _AVAILABLE_CAMPAIGNS_CACHE
        now = time.time()
        last_time, cached = _AVAILABLE_CAMPAIGNS_CACHE
        if now - last_time < _AVAILABLE_CAMPAIGNS_TTL and cached and cached[0]:
            return list(cached[0]), list(cached[1])

        all_tenants = list_available_tenants()
        all_campaigns = []
        for s in all_tenants:
            try:
                t_cfg = load_tenant_config(s)
            except Exception:
                t_cfg = {}
            meta = format_campaign_for_display(s, t_cfg)
            all_campaigns.append(meta)
        all_campaigns.sort(key=lambda x: x['name'])
        _AVAILABLE_CAMPAIGNS_CACHE = (now, (all_tenants, all_campaigns))
        return list(all_tenants), list(all_campaigns)

    # 4. Template context processor: inject campaign, CAMPAIGN_CONFIG, current_tenant, and core mode
    @app.context_processor
    def inject_campaign_context():
        host = request.host.split(':')[0].lower()
        mapped_tenant = HOST_TENANT_MAP.get(host)
        is_reseller = (session.get('role') == 'reseller')
        owned_campaigns = []
        is_admin_path = (request.path == '/admin' or request.path.startswith('/admin/'))
        if is_reseller:
            user_id = session.get('user_id')
            if user_id:
                owned_campaigns = user_manager.get_owned_tenants(user_id)
            if not getattr(g, 'tenant_slug', None):
                active_slug = ''
                cfg = {
                    'tenant_slug': '',
                    'candidate': {'name_hi': '', 'name_en': ''},
                    'election': {'ward_no': '', 'ward_name_hi': 'रीसेलर पोर्टल', 'ward_name_en': 'Reseller Portal', 'app_title': 'रीसेलर कमान केंद्र (Reseller Command Center)', 'portal_subtitle': 'Smart Booth Reseller Platform'},
                    'party': {},
                    'campaign_message': ''
                }
            else:
                active_slug = str(getattr(g, 'tenant_slug', '')).strip().lower()
                try:
                    cfg = load_tenant_config(active_slug)
                except Exception as e:
                    cfg = {
                        'tenant_slug': active_slug,
                        'candidate': {'name_hi': active_slug, 'name_en': active_slug},
                        'election': {'ward_no': '', 'ward_name_hi': active_slug, 'ward_name_en': active_slug, 'app_title': 'Smart Booth'},
                        'party': {}
                    }
        else:
            if is_admin_path and not request.args.get('tenant'):
                active_slug = ''
                cfg = {
                    'tenant_slug': '',
                    'candidate': {'name_hi': '', 'name_en': ''},
                    'election': {'ward_no': '', 'ward_name_hi': 'कमान केंद्र', 'ward_name_en': 'Control Centre', 'app_title': 'Smart Booth Control Centre', 'portal_subtitle': 'समस्त अभियान कमान केंद्र (Super Admin Fleet)'},
                    'party': {},
                    'campaign_message': ''
                }
            else:
                active_slug = getattr(g, 'tenant_slug', None) or mapped_tenant or request.args.get('tenant') or request.cookies.get('voter_tenant') or tenant_slug
                active_slug = str(active_slug).strip().lower()
                try:
                    cfg = load_tenant_config(active_slug)
                except Exception as e:
                    logger.error("Failed to load campaign config for tenant '%s': %s", active_slug, e)
                    if active_slug == tenant_slug:
                        cfg = campaign_config
                    else:
                        cfg = {
                            'tenant_slug': active_slug,
                            'candidate': {'name_hi': active_slug, 'name_en': active_slug},
                            'election': {'ward_no': '', 'ward_name_hi': active_slug, 'ward_name_en': active_slug, 'app_title': 'Smart Booth'},
                            'party': {}
                        }

        # Dynamically fetch booths from database if configured booths is empty
        cfg_booths = dict(cfg.get('booths') or {})
        if not cfg_booths and repo and hasattr(repo, 'get_booths_dict'):
            try:
                booths = repo.get_booths_dict(active_slug) or {}
            except Exception:
                booths = {}
        else:
            booths = cfg_booths

        # Wards & physical polling booth hierarchy.
        # Local body campaigns (Gram Panchayat, Zila Parishad, Panchayat Samiti, Municipal)
        # where electors are organized by ward get the multi-ward dropdown and booth hierarchy.
        # Assembly seats (Vidhan Sabha like Sanchore) and Parliamentary seats are strictly Booth-based.
        # Memoized with 5-minute TTL to avoid scanning the entire partition on every render.
        wards, booths_with_wards = get_cached_ward_hierarchy(repo, active_slug, cfg)
        is_ward_roll = is_ward_campaign(cfg)

        cfg_copy = dict(cfg)
        cfg_copy['booths'] = booths
        cfg_copy['wards'] = wards
        cfg_copy['booths_with_wards'] = booths_with_wards
        cfg_copy['is_panchayat'] = bool(is_ward_roll)
        cfg_copy['is_ward_election'] = bool(is_ward_roll)

        is_super = (session.get('role') == 'super_admin' or session.get('tenant_slug') == '*')
        user = getattr(g, 'current_user', {
            'id': session.get('user_id', 1),
            'username': session.get('username', 'admin'),
            'name': session.get('full_name', 'Administrator'),
            'role': session.get('role', 'super_admin'),
            'tenant_slug': active_slug,
            'assigned_tenant': session.get('tenant_slug', '*'),
            'is_super_admin': is_super
        })

        if is_reseller:
            filtered_tenants = [str(t.get('campaign_slug', '')).strip().lower() for t in owned_campaigns if t.get('campaign_slug')]
            filtered_campaigns = []
            for t in owned_campaigns:
                s = str(t.get('campaign_slug', '')).strip().lower()
                if not s:
                    continue
                try:
                    t_cfg = load_tenant_config(s)
                except Exception:
                    t_cfg = {}
                meta = format_campaign_for_display(s, t_cfg, fallback_candidate=t.get('candidate_name', ''))
                filtered_campaigns.append(meta)
            filtered_campaigns.sort(key=lambda x: x['name'])
        else:
            filtered_tenants, filtered_campaigns = get_cached_available_campaigns()

        return {
            'campaign': cfg_copy,
            'CAMPAIGN': cfg_copy,
            'CAMPAIGN_CONFIG': cfg_copy,
            'current_tenant': active_slug,
            'tenant_slug': active_slug,
            'booths': booths,
            'BOOTHS': booths,
            'wards': wards,
            'WARDS': wards,
            'booths_with_wards': booths_with_wards,
            'BOOTHS_WITH_WARDS': booths_with_wards,
            'IS_WARD_ELECTION': bool(is_ward_roll),
            'default_campaign_msg': cfg_copy.get('campaign_message', ''),
            'current_user': user,
            'CORE_MODE': app.config.get('CORE_MODE', False),
            'ENABLE_VOICE_MODULE': app.config.get('ENABLE_VOICE_MODULE', False),
            'available_tenants': filtered_tenants,
            'available_campaigns': filtered_campaigns,
            'owned_campaigns': owned_campaigns,
            'is_reseller': is_reseller,
            'APP_VERSION': app.config.get('APP_VERSION', APP_VERSION)
        }

    # 5. Static route for tenant assets
    @app.route('/tenant_assets/<path:filename>')
    def serve_tenant_asset(filename: str):
        target_slug = request.args.get('tenant') or getattr(g, 'tenant_slug', None) or tenant_slug
        target_slug = str(target_slug).strip().lower()
        if target_slug not in list_available_tenants():
            return jsonify({'error': f"Tenant '{target_slug}' not found"}), 404

        # If S3 is active, redirect to the cloud-hosted S3/CDN asset directly
        from electionapp import s3_storage
        if s3_storage.S3_ENABLED and s3_storage.S3_BUCKET:
            return redirect(s3_storage.build_s3_url(f"tenants/{target_slug}/{filename}"), code=302)

        # Check persistent git-ignored data/<slug>/assets first
        persistent_dir = os.path.join(PLATFORM_ROOT, 'data', target_slug, 'assets')
        if os.path.isfile(os.path.join(persistent_dir, filename)):
            resp = send_from_directory(persistent_dir, filename, max_age=604800)
            resp.headers['Cache-Control'] = 'public, max-age=604800, stale-while-revalidate=86400'
            return resp

        assets_dir = get_tenant_assets_dir(target_slug)
        resp = send_from_directory(assets_dir, filename, max_age=604800)
        resp.headers['Cache-Control'] = 'public, max-age=604800, stale-while-revalidate=86400'
        return resp

    # 6. Register modular blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(core_bp)
    app.register_blueprint(voters_bp)
    app.register_blueprint(search_bp)
    app.register_blueprint(warroom_bp)
    app.register_blueprint(dispatch_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(slip_bp)
    app.register_blueprint(campaign_bp)
    app.register_blueprint(turnout_bp)
    app.register_blueprint(karyakartas_bp)
    app.register_blueprint(caste_bp)
    app.register_blueprint(family_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(free_slips_bp)
    app.register_blueprint(voice_bp)
    app.register_blueprint(voice_agent_bp)
    app.register_blueprint(agent_bp)

    @app.route('/demo')
    @app.route('/demo/sarana')
    def public_buildathon_demo():
        return render_template('demo_console.html')

    @app.route('/favicon.ico')
    def favicon():
        return ('', 204)

    @app.context_processor
    def inject_symbol_catalog():
        from electionapp.symbol_catalog import (
            SARPANCH_SYMBOLS,
            WARD_PANCH_SYMBOLS,
            POPULAR_FREE_SYMBOLS,
            POLITICAL_PARTY_SYMBOLS,
        )
        return {
            'sarpanch_symbols': SARPANCH_SYMBOLS,
            'ward_panch_symbols': WARD_PANCH_SYMBOLS,
            'popular_symbols': POPULAR_FREE_SYMBOLS,
            'party_symbols': POLITICAL_PARTY_SYMBOLS,
        }

    @app.errorhandler(Exception)
    def handle_global_exception(e):
        from werkzeug.exceptions import HTTPException
        if request.path.startswith('/api/'):
            status_code = e.code if isinstance(e, HTTPException) else 500
            err_msg = e.description if isinstance(e, HTTPException) else str(e)
            app.logger.exception("API exception on %s: %s", request.path, e)
            return jsonify({
                'success': False,
                'error': err_msg or 'Internal Server Error'
            }), status_code
        if isinstance(e, HTTPException):
            return e
        app.logger.exception("Web request exception on %s: %s", request.path, e)
        return "Internal Server Error", 500

    return app
