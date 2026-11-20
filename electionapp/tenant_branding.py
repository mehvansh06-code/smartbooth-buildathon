"""
tenant_branding.py
Modular Tenant Branding & Asset Resolution Service for Chunav Saathi Platform.

Responsibilities:
- Dynamic resolution of candidate profile, party, EVM ballot serial, symbol, and appeal message per tenant.
- Completely eliminates hardcoded candidate/party/booth constants across card generators and WhatsApp templates.
- Thread-safe in-memory caching of base64 assets with instant invalidation on campaign edits.
"""

import os
import base64
import re
from urllib.request import pathname2url
import threading
from typing import Dict, Any, Optional
from electionapp.config import PLATFORM_ROOT, TENANTS_DIR, load_tenant_config, get_tenant_assets_dir, get_voting_method

_BRANDING_CACHE: Dict[str, Any] = {}
_BRANDING_LOCK = threading.Lock()


def invalidate_tenant_branding(tenant_slug: Optional[str] = None) -> None:
    """Evicts cached branding data and base64 assets for a tenant, or all tenants."""
    with _BRANDING_LOCK:
        if tenant_slug:
            _BRANDING_CACHE.pop(tenant_slug, None)
        else:
            _BRANDING_CACHE.clear()


def _encode_image_to_data_uri(file_path: str) -> str:
    """Reads an image file and converts to base64 data URI string."""
    if not file_path or not os.path.exists(file_path):
        return ""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        mime = 'image/jpeg' if ext in ['.jpg', '.jpeg'] else 'image/png'
        with open(file_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('ascii')
            return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


def _file_uri(file_path: str) -> str:
    """Absolute path -> percent-encoded file:// URL for Chrome.

    Used instead of base64 data: URIs so Blink's MemoryCache can cache the
    decoded bitmap across renders. A data: URI is not cacheable, so the
    1.4MB candidate photo was being re-inflated on every single slip.

    Percent-encoding is mandatory: tenant asset paths may contain spaces or
    Devanagari. A malformed file:// URL fails *silently* in Chrome (blank
    image box, no error), so never hand-concatenate this.
    """
    if not file_path or not os.path.exists(file_path):
        return ""
    try:
        return "file:" + pathname2url(os.path.abspath(file_path))
    except Exception:
        return ""


def get_tenant_custom_background(tenant_slug: str) -> Optional[str]:
    """
    Checks if a custom user-designed background poster exists for the tenant.
    Checks:
    1. tenants/{slug}/assets/voter_card_background.png / .jpg
    2. Desktop / project root fallbacks
    """
    assets_dir = get_tenant_assets_dir(tenant_slug)
    tenant_bg_names = [
        'voter_card_background.png', 'voter_card_background.jpg',
        'voter_slip_background.png', 'voter_slip_background.jpg',
        'custom_background.png', 'custom_background.jpg'
    ]
    for fn in tenant_bg_names:
        p = os.path.join(assets_dir, fn)
        if os.path.exists(p) and os.path.getsize(p) > 2000:
            return p

    # Global Desktop / Platform fallback
    desktop_dirs = [
        os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop'),
        os.path.join(os.path.expanduser('~'), 'Desktop'),
        PLATFORM_ROOT
    ]
    for d in desktop_dirs:
        for fn in tenant_bg_names:
            p = os.path.join(d, fn)
            if os.path.exists(p) and os.path.getsize(p) > 2000:
                return p
    return None


def _clean_str(val: Any) -> str:
    """Sanitizes any string from tenant config, auto-repairing or stripping Mojibake."""
    if not val:
        return ''
    s = str(val).strip()
    if any(m in s for m in ('à¤', 'à¥', 'Ã', 'â', 'Â')):
        for enc in ('latin1', 'cp1252', 'iso-8859-1'):
            try:
                fixed = s.encode(enc).decode('utf-8')
                if not any(m in fixed for m in ('à¤', 'à¥', 'Ã')):
                    s = fixed
                    break
            except Exception:
                pass
        if any(m in s for m in ('à¤', 'à¥', 'Ã')):
            return ''
    return s


def get_tenant_branding(tenant_slug: str = 'ward82') -> Dict[str, Any]:
    """
    Returns fully resolved branding and asset dictionary for a specific tenant.
    Thread-safe and cached with file mtime verification for zero-downtime multi-worker sync.
    """
    tenant_slug = (tenant_slug or 'ward82').strip().lower()

    cfg_file = os.path.join(TENANTS_DIR, tenant_slug, 'campaign.json')
    current_mtime = os.path.getmtime(cfg_file) if os.path.exists(cfg_file) else 0.0

    with _BRANDING_LOCK:
        cached = _BRANDING_CACHE.get(tenant_slug)
        if cached:
            # Handle both legacy dict format and (mtime, dict) format safely
            if isinstance(cached, tuple) and len(cached) == 2:
                if cached[0] == current_mtime and current_mtime > 0:
                    return cached[1]
            elif isinstance(cached, dict) and current_mtime == 0.0:
                return cached

    try:
        cfg = load_tenant_config(tenant_slug)
    except Exception:
        # Fallback to ward82 if specified tenant config fails to load
        try:
            cfg = load_tenant_config('ward82')
        except Exception:
            cfg = {}

    candidate = cfg.get('candidate', {})
    party = cfg.get('party', {})
    election = cfg.get('election', {})
    booths = cfg.get('booths', {})
    campaign_msg = cfg.get('campaign_message')

    # Asset paths
    assets_dir = get_tenant_assets_dir(tenant_slug)

    # 1. Candidate Photo
    cand_fn = (candidate.get('photo_filename') or '').strip()
    fallback_fn = (candidate.get('fallback_photo_filename') or '').strip()

    candidate_file = None
    candidate_candidates = []
    if cand_fn:
        candidate_candidates.append(os.path.join(assets_dir, cand_fn))
    if fallback_fn:
        candidate_candidates.append(os.path.join(assets_dir, fallback_fn))

    for c_path in candidate_candidates:
        if c_path and os.path.exists(c_path) and os.path.getsize(c_path) > 500:
            candidate_file = c_path
            break

    candidate_src = _encode_image_to_data_uri(candidate_file) if candidate_file else ""

    # 2. Party Symbol
    symbol_fn = (party.get('symbol_filename') or '').strip()
    symbol_file = None
    symbol_candidates = []
    persistent_assets = os.path.join(PLATFORM_ROOT, 'data', tenant_slug, 'assets')
    if symbol_fn:
        symbol_candidates.append(os.path.join(assets_dir, symbol_fn))
        symbol_candidates.append(os.path.join(persistent_assets, symbol_fn))
        symbol_candidates.append(os.path.join(PLATFORM_ROOT, symbol_fn))
    else:
        symbol_candidates.append(os.path.join(assets_dir, 'symbol.png'))
        symbol_candidates.append(os.path.join(assets_dir, 'symbol.jpg'))
        symbol_candidates.append(os.path.join(persistent_assets, 'symbol.png'))

    for s_path in symbol_candidates:
        if s_path and os.path.exists(s_path) and os.path.getsize(s_path) > 500:
            symbol_file = s_path
            break

    # Dynamic fallback: If no symbol file in assets yet, try resolving from preset / symbol name
    if not symbol_file:
        sym_name = party.get('symbol_name_hi') or party.get('symbol_name_en') or party.get('preset_symbol')
        from electionapp.campaign_manager import _resolve_preset_symbol_path
        static_fallback = _resolve_preset_symbol_path(sym_name)
        if static_fallback and os.path.exists(static_fallback) and os.path.getsize(static_fallback) > 500:
            symbol_file = static_fallback

    lotus_src = _encode_image_to_data_uri(symbol_file) if symbol_file else ""
    has_symbol = bool(lotus_src)

    # 3. Ballot & Party Strings
    ballot_no = str(party.get('ballot_serial') or party.get('ballot_serial_no') or '').strip()
    has_ballot = bool(ballot_no)
    symbol_name_hi = _clean_str(party.get('symbol_name_hi'))
    symbol_name_en = _clean_str(party.get('symbol_name_en'))
    party_name_hi = _clean_str(party.get('name_hi'))
    party_name_en = _clean_str(party.get('name_en'))

    cand_hi = _clean_str(candidate.get('name_hi'))
    cand_en = _clean_str(candidate.get('name_en'))
    is_placeholder_en = bool(re.search(r'\b(candidate|sarpanch|pratyashi|panchayat|zila|parishad|ward)\b', cand_en, re.IGNORECASE))
    if cand_hi and cand_en and cand_hi.lower() != cand_en.lower() and not is_placeholder_en:
        candidate_disp = f"{cand_hi} ({cand_en})"
    else:
        candidate_disp = cand_hi or (cand_en if not is_placeholder_en else '') or ''

    has_candidate = bool(candidate_src or candidate_disp)

    election_type = str(election.get('election_type') or 'MUNICIPAL').strip().upper()
    voting_method = get_voting_method(
        election_type,
        party.get('voting_method') or cfg.get('voting_method') or election.get('voting_method')
    )
    is_ballot_election = (voting_method == 'BALLOT_PAPER')
    ballot_color = str(party.get('ballot_color') or cfg.get('ballot_color') or election.get('ballot_color') or '').strip()

    is_panchayat = (election_type == 'PANCHAYAT')
    if is_panchayat:
        cand_title = "सरपंच प्रत्याशी"
    elif election_type == 'PANCHAYAT_SAMITI':
        cand_title = f"{party_name_hi} प्रत्याशी" if party_name_hi else "पंचायत समिति सदस्य प्रत्याशी"
    elif election_type == 'ZILA_PARISHAD':
        cand_title = f"{party_name_hi} प्रत्याशी" if party_name_hi else "जिला परिषद सदस्य प्रत्याशी"
    else:
        cand_title = f"{party_name_hi} प्रत्याशी" if party_name_hi else "प्रत्याशी"

    if ballot_no and symbol_name_hi:
        party_line = f"{cand_title} (क्रम संख्या {ballot_no} पर {symbol_name_hi} का निशान)"
    elif symbol_name_hi:
        party_line = f"{cand_title} (चुनाव चिन्ह: {symbol_name_hi})"
    elif ballot_no:
        party_line = f"{cand_title} (क्रम संख्या: {ballot_no})"
    elif party_name_hi:
        party_line = f"{party_name_hi} प्रत्याशी"
    else:
        party_line = cand_title

    ward_hi = _clean_str(election.get('ward_name_hi') or election.get('ward_name_en'))
    district = _clean_str(election.get('district'))
    place_hi = f"{ward_hi} {district}".strip()

    slogan = _clean_str(party.get('slogan'))
    if any(m in slogan for m in ('à¤', 'à¥', 'Ã', 'â')):
        try:
            slogan = slogan.encode('latin1').decode('utf-8')
        except Exception:
            slogan = ''
    if not slogan or any(m in slogan for m in ('à¤', 'à¥', 'Ã', 'â')):
        slogan = 'मतदान हमारा अधिकार एवं कर्तव्य है' if not party_name_hi else 'राष्ट्रहित सर्वोपरि'
    theme_color = str(party.get('theme_color') or '#2563eb').strip()

    if not campaign_msg:
        if ward_hi and party_name_hi and cand_hi:
            campaign_msg = f"{ward_hi} से {party_name_hi} प्रत्याशी {cand_hi} को अपना अमूल्य वोट देकर भारी मतों से विजयी बनाएं।"
        else:
            campaign_msg = "सादर प्रणाम! लोकतंत्र के महापर्व में मतदान अवश्य करें।"
    else:
        # Resolve dynamic placeholders in user-configured campaign message
        gp_clean = (ward_hi or '').replace('ग्राम पंचायत', '').strip()
        campaign_msg = (
            str(campaign_msg)
            .replace('{candidate_name}', cand_hi or cand_en)
            .replace('{candidate}', cand_hi or cand_en)
            .replace('{gp_name}', gp_clean)
            .replace('{ward_name}', ward_hi)
        )

    def _get_booth_info(part_no: Any) -> Dict[str, Any]:
        """Lookup booth info by part number, supporting int and str keys."""
        p_str = str(part_no)
        b_data = booths.get(p_str) or booths.get(int(p_str) if p_str.isdigit() else p_str) or {}
        if isinstance(b_data, str):
            clean_n = _clean_str(b_data)
            return {'name': clean_n, 'name_en': clean_n, 'area': '', 'maps_url': ''}
        b_copy = dict(b_data)
        b_copy['name'] = _clean_str(b_copy.get('name'))
        b_copy['area'] = _clean_str(b_copy.get('area'))
        return b_copy

    branding = {
        'tenant_slug': tenant_slug,
        'candidate_name_hi': cand_hi,
        'candidate_name_en': cand_en,
        'candidate_disp': candidate_disp,
        'candidate_photo_file': candidate_file,
        'candidate_src': candidate_src,
        'candidate_url': _file_uri(candidate_file),
        'has_candidate': has_candidate,
        'cand_title': cand_title,
        'party_name_hi': party_name_hi,
        'party_name_en': party_name_en,
        'party_line': party_line,
        'symbol_name_hi': symbol_name_hi,
        'symbol_name_en': symbol_name_en,
        'symbol_file': symbol_file,
        'lotus_src': lotus_src,
        'lotus_url': _file_uri(symbol_file),
        'has_symbol': has_symbol,
        'ballot_no': ballot_no,
        'has_ballot': has_ballot,
        'voting_method': voting_method,
        'is_ballot_election': is_ballot_election,
        'ballot_color': ballot_color,
        'slogan': slogan,
        'theme_color': theme_color,
        'place_hi': place_hi,
        'ward_name_hi': ward_hi,
        'ward_no': str(election.get('ward_no') or '').strip(),
        'constituency': str(election.get('constituency') or '').strip(),
        'district': district,
        'state': str(election.get('state') or 'Rajasthan').strip(),
        'election_type': str(election.get('election_type') or 'MUNICIPAL').strip(),
        'campaign_message': campaign_msg,
        'appeal_msg': campaign_msg,
        'booths': booths,
        'get_booth': _get_booth_info,
        'custom_bg': get_tenant_custom_background(tenant_slug)
    }

    with _BRANDING_LOCK:
        _BRANDING_CACHE[tenant_slug] = (current_mtime, branding)

    return branding
