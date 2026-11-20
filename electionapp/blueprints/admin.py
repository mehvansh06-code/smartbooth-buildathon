"""
admin.py
Super Admin Command Center Blueprint for Unified Multi-Tenant Voter Platform:
- Fleet Dashboard UI (/admin)
- Fleet Live Analytics API (/api/admin/fleet_stats)
- Campaign Portfolio Listing (/api/admin/campaigns)
- 1-Click Multi-Tenant Provisioning (/api/admin/campaigns/create)
- 5-Tier RBAC User & Permission Manager (/api/admin/users)
"""

import os
import json
import hashlib
import re
import time
import copy
import logging
from typing import Dict, Any, List, Optional, Tuple
from flask import Blueprint, render_template, request, jsonify, current_app, g, send_file, Response, session, redirect
from electionapp.audit import audit_action
from electionapp.blueprints.auth import super_admin_required
from electionapp.config import TENANTS_DIR, clear_tenant_config_cache
from electionapp import whatsapp_quota
from electionapp.symbol_catalog import (
    SARPANCH_SYMBOLS,
    WARD_PANCH_SYMBOLS,
    POPULAR_FREE_SYMBOLS,
    POLITICAL_PARTY_SYMBOLS,
)

logger = logging.getLogger("smartbooth.admin")

admin_bp = Blueprint('admin', __name__)

_FLEET_CAMPAIGNS_CACHE: Tuple[float, List[Dict[str, Any]]] = (0.0, [])
_FLEET_CACHE_TTL = 300.0  # seconds
_FLEET_VERSION_FILE = os.path.join(TENANTS_DIR, '.fleet_version')
_FLEET_PAYLOAD_FILE = os.path.join(TENANTS_DIR, '.fleet_campaigns.json')


def clear_fleet_cache(clear_tenant_configs: bool = False) -> None:
    """Invalidates in-memory and shared disk fleet statistics cache across all Gunicorn workers."""
    global _FLEET_CAMPAIGNS_CACHE
    _FLEET_CAMPAIGNS_CACHE = (0.0, [])
    if clear_tenant_configs:
        try:
            clear_tenant_config_cache()
        except Exception:
            pass
    try:
        os.makedirs(TENANTS_DIR, exist_ok=True)
        with open(_FLEET_VERSION_FILE, 'w') as f:
            f.write(str(time.time()))
        if os.path.exists(_FLEET_PAYLOAD_FILE):
            os.remove(_FLEET_PAYLOAD_FILE)
    except Exception:
        pass


@admin_bp.before_request
def enforce_admin_access():
    force_auth = os.environ.get('FORCE_AUTH_CHECK') == '1'
    is_testing = (
        not force_auth
        and (current_app.config.get('TESTING', False) or os.environ.get('FLASK_TESTING') == '1')
    )
    if not is_testing and not session.get('user_id'):
        if request.is_json or request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'लॉगिन आवश्यक है (Authentication required)'}), 401
        return redirect(f"/login?next={request.path}")
    if not is_testing and session.get('role') not in ('super_admin', 'reseller'):
        if request.is_json or request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'अनुमति अस्वीकृत (Super Admin access required)'}), 403
        return redirect('/')

from electionapp import campaign_manager
from electionapp import campaign_lifecycle
from electionapp import voter_ingest
import user_manager


def _reseller_owns_slug(slug: str) -> bool:
    """Ownership guard: a super_admin passes through unconditionally; a reseller
    must own the tenant by `tenants.owner_user_id`."""
    if session.get('role') != 'reseller':
        return True
    return user_manager.reseller_owns_tenant(session.get('user_id'), slug)

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TENANTS_DIR = os.path.join(PLATFORM_ROOT, 'tenants')
DATA_DIR = os.path.join(PLATFORM_ROOT, 'data')


def get_pg_connection():
    """Helper to connect to PostgreSQL 17 if available."""
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(
            dbname=os.environ.get('PGDATABASE', 'voter_platform'),
            user=os.environ.get('PGUSER', 'postgres'),
            password=os.environ.get('PGPASSWORD', 'postgres'),
            host=os.environ.get('PGHOST', 'localhost'),
            port=int(os.environ.get('PGPORT', 5432)),
            connect_timeout=3
        )
        return conn
    except Exception as e:
        return None


@admin_bp.route('/admin')
def admin_dashboard():
    """Renders the Super Admin Command Center web dashboard. Resellers are
    bounced to their own scoped /my-campaigns page instead — this template
    assumes fleet-wide, super_admin-only actions (credit grants, sub-account
    limits, owner reassignment) that a reseller can't use."""
    if session.get('role') == 'reseller':
        return redirect('/my-campaigns')
    return render_template('admin_dashboard.html')


@admin_bp.route('/campaign/new')
@admin_bp.route('/admin/campaign/new')
def campaign_create_page():
    """Renders the full-page campaign creation screen for Super Admin and Resellers."""
    reseller_credits = 0
    if session.get('role') == 'reseller':
        reseller_credits = user_manager.get_reseller_whatsapp_credits(session.get('user_id'))
    return render_template(
        'admin/campaign_create_page.html',
        is_reseller=(session.get('role') == 'reseller'),
        reseller_credits=reseller_credits,
        edit_slug=None,
        sarpanch_symbols=SARPANCH_SYMBOLS,
        ward_panch_symbols=WARD_PANCH_SYMBOLS,
        popular_symbols=POPULAR_FREE_SYMBOLS,
        party_symbols=POLITICAL_PARTY_SYMBOLS,
    )


@admin_bp.route('/campaign/edit/<slug>')
@admin_bp.route('/admin/campaign/edit/<slug>')
def campaign_edit_page(slug: str):
    """Renders the full-page campaign editor pre-loaded for an existing campaign slug."""
    if not _reseller_owns_slug(slug):
        return redirect('/admin')
    reseller_credits = 0
    if session.get('role') == 'reseller':
        reseller_credits = user_manager.get_reseller_whatsapp_credits(session.get('user_id'))
    return render_template(
        'admin/campaign_create_page.html',
        is_reseller=(session.get('role') == 'reseller'),
        reseller_credits=reseller_credits,
        edit_slug=slug,
        sarpanch_symbols=SARPANCH_SYMBOLS,
        ward_panch_symbols=WARD_PANCH_SYMBOLS,
        popular_symbols=POPULAR_FREE_SYMBOLS,
        party_symbols=POLITICAL_PARTY_SYMBOLS,
    )


@admin_bp.route('/api/catalog/symbols', methods=['GET'])
def api_catalog_symbols():
    """Returns official SEC Rajasthan free symbols and recognized political party symbols."""
    ctype = (request.args.get("type") or "PANCHAYAT").strip().upper()
    return jsonify({
        "success": True,
        "type": ctype,
        "sarpanch_symbols": SARPANCH_SYMBOLS,
        "ward_panch_symbols": WARD_PANCH_SYMBOLS,
        "popular_symbols": POPULAR_FREE_SYMBOLS,
        "party_symbols": POLITICAL_PARTY_SYMBOLS,
    })


@admin_bp.route('/api/admin/fleet_stats')
def fleet_stats():
    """Returns aggregate analytics across all live campaigns."""
    campaigns = _gather_all_campaigns()
    if session.get('role') == 'reseller':
        owned_slugs = {t['campaign_slug'] for t in user_manager.get_owned_tenants(session.get('user_id'))}
        campaigns = [c for c in campaigns if c.get('tenant_slug') in owned_slugs]
    total_campaigns = len(campaigns)
    total_voters = sum(c.get('total_voters', 0) for c in campaigns)
    total_voted = sum(c.get('voted_count', 0) for c in campaigns)
    total_dispatches = sum(c.get('dispatched_count', 0) for c in campaigns)
    overall_turnout = (total_voted / total_voters * 100) if total_voters > 0 else 0.0

    return jsonify({
        'total_campaigns': total_campaigns,
        'total_voters': total_voters,
        'total_voted': total_voted,
        'total_dispatches': total_dispatches,
        'overall_turnout_pct': round(overall_turnout, 2)
    })


@admin_bp.route('/api/admin/campaigns', methods=['GET'])
def list_campaigns():
    """Returns detailed portfolio of all registered campaigns."""
    force = request.args.get('force') in ['1', 'true', 'True'] or request.args.get('refresh') in ['1', 'true', 'True']
    campaigns = _gather_all_campaigns(force_refresh=force)
    if session.get('role') == 'reseller':
        owned_slugs = {t['campaign_slug'] for t in user_manager.get_owned_tenants(session.get('user_id'))}
        campaigns = [c for c in campaigns if c.get('tenant_slug') in owned_slugs]
    total_voters = sum(c.get('total_voters', 0) for c in campaigns)
    total_voted = sum(c.get('voted_count', 0) for c in campaigns)
    total_dispatches = sum(c.get('dispatched_count', 0) for c in campaigns)
    overall_turnout = (total_voted / total_voters * 100) if total_voters > 0 else 0.0

    return jsonify({
        'campaigns': campaigns,
        'fleet_stats': {
            'total_campaigns': len(campaigns),
            'total_voters': total_voters,
            'total_voted': total_voted,
            'total_dispatches': total_dispatches,
            'overall_turnout_pct': round(overall_turnout, 2)
        }
    })


def _gather_all_campaigns(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Gathers campaigns from tenants/ directory and PostgreSQL with cross-worker invalidation."""
    global _FLEET_CAMPAIGNS_CACHE
    now = time.time()
    last_time, cached = _FLEET_CAMPAIGNS_CACHE

    version_mtime = 0.0
    if os.path.exists(_FLEET_VERSION_FILE):
        try:
            version_mtime = os.path.getmtime(_FLEET_VERSION_FILE)
        except OSError:
            pass

    # If another worker updated campaigns since our last cache build, invalidate!
    if version_mtime > last_time:
        cached = None

    if not force_refresh and cached and (now - last_time < _FLEET_CACHE_TTL):
        return copy.deepcopy(cached)

    # Check shared disk cache across Gunicorn workers
    if not force_refresh and os.path.exists(_FLEET_PAYLOAD_FILE):
        try:
            payload_mtime = os.path.getmtime(_FLEET_PAYLOAD_FILE)
            if payload_mtime > version_mtime and (now - payload_mtime < _FLEET_CACHE_TTL):
                with open(_FLEET_PAYLOAD_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        _FLEET_CAMPAIGNS_CACHE = (now, data)
                        return copy.deepcopy(data)
        except Exception:
            pass

    campaigns = []
    if not os.path.exists(TENANTS_DIR):
        return campaigns

    pg_conn = get_pg_connection()
    try:
        pg_tenants = {}
        tenant_dispatches: dict = {}
        if pg_conn:
            try:
                with pg_conn.cursor() as cur:
                    cur.execute(
                        "SELECT tenant_id, campaign_slug, candidate_name, state, election_type, status, max_sub_accounts, config, COALESCE(total_voters, 0) FROM tenants"
                    )
                    for r in cur.fetchall():
                        pg_tenants[r[1]] = {
                            'tenant_id': r[0], 'candidate': r[2], 'state': r[3],
                            'type': r[4], 'status': r[5], 'max_sub_accounts': r[6],
                            'config': r[7], 'total_voters': int(r[8] or 0)
                        }
            except Exception as e:
                # Graceful fallback if total_voters column was missing in query
                try:
                    with pg_conn.cursor() as cur:
                        cur.execute("SELECT tenant_id, campaign_slug, candidate_name, state, election_type, status, max_sub_accounts, config FROM tenants")
                        for r in cur.fetchall():
                            pg_tenants[r[1]] = {
                                'tenant_id': r[0], 'candidate': r[2], 'state': r[3],
                                'type': r[4], 'status': r[5], 'max_sub_accounts': r[6],
                                'config': r[7], 'total_voters': 0
                            }
                except Exception as fallback_err:
                    logger.warning("Error fetching tenants from PostgreSQL: %s", fallback_err)

            # WhatsApp credit balance per campaign, in one round trip (~3ms).
            try:
                with pg_conn.cursor() as cur:
                    cur.execute(
                        "SELECT t.campaign_slug, q.granted, q.consumed, q.reserved, q.is_blocked"
                        "  FROM whatsapp_quota q"
                        "  JOIN tenants t ON t.tenant_id = q.tenant_id"
                    )
                    for r in cur.fetchall():
                        if r[0] in pg_tenants:
                            pg_tenants[r[0]]['wa_quota'] = {
                                'granted': int(r[1]), 'consumed': int(r[2]),
                                'reserved': int(r[3]),
                                'available': int(r[1]) - int(r[2]) - int(r[3]),
                                'is_blocked': bool(r[4]), 'configured': True,
                            }
            except Exception as e:
                logger.warning("Error fetching WhatsApp quota from PostgreSQL: %s", e)


            # Instant pg_class reltuples fallback for any tenants where total_voters is not yet cached (~3ms)
            missing_tids = [meta['tenant_id'] for meta in pg_tenants.values() if not meta.get('total_voters')]
            if missing_tids:
                try:
                    with pg_conn.cursor() as cur:
                        cur.execute(
                            "SELECT substring(relname from 9)::int, reltuples::bigint"
                            "  FROM pg_class"
                            " WHERE relname LIKE 'voters_t%' AND relkind = 'r'"
                        )
                        reltuple_map = {row[0]: max(0, row[1]) for row in cur.fetchall() if row[1] > 0}
                        for meta in pg_tenants.values():
                            if not meta.get('total_voters') and meta.get('tenant_id') in reltuple_map:
                                meta['total_voters'] = int(reltuple_map[meta['tenant_id']])
                except Exception:
                    pass

        for slug in sorted(os.listdir(TENANTS_DIR)):
            tenant_path = os.path.join(TENANTS_DIR, slug)
            cfg_file = os.path.join(tenant_path, 'campaign.json')
            if not os.path.isdir(tenant_path) or not os.path.exists(cfg_file):
                continue

            # If PostgreSQL is active and authoritative, skip orphaned disk directories
            # that were deleted from the database to prevent zombie campaigns
            if pg_conn and pg_tenants and slug not in pg_tenants:
                continue

            try:
                with open(cfg_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)

                # Overlay authoritative PostgreSQL configuration if active
                if pg_conn and slug in pg_tenants and pg_tenants[slug].get('config'):
                    db_cfg = pg_tenants[slug]['config']
                    if isinstance(db_cfg, dict) and db_cfg:
                        from electionapp.config import _deep_merge
                        _deep_merge(cfg, db_cfg)

                c_info = cfg.get('candidate', {})
                e_info = cfg.get('election', {})
                p_info = cfg.get('party', {})
                booths = cfg.get('booths', {})

                # Determine stats — look up from pre-fetched metadata (0ms)
                voters_count = 0
                voted_count = 0
                disp_count = 0
                backend = "postgres"

                if pg_conn and slug in pg_tenants:
                    t_id = pg_tenants[slug]['tenant_id']
                    voters_count = pg_tenants[slug].get('total_voters', 0)
                    disp_count = tenant_dispatches.get(t_id, 0)

                turnout_pct = (voted_count / voters_count * 100) if voters_count > 0 else 0.0

                # Photo & Symbol URLs (Direct S3 / CDN with local fallback)
                from electionapp import s3_storage
                photo_file = c_info.get('photo_filename')
                photo_url = s3_storage.resolve_photo_url(slug, photo_file, stored_url=c_info.get('photo_url'), prefer_thumb=True)

                symbol_file = p_info.get('symbol_filename')
                symbol_url = s3_storage.resolve_symbol_url(slug, symbol_file, stored_url=p_info.get('symbol_url'))
                if not symbol_url:
                    from electionapp.campaign_manager import _get_preset_symbol_key
                    preset_key = _get_preset_symbol_key(p_info.get('symbol_name_hi') or p_info.get('symbol_name_en') or p_info.get('preset_symbol'))
                    if preset_key:
                        symbol_url = f"/static/symbols/{preset_key}.png"

                cand_hi = c_info.get('name_hi', '')
                if pg_conn and slug in pg_tenants and pg_tenants[slug].get('candidate'):
                    db_cand = str(pg_tenants[slug]['candidate']).strip()
                    if db_cand and '?' not in db_cand:
                        cand_hi = db_cand

                campaign_status = str(pg_tenants.get(slug, {}).get('status') or cfg.get('status') or 'ACTIVE').upper()
                if campaign_status == 'DELETED':
                    continue

                campaigns.append({
                    'tenant_slug': slug,
                    'candidate_name_hi': cand_hi,
                    'candidate_name_en': c_info.get('name_en', ''),
                    'designation': c_info.get('designation', ''),
                    'phone': c_info.get('phone', ''),
                    'office_address': c_info.get('office_address', ''),
                    'photo_url': photo_url,
                    'symbol_url': symbol_url,
                    'ward_name_hi': e_info.get('ward_name_hi', ''),
                    'ward_name_en': e_info.get('ward_name_en', ''),
                    'ward_no': e_info.get('ward_no', ''),
                    'constituency': e_info.get('constituency', ''),
                    'district': e_info.get('district', ''),
                    'city': e_info.get('city', ''),
                    'state': e_info.get('state', 'Rajasthan'),
                    'election_type': e_info.get('election_type', 'MUNICIPAL'),
                    'party_name_hi': p_info.get('name_hi', ''),
                    'party_name_en': p_info.get('name_en', ''),
                    'symbol_name_hi': p_info.get('symbol_name_hi', ''),
                    'symbol_name_en': p_info.get('symbol_name_en', ''),
                    'ballot_serial': p_info.get('ballot_serial') or cfg.get('ballot_serial', ''),
                    'slogan': p_info.get('slogan') or cfg.get('slogan', ''),
                    'theme_color': p_info.get('theme_color', '#2563eb'),
                    'campaign_message': cfg.get('campaign_message', ''),
                    'max_sub_accounts': (pg_tenants.get(slug, {}).get('max_sub_accounts') if slug in pg_tenants else None) or cfg.get('max_sub_accounts', 50),
                    'wa_quota': pg_tenants.get(slug, {}).get('wa_quota') or {
                        'granted': 0, 'consumed': 0, 'reserved': 0, 'available': 0,
                        'is_blocked': False, 'configured': False,
                    },
                    'booth_count': len(booths) if isinstance(booths, dict) else 0,
                    'total_voters': voters_count,
                    'voted_count': voted_count,
                    'turnout_percent': round(turnout_pct, 2),
                    'dispatched_count': disp_count,
                    'status': campaign_status,
                    'storage_backend': backend
                })
            except Exception:
                continue
    finally:
        if pg_conn:
            try:
                pg_conn.close()
            except Exception:
                pass

    _FLEET_CAMPAIGNS_CACHE = (now, campaigns)
    try:
        temp_file = _FLEET_PAYLOAD_FILE + f".tmp.{os.getpid()}"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(campaigns, f, ensure_ascii=False)
        os.replace(temp_file, _FLEET_PAYLOAD_FILE)
    except Exception as e:
        logger.debug("Failed to write fleet cache file: %s", e)
    return copy.deepcopy(campaigns)


def _parse_booths_input(raw):
    """Parses JSON or CSV string or dict into structured booths map."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    raw = raw.strip()
    if not raw:
        return {}
    if (raw.startswith('{') and raw.endswith('}')) or (raw.startswith('[') and raw.endswith(']')):
        try:
            val = json.loads(raw)
            if isinstance(val, dict):
                return val
            if isinstance(val, list):
                res = {}
                for idx, item in enumerate(val, 1):
                    b_no = str(item.get('booth_no', idx))
                    res[b_no] = item
                return res
        except Exception:
            pass

    # Parse CSV lines
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines:
        return {}
    res = {}
    for idx, line in enumerate(lines, 1):
        if idx == 1 and ('booth' in line.lower() or 'मतदान' in line or 'part' in line.lower()):
            continue
        parts = [p.strip() for p in line.split(',')]
        if parts:
            b_no = parts[0] or str(idx)
            name = parts[1] if len(parts) > 1 else f"Booth {b_no}"
            area = parts[2] if len(parts) > 2 else ""
            maps = parts[3] if len(parts) > 3 else ""
            res[str(b_no)] = {
                "booth_no": b_no,
                "name": name,
                "area": area,
                "maps_url": maps
            }
    return res


@admin_bp.route('/api/admin/campaigns/create', methods=['POST'])
@audit_action('admin.campaign_create', entity_type='campaign')
def create_campaign():
    """
    1-Click Self-Serve Campaign Provisioner.
    Delegates to electionapp.campaign_manager to keep controllers decoupled.
    """
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict() or {}

    election_type = str(data.get('election_type') or 'MUNICIPAL').strip().upper()
    block = str(data.get('block') or data.get('panchayat_samiti') or data.get('rajasthan_block') or '').strip()
    ulb = str(data.get('ulb_name') or data.get('body_name') or data.get('rajasthan_ulb') or '').strip()
    if election_type == 'MUNICIPAL' and ulb and not block:
        block = ulb
        data['panchayat_samiti'] = ulb
        data['block'] = ulb

    if election_type in ('PANCHAYAT', 'PANCHAYAT_SAMITI') and not block:
        return jsonify({'success': False, 'error': f'panchayat_samiti / block is mandatory for {election_type} elections'}), 400
    if election_type == 'MUNICIPAL':
        if not block and not ulb and not data.get('city') and not data.get('ward_name_hi'):
            return jsonify({'success': False, 'error': 'नगर निकाय या शहर का नाम आवश्यक है (Urban Body or City is mandatory for Municipal elections)'}), 400
        if not str(data.get('ward_no') or '').strip():
            return jsonify({'success': False, 'error': 'पार्षद चुनाव हेतु वार्ड संख्या आवश्यक है (Ward number is mandatory for Councillor elections)'}), 400
    if election_type == 'ASSEMBLY':
        ac_name = str(data.get('ward_name_hi') or data.get('constituency') or data.get('ward_name_en') or '').strip()
        ac_no = str(data.get('ac_no') or data.get('ward_no') or '').strip()
        if not ac_name and not ac_no:
            return jsonify({'success': False, 'error': 'विधान सभा क्षेत्र का नाम या संख्या आवश्यक है (Assembly Constituency name or number is required)'}), 400

    owner_user_id = None
    if session.get('role') == 'reseller':
        owner_user_id = session.get('user_id')
        user_row = user_manager.get_user_by_id(owner_user_id)
        max_campaigns = (user_row or {}).get('max_campaigns')
        if max_campaigns is not None and user_manager.count_owned_tenants(owner_user_id) >= max_campaigns:
            return jsonify({'success': False, 'error': f'Campaign limit of {max_campaigns} reached'}), 403
    # Pass base_url for accurate login and direct links
    data = dict(data)
    host_url = request.host_url.rstrip('/')
    if (request.headers.get('X-Forwarded-Proto') == 'https' or os.environ.get('APP_ENV') == 'production' or 'smartbooth.vedmap.com' in host_url) and host_url.startswith('http://'):
        host_url = 'https://' + host_url[len('http://'):]
    data['base_url'] = host_url

    success, result, status_code = campaign_manager.create_campaign(data, request.files, owner_user_id=owner_user_id)
    if success:
        clear_fleet_cache()
    return jsonify(result), status_code


# API alias
create_campaign_api = create_campaign


@admin_bp.route('/api/admin/transliterate', methods=['GET', 'POST'])
def transliterate_text():
    """
    Real-time transliteration endpoint for campaign inputs:
    - en_to_hi: English input to Devanagari (Hindi)
    - hi_to_en: Devanagari (Hindi) input to Latin (English)
    """
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form.to_dict()
    else:
        data = request.args.to_dict()

    text = str(data.get('text') or '').strip()
    direction = str(data.get('direction') or 'en_to_hi').strip().lower()

    if not text:
        return jsonify({'success': True, 'original': '', 'result': '', 'direction': direction})

    # Common election/party shortcuts
    PARTY_SHORTCUTS = {
        'bjp': 'भारतीय जनता पार्टी',
        'inc': 'भारतीय राष्ट्रीय कांग्रेस',
        'congress': 'भारतीय राष्ट्रीय कांग्रेस',
        'rlp': 'राष्ट्रीय लोकतांत्रिक पार्टी',
        'bsp': 'बहुजन समाज पार्टी',
        'aap': 'आम आदमी पार्टी',
        'independent': 'निर्दलीय',
        'ind': 'निर्दलीय',
        'sarpanch': 'सरपंच',
        'panchayat': 'पंचायत',
        'ward': 'वार्ड',
        'vidhan sabha': 'विधान सभा',
    }

    if direction == 'en_to_hi':
        clean_lower = text.lower()
        if clean_lower in PARTY_SHORTCUTS:
            return jsonify({
                'success': True,
                'original': text,
                'result': PARTY_SHORTCUTS[clean_lower],
                'direction': 'en_to_hi'
            })

        # Try high-speed Google Input Tools API
        try:
            import requests
            resp = requests.get(
                'https://inputtools.google.com/request',
                params={'text': text, 'itc': 'hi-t-i0-und', 'num': 3},
                timeout=1.5
            )
            if resp.status_code == 200:
                body = resp.json()
                if len(body) > 1 and body[0] == 'SUCCESS' and body[1]:
                    tokens = []
                    for item in body[1]:
                        if len(item) > 1 and item[1]:
                            tokens.append(item[1][0])
                    if tokens:
                        return jsonify({
                            'success': True,
                            'original': text,
                            'result': ' '.join(tokens),
                            'direction': 'en_to_hi'
                        })
        except Exception as e:
            logger.debug(f"Input tools transliteration error: {e}")

        # Fallback: return unchanged text if offline
        return jsonify({
            'success': True,
            'original': text,
            'result': text,
            'direction': 'en_to_hi',
            'fallback': True
        })

    else:
        # hi_to_en direction using electionapp.translit.rules
        try:
            from electionapp.translit import rules as translit_rules
            eng = translit_rules.transliterate_name(text)
            return jsonify({
                'success': True,
                'original': text,
                'result': eng or text,
                'direction': 'hi_to_en'
            })
        except Exception as e:
            return jsonify({
                'success': True,
                'original': text,
                'result': text,
                'direction': 'hi_to_en',
                'error': str(e)
            })


@admin_bp.route('/api/admin/campaigns/get', methods=['GET'])
@admin_bp.route('/api/admin/campaigns/<slug>', methods=['GET'])
def get_campaign(slug: Optional[str] = None):
    """Returns detailed configuration and asset paths for a single campaign."""
    target_slug = slug or request.args.get('slug', '').strip().lower()
    if not target_slug:
        return jsonify({'success': False, 'error': 'slug is required'}), 400
    if not _reseller_owns_slug(target_slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403

    tenant_dir = os.path.join(TENANTS_DIR, target_slug)
    cfg_path = os.path.join(tenant_dir, 'campaign.json')
    if not os.path.exists(cfg_path):
        return jsonify({'success': False, 'error': f"Campaign '{target_slug}' not found"}), 404

    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    assets_dir = os.path.join(tenant_dir, 'assets')
    c_info = cfg.get('candidate', {})
    e_info = cfg.get('election', {})
    p_info = cfg.get('party', {})

    from electionapp import s3_storage
    photo_file = c_info.get('photo_filename')
    photo_url = s3_storage.resolve_photo_url(target_slug, photo_file, stored_url=c_info.get('photo_url'))

    symbol_file = p_info.get('symbol_filename')
    symbol_url = s3_storage.resolve_symbol_url(target_slug, symbol_file, stored_url=p_info.get('symbol_url'))
    if not symbol_url:
        from electionapp.campaign_manager import _get_preset_symbol_key
        preset_key = _get_preset_symbol_key(p_info.get('symbol_name_hi') or p_info.get('symbol_name_en') or p_info.get('preset_symbol'))
        if preset_key:
            symbol_url = f"/static/symbols/{preset_key}.png"

    backend = 'postgres_degraded'  # Will be overwritten to 'postgres' on successful PG connection
    max_sub = cfg.get('max_sub_accounts', 50)
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT tenant_id, max_sub_accounts FROM tenants WHERE campaign_slug = %s", (target_slug,))
                row = cur.fetchone()
                if row:
                    backend = 'postgres'
                    if row[1] is not None:
                        max_sub = row[1]
        except Exception:
            pass
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass

    result = {
        'tenant_slug': target_slug,
        'max_sub_accounts': max_sub,
        'candidate_name_hi': c_info.get('name_hi', ''),
        'candidate_name_en': c_info.get('name_en', ''),
        'designation': c_info.get('designation', ''),
        'phone': c_info.get('phone', ''),
        'office_address': c_info.get('office_address', ''),
        'photo_url': photo_url,
        'symbol_url': symbol_url,
        'election_type': e_info.get('election_type', 'MUNICIPAL'),
        'ward_no': e_info.get('ward_no', ''),
        'ward_name_hi': e_info.get('ward_name_hi', ''),
        'ward_name_en': e_info.get('ward_name_en', ''),
        'constituency': e_info.get('constituency', ''),
        'city': e_info.get('city', ''),
        'district': e_info.get('district', ''),
        'state': e_info.get('state', 'Rajasthan'),
        'party_name_hi': p_info.get('name_hi', ''),
        'party_name_en': p_info.get('name_en', ''),
        'ballot_serial': p_info.get('ballot_serial') or cfg.get('ballot_serial', ''),
        'ballot_color': p_info.get('ballot_color') or e_info.get('ballot_color') or cfg.get('ballot_color', ''),
        'voting_method': e_info.get('voting_method') or p_info.get('voting_method') or cfg.get('voting_method', ''),
        'symbol_name_hi': p_info.get('symbol_name_hi', ''),
        'symbol_name_en': p_info.get('symbol_name_en', ''),
        'slogan': p_info.get('slogan') or cfg.get('slogan', ''),
        'theme_color': p_info.get('theme_color', '#2563eb'),
        'campaign_message': cfg.get('campaign_message', ''),
        'booths': cfg.get('booths', {}),
        'storage_backend': backend,
        'config': cfg
    }
    return jsonify({'success': True, 'campaign': result})


@admin_bp.route('/api/admin/campaigns/update', methods=['POST'])
def update_campaign():
    """Updates campaign parameters, candidate photo, symbol, message, and booths."""
    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict() or {}

    slug = (data.get('wizard_existing_slug') or data.get('campaign_slug') or '').strip().lower()
    if not slug:
        return jsonify({'success': False, 'error': 'campaign_slug is required'}), 400
    if not _reseller_owns_slug(slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403

    success, result, status_code = campaign_manager.update_campaign(slug, data, request.files)
    if success:
        clear_fleet_cache()
    return jsonify(result), status_code


@admin_bp.route('/api/admin/campaigns/<slug>/assets', methods=['POST'])
def update_campaign_assets(slug: str):
    """Updates candidate photo and party symbol for an existing campaign."""
    if not _reseller_owns_slug(slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403
    data = request.form.to_dict() or {}
    success, result, status_code = campaign_manager.update_campaign(slug, data, request.files)
    if success:
        clear_fleet_cache()
    return jsonify(result), status_code



@admin_bp.route('/api/admin/campaigns/<slug>/upload_voters', methods=['POST'])
def upload_campaign_voters(slug: str):
    """Accepts uploaded voter roll file (CSV / Excel) and batch ingests into campaign partition."""
    if not _reseller_owns_slug(slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403
    file = request.files.get('file')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded under key "file"'}), 400

    filename = file.filename or 'voter_roll.csv'
    storage_backend = request.form.get('storage_backend') or request.args.get('storage_backend')

    try:
        summary = voter_ingest.ingest_voter_file(
            file_obj_or_path=file,
            filename=filename,
            tenant_slug=slug,
            storage_backend=storage_backend
        )
        status_code = 200 if summary.get('success') else 400
        if summary.get('success'):
            clear_fleet_cache()
        return jsonify(summary), status_code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/admin/campaigns/<slug>/sync_master_voters', methods=['POST'])
def sync_master_voters(slug: str):
    """
    High-performance master voter sync endpoint.
    Directly extracts elector records from the 41.3 million row master dataset (rajasthan_voters)
    and batch upserts into the campaign's PostgreSQL partition (voters_t{tenant_id}).
    """
    clean_slug = (slug or '').strip().lower()
    if not clean_slug:
        return jsonify({'success': False, 'error': 'Campaign slug is required'}), 400
    if not _reseller_owns_slug(clean_slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403

    # 1. Parameter resolution: request body/form/args or fallback to tenants/{slug}/campaign.json
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict() or {}

    district = (data.get('district') or request.args.get('district') or '').strip()
    block = (data.get('block') or data.get('panchayat_samiti') or request.args.get('block') or request.args.get('panchayat_samiti') or '').strip()
    gp = (data.get('gp') or data.get('gram_panchayat') or request.args.get('gp') or request.args.get('gram_panchayat') or '').strip()

    tenant_dir = os.path.join(TENANTS_DIR, clean_slug)
    cfg_path = os.path.join(tenant_dir, 'campaign.json')
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception as e:
            return jsonify({'success': False, 'error': f"Failed to load campaign config: {e}"}), 500

    election_cfg = cfg.get('election', {})
    if not district:
        district = str(election_cfg.get('district') or '').strip()
    if not block:
        block = str(election_cfg.get('panchayat_samiti') or '').strip()
    if not gp:
        gp = str(
            election_cfg.get('gram_panchayat') or
            election_cfg.get('ward_name_en') or
            election_cfg.get('ward_name_hi') or ''
        ).strip()

    election_type = str(data.get('election_type') or election_cfg.get('election_type') or 'PANCHAYAT').strip().upper()
    clean_block = re.sub(r"\s+(panchayat\s+samiti|ps)$", "", (block or '').strip(), flags=re.I)
    if election_type == 'PANCHAYAT' and not clean_block:
        return jsonify({'success': False, 'error': 'panchayat_samiti / block is mandatory for Gram Panchayat sync'}), 400

    if not district or not gp:
        return jsonify({
            'success': False,
            'error': 'Missing required election parameters (district and gram_panchayat must be provided or configured)'
        }), 400

    try:
        from electionapp import rajasthan_catalog
        import psycopg2.extras
        from electionapp.tenant_branding import invalidate_tenant_branding

        # Helper to strip prefixes like "Gram Panchayat " or "ग्राम पंचायत "
        def _clean_gp_candidate(name: str) -> str:
            if not name:
                return ""
            val = name
            for prefix in ['Gram Panchayat', 'gram panchayat', 'Gram panchayat', 'ग्राम पंचायत', 'GP', 'gp']:
                val = val.replace(prefix, '')
            return val.strip(' -:_')

        # Candidates to try for Gram Panchayat matching in master database
        candidate_gps = []
        canonical_target = rajasthan_catalog.resolve_canonical_gp(district, block, gp)
        if canonical_target and canonical_target not in candidate_gps:
            candidate_gps.append(canonical_target)
        if gp:
            candidate_gps.append(gp)
            cleaned = _clean_gp_candidate(gp)
            if cleaned and cleaned not in candidate_gps:
                candidate_gps.append(cleaned)
        if election_cfg.get('ward_name_en'):
            cleaned = _clean_gp_candidate(election_cfg.get('ward_name_en'))
            if cleaned and cleaned not in candidate_gps:
                candidate_gps.append(cleaned)
        if election_cfg.get('ward_name_hi'):
            cleaned = _clean_gp_candidate(election_cfg.get('ward_name_hi'))
            if cleaned and cleaned not in candidate_gps:
                candidate_gps.append(cleaned)
        if election_cfg.get('gram_panchayat'):
            cleaned = _clean_gp_candidate(election_cfg.get('gram_panchayat'))
            if cleaned and cleaned not in candidate_gps:
                candidate_gps.append(cleaned)

        elector_records = []
        resolved_gp = gp
        for cand_gp in candidate_gps:
            elector_records = rajasthan_catalog.load_gp_voter_records(district, block, cand_gp)
            if elector_records:
                resolved_gp = cand_gp
                break

        if not elector_records:
            return jsonify({
                'success': False,
                'error': f"No voter records found in Master DB for district='{district}', block='{block}', gp='{gp}'"
            }), 404

        # Resolve tenant_id from tenants table in voter_platform
        pg_conn = get_pg_connection()
        if not pg_conn:
            # Direct connect in case STORAGE_BACKEND was temporarily set in env
            try:
                import psycopg2
                pg_conn = psycopg2.connect(
                    dbname=os.environ.get('PGDATABASE', 'voter_platform'),
                    user=os.environ.get('PGUSER', 'postgres'),
                    password=os.environ.get('PGPASSWORD', 'postgres'),
                    host=os.environ.get('PGHOST', 'localhost'),
                    port=int(os.environ.get('PGPORT', 5432)),
                    connect_timeout=3
                )
            except Exception:
                pg_conn = None

        if not pg_conn:
            return jsonify({'success': False, 'error': 'Database connection to voter_platform unavailable'}), 503

        try:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT tenant_id FROM tenants WHERE campaign_slug = %s", (clean_slug,))
                row = cur.fetchone()
                if not row:
                    return jsonify({'success': False, 'error': f"Campaign '{clean_slug}' not found in tenants table"}), 404
                tenant_id = row[0]

                part_tbl = f"voters_t{tenant_id}"
                cur.execute("SELECT to_regclass(%s);", (part_tbl,))
                reg = cur.fetchone()
                if not reg or not reg[0]:
                    cur.execute("SELECT to_regclass('voters');")
                    v_reg = cur.fetchone()
                    if v_reg and v_reg[0]:
                        cur.execute(f"""
                            CREATE TABLE IF NOT EXISTS {part_tbl}
                            PARTITION OF voters
                            FOR VALUES IN ({tenant_id});
                        """)
                        pg_conn.commit()
                    else:
                        return jsonify({'success': False, 'error': f"Partition table '{part_tbl}' does not exist"}), 404

            # Ensure unique index exists on (tenant_id, part_no, sl_no)
            try:
                cur.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_{part_tbl}_part_sl_unique
                    ON {part_tbl} (tenant_id, part_no, sl_no);
                """)
                pg_conn.commit()
            except Exception:
                pg_conn.rollback()
                try:
                    cur.execute(f"UPDATE {part_tbl} SET part_no = ward_no WHERE part_no != ward_no AND ward_no IS NOT NULL;")
                    cur.execute(f"""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_{part_tbl}_part_sl_unique
                        ON {part_tbl} (tenant_id, part_no, sl_no);
                    """)
                    pg_conn.commit()
                except Exception:
                    pg_conn.rollback()

            # First attempt ultra-fast direct internal SQL copy if voters_master is in this DB
            cur.execute("SELECT to_regclass('voters_master');")
            has_vm_local = cur.fetchone()[0]
            count = 0
            if has_vm_local:
                clean_block = re.sub(r"\s+(panchayat\s+samiti|ps)$", "", (block or '').strip(), flags=re.I)
                gp_candidates = [resolved_gp, resolved_gp.upper(), resolved_gp.title(), resolved_gp.lower()]
                matched_gp = None
                if clean_block:
                    for cand in gp_candidates:
                        cur.execute("SELECT 1 FROM voters_master WHERE district = %s AND gram_panchayat = %s AND panchayat_samiti ILIKE %s LIMIT 1;", (district.upper(), cand, f"%{clean_block}%"))
                        if cur.fetchone():
                            matched_gp = cand
                            break
                if not matched_gp and clean_block:
                    cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat ILIKE %s AND panchayat_samiti ILIKE %s LIMIT 1;", (district.upper(), f"%{resolved_gp}%", f"%{clean_block}%"))
                    row = cur.fetchone()
                    if row:
                        matched_gp = row[0]

                if matched_gp:
                    cur.execute(f"""
                        INSERT INTO {part_tbl} (
                            tenant_id, district, panchayat_samiti, ward_no, part_no, sl_no, epic_no, name_hi, name_en, name_norm,
                            rel_type, rel_name_hi, rel_name_en, rel_norm, house_no, age, gender,
                            booth_name, booth_area, contact_phone, phone_source_type, family_id, status
                        )
                        SELECT DISTINCT ON (
                            COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, part_no, 1),
                            COALESCE(sl_no, 1)
                        )
                            {tenant_id},
                            district,
                            panchayat_samiti,
                            COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, 1),
                            COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, part_no, 1),
                            COALESCE(sl_no, 1),
                            UPPER(TRIM(epic_no)),
                            COALESCE(NULLIF(TRIM(name_hi), ''), NULLIF(TRIM(name_en), ''), 'मतदाता ' || COALESCE(NULLIF(TRIM(epic_no), ''), id::text)),
                            CASE 
                                WHEN name_en IS NOT NULL AND trim(name_en) <> '' AND name_en !~ '[\u0900-\u097F]' THEN trim(name_en) 
                                ELSE NULL 
                            END,
                            LOWER(TRIM(COALESCE(
                                NULLIF(TRIM(CASE WHEN name_en IS NOT NULL AND trim(name_en) <> '' AND name_en !~ '[\u0900-\u097F]' THEN trim(name_en) ELSE NULL END), ''),
                                NULLIF(TRIM(name_hi), ''),
                                'voter'
                            ))),
                            COALESCE(rel_type, 'पिता'),
                            CASE
                                WHEN rel_name_hi ILIKE '%available%' THEN NULL
                                ELSE TRIM(rel_name_hi)
                            END,
                            CASE 
                                WHEN rel_name_en IS NOT NULL AND trim(rel_name_en) <> '' AND rel_name_en !~ '[\u0900-\u097F]' AND rel_name_en !~* 'available' THEN trim(rel_name_en) 
                                ELSE NULL 
                            END,
                            LOWER(TRIM(COALESCE(
                                NULLIF(TRIM(CASE WHEN rel_name_en IS NOT NULL AND trim(rel_name_en) <> '' AND rel_name_en !~ '[\u0900-\u097F]' AND rel_name_en !~* 'available' THEN trim(rel_name_en) ELSE NULL END), ''),
                                NULLIF(TRIM(rel_name_hi), ''),
                                ''
                            ))),
                            TRIM(house_no),
                            COALESCE(age, 18),
                            COALESCE(gender, 'पुरुष'),
                            TRIM(booth_name),
                            TRIM(booth_area),
                            mobile,
                            CASE WHEN mobile IS NOT NULL AND length(mobile) >= 10 THEN 'MASTER_MOBILE' ELSE NULL END,
                            family_id,
                            'ACTIVE'
                        FROM voters_master
                        WHERE district = %s AND gram_panchayat = %s
                          AND status = 'Active'
                          AND panchayat_samiti ILIKE %s
                          AND (
                              (name_hi IS NOT NULL AND length(trim(name_hi)) > 0)
                              OR (name_en IS NOT NULL AND length(trim(name_en)) > 0)
                              OR (epic_no IS NOT NULL AND length(trim(epic_no)) > 0)
                          )
                        ORDER BY 
                            COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, part_no, 1),
                            COALESCE(sl_no, 1),
                            (name_hi IS NOT NULL AND length(trim(name_hi)) > 0 AND name_hi NOT LIKE 'मतदाता%') DESC,
                            id DESC
                        ON CONFLICT (tenant_id, part_no, sl_no) DO UPDATE SET
                            epic_no = COALESCE(NULLIF(EXCLUDED.epic_no, ''), {part_tbl}.epic_no),
                            name_hi = CASE 
                                WHEN EXCLUDED.name_hi NOT LIKE 'मतदाता%' AND length(trim(EXCLUDED.name_hi)) > 0 THEN EXCLUDED.name_hi 
                                ELSE {part_tbl}.name_hi 
                            END,
                            name_en = CASE 
                                WHEN EXCLUDED.name_en IS NOT NULL AND EXCLUDED.name_en NOT LIKE 'Voter%' AND length(trim(EXCLUDED.name_en)) > 0 AND EXCLUDED.name_en !~ '[\u0900-\u097F]' THEN EXCLUDED.name_en 
                                ELSE {part_tbl}.name_en 
                            END,
                            contact_phone = COALESCE(NULLIF(EXCLUDED.contact_phone, ''), {part_tbl}.contact_phone),
                            phone_source_type = COALESCE(NULLIF(EXCLUDED.phone_source_type, ''), {part_tbl}.phone_source_type);
                    """, (district.upper(), matched_gp, f"%{clean_block}%"))
                    count = cur.rowcount
                    pg_conn.commit()

            if count == 0 and elector_records:
                upsert_sql = f"""
                    INSERT INTO {part_tbl} (
                        tenant_id, ward_no, part_no, sl_no, epic_no, name_hi, name_en, name_norm,
                        rel_type, rel_name_hi, rel_name_en, rel_norm, house_no, age, gender,
                        booth_name, booth_area, status, source_file, pdf_page, contact_phone, phone_source_type, family_id
                    ) VALUES %s
                    ON CONFLICT (tenant_id, part_no, sl_no) DO UPDATE SET
                        epic_no = COALESCE(NULLIF(EXCLUDED.epic_no, ''), {part_tbl}.epic_no),
                        name_hi = COALESCE(NULLIF(EXCLUDED.name_hi, ''), {part_tbl}.name_hi),
                        name_en = COALESCE(NULLIF(EXCLUDED.name_en, ''), {part_tbl}.name_en),
                        name_norm = COALESCE(NULLIF(EXCLUDED.name_norm, ''), {part_tbl}.name_norm),
                        contact_phone = COALESCE(NULLIF(EXCLUDED.contact_phone, ''), {part_tbl}.contact_phone),
                        phone_source_type = COALESCE(NULLIF(EXCLUDED.phone_source_type, ''), {part_tbl}.phone_source_type);
                """

                tuples = [
                    (
                        tenant_id, r['ward_no'], r['part_no'], r['sl_no'], r['epic_no'],
                        r['name_hi'], r['name_en'], r['name_norm'], r['rel_type'],
                        r['rel_name_hi'], r['rel_name_en'], r['rel_norm'], r['house_no'],
                        r['age'], r['gender'], r['booth_name'], r['booth_area'],
                        r['status'], r['source_file'], r['pdf_page'],
                        r.get('contact_phone'), r.get('phone_source_type'), r.get('family_id')
                    )
                    for r in elector_records
                ]

                chunk_size = 500
                for i in range(0, len(tuples), chunk_size):
                    chunk = tuples[i:i + chunk_size]
                    psycopg2.extras.execute_values(cur, upsert_sql, chunk, page_size=500)
                pg_conn.commit()
                count = len(tuples)

            # Automated Universal Duplicate Detection Engine
            if count > 0:
                try:
                    from scripts.detect_duplicate_voters import detect_duplicates_in_campaign
                    detect_duplicates_in_campaign(cur, tenant_id, clean_slug, apply=True)
                    pg_conn.commit()
                except Exception as de:
                    logger.warning(f"Duplicate detection post sync_master_voters failed for {clean_slug}: {de}")
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass

        # Update booths in tenants/{slug}/campaign.json if booths found in preview
        preview_booths = {}
        try:
            prev = rajasthan_catalog.get_area_preview(district, block, resolved_gp)
            if prev and prev.get('booths'):
                preview_booths = prev['booths']
        except Exception:
            pass

        if not preview_booths and elector_records:
            derived = {}
            for r in elector_records:
                b_no = str(r.get('part_no') or r.get('ward_no') or '').strip()
                if b_no and b_no not in derived:
                    derived[b_no] = {
                        'booth_no': b_no,
                        'name': r.get('booth_name') or f"Booth #{b_no}",
                        'area': r.get('booth_area') or '',
                        'maps_url': ''
                    }
            preview_booths = derived

        if preview_booths and os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg_data = json.load(f)
                cfg_data['booths'] = preview_booths
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    json.dump(cfg_data, f, ensure_ascii=False, indent=2)
                invalidate_tenant_branding(clean_slug)
            except Exception as be:
                print(f"[BOOTHS_SYNC_ERR] {be}", flush=True)

        clear_fleet_cache()
        return jsonify({
            'success': True,
            'tenant_slug': clean_slug,
            'tenant_id': tenant_id,
            'voters_synced': count,
            'message': f"Successfully linked {count} voters from Master DB to campaign '{clean_slug}'"
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/admin/partitions', methods=['GET'])
def get_partitions_info():
    """
    Returns partition information for PostgreSQL voters_t* tables.
    Includes partition name, row count, table size MB, and index size MB.
    """
    partitions = []
    total_size_bytes = 0
    total_rows = 0
    backend = "postgres_unavailable"

    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        c.relname AS partition_name,
                        COALESCE(s.n_live_tup, c.reltuples::bigint, 0) AS row_count,
                        pg_total_relation_size(c.oid) AS total_size_bytes,
                        pg_relation_size(c.oid) AS table_size_bytes,
                        pg_indexes_size(c.oid) AS index_size_bytes,
                        t.campaign_slug
                    FROM pg_class c
                    JOIN pg_inherits i ON i.inhrelid = c.oid
                    JOIN pg_class p ON p.oid = i.inhparent
                    LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                    LEFT JOIN tenants t ON ('voters_t' || t.tenant_id::text) = c.relname
                    WHERE p.relname = 'voters'
                    ORDER BY c.relname;
                """)
                rows = cur.fetchall()
                if rows:
                    backend = "postgres"
                    for r in rows:
                        p_name = r[0]
                        rc = r[1]
                        if rc <= 0:
                            try:
                                cur.execute(f"SELECT COUNT(*) FROM {p_name};")
                                rc = cur.fetchone()[0]
                            except Exception:
                                rc = 0
                        tot_b = r[2] or 0
                        tbl_b = r[3] or 0
                        idx_b = r[4] or 0
                        t_slug = r[5] or (p_name.replace('voters_t', 'tenant_') if 'voters_t' in p_name else 'system')
                        total_size_bytes += tot_b
                        total_rows += rc

                        tbl_mb = round(tbl_b / (1024 * 1024), 2)
                        idx_mb = round(idx_b / (1024 * 1024), 2)
                        tot_mb = round(tot_b / (1024 * 1024), 2)

                        partitions.append({
                            'partition_name': p_name,
                            'tenant_slug': t_slug,
                            'row_count': rc,
                            'table_size_mb': tbl_mb,
                            'index_size_mb': idx_mb,
                            'total_size_mb': tot_mb,
                            'status': 'Healthy'
                        })
            if backend == "postgres":
                return jsonify({
                    'success': True,
                    'backend': 'postgres',
                    'partitions': partitions,
                    'total_partitions': len(partitions),
                    'total_size_mb': round(total_size_bytes / (1024 * 1024), 2),
                    'total_rows': total_rows
                })
        except Exception as e:
            return jsonify({
                'success': False,
                'backend': 'postgres',
                'error': f'Failed to query PostgreSQL partitions: {e}',
                'partitions': [],
                'total_partitions': 0,
                'total_size_mb': 0.0,
                'total_rows': 0
            })
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass

    return jsonify({
        'success': False,
        'backend': 'postgres',
        'error': 'PostgreSQL connection unavailable',
        'partitions': [],
        'total_partitions': 0,
        'total_size_mb': 0.0,
        'total_rows': 0
    })


@admin_bp.route('/api/admin/system_health', methods=['GET'])
def get_system_health():
    """Returns server RAM, CPU, disk usage, and active database pool status."""
    import os
    import shutil

    repo = current_app.extensions.get('repo')
    backend = getattr(repo, 'storage_backend', 'postgres') if repo else 'postgres'

    db_status = 'connected'
    pool_info = {}
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();")
                active_conns = cur.fetchone()[0]
                pool_info['active_connections'] = active_conns
                pool_info['server_version'] = getattr(pg_conn, 'server_version', 170000)
        except Exception as e:
            db_status = f'degraded: {e}'
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass
    else:
        pool_info['mode'] = 'postgres_unavailable'
        pool_info['status'] = 'operational'

    # CPU, Memory, Disk with optional psutil and stdlib fallbacks
    try:
        import psutil
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(PLATFORM_ROOT)
        cpu_data = {
            'percent': psutil.cpu_percent(interval=None),
            'cores': psutil.cpu_count(logical=True)
        }
        mem_data = {
            'total_mb': round(vm.total / (1024 * 1024), 1),
            'used_mb': round(vm.used / (1024 * 1024), 1),
            'free_mb': round(vm.available / (1024 * 1024), 1),
            'percent': vm.percent
        }
        disk_data = {
            'total_gb': round(disk.total / (1024 ** 3), 1),
            'used_gb': round(disk.used / (1024 ** 3), 1),
            'free_gb': round(disk.free / (1024 ** 3), 1),
            'percent': disk.percent
        }
    except ImportError:
        disk_usage = shutil.disk_usage(PLATFORM_ROOT)
        cores = os.cpu_count() or 1
        
        # Parse /proc/meminfo on Linux if present
        total_mem = 0
        free_mem = 0
        if os.path.exists('/proc/meminfo'):
            try:
                with open('/proc/meminfo', 'r') as f:
                    for line in f:
                        if line.startswith('MemTotal:'):
                            total_mem = int(line.split()[1]) * 1024
                        elif line.startswith('MemAvailable:'):
                            free_mem = int(line.split()[1]) * 1024
            except Exception:
                pass
        
        used_mem = max(0, total_mem - free_mem)
        mem_pct = round((used_mem / total_mem * 100), 1) if total_mem else 0

        cpu_data = {'percent': 0.0, 'cores': cores}
        mem_data = {
            'total_mb': round(total_mem / (1024 * 1024), 1),
            'used_mb': round(used_mem / (1024 * 1024), 1),
            'free_mb': round(free_mem / (1024 * 1024), 1),
            'percent': mem_pct
        }
        disk_pct = round((disk_usage.used / disk_usage.total * 100), 1) if disk_usage.total else 0
        disk_data = {
            'total_gb': round(disk_usage.total / (1024 ** 3), 1),
            'used_gb': round(disk_usage.used / (1024 ** 3), 1),
            'free_gb': round(disk_usage.free / (1024 ** 3), 1),
            'percent': disk_pct
        }

    return jsonify({
        'success': True,
        'status': 'healthy',
        'cpu': cpu_data,
        'memory': mem_data,
        'disk': disk_data,
        'database': {
            'backend': backend,
            'status': db_status,
            'pool': pool_info
        }
    })


@admin_bp.route('/api/admin/campaigns/<slug>/status', methods=['POST'])
def update_campaign_status(slug: str):
    """Updates campaign status ('ACTIVE', 'PAUSED', 'CONCLUDED') in tenants table and campaign.json."""
    if not _reseller_owns_slug(slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403
    data = request.get_json(silent=True) or request.form or {}
    new_status = (data.get('status') or 'ACTIVE').strip().upper()
    valid_statuses = ('ACTIVE', 'PAUSED', 'CONCLUDED', 'ARCHIVED')
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'error': f'Status must be one of {valid_statuses}'}), 400

    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("UPDATE tenants SET status = %s WHERE campaign_slug = %s", (new_status, slug))
            pg_conn.commit()
        except Exception:
            pass
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass

    cfg_file = os.path.join(TENANTS_DIR, slug, 'campaign.json')
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg['status'] = new_status
        except Exception:
            pass

    try:
        import user_manager
        user_manager.sync_users_with_campaign_status(slug, new_status)
    except Exception:
        pass

    clear_fleet_cache()
    return jsonify({'success': True, 'campaign_slug': slug, 'status': new_status})


@admin_bp.route('/api/admin/campaigns/<slug>/delete', methods=['POST'])
@audit_action('admin.campaign_delete', entity_type='campaign', entity_id_arg='slug')
def delete_campaign(slug: str):
    """Archives or permanently deletes a campaign based on mode parameter."""
    data = request.get_json(silent=True) or request.form or {}
    mode = str(data.get('mode') or ('permanent' if data.get('permanent') in (True, 'true', '1', 1) else 'archive')).lower()
    user_id = session.get('user_id')
    role = session.get('role')

    if mode == 'permanent':
        if role != 'super_admin':
            return jsonify({
                'success': False,
                'error': 'केवल सुपर एडमिन ही अभियान को स्थायी रूप से हटा सकते हैं (Permanent campaign deletion is strictly restricted to Super Admin)'
            }), 403
        confirm_slug = str(data.get('confirmation_slug') or '').strip()
        ok, res, status = campaign_lifecycle.purge_campaign_permanent(
            slug=slug,
            confirmation_slug=confirm_slug,
            user_id=user_id,
            role=role
        )
        if ok:
            clear_fleet_cache()
        return jsonify(res), status
    else:
        ok, res, status = campaign_lifecycle.archive_campaign(
            slug=slug,
            user_id=user_id,
            role=role
        )
        if ok:
            clear_fleet_cache()
        return jsonify(res), status


@admin_bp.route('/api/admin/campaigns/<slug>/restore', methods=['POST'])
@audit_action('admin.campaign_restore', entity_type='campaign', entity_id_arg='slug')
def restore_campaign(slug: str):
    """Restores an archived campaign back to ACTIVE."""
    user_id = session.get('user_id')
    role = session.get('role')
    ok, res, status = campaign_lifecycle.restore_campaign(
        slug=slug,
        user_id=user_id,
        role=role
    )
    if ok:
        clear_fleet_cache()
    return jsonify(res), status


@admin_bp.route('/api/admin/campaigns/<slug>/reassign_owner', methods=['POST'])
def reassign_campaign_owner(slug):
    """Reassigns (or clears, with a null owner_user_id) a campaign's reseller owner. Super_admin only."""
    if session.get('role') != 'super_admin':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    data = request.get_json(silent=True) or request.form or {}
    new_owner_id = data.get('owner_user_id')
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE tenants SET owner_user_id = %s WHERE campaign_slug = %s",
                    (int(new_owner_id) if new_owner_id else None, slug)
                )
            pg_conn.commit()
        finally:
            pg_conn.close()
    clear_fleet_cache()
    return jsonify({'success': True, 'campaign_slug': slug, 'owner_user_id': new_owner_id})


@admin_bp.route('/api/admin/campaigns/<slug>/whatsapp_quota', methods=['GET'])
def api_admin_get_whatsapp_quota(slug: str):
    """Current WhatsApp credit balance plus recent ledger for one campaign.

    The slug comes from the URL, never from g.tenant_slug: a super admin's campaign
    switcher points at whatever campaign they last viewed, which is not necessarily
    the one being administered here.
    """
    if not _reseller_owns_slug(slug):
        return jsonify({'success': False, 'error': 'Access denied.'}), 403
    try:
        quota = whatsapp_quota.get_quota(slug)
        ledger = whatsapp_quota.get_ledger(slug, limit=25)
    except whatsapp_quota.QuotaError as qe:
        return jsonify({'success': False, 'error': str(qe)}), 400

    res_data = {'success': True, 'campaign_slug': slug, 'quota': quota, 'ledger': ledger}
    if session.get('role') == 'reseller':
        res_data['reseller_credits'] = user_manager.get_reseller_whatsapp_credits(session.get('user_id'))

    resp = jsonify(res_data)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@admin_bp.route('/api/admin/campaigns/<slug>/whatsapp_quota', methods=['POST'])
def api_admin_update_whatsapp_quota(slug: str):
    """Grants, sets, or blocks a campaign's WhatsApp message credits.

    Body: {action: 'grant'|'set'|'block'|'unblock', amount: int, note: str}
      grant   - add `amount` credits (negative revokes for super admin)
      set     - set the lifetime total to `amount` (super admin only)
      block   - immediately stop all sending for this campaign (super admin only)
      unblock - resume sending (super admin only)
    """
    is_reseller = (session.get('role') == 'reseller')
    performed_by = session.get('user_id')

    if is_reseller:
        if not user_manager.reseller_owns_tenant(performed_by, slug):
            return jsonify({'success': False, 'error': 'Access denied: You do not own this campaign'}), 403
        data = request.get_json(silent=True) or request.form or {}
        action = str(data.get('action') or 'grant').strip().lower()
        if action != 'grant':
            return jsonify({'success': False, 'error': 'रीसेलर केवल क्रेडिट जोड़ सकते हैं (Resellers can only grant credits)'}), 403
        amount_val = data.get('amount')
        try:
            amount_int = int(amount_val)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'वैध क्रेडिट संख्या दर्ज करें (Valid amount required)'}), 400
        if amount_int <= 0:
            return jsonify({'success': False, 'error': 'क्रेडिट संख्या 1 या अधिक होनी चाहिए (Amount must be at least 1)'}), 400

        # Atomically deduct from reseller wallet
        if not user_manager.deduct_reseller_whatsapp_credits(performed_by, amount_int):
            avail = user_manager.get_reseller_whatsapp_credits(performed_by)
            return jsonify({'success': False, 'error': f'अपर्याप्त रीसेलर क्रेडिट। आपके पास केवल {avail:,} क्रेडिट उपलब्ध हैं (Insufficient reseller credits)'}), 400

        note = (data.get('note') or '').strip()
        grant_note = f"Top-up from reseller #{performed_by}" + (f": {note}" if note else "")
        try:
            quota = whatsapp_quota.grant(slug, amount_int, performed_by, grant_note)
        except whatsapp_quota.QuotaError as qe:
            user_manager.refund_reseller_whatsapp_credits(performed_by, amount_int)
            return jsonify({'success': False, 'error': str(qe)}), 400

        clear_fleet_cache()
        reseller_balance = user_manager.get_reseller_whatsapp_credits(performed_by)
        return jsonify({
            'success': True,
            'campaign_slug': slug,
            'action': 'grant',
            'quota': quota,
            'reseller_credits': reseller_balance,
            'message': f"WhatsApp संदेश सीमा अद्यतन ({slug}): {quota['available']} शेष"
        })

    # Super Admin execution
    data = request.get_json(silent=True) or request.form or {}
    action = str(data.get('action') or 'set').strip().lower()
    note = (data.get('note') or '').strip() or None

    if action in ('grant', 'set'):
        amount_val = data.get('amount')
        if amount_val is None or str(amount_val).strip() == '':
            return jsonify({'success': False, 'error': 'amount parameter is required'}), 400
        try:
            amount_int = int(amount_val)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'amount must be an integer'}), 400
        if action == 'set' and amount_int < 0:
            return jsonify({'success': False, 'error': 'amount must be zero or greater'}), 400
    elif action == 'set_price':
        price_val = data.get('price_per_message', data.get('amount'))
        if price_val is None or str(price_val).strip() == '':
            return jsonify({'success': False, 'error': 'price_per_message parameter is required'}), 400
        try:
            price_float = float(price_val)
            if price_float <= 0:
                return jsonify({'success': False, 'error': 'price must be greater than zero'}), 400
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'price must be a valid number'}), 400

    try:
        if action == 'grant':
            quota = whatsapp_quota.grant(slug, amount_int, performed_by, note)
        elif action == 'set':
            quota = whatsapp_quota.set_granted(slug, amount_int, performed_by, note)
        elif action == 'set_price':
            quota = whatsapp_quota.set_price_per_message(slug, price_float, performed_by, note)
        elif action == 'block':
            quota = whatsapp_quota.set_blocked(slug, True, performed_by, note)
        elif action == 'unblock':
            quota = whatsapp_quota.set_blocked(slug, False, performed_by, note)
        else:
            return jsonify({'success': False,
                            'error': "action must be one of grant, set, set_price, block, unblock"}), 400
    except whatsapp_quota.QuotaError as qe:
        return jsonify({'success': False, 'error': str(qe)}), 400

    clear_fleet_cache()
    return jsonify({
        'success': True,
        'campaign_slug': slug,
        'action': action,
        'quota': quota,
        'message': f"WhatsApp संदेश सीमा अद्यतन ({slug}): {quota['available']} शेष "
                   f"(quota updated, {quota['available']} credits remaining)"
    })


@admin_bp.route('/api/admin/campaigns/<slug>/sub_account_limit', methods=['POST'])
def api_admin_update_sub_account_limit(slug: str):
    """Sets sub-account creation limit for a campaign."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    data = request.get_json(silent=True) or request.form or {}
    limit_val = data.get('limit')
    if limit_val is None:
        return jsonify({'success': False, 'error': 'limit parameter is required'}), 400
    try:
        limit_int = int(limit_val)
        if limit_int < 0:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'limit must be a positive integer'}), 400

    ok = user_manager.set_tenant_sub_account_limit(slug, limit_int)
    cfg_file = os.path.join(TENANTS_DIR, slug, 'campaign.json')
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg['max_sub_accounts'] = limit_int
            with open(cfg_file, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    quota = user_manager.get_sub_account_quota(slug)
    return jsonify({'success': ok, 'campaign_slug': slug, 'limit': limit_int, 'quota': quota})



@admin_bp.route('/api/admin/users', methods=['GET'])
def list_users():
    """Lists users with their RBAC role tiers and booth assignments directly from PostgreSQL."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    tenant_slug = request.args.get('tenant') or request.args.get('tenant_slug')
    if tenant_slug and tenant_slug != '*' and str(tenant_slug).strip().upper() != 'ALL':
        users = user_manager.list_users(tenant_slug=tenant_slug, exclude_super_admin=True)
    else:
        users = user_manager.list_users()
    return jsonify({'success': True, 'users': users})


@admin_bp.route('/api/admin/users/create', methods=['POST'])
@audit_action('admin.user_create', entity_type='user')
def create_user():
    """Creates a user with a specific 5-Tier role and assigned booths in PostgreSQL."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    data = request.get_json(silent=True) or request.form or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '').strip()
    role_input = data.get('role') or data.get('role_str') or data.get('role_tier')
    role_to_tier = {
        'super_admin': 1,
        'candidate_admin': 2,
        'admin': 2,
        'operator': 2,
        'reseller': 2,
        'booth_agent': 3,
        'karyakarta': 4,
        'viewer': 5
    }
    tier_to_role = {
        1: 'super_admin',
        2: 'candidate_admin',
        3: 'booth_agent',
        4: 'karyakarta',
        5: 'viewer'
    }

    if isinstance(role_input, str) and not role_input.isdigit():
        role_str = role_input.lower().strip()
        role_tier = role_to_tier.get(role_str, 2)
    elif role_input is not None:
        try:
            role_tier = int(role_input)
            role_str = tier_to_role.get(role_tier, 'candidate_admin')
        except Exception:
            role_tier = 2
            role_str = 'candidate_admin'
    else:
        role_tier = 2
        role_str = 'candidate_admin'

    tenant_slug = str(data.get('tenant_slug') or '').strip()
    full_name = str(data.get('full_name') or data.get('name') or username).strip()
    assigned_booths = data.get('assigned_booths', [])
    assigned_booth = data.get('assigned_booth')
    assigned_wards = data.get('assigned_wards', [])
    if assigned_wards is not None and not isinstance(assigned_wards, list):
        try:
            if isinstance(assigned_wards, str):
                assigned_wards = [int(s.strip()) for s in assigned_wards.split(',') if s.strip().isdigit()]
            else:
                assigned_wards = [int(assigned_wards)]
        except Exception:
            assigned_wards = []
    max_campaigns_val = data.get('max_campaigns')
    max_campaigns = None
    if max_campaigns_val is not None and str(max_campaigns_val).strip() != '':
        try:
            max_campaigns = int(max_campaigns_val)
        except (ValueError, TypeError):
            max_campaigns = None

    whatsapp_credits_val = data.get('whatsapp_credits')
    whatsapp_credits = None
    if whatsapp_credits_val is not None and str(whatsapp_credits_val).strip() != '':
        try:
            whatsapp_credits = int(whatsapp_credits_val)
        except (ValueError, TypeError):
            whatsapp_credits = None

    if assigned_booth is not None and not assigned_booths:
        try:
            assigned_booths = [int(assigned_booth)]
        except Exception:
            pass

    if not username or not password:
        return jsonify({'success': False, 'error': 'username and password required'}), 400

    if role_str not in ('super_admin', 'reseller') and not tenant_slug:
        return jsonify({'success': False, 'error': 'tenant_slug is required for campaign accounts'}), 400

    first_booth = assigned_booths[0] if (assigned_booths and isinstance(assigned_booths, list)) else assigned_booth

    ok, msg, u_id = user_manager.create_user_account(
        username=username,
        password=password,
        full_name=full_name,
        phone=data.get('phone', ''),
        role=role_str,
        tenant_slug=tenant_slug if role_str not in ('super_admin', 'reseller') else '',
        assigned_booth=first_booth,
        assigned_booths=assigned_booths,
        assigned_wards=assigned_wards,
        max_campaigns=max_campaigns,
        whatsapp_credits=whatsapp_credits,
        overwrite_password=True
    )
    if ok:
        return jsonify({'success': True, 'user_id': u_id, 'username': username}), 201
    return jsonify({'success': False, 'error': msg}), 400


@admin_bp.route('/api/admin/users/<int:user_id>/delete', methods=['POST'])
@audit_action('admin.user_delete', entity_type='user', entity_id_arg='user_id')
def delete_user(user_id: int):
    """Deletes user from PostgreSQL users table."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    deleted = user_manager.delete_user_account(user_id)
    return jsonify({
        'success': True,
        'user_id': user_id,
        'message': 'User deleted successfully',
        'deleted': deleted
    })


@admin_bp.route('/api/admin/users/<int:user_id>', methods=['GET'])
def get_user_profile(user_id: int):
    """Returns single user details for editing."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    u = user_manager.get_user_by_id(user_id)
    if not u:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    return jsonify({'success': True, 'user': u})


@admin_bp.route('/api/admin/users/<int:user_id>/update', methods=['POST'])
@audit_action('admin.user_update', entity_type='user', entity_id_arg='user_id')
def update_user_profile(user_id: int):
    """Updates user fields in PostgreSQL."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    data = request.get_json(silent=True) or request.form or {}
    username = data.get('username')
    role_tier = data.get('role_tier')
    role = data.get('role')
    tenant_slug = data.get('tenant_slug')
    password = data.get('password')
    full_name = data.get('full_name') or data.get('name')
    phone = data.get('phone')
    assigned_booths = data.get('assigned_booths')
    assigned_wards = data.get('assigned_wards')
    is_active = data.get('is_active')
    max_campaigns = data.get('max_campaigns')
    whatsapp_credits = data.get('whatsapp_credits')

    if assigned_booths is not None and not isinstance(assigned_booths, list):
        try:
            if isinstance(assigned_booths, str):
                assigned_booths = [int(s.strip()) for s in assigned_booths.split(',') if s.strip().isdigit()]
            else:
                assigned_booths = [int(assigned_booths)]
        except Exception:
            assigned_booths = []

    if assigned_wards is not None and not isinstance(assigned_wards, list):
        try:
            if isinstance(assigned_wards, str):
                assigned_wards = [int(s.strip()) for s in assigned_wards.split(',') if s.strip().isdigit()]
            else:
                assigned_wards = [int(assigned_wards)]
        except Exception:
            assigned_wards = []

    try:
        ok = user_manager.update_user_account(
            user_id=user_id,
            username=username,
            full_name=full_name,
            phone=phone,
            role=role,
            role_tier=role_tier,
            tenant_slug=tenant_slug,
            password=password,
            assigned_booths=assigned_booths,
            assigned_wards=assigned_wards,
            max_campaigns=max_campaigns,
            whatsapp_credits=whatsapp_credits,
            is_active=is_active
        )
        updated_user = user_manager.get_user_by_id(user_id)
        return jsonify({'success': ok, 'message': 'User profile updated successfully', 'user': updated_user})
    except ValueError as ve:
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        logger.error("Admin user update error for user_id %s: %s", user_id, e)
        return jsonify({'success': False, 'error': f'अद्यतन विफल: {str(e)}'}), 500


@admin_bp.route('/api/admin/users/reset_password', methods=['POST'])
@admin_bp.route('/api/admin/users/<int:user_id>/reset_password', methods=['POST'])
def api_admin_users_reset_password(user_id: Optional[int] = None):
    """Resets user password in PostgreSQL."""
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    data = request.get_json(silent=True) or request.form or {}
    uid_raw = user_id or data.get('user_id') or data.get('id')
    new_pwd = data.get('password')

    if not uid_raw:
        return jsonify({'success': False, 'error': 'user_id required'}), 400

    try:
        uid = int(uid_raw)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid user_id'}), 400

    ok, final_pwd = user_manager.reset_user_password(uid, new_pwd)
    if ok:
        return jsonify({'success': True, 'password': final_pwd})
    return jsonify({'success': False, 'error': str(final_pwd)}), 400



# ---------------------------------------------------------------------------
# Rajasthan Electoral Catalog REST APIs (SEC 2026 Fleet)
# ---------------------------------------------------------------------------

@admin_bp.route('/api/admin/rajasthan/districts', methods=['GET'])
def get_rajasthan_districts():
    """Returns all 47 Rajasthan districts with block & GP metrics."""
    from electionapp import rajasthan_catalog
    return jsonify({
        'success': True,
        'districts': rajasthan_catalog.get_districts()
    })


@admin_bp.route('/api/admin/rajasthan/blocks', methods=['GET'])
def get_rajasthan_blocks():
    """Returns Panchayat Samitis / Blocks for specified district."""
    from electionapp import rajasthan_catalog
    district = request.args.get('district', '')
    blocks = rajasthan_catalog.get_blocks(district)
    return jsonify({
        'success': True,
        'district': district,
        'blocks': blocks
    })


@admin_bp.route('/api/admin/rajasthan/ulbs', methods=['GET'])
def get_rajasthan_ulbs():
    """Returns Urban Local Bodies (Nagar Palikas, Nagar Parishads, Nagar Nigams) for specified district."""
    from electionapp import rajasthan_catalog
    district = request.args.get('district', '')
    ulb_type = request.args.get('type', '')
    ulbs = rajasthan_catalog.get_ulbs(district, ulb_type=ulb_type or None)
    return jsonify({
        'success': True,
        'district': district,
        'ulbs': ulbs,
        'count': len(ulbs)
    })


@admin_bp.route('/api/admin/rajasthan/gps', methods=['GET'])
def get_rajasthan_gps():
    """Returns Gram Panchayats for specified district and block."""
    from electionapp import rajasthan_catalog
    district = request.args.get('district', '')
    block = request.args.get('block', '')
    gps = rajasthan_catalog.get_gps(district, block)
    return jsonify({
        'success': True,
        'district': district,
        'block': block,
        'gps': gps
    })


@admin_bp.route('/api/admin/rajasthan/ps_wards', methods=['GET'])
def get_rajasthan_ps_wards():
    """Returns official delimitation wards & constituent GPs for a Panchayat Samiti."""
    from electionapp import rajasthan_catalog
    district = request.args.get('district', '')
    block = request.args.get('block', '')
    wards = rajasthan_catalog.get_panchayat_samiti_wards(district, block)
    return jsonify({
        'success': True,
        'district': district,
        'block': block,
        'has_official_delimitation': len(wards) > 0,
        'wards': wards
    })



@admin_bp.route('/api/admin/rajasthan/gp_wards', methods=['GET'])
def get_rajasthan_gp_wards():
    """Returns distinct wards and elector counts for a specific Gram Panchayat."""
    district = (request.args.get('district') or '').strip()
    block = (request.args.get('block') or '').strip()
    gp = (request.args.get('gp') or '').strip()

    if not gp:
        return jsonify({'success': False, 'error': 'gp parameter is required', 'wards': []}), 400

    from electionapp import rajasthan_catalog
    candidate_districts = rajasthan_catalog.get_candidate_districts(district) if district else []
    wards = []

    m_conn = rajasthan_catalog.get_master_db_connection() or get_pg_connection()
    if m_conn:
        try:
            with m_conn.cursor() as cur:
                canonical_gp = rajasthan_catalog.resolve_canonical_gp(district, block, gp) if district and block else gp
                gp_candidates = [gp]
                if canonical_gp and canonical_gp != gp:
                    gp_candidates.append(canonical_gp)
                if gp.upper() not in gp_candidates:
                    gp_candidates.append(gp.upper())
                if gp.title() not in gp_candidates:
                    gp_candidates.append(gp.title())

                for cand in gp_candidates:
                    if candidate_districts:
                        cur.execute("""
                            SELECT DISTINCT ward_no, count(*) as voters
                            FROM voters_master
                            WHERE district = ANY(%s) AND gram_panchayat = %s AND ward_no IS NOT NULL AND ward_no > 0
                            GROUP BY ward_no
                            ORDER BY ward_no ASC;
                        """, (candidate_districts, cand))
                    else:
                        cur.execute("""
                            SELECT DISTINCT ward_no, count(*) as voters
                            FROM voters_master
                            WHERE gram_panchayat = %s AND ward_no IS NOT NULL AND ward_no > 0
                            GROUP BY ward_no
                            ORDER BY ward_no ASC;
                        """, (cand,))
                    rows = cur.fetchall()
                    if rows:
                        wards = [{'ward_no': int(r[0]), 'voters': int(r[1])} for r in rows]
                        break
        except Exception as e:
            logger.warning(f"Error fetching wards from voters_master: {e}")
        finally:
            try:
                m_conn.close()
            except Exception:
                pass

    # Fallback to local SEC catalog files if voters_master had no rows
    if not wards:
        sec_recs = rajasthan_catalog.load_gp_voter_records(district, block, gp=gp)
        if sec_recs:
            ward_counts = {}
            for r in sec_recs:
                w = int(r.get('ward_no') or 1)
                ward_counts[w] = ward_counts.get(w, 0) + 1
            wards = [{'ward_no': w, 'voters': c} for w, c in sorted(ward_counts.items())]

    return jsonify({
        'success': True,
        'district': district,
        'block': block,
        'gp': gp,
        'wards': wards,
        'total_wards': len(wards),
        'total_voters': sum(w['voters'] for w in wards)
    })


@admin_bp.route('/api/admin/rajasthan/area_preview', methods=['GET'])
def get_rajasthan_area_preview():
    """Fast-scans ward JSON rolls to return elector count, booths, and suggested metadata."""
    from electionapp import rajasthan_catalog
    district = request.args.get('district', '')
    block = request.args.get('block', '')
    gp = request.args.get('gp', '')
    gps = request.args.getlist('gps') or request.args.get('gps')
    preview = rajasthan_catalog.get_area_preview(district, block, gp=gp, gps=gps)
    return jsonify(preview)


@admin_bp.route('/api/admin/rajasthan/assembly_constituencies', methods=['GET'])
def get_rajasthan_assembly_constituencies():
    """Returns standard list of 200 Rajasthan Assembly Constituencies (Vidhan Sabha), optionally filtered by district."""
    from electionapp import rajasthan_catalog
    district = (request.args.get('district') or '').strip()
    return jsonify({
        'success': True,
        'district': district,
        'constituencies': rajasthan_catalog.get_assembly_constituencies(district=district)
    })


@admin_bp.route('/api/admin/rajasthan/resolve', methods=['GET', 'POST'])
def resolve_rajasthan_client():
    """
    Intelligent NLP & Phonetic Resolver for Rajasthan Electoral Areas.
    Takes freeform conversational text (e.g. 'आलड़ी जालोर रानीवाड़ा' or 'Ajaysar Ajmer')
    and returns exact Gram Panchayat, Block, District, booths, and elector counts.
    """
    from scripts import client_provisioner

    query = ""
    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        query = data.get('query') or data.get('q') or ''
    else:
        query = request.args.get('q') or request.args.get('query') or ''

    if not query.strip():
        return jsonify({'success': False, 'error': 'query parameter (q) is required'}), 400

    try:
        resolved = client_provisioner.resolve_client_area(query.strip())
        return jsonify({
            'success': True,
            'query': query,
            'district': resolved['district'],
            'block': resolved['block'],
            'gp_name': resolved['gp_name'],
            'suggested_slug': resolved['suggested_slug'],
            'preview': resolved['preview'],
            'candidates': resolved.get('candidates', [])
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 404


@admin_bp.route('/api/admin/clients/provision', methods=['POST'])
def provision_client_endpoint():
    """
    1-Click Automated Client Campaign & User Provisioner with optional EC2 deployment.
    """
    if session.get('role') == 'reseller':
        return jsonify({'success': False, 'error': 'Super Admin access required'}), 403
    from scripts import client_provisioner

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    query = data.get('query') or data.get('q') or ''
    dist = data.get('district')
    blk = data.get('block')
    gp = data.get('gp')
    slug = data.get('slug')
    cand_hi = data.get('candidate_name_hi', 'सरपंच प्रत्याशी')
    cand_en = data.get('candidate_name_en', 'Sarpanch Candidate')
    deploy_ec2 = data.get('deploy', False) or data.get('deploy_to_ec2', False)
    if isinstance(deploy_ec2, str):
        deploy_ec2 = deploy_ec2.lower() in ['1', 'true', 'yes']

    try:
        if not (dist and blk and gp):
            if not query:
                return jsonify({'success': False, 'error': 'Either query (q) or (district, block, gp) must be provided'}), 400
            res = client_provisioner.resolve_client_area(query, district=dist, block=blk, gp=gp)
            dist = res['district']
            blk = res['block']
            gp = res['gp_name']
            if not slug:
                slug = res['suggested_slug']

        local_info = client_provisioner.provision_local_campaign(
            district=dist,
            block=blk,
            gp=gp,
            slug=slug,
            candidate_name_hi=cand_hi,
            candidate_name_en=cand_en
        )

        ec2_info = None
        if deploy_ec2:
            ec2_info = client_provisioner.deploy_campaign_to_ec2(local_info['slug'])

        clear_fleet_cache()
        return jsonify({
            'success': True,
            'local': local_info,
            'ec2': ec2_info,
            'message': f"Client campaign '{local_info['slug']}' provisioned successfully" + (" and deployed to EC2" if ec2_info else "")
        }), 201
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/admin/audit-log')
@super_admin_required
def audit_log_page():
    """Renders the cross-tenant Audit Logs viewer. Strictly super_admin — unlike
    most routes in this blueprint, resellers are NOT granted access (the blueprint's
    `enforce_admin_access` before_request allows 'reseller' through, so this route
    carries its own `@super_admin_required` check on top of that)."""
    return render_template('audit_log.html')


@admin_bp.route('/api/admin/audit_log', methods=['GET'])
@super_admin_required
def get_audit_log():
    """Cross-tenant super-admin audit log viewer, queried directly off the
    PostgreSQL `audit_log` table (schema + writes owned by electionapp/audit.py —
    not touched here).

    Scope note: SQLite-backed tenants keep their own `audit_log` table inside their
    per-tenant file with no `tenant_id` column at all, so there is no single query
    that could span them. admin.py's existing cross-tenant reads (`_gather_all_campaigns`,
    `get_partitions_info`) only ever aggregate summary counts per tenant — none of
    them fan out and read per-row data out of every tenant's SQLite file — so there's
    no established fan-out helper to reuse here. Building one just for this view
    would be new infrastructure beyond "view + filter + paginate", so v1 scopes this
    route to the Postgres `audit_log` table only. SQLite-tenant activity is not
    represented in this view.
    """
    pg_conn = get_pg_connection()
    if not pg_conn:
        return jsonify({'success': False, 'error': 'PostgreSQL connection unavailable', 'rows': [], 'total': 0}), 503

    tenant_param = (request.args.get('tenant') or '').strip()
    user_param = (request.args.get('user_id') or '').strip()
    action_param = (request.args.get('action') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

    try:
        limit = int(request.args.get('limit', 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    try:
        offset = int(request.args.get('offset', 0))
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)

    where = []
    params: List[Any] = []

    if tenant_param:
        if tenant_param.isdigit():
            where.append("a.tenant_id = %s")
            params.append(int(tenant_param))
        else:
            where.append("t.campaign_slug = %s")
            params.append(tenant_param)
    if user_param:
        if user_param.isdigit():
            where.append("a.user_id = %s")
            params.append(int(user_param))
        else:
            where.append("a.username ILIKE %s")
            params.append(f"%{user_param}%")
    if action_param:
        where.append("a.action ILIKE %s")
        params.append(f"%{action_param}%")
    if date_from:
        where.append("a.created_at >= %s")
        params.append(date_from)
    if date_to:
        # Inclusive of the whole "to" day when only a date (no time) is supplied.
        where.append("a.created_at <= %s")
        params.append(date_to if len(date_to) > 10 else f"{date_to} 23:59:59")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    try:
        import psycopg2.extras
        with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT a.id, a.tenant_id, t.campaign_slug AS tenant_slug, a.user_id,
                       a.username, a.action, a.entity_type, a.entity_id,
                       a.ip_address, a.user_agent, a.created_at
                FROM audit_log a
                LEFT JOIN tenants t ON t.tenant_id = a.tenant_id
                {where_sql}
                ORDER BY a.created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset]
            )
            rows = [dict(r) for r in cur.fetchall()]

            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM audit_log a LEFT JOIN tenants t ON t.tenant_id = a.tenant_id {where_sql}",
                params
            )
            total = cur.fetchone()['cnt']

        for row in rows:
            if row.get('created_at') is not None:
                row['created_at'] = row['created_at'].isoformat()
            if row.get('ip_address') is not None:
                row['ip_address'] = str(row['ip_address'])

        return jsonify({'success': True, 'rows': rows, 'total': total, 'limit': limit, 'offset': offset})
    except Exception as e:
        logger.exception("audit_log query failed")
        return jsonify({'success': False, 'error': str(e), 'rows': [], 'total': 0}), 500
    finally:
        try:
            pg_conn.close()
        except Exception:
            pass
