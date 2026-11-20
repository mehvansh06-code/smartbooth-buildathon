"""
auth.py
Authentication Blueprint for Unified Multi-Tenant Election Platform:
- /login   (GET, POST) — Session-based authentication with tenant binding
- /logout  (GET, POST) — Terminate session & clear cookies
- /api/users/list      — List users (scoped for tenant or fleet-wide for super_admin)
- /api/users/create    — Create campaign-specific user account
- /api/users/reset_password — Reset password for user
- /api/users/toggle_status  — Enable / disable user account
"""

import os
import re
import time
import threading
import logging
from functools import wraps
from typing import Optional, List, Dict, Any
from flask import Blueprint, render_template, request, redirect, session, jsonify, g, current_app

import user_manager
import psycopg2.extras
import meta_whatsapp
from electionapp import whatsapp_quota
from electionapp.audit import audit_action
from electionapp.config import load_tenant_config, list_available_tenants, get_tenant_status
from electionapp.tenant_branding import get_tenant_branding

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

_SLUG_RE = re.compile(r'^[a-z0-9_-]{1,64}$')


def _safe_tenant_slug(raw: Optional[str]) -> Optional[str]:
    """Normalises a ?tenant= value, or None if it is not a real campaign.

    Validated against the provisioned campaigns so an arbitrary value can never be
    reflected back into a redirect target or rendered page.
    """
    if not raw:
        return None
    slug = str(raw).strip().lower()
    if not _SLUG_RE.match(slug):
        return None
    try:
        if slug not in list_available_tenants():
            return None
    except Exception:
        return None
    return slug

_LOGIN_ATTEMPTS = {}
_LOGIN_LOCK = threading.Lock()
MAX_LOGIN_ATTEMPTS = 15
LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        host = (request.host or '').split(':')[0].lower()
        is_buildathon = (
            'build.smartbooth.in' in host
            or host == '8.231.107.200'
            or os.environ.get('DISABLE_AUTH') == '1'
        )
        if is_buildathon:
            return f(*args, **kwargs)
        if not session.get('user_id'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'लॉगिन आवश्यक है (Authentication required)'}), 401
            return redirect(f"/login?next={request.path}")
        return f(*args, **kwargs)
    return decorated_function


def super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        host = (request.host or '').split(':')[0].lower()
        is_buildathon = (
            'build.smartbooth.in' in host
            or host == '8.231.107.200'
            or os.environ.get('DISABLE_AUTH') == '1'
        )
        if is_buildathon:
            return f(*args, **kwargs)
        force_auth = os.environ.get('FORCE_AUTH_CHECK') == '1'
        is_testing = (
            not force_auth
            and (current_app.config.get('TESTING', False) or os.environ.get('FLASK_TESTING') == '1')
        )
        if not is_testing and not session.get('user_id'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'लॉगिन आवश्यक है (Authentication required)'}), 401
            return redirect(f"/login?next={request.path}")
        if not is_testing and session.get('role') != 'super_admin':
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Super Admin access required)'}), 403
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route('/login', methods=['GET', 'POST'])
@audit_action('auth.login', entity_type='user')
def login():
    host = (request.host or '').split(':')[0].lower()
    is_buildathon = (
        'build.smartbooth.in' in host
        or host == '8.231.107.200'
        or os.environ.get('DISABLE_AUTH') == '1'
    )
    if is_buildathon and request.method == 'GET':
        req_tenant = _safe_tenant_slug(request.args.get('tenant')) or 'sarana'
        next_url = request.args.get('next')
        if next_url and not next_url.startswith('/login'):
            return redirect(next_url)
        return redirect(f"/?tenant={req_tenant}")
    # If switch=1 or force=1 is requested, clear previous session for clean login
    req_tenant = _safe_tenant_slug(request.args.get('tenant'))
    sess_tenant = str(session.get('tenant_slug') or '').strip().lower()
    if request.args.get('switch') == '1' or request.args.get('force') == '1':
        session.clear()
    elif req_tenant and sess_tenant and sess_tenant != '*' and sess_tenant != req_tenant:
        # Opening the login page for a different campaign is an account switch, so the
        # previous operator's session must not survive it. Super admins (tenant '*')
        # legitimately span campaigns and are left alone.
        session.clear()

    err_param = request.args.get('error')
    if err_param in ('account_revoked', 'deleted'):
        error = "आपका खाता हटा दिया गया है। कृपया अपने चुनाव कार्यालय से संपर्क करें (Your account has been deleted)."
    elif err_param == 'inactive':
        error = "यह खाता या चुनाव अभियान निष्क्रिय है (Account or campaign is inactive)."
    elif err_param == 'password_changed':
        error = "पासवर्ड बदल दिया गया है। कृपया नए पासवर्ड से पुनः लॉगिन करें (Password changed. Please login again)."
    elif err_param == 'token_revoked':
        error = "लॉगिन लिंक निरस्त कर दिया गया है (Login link has been revoked)."
    elif err_param == 'revoked':
        error = "आपका लॉगिन सत्र समाप्त या निरस्त कर दिया गया है (Session revoked)."
    else:
        error = None

    if request.method == 'POST':
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '127.0.0.1').split(',')[0].strip()
        now = time.time()

        if os.environ.get('FLASK_TESTING') != '1':
            with _LOGIN_LOCK:
                history = _LOGIN_ATTEMPTS.get(client_ip, [])
                history = [t for t in history if now - t < LOCKOUT_WINDOW_SECONDS]
                if len(history) >= MAX_LOGIN_ATTEMPTS:
                    retry_after = int(LOCKOUT_WINDOW_SECONDS - (now - history[0]))
                    if request.is_json:
                        return jsonify({
                            'success': False,
                            'error': f'अत्यधिक विफल प्रयास। कृपया {max(1, retry_after)} सेकंड बाद पुनः प्रयास करें (Too many failed login attempts. Please try again later.)'
                        }), 429
                    return render_template(
                        'login.html',
                        error='अत्यधिक विफल प्रयास। कृपया 5 मिनट बाद पुनः प्रयास करें (Too many login attempts. Please try again later.)',
                        next_url=request.args.get('next', '')
                    ), 429
                _LOGIN_ATTEMPTS[client_ip] = history

        data = request.get_json(silent=True) or request.form or {}
        username = str(data.get('username') or '').strip()
        password = str(data.get('password') or '').strip()

        user, err = user_manager.authenticate_user(username, password)
        if not user or err:
            with _LOGIN_LOCK:
                attempts = _LOGIN_ATTEMPTS.get(client_ip, [])
                attempts.append(now)
                _LOGIN_ATTEMPTS[client_ip] = attempts
            error = err or "अमान्य यूज़रनेम या पासवर्ड (Invalid username or password)"
            if request.is_json:
                return jsonify({'success': False, 'error': error}), 401
        else:
            target_tenant = str(user.get('tenant_slug') or '').strip().lower()
            if user.get('role') not in ('super_admin', 'reseller') and target_tenant and target_tenant != '*':
                c_status = get_tenant_status(target_tenant)
                if c_status in ('PAUSED', 'ARCHIVED'):
                    status_lbl = 'स्थगित (Paused)' if c_status == 'PAUSED' else 'पुरालेख (Archived)'
                    err_msg = f"यह चुनाव अभियान ({target_tenant}) वर्तमान में {status_lbl} है। अधिक जानकारी के लिए अपने चुनाव व्यवस्थापक से संपर्क करें।"
                    if request.is_json:
                        return jsonify({'success': False, 'error': err_msg, 'campaign_status': c_status}), 403
                    return render_template(
                        'login.html',
                        error=err_msg,
                        next_url=request.args.get('next', '')
                    ), 403

            with _LOGIN_LOCK:
                _LOGIN_ATTEMPTS.pop(client_ip, None)
            session.clear()
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            session['role_tier'] = user.get('role_tier', 2)
            session['tenant_slug'] = user.get('tenant_slug') or ''
            session['pw_hash'] = str(user.get('password_hash') or '')[:10]
            session['assigned_booths'] = user.get('assigned_booths') or []
            session['assigned_booth'] = user.get('assigned_booth')
            session['assigned_wards'] = user.get('assigned_wards') or []
            session['assigned_villages'] = user.get('assigned_villages') or []
            if user.get('role') == 'karyakarta':
                session['scope_type'] = 'assigned_voters'
                session['allowed_modules'] = ['my_voters']
            else:
                session['scope_type'] = user.get('scope_type') or 'all'
                session['allowed_modules'] = user.get('allowed_modules') or ['all']

            target_tenant = user.get('tenant_slug') or ''
            if user['role'] == 'reseller':
                next_url = "/my-campaigns"
                target_tenant = request.cookies.get('voter_tenant') or ''
            elif user['role'] == 'karyakarta':
                target_tenant = str(user.get('tenant_slug') or '').strip().lower()
                next_url = f"/my-voters?tenant={target_tenant}" if target_tenant else "/my-voters"
            elif user['role'] == 'super_admin' or target_tenant == '*':
                req_tenant = request.args.get('tenant') or (request.form.get('tenant') if not request.is_json else None)
                next_url = request.args.get('next') or request.form.get('next')
                if next_url and next_url not in ['/', '']:
                    target_tenant = req_tenant or request.cookies.get('voter_tenant') or ''
                elif req_tenant:
                    target_tenant = req_tenant
                    next_url = f"/?tenant={target_tenant}"
                else:
                    # Super admin defaults directly to Fleet Admin Command Center
                    next_url = "/admin"
                    target_tenant = request.cookies.get('voter_tenant') or ''
            else:
                # Force regular operators and campaign admins to their assigned campaign
                target_tenant = str(user.get('tenant_slug') or '').strip().lower()
                next_url = f"/?tenant={target_tenant}" if target_tenant else "/"

            if request.is_json:
                resp = jsonify({'success': True, 'redirect': next_url, 'user': user})
                if target_tenant:
                    resp.set_cookie('voter_tenant', str(target_tenant).strip().lower(), max_age=30 * 86400, path='/', samesite='Lax')
                return resp

            resp = redirect(next_url)
            if target_tenant:
                resp.set_cookie('voter_tenant', str(target_tenant).strip().lower(), max_age=30 * 86400, path='/', samesite='Lax')
            return resp

    return render_template(
        'login.html',
        error=error,
        next_url=request.args.get('next', '')
    )


@auth_bp.route('/logout', methods=['GET', 'POST'])
@audit_action('auth.logout', entity_type='user')
def logout():
    # Keep an explicitly requested campaign across the logout, so the login page comes
    # back scoped to the campaign the user is switching to.
    target_tenant = _safe_tenant_slug(request.args.get('tenant'))
    session.clear()
    resp = redirect(f"/login?tenant={target_tenant}" if target_tenant else '/login')
    resp.set_cookie('voter_tenant', '', expires=0, max_age=0, path='/', samesite='Lax')
    resp.set_cookie('session', '', expires=0, max_age=0, path='/', samesite='Lax')
    resp.delete_cookie('voter_tenant', path='/')
    resp.delete_cookie('session', path='/')
    return resp


@auth_bp.route('/k/<token>', methods=['GET'])
@auth_bp.route('/magic/<token>', methods=['GET'])
def magic_login(token: str):
    """Zero-password 1-click magic link authentication for village field workers (Karyakartas / Panna Pramukhs).

    1. Idempotent GET: safe to load multiple times (and safe from WhatsApp preview crawler).
    2. Validates token against karyakartas table and checks is_active.
    3. Scopes session strictly to karyakarta role (Tier 4) and assigned booth/voters.
    4. Sets 60-day persistent cookie and redirects straight to /my-voters?tenant=<slug>.
    """
    clean_tok = str(token or '').strip()
    if not clean_tok or len(clean_tok) < 16 or len(clean_tok) > 64:
        return render_template(
            'login.html',
            error="लॉगिन लिंक अमान्य या समाप्त हो चुका है (Invalid or expired login link). कृपया अपने चुनाव कार्यालय से संपर्क करें。"
        ), 404

    # 1. First check users table (Campaign Admin, Candidate Admin, Operator, Reseller)
    import user_manager
    user = user_manager.get_user_by_magic_token(clean_tok)
    if user and user.get('is_active', 1):
        slug = str(user.get('tenant_slug') or '').strip().lower()
        uid = int(user['user_id'])
        role = user.get('role', 'admin')
        role_tier = int(user.get('role_tier') or 2)

        if role not in ('super_admin', 'reseller') and slug and slug != '*':
            c_status = get_tenant_status(slug)
            if c_status in ('PAUSED', 'ARCHIVED'):
                branding = get_tenant_branding(slug)
                return render_template(
                    'campaign_paused.html',
                    tenant_slug=slug,
                    branding=branding,
                    status=c_status
                ), 403

        session.clear()
        session.permanent = True
        session['user_id'] = uid
        session['username'] = user.get('username') or f"user_{uid}"
        session['full_name'] = user.get('full_name') or user.get('username') or ''
        session['phone'] = user.get('phone') or ''
        session['role'] = role
        session['role_tier'] = role_tier
        session['tenant_slug'] = slug
        session['pw_hash'] = str(user.get('password_hash') or '')[:10]
        session['assigned_booths'] = list(user.get('assigned_booths') or [])
        session['assigned_booth'] = user.get('assigned_booth')
        session['assigned_wards'] = list(user.get('assigned_wards') or [])
        session['assigned_villages'] = list(user.get('assigned_villages') or [])
        if role == 'karyakarta':
            session['scope_type'] = 'assigned_voters'
            session['allowed_modules'] = ['my_voters']
        else:
            session['scope_type'] = user.get('scope_type') or 'all'
            session['allowed_modules'] = list(user.get('allowed_modules') or ['all'])
        session['magic_token'] = clean_tok

        next_req = request.args.get('next')
        if next_req and next_req.startswith('/'):
            target_url = f"{next_req}?tenant={slug}" if ('?' not in next_req and slug and slug != '*') else next_req
        elif role == 'reseller':
            target_url = "/my-campaigns"
        elif role == 'karyakarta':
            target_url = f"/my-voters?tenant={slug}" if slug and slug != '*' else "/my-voters"
        elif slug and slug != '*':
            target_url = f"/search?tenant={slug}"
        else:
            target_url = "/search"

        resp = redirect(target_url)
        if slug and slug != '*':
            resp.set_cookie('voter_tenant', slug, max_age=60 * 86400, path='/', samesite='Lax')
        return resp

    # 2. Check karyakartas table (Panna Pramukh / Field Worker)
    from electionapp.tenancy import resolve_tenant
    _, repo = resolve_tenant()
    if not hasattr(repo, 'get_connection'):
        return render_template('login.html', error="डेटाबेस सेवा उपलब्ध नहीं है (Database service unavailable)"), 503

    k_row = None
    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    k.karyakarta_id,
                    k.tenant_id,
                    k.name,
                    k.phone,
                    COALESCE(k.part_no, 0) as part_no,
                    k.role,
                    k.access_token,
                    COALESCE(k.is_active, TRUE) as is_active,
                    t.campaign_slug
                FROM karyakartas k
                JOIN tenants t ON t.tenant_id = k.tenant_id
                WHERE k.access_token = %s
            """, (clean_tok,))
            k_row = cur.fetchone()
            if k_row and k_row.get('is_active'):
                # Update last accessed timestamp
                try:
                    cur.execute("UPDATE karyakartas SET last_accessed_at = NOW() WHERE karyakarta_id = %s", (k_row['karyakarta_id'],))
                    conn.commit()
                except Exception:
                    pass

    if not k_row or not k_row.get('is_active'):
        return render_template(
            'login.html',
            error="यह लॉगिन लिंक अमान्य या निष्क्रिय कर दिया गया है (This login link is invalid or deactivated). कृपया अपने चुनाव कार्यालय से संपर्क करें।"
        ), 404

    slug = str(k_row.get('campaign_slug') or '').strip().lower()
    kid = int(k_row['karyakarta_id'])
    booth_no = int(k_row.get('part_no') or 0)

    if slug and slug != '*':
        c_status = get_tenant_status(slug)
        if c_status in ('PAUSED', 'ARCHIVED'):
            branding = get_tenant_branding(slug)
            return render_template(
                'campaign_paused.html',
                tenant_slug=slug,
                branding=branding,
                status=c_status
            ), 403

    # Establish authenticated session
    session.clear()
    session.permanent = True
    session['user_id'] = kid
    session['karyakarta_id'] = kid
    session['username'] = f"karya_{kid}"
    session['full_name'] = k_row['name']
    session['phone'] = k_row.get('phone') or ''
    session['role'] = 'karyakarta'
    session['role_tier'] = 4
    session['tenant_slug'] = slug
    session['assigned_booths'] = [booth_no] if booth_no else []
    session['assigned_booth'] = booth_no if booth_no else None
    session['assigned_wards'] = []
    session['assigned_villages'] = []
    session['scope_type'] = 'assigned_voters'
    session['allowed_modules'] = ['my_voters']
    session['karyakarta_token'] = clean_tok

    target_url = f"/my-voters?tenant={slug}"
    resp = redirect(target_url)
    resp.set_cookie('voter_tenant', slug, max_age=60 * 86400, path='/', samesite='Lax')
    return resp


def operator_or_super_admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        force_auth = os.environ.get('FORCE_AUTH_CHECK') == '1'
        is_testing = (
            not force_auth
            and (current_app.config.get('TESTING', False) or os.environ.get('FLASK_TESTING') == '1')
        )
        if not is_testing and not session.get('user_id'):
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'लॉगिन आवश्यक है (Authentication required)'}), 401
            return redirect(f"/login?next={request.path}")
        user_role = session.get('role', 'super_admin' if is_testing else 'guest')
        if not is_testing and user_role not in ['super_admin', 'admin', 'operator', 'reseller']:
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Operator or Admin access required)'}), 403
            return redirect('/')
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# User & Sub-Account Management APIs (Super Admin & Campaign Operators)
# ---------------------------------------------------------------------------

def _reseller_tenant_guard(active_tenant: Optional[str]):
    """Defense-in-depth re-check for reseller sub-account actions below.

    The tenancy middleware already validates `?tenant=` against
    `reseller_owns_tenant` before setting g.tenant_slug, but an unbound
    reseller (no campaign selected) formats to tenant_slug == '' — and so does
    every OTHER unbound reseller. Without this guard, an empty active_tenant
    would either fall through to list_users('')'s global "every tenant"
    branch, or let one unbound reseller's '' match another's '' in the
    plain string-equality ownership checks used below, letting them edit
    each other's accounts. Always re-verify real ownership here.
    """
    if session.get('role') != 'reseller':
        return None
    if not active_tenant or not user_manager.reseller_owns_tenant(session.get('user_id'), active_tenant):
        return jsonify({'success': False, 'error': 'कोई अधिकृत अभियान चयनित नहीं (No owned campaign selected)'}), 400
    return None


def _can_manage_user(target_user: Optional[Dict[str, Any]]) -> bool:
    """Verifies whether the current session can view/edit/delete the given user."""
    if not target_user:
        return False
    if session.get('role') == 'super_admin' or session.get('tenant_slug') == '*':
        return True
    if target_user.get('role') in ('super_admin', 'reseller'):
        return False
    if session.get('role') == 'reseller':
        slug = target_user.get('tenant_slug')
        return bool(slug and user_manager.reseller_owns_tenant(session.get('user_id'), slug))
    active_tenant = getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
    return str(target_user.get('tenant_slug', '')).strip().lower() == str(active_tenant).strip().lower()


@auth_bp.route('/api/campaign/scope_options', methods=['GET'])
@operator_or_super_admin_required
def api_campaign_scope_options():
    """Returns available polling booths, wards, and villages for active campaign."""
    repo = current_app.extensions.get('repo')
    tenant = getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
    tenant = str(tenant).strip().lower()

    booths = []
    wards = []
    villages = []
    if repo:
        try:
            if hasattr(repo, 'get_parts'):
                booths = repo.get_parts(tenant)
        except Exception:
            pass
        try:
            if hasattr(repo, 'get_wards'):
                wards = repo.get_wards(tenant)
        except Exception:
            pass
        try:
            if hasattr(repo, 'get_villages'):
                villages = repo.get_villages(tenant)
        except Exception:
            pass

    quota = user_manager.get_sub_account_quota(tenant)
    from electionapp import tenant_branding
    tb = tenant_branding.get_tenant_branding(tenant) or {}
    voting_method = tb.get('voting_method', 'EVM')
    election_type = tb.get('election_type', 'MUNICIPAL')
    is_multi_gp = bool(tb.get('is_multi_gp'))
    panchayats = tb.get('panchayats') or []

    return jsonify({
        'success': True,
        'tenant': tenant,
        'booths': booths,
        'wards': wards,
        'villages': villages,
        'quota': quota,
        'voting_method': voting_method,
        'election_type': election_type,
        'is_multi_gp': is_multi_gp,
        'panchayats': panchayats
    })


@auth_bp.route('/api/campaign/sub_account_quota', methods=['GET'])
@operator_or_super_admin_required
def api_campaign_sub_account_quota():
    """Returns current sub-account quota and count for active campaign."""
    tenant = getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
    quota = user_manager.get_sub_account_quota(str(tenant).strip().lower())
    return jsonify({'success': True, 'quota': quota})


@auth_bp.route('/api/users/list', methods=['GET'])
@operator_or_super_admin_required
def api_users_list():
    is_super = (session.get('role') == 'super_admin' or session.get('tenant_slug') == '*')
    if is_super:
        req_tenant = request.args.get('tenant') or request.args.get('tenant_slug')
        users = user_manager.list_users(req_tenant)
        quota = user_manager.get_sub_account_quota(req_tenant) if req_tenant else None
    elif session.get('role') == 'reseller':
        req_tenant = request.args.get('tenant') or request.args.get('tenant_slug') or getattr(g, 'tenant_slug', None)
        if req_tenant and not user_manager.reseller_owns_tenant(session.get('user_id'), req_tenant):
            return jsonify({'success': False, 'error': 'Access denied'}), 403
        users = user_manager.list_users_for_reseller(session.get('user_id'), req_tenant)
        quota = user_manager.get_sub_account_quota(req_tenant) if req_tenant else None
    else:
        active_tenant = getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
        guard = _reseller_tenant_guard(active_tenant)
        if guard:
            return guard
        users = user_manager.list_users(active_tenant, exclude_super_admin=True)
        quota = user_manager.get_sub_account_quota(active_tenant)

    return jsonify({'success': True, 'users': users, 'quota': quota})


@auth_bp.route('/api/users/create', methods=['POST'])
@audit_action('user.create', entity_type='user')
@operator_or_super_admin_required
def api_users_create():
    data = request.get_json(silent=True) or request.form or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '').strip()
    full_name = str(data.get('full_name') or username).strip()
    phone = str(data.get('phone') or '').strip()
    role = str(data.get('role') or 'booth_agent').strip().lower()
    
    is_super = (session.get('role') == 'super_admin' or session.get('tenant_slug') == '*')
    if is_super:
        tenant_slug = str(data.get('tenant_slug') or getattr(g, 'tenant_slug', '') or '').strip().lower()
        enforce_quota = False
    elif session.get('role') == 'reseller':
        tenant_slug = str(data.get('tenant_slug') or getattr(g, 'tenant_slug', '')).strip().lower()
        if not tenant_slug or not user_manager.reseller_owns_tenant(session.get('user_id'), tenant_slug):
            return jsonify({'success': False, 'error': 'अधिकृत अभियान अमान्य है या आपका स्वामित्व नहीं है (Access denied for this campaign)'}), 403
        enforce_quota = True
        if role in ['super_admin', 'reseller']:
            return jsonify({
                'success': False,
                'error': 'रीसेलर सुपर एडमिन या नया रीसेलर नहीं बना सकते (Cannot create super_admin or reseller)'
            }), 403
    else:
        # Campaign operators are strictly locked to their active campaign
        tenant_slug = getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
        tenant_slug = str(tenant_slug).strip().lower()
        enforce_quota = True

        guard = _reseller_tenant_guard(tenant_slug)
        if guard:
            return guard

        # Campaign operators can ONLY create sub-accounts (booth_agent, karyakarta, viewer, operator)
        if role in ['super_admin', 'admin', 'candidate_admin', 'reseller']:
            return jsonify({
                'success': False,
                'error': 'अभियान ऑपरेटर केवल सब-अकाउंट (बूथ एजेंट, कार्यकर्ता, दर्शक) ही बना सकते हैं (Only sub-accounts permitted)'
            }), 403

    # Assigned scope parsing
    scope_type = str(data.get('scope_type') or 'all').strip().lower()
    
    raw_booths = data.get('assigned_booths')
    assigned_booths = []
    if isinstance(raw_booths, list):
        for b in raw_booths:
            try:
                assigned_booths.append(int(b))
            except (ValueError, TypeError):
                pass
    elif raw_booths and str(raw_booths).isdigit():
        assigned_booths = [int(raw_booths)]

    assigned_booth = data.get('assigned_booth')
    if assigned_booth is not None and str(assigned_booth).isdigit():
        assigned_booth = int(assigned_booth)
        if assigned_booth not in assigned_booths:
            assigned_booths.insert(0, assigned_booth)
    elif assigned_booths:
        assigned_booth = assigned_booths[0]
    else:
        assigned_booth = None

    raw_wards = data.get('assigned_wards')
    assigned_wards = []
    if isinstance(raw_wards, list):
        for w in raw_wards:
            try:
                assigned_wards.append(int(w))
            except (ValueError, TypeError):
                pass
    elif raw_wards and str(raw_wards).isdigit():
        assigned_wards = [int(raw_wards)]

    raw_villages = data.get('assigned_villages')
    assigned_villages = []
    if isinstance(raw_villages, list):
        assigned_villages = [str(v).strip() for v in raw_villages if str(v).strip()]
    elif isinstance(raw_villages, str) and raw_villages.strip():
        assigned_villages = [str(v).strip() for v in raw_villages.split(',') if str(v).strip()]

    raw_modules = data.get('allowed_modules')
    allowed_modules = None
    if isinstance(raw_modules, list):
        allowed_modules = [str(m).strip().lower() for m in raw_modules if str(m).strip()]
    elif isinstance(raw_modules, str) and raw_modules.strip():
        allowed_modules = [str(m).strip().lower() for m in raw_modules.split(',') if str(m).strip()]

    raw_karya_id = data.get('karyakarta_id')
    karyakarta_id = None
    if raw_karya_id is not None and str(raw_karya_id).isdigit():
        karyakarta_id = int(raw_karya_id)

    parent_user_id = session.get('user_id')

    ok, msg, uid = user_manager.create_user_account(
        username=username,
        password=password,
        full_name=full_name,
        phone=phone,
        role=role,
        tenant_slug=tenant_slug,
        assigned_booth=assigned_booth,
        assigned_booths=assigned_booths,
        assigned_wards=assigned_wards,
        assigned_villages=assigned_villages,
        scope_type=scope_type,
        allowed_modules=allowed_modules,
        parent_user_id=parent_user_id,
        karyakarta_id=karyakarta_id,
        enforce_quota=enforce_quota
    )

    if ok:
        quota = user_manager.get_sub_account_quota(tenant_slug)
        return jsonify({'success': True, 'message': msg, 'user_id': uid, 'quota': quota}), 201
    return jsonify({'success': False, 'error': msg}), 400


@auth_bp.route('/api/users/<int:user_id>/update_modules', methods=['POST'])
@operator_or_super_admin_required
def api_users_update_modules(user_id: int):
    """Updates allowed modules for a sub-account."""
    data = request.get_json(silent=True) or request.form or {}
    raw_modules = data.get('allowed_modules')
    if raw_modules is None:
        return jsonify({'success': False, 'error': 'allowed_modules parameter is required'}), 400

    if isinstance(raw_modules, list):
        allowed_modules = [str(m).strip().lower() for m in raw_modules if str(m).strip()]
    elif isinstance(raw_modules, str) and raw_modules.strip():
        allowed_modules = [str(m).strip().lower() for m in raw_modules.split(',') if str(m).strip()]
    else:
        allowed_modules = ['all']

    target_user = user_manager.get_user_by_id(user_id)
    if not _can_manage_user(target_user):
        return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Access denied)'}), 403

    ok = user_manager.update_user_account(user_id, allowed_modules=allowed_modules)
    if ok:
        u = user_manager.get_user_by_id(user_id)
        return jsonify({'success': True, 'user': u, 'allowed_modules': u.get('allowed_modules') if u else [], 'message': 'मॉड्यूल अनुमतियाँ सफलतापूर्वक अपडेट की गईं'}), 200
    return jsonify({'success': False, 'error': 'मॉड्यूल अपडेट करने में विफल'}), 500


@auth_bp.route('/api/users/<int:user_id>/update', methods=['POST'])
@audit_action('user.update_profile', entity_type='user', entity_id_arg='user_id')
@operator_or_super_admin_required
def api_users_update(user_id: int):
    """Updates sub-account details with strict tenancy authorization."""
    target_user = user_manager.get_user_by_id(user_id)
    if not target_user:
        return jsonify({'success': False, 'error': 'उपयोगकर्ता नहीं मिला'}), 404

    if not _can_manage_user(target_user):
        return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Access denied)'}), 403

    caller_role = session.get('role')
    data = request.get_json(silent=True) or request.form or {}

    role = data.get('role')
    if role and caller_role != 'super_admin':
        # Operators cannot elevate users to super_admin or reseller
        if role in ('super_admin', 'reseller'):
            return jsonify({'success': False, 'error': 'अवैध भूमिका चयन: केवल सुपर एडमिन ही यह भूमिका दे सकते हैं'}), 403

    tenant_slug = data.get('tenant_slug')
    if tenant_slug and caller_role != 'super_admin':
        # Operators cannot move accounts to other campaigns
        allowed_tenant = str(target_user.get('tenant_slug') or '').strip().lower()
        if str(tenant_slug).strip().lower() != allowed_tenant:
            return jsonify({'success': False, 'error': 'अभियान बदलने की अनुमति नहीं है'}), 403

    username = data.get('username')
    full_name = data.get('full_name') or data.get('name')
    phone = data.get('phone')
    password = data.get('password')
    is_active = data.get('is_active')
    scope_type = data.get('scope_type')
    assigned_booths = data.get('assigned_booths')
    assigned_wards = data.get('assigned_wards')
    assigned_villages = data.get('assigned_villages')
    allowed_modules = data.get('allowed_modules')

    if assigned_booths is not None and not isinstance(assigned_booths, list):
        try:
            if isinstance(assigned_booths, str):
                assigned_booths = [int(s.strip()) for s in assigned_booths.split(',') if s.strip().isdigit()]
            else:
                assigned_booths = [int(assigned_booths)]
        except Exception:
            assigned_booths = []

    try:
        ok = user_manager.update_user_account(
            user_id=user_id,
            username=username,
            full_name=full_name,
            phone=phone,
            role=role,
            tenant_slug=tenant_slug,
            password=password,
            assigned_booths=assigned_booths,
            assigned_wards=assigned_wards,
            assigned_villages=assigned_villages,
            scope_type=scope_type,
            allowed_modules=allowed_modules,
            is_active=is_active
        )
        updated = user_manager.get_user_by_id(user_id)
        return jsonify({'success': ok, 'message': 'खाता सफलतापूर्वक अपडेट किया गया', 'user': updated})
    except ValueError as ve:
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        logger.error("User update error for user_id %s: %s", user_id, e)
        return jsonify({'success': False, 'error': f'अद्यतन विफल: {str(e)}'}), 500



@auth_bp.route('/api/users/reset_password', methods=['POST'])
@audit_action('user.reset_password', entity_type='user')
@operator_or_super_admin_required
def api_users_reset_password():
    data = request.get_json(silent=True) or request.form or {}
    uid = data.get('user_id')
    new_pwd = data.get('password')
    if not uid:
        return jsonify({'success': False, 'error': 'user_id आवश्यक है'}), 400

    target_user = user_manager.get_user_by_id(int(uid))
    if not _can_manage_user(target_user):
        return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Access denied)'}), 403

    ok, pwd = user_manager.reset_user_password(int(uid), new_pwd)
    return jsonify({'success': True, 'password': pwd, 'message': 'पासवर्ड सफलतापूर्वक बदल दिया गया'})


@auth_bp.route('/api/users/toggle_status', methods=['POST'])
@audit_action('user.toggle_status', entity_type='user')
@operator_or_super_admin_required
def api_users_toggle_status():
    data = request.get_json(silent=True) or request.form or {}
    uid = data.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': 'user_id आवश्यक है'}), 400

    target_user = user_manager.get_user_by_id(int(uid))
    if not _can_manage_user(target_user):
        return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Access denied)'}), 403

    ok = user_manager.toggle_user_status(int(uid))
    return jsonify({'success': True, 'message': 'उपयोगकर्ता स्थिति बदल दी गई'})


@auth_bp.route('/api/users/<int:user_id>/delete', methods=['POST'])
@auth_bp.route('/api/users/delete', methods=['POST'])
@audit_action('user.delete', entity_type='user', entity_id_arg='user_id')
@operator_or_super_admin_required
def api_users_delete(user_id: Optional[int] = None):
    data = request.get_json(silent=True) or request.form or {}
    uid = user_id or data.get('user_id')
    if not uid:
        return jsonify({'success': False, 'error': 'user_id आवश्यक है'}), 400

    target_user = user_manager.get_user_by_id(int(uid))
    if not _can_manage_user(target_user):
        return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Access denied)'}), 403

    target_tenant = target_user.get('tenant_slug') or getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
    ok = user_manager.delete_user_account(int(uid))
    quota = user_manager.get_sub_account_quota(str(target_tenant).lower())
    return jsonify({'success': ok, 'message': 'उपयोगकर्ता सफलतापूर्वक हटा दिया गया', 'quota': quota})


@auth_bp.route('/api/users/<int:user_id>/send_credentials_whatsapp', methods=['POST'])
@audit_action('user.send_credentials_whatsapp', entity_type='user', entity_id_arg='user_id')
@operator_or_super_admin_required
def api_users_send_credentials_whatsapp(user_id: int):
    target_user = user_manager.get_user_by_id(user_id)
    if not target_user:
        return jsonify({'success': False, 'error': 'उपयोगकर्ता नहीं मिला (User not found)'}), 404

    if not _can_manage_user(target_user):
        return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Access denied)'}), 403

    data = request.get_json(silent=True) or request.form or {}
    override_phone = str(data.get('phone') or '').strip()
    phone = override_phone if override_phone else str(target_user.get('phone') or '').strip()
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 10:
        return jsonify({
            'success': False,
            'error': 'उपयोगकर्ता का फ़ोन नंबर अमान्य या अनुपलब्ध है (कम से कम 10 अंक आवश्यक हैं)',
            'phone': phone
        }), 400

    password = str(data.get('password') or '').strip() or '(पूर्ववत सुरक्षित / Unchanged)'

    full_name = target_user.get('full_name') or target_user.get('username') or ''
    username = target_user.get('username') or ''
    target_tenant = (target_user.get('tenant_slug') or getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or '').strip().lower()
    host_url = request.host_url.rstrip('/')
    if (request.headers.get('X-Forwarded-Proto') == 'https' or os.environ.get('APP_ENV') == 'production' or 'smartbooth.vedmap.com' in host_url) and host_url.startswith('http://'):
        host_url = 'https://' + host_url[len('http://'):]
    magic_tok = user_manager.get_or_create_user_magic_token(target_user['user_id'])
    if magic_tok:
        login_url = f"{host_url}/magic/{magic_tok}"
    elif target_tenant and target_tenant != '*':
        login_url = f"{host_url}/login?tenant={target_tenant}"
    else:
        login_url = f"{host_url}/login"

    msg = (
        f"*🇮🇳 Smart Booth — अधिकृत लॉगिन क्रेडेंशियल्स*\n\n"
        f"👤 *नाम:* {full_name}\n"
        f"🔑 *यूज़रनेम:* {username}\n"
        f"🔒 *पासवर्ड:* {password}\n"
        f"🌐 *लॉगिन लिंक:* {login_url}\n\n"
        f"कृपया लॉगिन करके अपने नियत बूथ/वार्ड का कार्य शुरू करें।"
    )

    tid = target_tenant or getattr(g, 'tenant_slug', None) or session.get('tenant_slug') or ''
    try:
        credit = whatsapp_quota.reserve(tid, 1, performed_by=session.get('user_id'))
    except whatsapp_quota.QuotaError as qe:
        return jsonify({
            'success': False,
            'error': str(qe),
            'phone': phone,
            'quota_blocked': True
        }), 429

    try:
        with credit:
            success, _data, err = meta_whatsapp.send_credentials_template(
                phone=phone,
                full_name=full_name,
                username=username,
                password=password,
                login_url=login_url,
                sender_label="SmartBooth Admin",
                credit=credit
            )
    except Exception as e:
        logger.error("Failed to send credentials via WhatsApp for user %s: %s", user_id, e)
        return jsonify({'success': False, 'error': str(e), 'phone': phone}), 500

    return jsonify({
        'success': bool(success),
        'error': err if not success else None,
        'phone': phone,
        'channel': 'Meta Cloud API'
    })


