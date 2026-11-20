"""
core.py
Core Blueprint for Unified Multi-Tenant Voter Platform:
- Index / Dashboard UI (/)
- Page routes (/voters, /my-voters, /turnout, /karyakartas, /caste, /voting-day, /users)
- Health check (/healthz)
- Static manifests (/manifest.json, /sw.js, /favicon.ico)
- System endpoints (/api/campaign_config, /api/booths)
"""

import os
import datetime
import user_manager
from flask import Blueprint, render_template, jsonify, current_app, request, g, send_from_directory, redirect, send_file, session

from electionapp.config import load_tenant_config, is_panchayat_campaign, is_ward_campaign, get_cached_ward_hierarchy, get_cached_panchayat_hierarchy, get_cached_community_tags, PLATFORM_ROOT
from electionapp.tenancy import resolve_tenant
from electionapp.blueprints.auth import super_admin_required, login_required

core_bp = Blueprint('core', __name__)


def _render_page(template_name: str, active_page: str):
    """Unified page renderer ensuring complete multi-tenant context."""
    tenant_slug, repo = resolve_tenant()
    try:
        campaign = load_tenant_config(tenant_slug)
    except Exception:
        campaign = current_app.config.get('CAMPAIGN', {})
        tenant_slug = current_app.config.get('TENANT_SLUG', '') or tenant_slug or ''

    cfg_booths = dict(campaign.get('booths') or {})
    db_booths = {}
    if repo and hasattr(repo, 'get_booths_dict'):
        try:
            db_booths = repo.get_booths_dict(tenant_slug) or {}
        except Exception:
            db_booths = {}

    if not cfg_booths:
        booths = db_booths
    else:
        booths = cfg_booths
        if db_booths:
            for p_no, b_info in booths.items():
                if isinstance(b_info, dict):
                    db_b = db_booths.get(str(p_no)) or db_booths.get(p_no)
                    if db_b and 'voter_count' in db_b:
                        b_info.setdefault('voter_count', db_b['voter_count'])
                        b_info.setdefault('total', db_b.get('total', db_b['voter_count']))

    # Clean any unmapped font artifacts (U+FFFD or dangling anusvara) from booths and hierarchies
    from electionapp.translit.normalizer import clean_text_or_structure
    booths = clean_text_or_structure(booths)

    # Wards & physical polling booth hierarchy memoized with 5-minute TTL.
    wards, booths_with_wards = get_cached_ward_hierarchy(repo, tenant_slug, campaign)
    wards = clean_text_or_structure(wards)
    booths_with_wards = clean_text_or_structure(booths_with_wards)
    panchayat_hierarchy = clean_text_or_structure(get_cached_panchayat_hierarchy(repo, tenant_slug, campaign))
    is_ward_roll = is_ward_campaign(campaign)

    # Community badges auto-derived with 5-minute TTL memoization
    community_tags = get_cached_community_tags(repo, tenant_slug, campaign)

    campaign = dict(campaign)
    campaign['booths'] = booths
    campaign['wards'] = wards
    campaign['booths_with_wards'] = booths_with_wards
    campaign['panchayat_hierarchy'] = panchayat_hierarchy
    campaign['community_tags'] = community_tags
    campaign['is_panchayat'] = bool(is_ward_roll)
    campaign['is_ward_election'] = bool(is_ward_roll)
    default_campaign_msg = campaign.get('campaign_message', '')

    is_super = (session.get('role') == 'super_admin' or session.get('tenant_slug') == '*')
    assigned_booths = session.get('assigned_booths') or []
    assigned_booth = session.get('assigned_booth')
    assigned_wards = session.get('assigned_wards') or []
    assigned_villages = session.get('assigned_villages') or []
    assigned_gps = session.get('assigned_gps') or []
    scope_type = session.get('scope_type') or 'all'
    is_scoped = not is_super and bool(assigned_booths or assigned_booth is not None or assigned_wards or assigned_villages or assigned_gps)

    user = getattr(g, 'current_user', None) or {
        'id': session.get('user_id', 1),
        'username': session.get('username', 'guest'),
        'name': session.get('full_name', 'Guest'),
        'full_name': session.get('full_name', 'Guest'),
        'role': session.get('role', 'operator'),
        'role_tier': session.get('role_tier', 1 if is_super else 2),
        'tenant_slug': tenant_slug,
        'assigned_tenant': session.get('tenant_slug', tenant_slug),
        'assigned_booths': assigned_booths,
        'assigned_booth': assigned_booth,
        'assigned_wards': assigned_wards,
        'assigned_villages': assigned_villages,
        'assigned_gps': assigned_gps,
        'scope_type': scope_type,
        'allowed_modules': session.get('allowed_modules') or ['all'],
        'is_scoped_user': is_scoped,
        'is_super_admin': is_super
    }

    return render_template(
        template_name,
        campaign=campaign,
        CAMPAIGN=campaign,
        CAMPAIGN_CONFIG=campaign,
        current_tenant=tenant_slug,
        tenant_slug=tenant_slug,
        community_tags=community_tags,
        COMMUNITY_TAGS=community_tags,
        booths=booths,
        BOOTHS=booths,
        wards=wards,
        WARDS=wards,
        booths_with_wards=booths_with_wards,
        BOOTHS_WITH_WARDS=booths_with_wards,
        panchayat_hierarchy=panchayat_hierarchy,
        PANCHAYAT_HIERARCHY=panchayat_hierarchy,
        IS_MULTI_GP=bool(panchayat_hierarchy.get('is_multi_gp')),
        IS_WARD_ELECTION=bool(is_ward_roll),
        default_campaign_msg=default_campaign_msg,
        local_ip=request.host.split(':')[0],
        current_user=user,
        active_page=active_page
    )


@core_bp.route('/')
@core_bp.route('/hub')
def index():
    if session.get('role') == 'karyakarta':
        slug = session.get('tenant_slug') or getattr(g, 'tenant_slug', '')
        return redirect(f"/my-voters?tenant={slug}" if slug else "/my-voters")
    return _render_page('hub.html', active_page='hub')


@core_bp.route('/my-campaigns')
@login_required
def my_campaigns_page():
    if session.get('role') != 'reseller':
        return redirect('/')
    summary = user_manager.get_reseller_dashboard_summary(session.get('user_id'))
    return render_template(
        'my_campaigns.html',
        campaigns=summary.get('campaigns', []),
        fleet_stats=summary.get('fleet_stats', {}),
        current_user=g.current_user or summary.get('user'),
        active_page='my_campaigns'
    )



@core_bp.route('/voters')
@core_bp.route('/search')
def voters_page():
    if session.get('role') == 'karyakarta':
        slug = session.get('tenant_slug') or getattr(g, 'tenant_slug', '')
        return redirect(f"/my-voters?tenant={slug}" if slug else "/my-voters")
    return _render_page('index.html', active_page='search')


@core_bp.route('/my-voters')
def my_voters_page():
    return _render_page('my_voters.html', active_page='my_voters')


@core_bp.route('/turnout')
def turnout_page():
    if session.get('role') == 'karyakarta':
        slug = session.get('tenant_slug') or getattr(g, 'tenant_slug', '')
        return redirect(f"/my-voters?tenant={slug}" if slug else "/my-voters")
    return _render_page('turnout.html', active_page='turnout')


@core_bp.route('/karyakartas')
def karyakartas_page():
    role = session.get('role')
    if role not in ['super_admin', 'admin', 'operator', 'reseller']:
        return redirect('/my-voters' if role == 'karyakarta' else '/')
    return _render_page('karyakartas.html', active_page='karyakartas')


@core_bp.route('/caste')
def caste_page():
    if session.get('role') == 'karyakarta':
        slug = session.get('tenant_slug') or getattr(g, 'tenant_slug', '')
        return redirect(f"/my-voters?tenant={slug}" if slug else "/my-voters")
    return _render_page('caste.html', active_page='caste')


@core_bp.route('/family')
@core_bp.route('/families')
def family_page():
    if session.get('role') == 'karyakarta':
        slug = session.get('tenant_slug') or getattr(g, 'tenant_slug', '')
        return redirect(f"/my-voters?tenant={slug}" if slug else "/my-voters")
    return _render_page('family.html', active_page='family')


@core_bp.route('/voting-day')
def voting_day_page():
    if session.get('role') == 'karyakarta':
        slug = session.get('tenant_slug') or getattr(g, 'tenant_slug', '')
        return redirect(f"/my-voters?tenant={slug}" if slug else "/my-voters")
    return _render_page('voting_day.html', active_page='voting_day')



@core_bp.route('/users')
def users_page():
    role = session.get('role')
    if role not in ['super_admin', 'admin', 'operator', 'reseller']:
        return redirect('/')
    return _render_page('users.html', active_page='users')


@core_bp.route('/history')
def history_page():
    return _render_page('history.html', active_page='history')


@core_bp.route('/outside')
def outside_page():
    return _render_page('outside.html', active_page='outside')


@core_bp.route('/reports')
@core_bp.route('/print-center')
def reports_page():
    # Reports & PDF print center rollout is paused; only super_admin can view if accessed directly
    curr_user = getattr(g, 'current_user', {}) or {}
    user_role = session.get('role') or curr_user.get('role')
    is_super = (
        user_role == 'super_admin' or
        curr_user.get('is_super_admin', False) or
        curr_user.get('tenant_slug') == '*'
    )
    if not is_super:
        tenant_slug, _ = resolve_tenant()
        dest = f'/?tenant={tenant_slug}' if tenant_slug else '/'
        return redirect(dest)
    return _render_page('reports.html', active_page='reports')


@core_bp.route('/manifest.json')
def pwa_manifest():
    static_dir = os.path.join(PLATFORM_ROOT, 'static')
    response = send_from_directory(static_dir, 'manifest.json', mimetype='application/manifest+json')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


@core_bp.route('/sw.js')
def pwa_service_worker():
    static_dir = os.path.join(PLATFORM_ROOT, 'static')
    response = send_from_directory(static_dir, 'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@core_bp.route('/favicon.ico')
def favicon():
    return ('', 204)


@core_bp.route('/api/booths')
def api_booths():
    tenant_slug, repo = resolve_tenant()
    if repo and hasattr(repo, 'get_booths_dict'):
        try:
            return jsonify(repo.get_booths_dict(tenant_slug)), 200
        except Exception:
            pass
    try:
        cfg = load_tenant_config(tenant_slug)
        return jsonify(cfg.get('booths', {})), 200
    except Exception:
        return jsonify({}), 200


@core_bp.route('/healthz')
def healthz():
    """
    Structured status report with tenant, backend, and row counts.
    """
    repo = current_app.extensions.get('repo')
    tenant_slug = current_app.config.get('TENANT_SLUG', 'unknown')
    backend = getattr(repo, 'storage_backend', 'postgres') if repo else 'unknown'
    row_counts = repo.get_row_counts() if repo else {}

    return jsonify({
        'status': 'healthy',
        'tenant_slug': tenant_slug,
        'tenant': tenant_slug,
        'backend': backend,
        'storage_backend': backend,
        'row_counts': row_counts,
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
    }), 200


@core_bp.route('/api/campaign_config')
def api_campaign_config():
    """
    Returns the campaign configuration for the requested tenant (or active tenant).
    """
    tenant_slug, repo = resolve_tenant()
    try:
        campaign = load_tenant_config(tenant_slug)
    except Exception:
        campaign = current_app.config.get('CAMPAIGN', {})
        tenant_slug = current_app.config.get('TENANT_SLUG', '') or tenant_slug or ''

    payload = dict(campaign)
    payload['tenant_slug'] = tenant_slug
    payload['success'] = True
    return jsonify(payload), 200


@core_bp.route('/api/poster_image')
def api_poster_image():
    """Returns the campaign poster image for the active tenant or local desktop poster."""
    tenant_slug, repo = resolve_tenant()

    candidates = [
        os.path.join(PLATFORM_ROOT, 'tenants', tenant_slug, 'assets', 'campaign_poster.jpg'),
        os.path.join(PLATFORM_ROOT, 'tenants', tenant_slug, 'assets', 'poster.jpg'),
        os.path.join(PLATFORM_ROOT, 'campaign_poster.jpg'),
        os.path.join(os.path.expanduser('~'), 'Desktop', 'campaign_poster.jpg')
    ]
    for p in candidates:
        if os.path.exists(p):
            ext = os.path.splitext(p)[1].lower()
            mimetype = 'image/png' if ext == '.png' else 'image/jpeg'
            return send_file(p, mimetype=mimetype)
    return "Poster not found", 404


@core_bp.route('/api/shutdown', methods=['POST'])
@super_admin_required
def api_shutdown():
    """Gracefully shuts down the local development server (Super Admin only)."""
    import threading
    import time
    def kill_later():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=kill_later).start()
    return jsonify({'success': True, 'message': 'Server shutting down...'})

