import logging
logger = logging.getLogger('smartbooth.campaign_manager')
"""
campaign_manager.py
Modular Campaign Lifecycle & Multi-Tenant Provisioning Manager for Chunav Saathi.

Responsibilities:
- Self-serve dynamic creation of new campaigns (candidate profile, party, EVM ballot serial, photo/symbol uploads, custom messages).
- Update and retrieval of existing campaign configurations.
- Storage isolation: Automatic non-blocking PostgreSQL 17 partition provisioning or SQLite database setup.
- Enforces strict modularity and separation of concerns away from monolithic route files.
"""

import os
import json
import re
import glob
import shutil
import time
from typing import List, Dict, Any, Tuple, Optional
from electionapp.config import (
    PLATFORM_ROOT, TENANTS_DIR, DATA_DIR,
    get_tenant_dir, get_tenant_assets_dir, load_tenant_config,
    clear_tenant_config_cache, get_voting_method
)
from electionapp.tenant_branding import invalidate_tenant_branding


def purge_tenant_generated_cards(slug: str) -> int:
    """
    Deletes all disk-cached voter-slip PNGs for a given tenant from generated_cards/.
    Must be called whenever campaign branding changes (name, symbol, ballot serial, etc.)
    so the next dispatch re-renders fresh slips with the updated candidate details.
    Returns the count of files deleted.
    """
    cards_dir = os.path.join(PLATFORM_ROOT, 'generated_cards')
    if not os.path.isdir(cards_dir):
        return 0
    deleted = 0
    # Slips are JPEG as of the direct-capture change; .png covers older artefacts.
    for ext in ('jpg', 'jpeg', 'png'):
        for f in glob.glob(os.path.join(cards_dir, f'bjp_slip_{slug}_*.{ext}')):
            try:
                os.remove(f)
                deleted += 1
            except Exception:
                pass
    return deleted


def get_pg_connection():
    """Helper to connect to PostgreSQL 17 cluster if active."""
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
    except Exception:
        return None


def sanitize_slug(slug: str) -> str:
    """Sanitizes candidate slug string to safe alphanumeric + underscore."""
    raw = (slug or '').strip().lower()
    return re.sub(r'[^a-z0-9_-]', '', raw)


def _get_election_type_display(etype: str) -> str:
    et = str(etype or '').strip().upper()
    if et == 'PANCHAYAT':
        return 'पंचायती राज (सरपंच) चुनाव'
    elif et == 'PANCHAYAT_SAMITI':
        return 'पंचायत समिति चुनाव'
    elif et == 'ZILA_PARISHAD':
        return 'जिला परिषद चुनाव'
    elif et == 'MUNICIPAL':
        return 'नगर पालिका / निगम चुनाव'
    elif et == 'ASSEMBLY':
        return 'विधान सभा चुनाव'
    return 'चुनाव'


def parse_booths_data(raw: Any) -> Dict[str, Any]:
    """Parses JSON or CSV string or dict into structured booths map."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    raw = raw.strip()
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


def _normalize_and_save_candidate_photo(photo_file: Any, dest_path: str) -> None:
    """Saves uploaded photo and normalizes EXIF orientation to prevent rotated display."""
    photo_file.save(dest_path)
    try:
        from PIL import Image, ImageOps
        with Image.open(dest_path) as img:
            transposed = ImageOps.exif_transpose(img)
            if transposed is not img:
                transposed.save(dest_path)
    except Exception:
        pass


PRESET_SYMBOLS_MAP = {
    # INC / Congress
    'congress_hand': 'congress_hand',
    'हाथ': 'congress_hand',
    'हाथ का पंजा': 'congress_hand',
    'पंजा': 'congress_hand',
    'कांग्रेस': 'congress_hand',
    'inc': 'congress_hand',
    # BJP
    'bjp_lotus': 'bjp_lotus',
    'कमल': 'bjp_lotus',
    'कमल का फूल': 'bjp_lotus',
    'भाजपा': 'bjp_lotus',
    'bjp': 'bjp_lotus',
    # RLP
    'rlp_bottle': 'rlp_bottle',
    'बोतल': 'rlp_bottle',
    'रालोपा': 'rlp_bottle',
    'rlp': 'rlp_bottle',
    # BAP
    'bap_hockey': 'bap_hockey',
    'हॉकी': 'bap_hockey',
    'हॉकी स्टिक और गेंद': 'bap_hockey',
    'हॉकी और गेंद': 'bap_hockey',
    'बीएपी': 'bap_hockey',
    'bap': 'bap_hockey',
    # AAP
    'aap_broom': 'aap_broom',
    'झाड़ू': 'aap_broom',
    'झाडू': 'aap_broom',
    'आप': 'aap_broom',
    'aap': 'aap_broom',
    # BSP
    'bsp_elephant': 'bsp_elephant',
    'हाथी': 'bsp_elephant',
    'बसपा': 'bsp_elephant',
    'bsp': 'bsp_elephant',
    # SP
    'sp_cycle': 'sp_cycle',
    'साइकिल': 'sp_cycle',
    'cycle': 'sp_cycle',
    'सपा': 'sp_cycle',
    'sp': 'sp_cycle',
    # CPI-M
    'cpim_hammer_sickle': 'cpim_hammer_sickle',
    'हँसिया-हथौड़ा': 'cpim_hammer_sickle',
    'हंसिया हथोड़ा': 'cpim_hammer_sickle',
    'हँसिया हथौड़ा': 'cpim_hammer_sickle',
    'माकपा': 'cpim_hammer_sickle',
    'cpim': 'cpim_hammer_sickle',
    # Panchayat & Free Symbols (SEC Rajasthan Approved)
    'tractor': 'tractor',
    'ट्रैक्टर': 'tractor',
    'handpump': 'handpump',
    'हैंडपंप': 'handpump',
    'हैंडपम्प': 'handpump',
    'almirah': 'almirah',
    'अलमारी': 'almirah',
    'lock_key': 'lock_key',
    'ताला-चाबी': 'lock_key',
    'ताला चाबी': 'lock_key',
    'ताला': 'lock_key',
    'lock': 'lock_key',
    'sun': 'sun',
    'उगता सूरज': 'sun',
    'सूरज': 'sun',
    'सूर्य': 'sun',
    'bat': 'bat',
    'बल्ला': 'bat',
    'bucket': 'bucket',
    'बाल्टी': 'bucket',
    'gas_cylinder': 'gas_cylinder',
    'गैस सिलेंडर': 'gas_cylinder',
    'सिलेंडर': 'gas_cylinder',
    'सिलिंडर': 'gas_cylinder',
    'cylinder': 'gas_cylinder',
    'kite': 'kite',
    'पतंग': 'kite',
    'apple': 'apple',
    'सेब': 'apple',
    'scissors': 'scissors',
    'scissor': 'scissors',
    'कैंची': 'scissors',
    'ceiling_fan': 'ceiling_fan',
    'पंखा': 'ceiling_fan',
    'छत का पंखा': 'ceiling_fan',
    'fan': 'ceiling_fan',
    'cup_saucer': 'cup_saucer',
    'कप प्लेट': 'cup_saucer',
    'कप-प्लेट': 'cup_saucer',
    'कप और प्लेट': 'cup_saucer',
    'sewing_machine': 'sewing_machine',
    'सिलाई मशीन': 'sewing_machine',
    'torch': 'torch',
    'टॉर्च': 'torch',
    'bell': 'bell',
    'घंटी': 'bell',
    'scale': 'scale',
    'तराजू': 'scale',
    'umbrella': 'umbrella',
    'छाता': 'umbrella',
    'diya': 'diya',
    'दीपक': 'diya',
    'दिया': 'diya',
    'cot': 'cot',
    'चारपाई': 'cot',
    'खाट': 'cot',
    'glass': 'glass',
    'गिलास': 'glass',
    'कांच का गिलास': 'glass',
    'balloon': 'balloon',
    'गुब्बारा': 'balloon',
    'candle': 'candle',
    'मोमबत्ती': 'candle',
    'whistle': 'whistle',
    'सीटी': 'whistle',
    # Additional Popular SEC Rajasthan Symbols
    'camera': 'camera',
    'कैमरा': 'camera',
    'pressure_cooker': 'pressure_cooker',
    'प्रेशर कुकर': 'pressure_cooker',
    'कुकर': 'pressure_cooker',
    'road_roller': 'road_roller',
    'रोड रोलर': 'road_roller',
    'television': 'television',
    'टेलीविजन': 'television',
    'टीवी': 'television',
    'telephone': 'telephone',
    'टेलीफोन': 'telephone',
    'truck': 'truck',
    'ट्रक': 'truck',
    'grapes': 'grapes',
    'अंगूर': 'grapes',
    'watermelon': 'watermelon',
    'तरबूज': 'watermelon',
}

# Augment with authoritative Rajasthan SEC 40 Sarpanch, 40 Ward Panch, and recognized party symbols
try:
    from electionapp.symbol_catalog import COMPREHENSIVE_SYMBOL_MAP
    PRESET_SYMBOLS_MAP.update(COMPREHENSIVE_SYMBOL_MAP)
except Exception:
    pass


def _get_preset_symbol_key(name_or_key: Optional[str]) -> Optional[str]:
    """Normalizes preset symbol key, URL, filename, or Hindi name to standard preset key."""
    if not name_or_key:
        return None
    val = str(name_or_key).strip()
    if not val:
        return None
    # Strip URL/path or filename extensions if passed
    if '/' in val or '\\' in val:
        val = os.path.basename(val)
    if val.endswith('.png') or val.endswith('.jpg') or val.endswith('.jpeg'):
        val = os.path.splitext(val)[0]

    val_clean = val.lower().strip()
    if val in PRESET_SYMBOLS_MAP:
        return PRESET_SYMBOLS_MAP[val]
    if val_clean in PRESET_SYMBOLS_MAP:
        return PRESET_SYMBOLS_MAP[val_clean]

    # Direct file match in static/symbols
    static_symbol = os.path.join(PLATFORM_ROOT, 'static', 'symbols', f"{val_clean}.png")
    if os.path.exists(static_symbol):
        return val_clean
    return None


def _resolve_preset_symbol_path(preset_name: Optional[str]) -> Optional[str]:
    """Finds preset symbol file path from static/symbols or legacy locations."""
    if not preset_name:
        return None
    key = _get_preset_symbol_key(preset_name)
    if key:
        static_symbol = os.path.join(PLATFORM_ROOT, 'static', 'symbols', f"{key}.png")
        if os.path.exists(static_symbol):
            return static_symbol

    preset = str(preset_name).strip().lower()
    # Legacy fallback locations
    preset_map = {
        'bjp_lotus': os.path.join(PLATFORM_ROOT, 'bjp_lotus.png'),
        'apple': os.path.join(PLATFORM_ROOT, '..', 'apple_symbol.png'),
        'neutral': os.path.join(PLATFORM_ROOT, 'static', 'symbols', 'neutral.png'),
    }
    legacy = preset_map.get(preset)
    if legacy and os.path.exists(legacy):
        return legacy
    return None


def get_campaign_details(slug: str) -> Tuple[bool, Dict[str, Any], int]:
    """
    Fetches full campaign.json and resolved asset URLs for super admin editing.
    Returns: (success, result_dict, status_code)
    """
    slug = sanitize_slug(slug)
    if not slug:
        return False, {'error': 'Campaign slug is required'}, 400

    try:
        cfg = load_tenant_config(slug)
    except FileNotFoundError:
        return False, {'error': f"Campaign '{slug}' not found"}, 404
    except Exception as e:
        return False, {'error': f"Failed to load campaign '{slug}': {str(e)}"}, 500

    assets_dir = get_tenant_assets_dir(slug)
    cand_info = cfg.get('candidate', {})
    party_info = cfg.get('party', {})

    from electionapp import s3_storage
    photo_url = None
    photo_file = cand_info.get('photo_filename')
    if photo_file:
        photo_url = s3_storage.resolve_photo_url(slug, photo_file, stored_url=cand_info.get('photo_url'))

    symbol_url = None
    symbol_file = party_info.get('symbol_filename')
    if symbol_file:
        symbol_url = s3_storage.resolve_symbol_url(slug, symbol_file, stored_url=party_info.get('symbol_url'))
    if not symbol_url:
        preset_key = _get_preset_symbol_key(party_info.get('symbol_name_hi') or party_info.get('symbol_name_en'))
        if preset_key:
            symbol_url = f"/static/symbols/{preset_key}.png"

    result = {
        'success': True,
        'tenant_slug': slug,
        'config': cfg,
        'photo_url': photo_url,
        'symbol_url': symbol_url
    }
    return True, result, 200


def create_campaign(data: Dict[str, Any], files: Any = None, owner_user_id: Optional[int] = None) -> Tuple[bool, Dict[str, Any], int]:
    """
    Provisions a complete new campaign dynamically:
    1. Validates slug and creates tenants/{slug}/assets directories
    2. Saves uploaded candidate photo and symbol image
    3. Builds and writes compliant campaign.json
    4. Automatically provisions PostgreSQL 17 non-blocking partition or SQLite database
    5. Invalidates branding cache
    """
    files = files or {}
    slug = sanitize_slug(data.get('campaign_slug') or data.get('slug') or '')
    if not slug:
        return False, {'error': 'campaign_slug is required (alphanumeric and underscores only)'}, 400

    tenant_dir = os.path.join(TENANTS_DIR, slug)
    if os.path.exists(tenant_dir) and os.path.exists(os.path.join(tenant_dir, 'campaign.json')):
        return False, {'error': f"Campaign slug '{slug}' already exists. Please pick a unique slug."}, 409

    assets_dir = os.path.join(tenant_dir, 'assets')
    os.makedirs(assets_dir, exist_ok=True)

    storage_backend = str(data.get('storage_backend', 'postgres')).lower()
    db_filename = f"voters_{slug}.db"

    # Photo & Symbol uploads (empty by default unless uploaded or preset selected)
    photo_filename = ''
    fallback_photo_filename = ''
    symbol_filename = ''

    photo_url = ''
    symbol_url = ''

    photo = files.get('candidate_photo') if files else None
    if photo and getattr(photo, 'filename', None) and str(photo.filename).strip():
        ext = os.path.splitext(photo.filename)[1].lower() or '.png'
        photo_filename = f"candidate_photo{ext}"
        photo_dest = os.path.join(assets_dir, photo_filename)
        _normalize_and_save_candidate_photo(photo, photo_dest)
        try:
            persistent_assets = os.path.join(DATA_DIR, slug, 'assets')
            os.makedirs(persistent_assets, exist_ok=True)
            shutil.copy2(photo_dest, os.path.join(persistent_assets, photo_filename))
        except Exception:
            pass
        try:
            from electionapp.s3_storage import upload_campaign_asset
            s3_res = upload_campaign_asset(photo_dest, slug, photo_filename)
            photo_url = s3_res.get('thumb_url') or s3_res.get('url') or ''
        except Exception as e:
            logger.warning("S3 candidate photo upload failed: %s", e)

    sym = files.get('party_symbol') if files else None
    if sym and getattr(sym, 'filename', None) and str(sym.filename).strip():
        ext = os.path.splitext(sym.filename)[1].lower() or '.png'
        symbol_filename = f"symbol{ext}"
        sym_dest = os.path.join(assets_dir, symbol_filename)
        sym.save(sym_dest)
        try:
            persistent_assets = os.path.join(DATA_DIR, slug, 'assets')
            os.makedirs(persistent_assets, exist_ok=True)
            shutil.copy2(sym_dest, os.path.join(persistent_assets, symbol_filename))
        except Exception:
            pass
        try:
            from electionapp.s3_storage import upload_campaign_asset
            s3_res = upload_campaign_asset(sym_dest, slug, symbol_filename, generate_thumb=False)
            symbol_url = s3_res.get('url') or ''
        except Exception as e:
            logger.warning("S3 party symbol upload failed: %s", e)
    else:
        preset_candidate = data.get('preset_symbol') or data.get('symbol_name_hi') or data.get('symbol_name_en')
        if preset_candidate:
            src = _resolve_preset_symbol_path(preset_candidate)
            if src and os.path.exists(src):
                sym_dest = os.path.join(assets_dir, 'symbol.png')
                shutil.copy2(src, sym_dest)
                symbol_filename = 'symbol.png'
                try:
                    persistent_assets = os.path.join(DATA_DIR, slug, 'assets')
                    os.makedirs(persistent_assets, exist_ok=True)
                    shutil.copy2(sym_dest, os.path.join(persistent_assets, 'symbol.png'))
                except Exception:
                    pass
                try:
                    from electionapp.s3_storage import upload_campaign_asset
                    s3_res = upload_campaign_asset(sym_dest, slug, symbol_filename, generate_thumb=False)
                    symbol_url = s3_res.get('url') or ''
                except Exception as e:
                    logger.warning("S3 preset symbol upload failed: %s", e)

    # Parse booths if provided or auto-populate from Rajasthan catalog
    booths = parse_booths_data(data.get('booths') or data.get('booths_data'))
    rajasthan_dist = str(data.get('rajasthan_district') or data.get('district') or '').strip()
    rajasthan_block = str(data.get('rajasthan_block') or data.get('block') or data.get('panchayat_samiti') or data.get('ulb_name') or data.get('body_name') or '').strip()
    rajasthan_gp = str(data.get('rajasthan_gp') or '').strip()
    el_type_early = str(data.get('election_type') or 'MUNICIPAL').strip().upper()
    if not rajasthan_gp and el_type_early == 'MUNICIPAL' and rajasthan_block:
        rajasthan_gp = rajasthan_block

    # Parse rajasthan_gps from form data list, comma-separated string, or fallback to [rajasthan_gp]
    rajasthan_gps: List[str] = []
    raw_gps = data.get('rajasthan_gps')
    if hasattr(data, 'getlist'):
        list_gps = data.getlist('rajasthan_gps')
        if list_gps and len(list_gps) > 1:
            raw_gps = list_gps
    if raw_gps:
        if isinstance(raw_gps, str):
            raw_gps_str = raw_gps.strip()
            if raw_gps_str.startswith('[') and raw_gps_str.endswith(']'):
                try:
                    parsed = json.loads(raw_gps_str)
                    if isinstance(parsed, list):
                        rajasthan_gps = [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            if not rajasthan_gps:
                rajasthan_gps = [g.strip() for g in raw_gps_str.split(',') if g.strip()]
        elif isinstance(raw_gps, (list, tuple, set)):
            for g in raw_gps:
                if isinstance(g, str) and ',' in g:
                    rajasthan_gps.extend([x.strip() for x in g.split(',') if x.strip()])
                elif str(g).strip():
                    rajasthan_gps.append(str(g).strip())
    if not rajasthan_gps and rajasthan_gp:
        if ',' in rajasthan_gp:
            rajasthan_gps = [g.strip() for g in rajasthan_gp.split(',') if g.strip()]
        else:
            rajasthan_gps = [rajasthan_gp]
    if rajasthan_gps and not rajasthan_gp:
        rajasthan_gp = rajasthan_gps[0]

    raw_gp_wards = data.get('gp_wards')
    gp_wards = {}
    if raw_gp_wards:
        if isinstance(raw_gp_wards, str):
            try:
                parsed_gw = json.loads(raw_gp_wards)
                if isinstance(parsed_gw, dict):
                    gp_wards = parsed_gw
            except Exception:
                pass
        elif isinstance(raw_gp_wards, dict):
            gp_wards = raw_gp_wards

    if not booths and rajasthan_dist and (rajasthan_gps or rajasthan_gp):
        try:
            from electionapp import rajasthan_catalog
            prev = rajasthan_catalog.get_area_preview(rajasthan_dist, rajasthan_block, gp=rajasthan_gp, gps=rajasthan_gps)
            if prev.get('booths'):
                booths = prev['booths']
        except Exception:
            pass

    election_type = str(data.get('election_type', 'MUNICIPAL')).upper()
    is_multi_gp = (
        election_type in ('PANCHAYAT_SAMITI', 'PS', 'ZILA_PARISHAD', 'ZP', 'ZILA_PARISHAD_WARD', 'PANCHAYAT_SAMITI_WARD')
        or len(rajasthan_gps) > 1
        or any(k in str(data.get('slug') or '').lower() for k in ['zp', 'panchayat_samiti', 'ps_'])
    )
    cand_name_hi = data.get('candidate_name_hi', '').strip()
    cand_name_en = data.get('candidate_name_en', '').strip()

    if election_type == 'PANCHAYAT':
        party_name_hi = str(data.get('party_name_hi') or '').strip()
        party_name_en = str(data.get('party_name_en') or '').strip()
        symbol_name_hi = str(data.get('symbol_name_hi') or '').strip()
        symbol_name_en = str(data.get('symbol_name_en') or '').strip()
        ballot_serial = str(data.get('ballot_serial') or '').strip()

        # Do NOT require ward_no; auto-compute ward_no = f"1-{ward_count}" (or "1-9")
        raw_ward_no = str(data.get('ward_no') or '').strip()
        if raw_ward_no and raw_ward_no != '1':
            ward_no = raw_ward_no
        else:
            ward_count = data.get('ward_count') or (len(booths) if booths else 9)
            ward_no = f"1-{ward_count}"

        # Set ward_name_hi = f"ग्राम पंचायत {rajasthan_gp}" if not provided
        raw_ward_name_hi = str(data.get('ward_name_hi') or '').strip()
        if raw_ward_name_hi:
            ward_name_hi = raw_ward_name_hi
        elif rajasthan_gp:
            ward_name_hi = f"ग्राम पंचायत {rajasthan_gp}"
        else:
            ward_name_hi = f"ग्राम पंचायत {ward_no}"

        raw_ward_name_en = str(data.get('ward_name_en') or '').strip()
        if raw_ward_name_en:
            ward_name_en = raw_ward_name_en
        elif rajasthan_gp:
            ward_name_en = f"Gram Panchayat {rajasthan_gp}"
        else:
            ward_name_en = f"Gram Panchayat {ward_no}"
    elif election_type == 'PANCHAYAT_SAMITI':
        ward_no = str(data.get('ward_no', '1')).strip()
        ward_name_hi = str(data.get('ward_name_hi') or f'पंचायत समिति वार्ड {ward_no}').strip()
        ward_name_en = str(data.get('ward_name_en') or f'Panchayat Samiti Ward {ward_no}').strip()
        party_name_hi = str(data.get('party_name_hi') or '').strip()
        party_name_en = str(data.get('party_name_en') or '').strip()
        symbol_name_hi = str(data.get('symbol_name_hi') or '').strip()
        symbol_name_en = str(data.get('symbol_name_en') or '').strip()
        ballot_serial = str(data.get('ballot_serial') or '').strip()
    elif election_type == 'ZILA_PARISHAD':
        ward_no = str(data.get('ward_no', '1')).strip()
        ward_name_hi = str(data.get('ward_name_hi') or f'जिला परिषद वार्ड {ward_no}').strip()
        ward_name_en = str(data.get('ward_name_en') or f'Zila Parishad Ward {ward_no}').strip()
        party_name_hi = str(data.get('party_name_hi') or '').strip()
        party_name_en = str(data.get('party_name_en') or '').strip()
        symbol_name_hi = str(data.get('symbol_name_hi') or '').strip()
        symbol_name_en = str(data.get('symbol_name_en') or '').strip()
        ballot_serial = str(data.get('ballot_serial') or '').strip()
    elif election_type == 'ASSEMBLY':
        ac_no = str(data.get('ac_no') or data.get('ward_no') or '1').strip()
        ward_no = ac_no
        raw_ac_name = str(data.get('ward_name_hi') or data.get('constituency') or f'विधान सभा क्षेत्र {ward_no}').strip()
        if not raw_ac_name.endswith('विधान सभा') and not raw_ac_name.endswith('विधानसभा') and not raw_ac_name.endswith('Assembly'):
            ward_name_hi = f"{raw_ac_name} विधान सभा"
        else:
            ward_name_hi = raw_ac_name
        ward_name_en = str(data.get('ward_name_en') or f'AC-{ward_no}').strip()
        party_name_hi = str(data.get('party_name_hi') or '').strip()
        party_name_en = str(data.get('party_name_en') or '').strip()
        symbol_name_hi = str(data.get('symbol_name_hi') or '').strip()
        symbol_name_en = str(data.get('symbol_name_en') or '').strip()
        ballot_serial = str(data.get('ballot_serial') or '').strip()
    elif election_type == 'MUNICIPAL':
        ward_no = str(data.get('ward_no') or '1').strip()
        ulb_part = rajasthan_block or str(data.get('city') or '').strip()
        raw_ward_name = str(data.get('ward_name_hi') or '').strip()
        if raw_ward_name:
            ward_name_hi = raw_ward_name
        elif ulb_part:
            ward_name_hi = f"{ulb_part} वार्ड {ward_no}"
        else:
            ward_name_hi = f"वार्ड संख्या {ward_no}"
        ward_name_en = str(data.get('ward_name_en') or f"Ward {ward_no}").strip()
        party_name_hi = str(data.get('party_name_hi') or '').strip()
        party_name_en = str(data.get('party_name_en') or '').strip()
        symbol_name_hi = str(data.get('symbol_name_hi') or '').strip()
        symbol_name_en = str(data.get('symbol_name_en') or '').strip()
        ballot_serial = str(data.get('ballot_serial') or '').strip()
    else:
        ward_no = str(data.get('ward_no', '1')).strip()
        ward_name_hi = str(data.get('ward_name_hi') or f'वार्ड नंबर {ward_no}').strip()
        ward_name_en = str(data.get('ward_name_en') or f'Ward {ward_no}').strip()
        party_name_hi = str(data.get('party_name_hi') or '').strip()
        party_name_en = str(data.get('party_name_en') or '').strip()
        symbol_name_hi = str(data.get('symbol_name_hi') or '').strip()
        symbol_name_en = str(data.get('symbol_name_en') or '').strip()
        ballot_serial = str(data.get('ballot_serial') or '').strip()

    slogan = str(data.get('slogan', 'राष्ट्रहित सर्वोपरि' if party_name_hi else 'मतदान हमारा अधिकार एवं कर्तव्य है')).strip()
    theme_color = str(data.get('theme_color', '#6366f1')).strip()

    voting_method = data.get('voting_method')
    ballot_color = data.get('ballot_color')
    resolved_voting_method = get_voting_method(election_type, voting_method)
    resolved_ballot_color = str(ballot_color or '').strip()

    campaign_msg = data.get('campaign_message')
    if campaign_msg:
        gp_clean = rajasthan_gp or ward_name_hi or ''
        if gp_clean.startswith("ग्राम पंचायत "):
            gp_clean = gp_clean[len("ग्राम पंचायत "):]
        campaign_msg = str(campaign_msg).replace('{gp_name}', gp_clean).replace('{candidate_name}', cand_name_hi or cand_name_en or '')
    else:
        if election_type == 'PANCHAYAT':
            gp_target = rajasthan_gp or ward_name_hi
            if gp_target.startswith("ग्राम पंचायत "):
                gp_target = gp_target[len("ग्राम पंचायत "):]
            if ballot_serial:
                campaign_msg = f"ग्राम पंचायत {gp_target} से सरपंच पद हेतु प्रत्याशी {cand_name_hi} (क्रम संख्या {ballot_serial}) को अपना अमूल्य मत देकर आधिकारिक मुहर लगाकर भारी मतों से विजयी बनाएं।"
            else:
                campaign_msg = f"ग्राम पंचायत {gp_target} से सरपंच पद हेतु प्रत्याशी {cand_name_hi} को अपना अमूल्य मत देकर भारी मतों से विजयी बनाएं।"
        elif election_type == 'PANCHAYAT_SAMITI':
            party_prefix = f"{party_name_hi} " if party_name_hi else ""
            if ballot_serial:
                campaign_msg = f"पंचायत समिति वार्ड संख्या {ward_no} ({ward_name_hi}) से {party_prefix}प्रत्याशी {cand_name_hi} को ईवीएम मशीन पर क्रम संख्या {ballot_serial} का नीला बटन दबाकर भारी मतों से विजयी बनाएं।"
            else:
                campaign_msg = f"पंचायत समिति वार्ड संख्या {ward_no} ({ward_name_hi}) से {party_prefix}प्रत्याशी {cand_name_hi} को ईवीएम का नीला बटन दबाकर भारी मतों से विजयी बनाएं।"
        elif election_type == 'ZILA_PARISHAD':
            party_prefix = f"{party_name_hi} " if party_name_hi else ""
            if ballot_serial:
                campaign_msg = f"जिला परिषद वार्ड संख्या {ward_no} ({ward_name_hi}) से {party_prefix}प्रत्याशी {cand_name_hi} को ईवीएम मशीन पर क्रम संख्या {ballot_serial} का नीला बटन दबाकर भारी मतों से विजयी बनाएं।"
            else:
                campaign_msg = f"जिला परिषद वार्ड संख्या {ward_no} ({ward_name_hi}) से {party_prefix}प्रत्याशी {cand_name_hi} को ईवीएम का नीला बटन दबाकर भारी मतों से विजयी बनाएं।"
        elif election_type == 'MUNICIPAL':
            party_prefix = f"{party_name_hi} " if party_name_hi else ""
            if ballot_serial:
                campaign_msg = f"{ward_name_hi} से पार्षद पद हेतु {party_prefix}प्रत्याशी {cand_name_hi} को ईवीएम मशीन पर क्रम संख्या {ballot_serial} का नीला बटन दबाकर भारी मतों से विजयी बनाएं।"
            else:
                campaign_msg = f"{ward_name_hi} से पार्षद पद हेतु {party_prefix}प्रत्याशी {cand_name_hi} को अपना अमूल्य मत देकर भारी मतों से विजयी बनाएं।"
        elif election_type == 'ASSEMBLY':
            party_prefix = f"{party_name_hi} " if party_name_hi else ""
            if ballot_serial:
                campaign_msg = f"{ward_name_hi} क्षेत्र से {party_prefix}प्रत्याशी {cand_name_hi} को ईवीएम मशीन पर क्रम संख्या {ballot_serial} का नीला बटन दबाकर भारी मतों से विजयी बनाएं।"
            else:
                campaign_msg = f"{ward_name_hi} क्षेत्र से {party_prefix}प्रत्याशी {cand_name_hi} को अपना अमूल्य मत देकर भारी मतों से विजयी बनाएं।"
        else:
            if cand_name_hi and party_name_hi:
                campaign_msg = f"{ward_name_hi} से {party_name_hi} प्रत्याशी {cand_name_hi} को अपना अमूल्य मत देकर भारी मतों से विजयी बनाएं।"
            else:
                campaign_msg = f"{ward_name_hi} चुनाव — लोकतंत्र के महापर्व में मतदान अवश्य करें।"

    try:
        max_sub = int(data.get('max_sub_accounts', 50))
    except (ValueError, TypeError):
        max_sub = 50

    # Initial WhatsApp message credits.
    # Resellers allocate from their prepaid wallet balance; Super Admin sets directly.
    raw_wa_credits = data.get('whatsapp_credits')
    if owner_user_id:
        try:
            wa_credits = max(0, int(raw_wa_credits or 0))
        except (ValueError, TypeError):
            wa_credits = 0
        if wa_credits > 0:
            import user_manager
            deducted = user_manager.deduct_reseller_whatsapp_credits(owner_user_id, wa_credits)
            if not deducted:
                avail = user_manager.get_reseller_whatsapp_credits(owner_user_id)
                return False, {
                    'success': False,
                    'error': f"अपर्याप्त WhatsApp क्रेडिट। आपके रीसेलर खाते में केवल {avail:,} क्रेडिट उपलब्ध हैं (Insufficient reseller credits)."
                }, 400
    else:
        if raw_wa_credits is not None and str(raw_wa_credits).strip() != '':
            try:
                wa_credits = max(0, int(raw_wa_credits))
            except (ValueError, TypeError):
                wa_credits = 10
        else:
            wa_credits = 10

    if owner_user_id:
        # Reseller accounts cannot grant unbilled voice credits at platform expense
        voice_credits = 0
    else:
        raw_voice = data.get('voice_credits')
        try:
            voice_credits = max(0, int(raw_voice or 0))
        except (ValueError, TypeError):
            voice_credits = 0

    campaign_cfg = {
        "tenant_slug": slug,
        "max_sub_accounts": max_sub,
        "candidate": {
            "name_hi": cand_name_hi,
            "name_en": cand_name_en,
            "designation": str(data.get('designation', '')).strip(),
            "phone": str(data.get('candidate_phone', '')).strip(),
            "office_address": str(data.get('office_address', '')).strip(),
            "photo_filename": photo_filename,
            "photo_url": photo_url,
            "fallback_photo_filename": fallback_photo_filename
        },
        "election": {
            "ward_no": ward_no,
            "ward_name_hi": ward_name_hi,
            "ward_name_en": ward_name_en,
            "constituency": data.get('constituency', '').strip(),
            "city": data.get('city', '').strip(),
            "district": data.get('district', '').strip(),
            "state": data.get('state', 'Rajasthan').strip(),
            "panchayat_samiti": str(data.get('panchayat_samiti') or data.get('block') or data.get('rajasthan_block') or '').strip(),
            "election_type": data.get('election_type', 'MUNICIPAL').upper(),
            "voting_method": resolved_voting_method,
            "ballot_color": resolved_ballot_color,
            "app_title": f"{ward_name_hi} Smart Booth",
            "portal_subtitle": f"{_get_election_type_display(data.get('election_type', 'MUNICIPAL'))} 2026 — मतदाता सेवा व वॉर-रूम"
        },
        "party": {
            "name_hi": party_name_hi,
            "name_en": party_name_en,
            "symbol_name_hi": symbol_name_hi,
            "symbol_name_en": symbol_name_en,
            "symbol_filename": symbol_filename,
            "symbol_url": symbol_url,
            "theme_color": theme_color,
            "ballot_serial": ballot_serial,
            "ballot_color": resolved_ballot_color,
            "voting_method": resolved_voting_method,
            "slogan": slogan
        },
        "campaign_message": campaign_msg,
        "database": {
            "db_filename": db_filename
        },
        "whatsapp": {
            "channel": "meta_cloud",
            "channel_priority": ["meta_cloud", "wa_me"],
            "phone_number_id": str(data.get('whatsapp_phone_number_id', '1382048191650184')).strip(),
            "waba_id": str(data.get('whatsapp_waba_id', '1288509326773409')).strip(),
            "template_name": "info_update_suchna_1",
            "template_lang": "en"
        },
        "booths": booths
    }

    if is_multi_gp and rajasthan_gps:
        campaign_cfg["gps"] = rajasthan_gps
        campaign_cfg["election"]["gps"] = rajasthan_gps
    if gp_wards:
        campaign_cfg["election"]["gp_wards"] = gp_wards

    cfg_path = os.path.join(tenant_dir, 'campaign.json')
    os.makedirs(tenant_dir, exist_ok=True)
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(campaign_cfg, f, ensure_ascii=False, indent=2)

    # Database provisioning (PostgreSQL multi-tenant partition)
    tenant_id = None
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT tenant_id FROM tenants WHERE campaign_slug = %s", (slug,))
                row = cur.fetchone()
                if row:
                    tenant_id = row[0]
                else:
                    cur.execute("""
                        INSERT INTO tenants (campaign_slug, candidate_name, state, election_type, status, max_sub_accounts, owner_user_id, config)
                        VALUES (%s, %s, %s, %s, 'active', %s, %s, %s)
                        RETURNING tenant_id;
                    """, (slug, cand_name_hi or slug, campaign_cfg['election']['state'], campaign_cfg['election']['election_type'], max_sub, owner_user_id, json.dumps(campaign_cfg)))
                    tenant_id = cur.fetchone()[0]

                # Seed the WhatsApp credit balance for this campaign. Every message is
                # a metered cost, so sending stays blocked until credits are granted.
                cur.execute("""
                    INSERT INTO whatsapp_quota (tenant_id, granted, consumed, reserved)
                    VALUES (%s, %s, 0, 0)
                    ON CONFLICT (tenant_id) DO NOTHING
                """, (tenant_id, wa_credits))
                if wa_credits:
                    ledger_note = f"initial grant from reseller (User #{owner_user_id})" if owner_user_id else "initial grant at campaign creation"
                    custom_note = str(data.get('whatsapp_note') or '').strip()
                    if custom_note:
                        ledger_note += f": {custom_note}"
                    cur.execute("""
                        INSERT INTO whatsapp_quota_ledger (tenant_id, action, amount, balance_after, note)
                        VALUES (%s, 'grant', %s, %s, %s)
                    """, (tenant_id, wa_credits, wa_credits, ledger_note))

                # Seed Voice credit balance for this campaign
                try:
                    from electionapp import voice_quota
                    voice_quota.ensure_voice_schema()
                except Exception:
                    pass

                cur.execute("""
                    INSERT INTO voice_quota (tenant_id, granted, consumed, reserved)
                    VALUES (%s, %s, 0, 0)
                    ON CONFLICT (tenant_id) DO NOTHING
                """, (tenant_id, voice_credits))
                if voice_credits > 0:
                    voice_ledger_note = f"initial grant from reseller (User #{owner_user_id})" if owner_user_id else "initial grant at campaign creation"
                    cur.execute("""
                        INSERT INTO voice_quota_ledger (tenant_id, action, amount, balance_after, note)
                        VALUES (%s, 'grant', %s, %s, %s)
                    """, (tenant_id, voice_credits, voice_credits, voice_ledger_note))

                # Create partition
                partition_tbl = f"voters_t{tenant_id}"
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {partition_tbl}
                    PARTITION OF voters
                    FOR VALUES IN ({tenant_id});
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{partition_tbl}_name
                    ON {partition_tbl} (name_en varchar_pattern_ops, name_hi);
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{partition_tbl}_epic
                    ON {partition_tbl} (epic_no);
                """)
                cur.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{partition_tbl}_part_sl
                    ON {partition_tbl} (part_no, sl_no);
                """)
                cur.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_{partition_tbl}_part_sl_unique
                    ON {partition_tbl} (tenant_id, part_no, sl_no);
                """)
                pg_conn.commit()
            pg_conn.close()
        except Exception as e:
            if pg_conn:
                pg_conn.close()
            pass

    # 1. User Account Provisioning (Admin & Operator accounts)
    created_user_info = None
    user_creation_error = None
    admin_uname = ''
    admin_pwd = ''
    op_uname = ''
    op_pwd = ''

    create_user_flag = data.get('create_admin_user')
    if create_user_flag not in [False, 'false', '0', 0]:
        clean_uname_base = re.sub(r'[^a-z0-9]', '', str(slug).lower())
        admin_uname = str(data.get('admin_username') or f"{clean_uname_base}_admin").strip().lower()
        admin_pwd = str(data.get('admin_password') or f"{clean_uname_base.capitalize()}@2026").strip()
        admin_name = str(data.get('admin_full_name') or cand_name_hi or cand_name_en or f"{slug} Admin").strip()
        admin_phone = str(data.get('admin_phone') or data.get('candidate_phone') or '').strip()
        admin_role = str(data.get('admin_role') or 'admin').strip()

        op_uname = f"{clean_uname_base}"
        op_pwd = f"{clean_uname_base.capitalize()}@2026"
        op_name = f"{cand_name_hi or slug} Karyakarta"

        try:
            import user_manager
            u_ok, u_msg, u_id = user_manager.create_user_account(
                username=admin_uname,
                password=admin_pwd,
                full_name=admin_name,
                phone=admin_phone,
                role=admin_role,
                tenant_slug=slug,
                overwrite_password=True
            )
            if u_ok:
                created_user_info = {
                    'admin_username': admin_uname,
                    'admin_password': admin_pwd,
                    'admin_user_id': u_id,
                    'operator_username': op_uname,
                    'operator_password': op_pwd,
                    'operator_user_id': None,
                    'role': admin_role
                }
            else:
                user_creation_error = u_msg
                print(f"[USER_PROVISION_WARN] Admin user creation failed: {u_msg}", flush=True)

            try:
                op_ok, op_msg, op_id = user_manager.create_user_account(
                    username=op_uname,
                    password=op_pwd,
                    full_name=op_name,
                    phone=admin_phone,
                    role='operator',
                    tenant_slug=slug,
                    overwrite_password=True
                )
                if created_user_info and op_ok:
                    created_user_info['operator_user_id'] = op_id
            except Exception as ope:
                print(f"[USER_PROVISION_WARN] Operator creation failed: {ope}", flush=True)
        except Exception as ue:
            user_creation_error = str(ue)
            print(f"[USER_PROVISION_ERR] {ue}", flush=True)

    # 2. Instant Rajasthan Elector Ingestion
    voters_imported_count = 0
    auto_ingest_flag = data.get('auto_ingest_rolls')
    if auto_ingest_flag in [False, 'false', '0', 0, 'no']:
        should_auto_ingest = False
    else:
        should_auto_ingest = (auto_ingest_flag in [True, 'true', '1', 1, 'yes'] or (auto_ingest_flag is None and bool(rajasthan_dist and (rajasthan_gps or rajasthan_gp))))
    if should_auto_ingest and rajasthan_dist and (rajasthan_gps or rajasthan_gp) and tenant_id:
        part_tbl = f"voters_t{tenant_id}"
        ingest_conn = get_pg_connection()
        if ingest_conn:
            try:
                with ingest_conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 0;")
                    # Check if voters_master is in the same database (instant direct SQL copy)
                    cur.execute("SELECT to_regclass('voters_master');")
                    has_vm = cur.fetchone()[0]
                    if has_vm:
                        clean_blk = re.sub(r"\s+(panchayat\s+samiti|ps)$", "", (rajasthan_block or '').strip(), flags=re.I)
                        if is_multi_gp:
                            matched_gps = []
                            for g in (rajasthan_gps or [rajasthan_gp]):
                                gp_candidates = [g, g.upper(), g.title(), g.lower()]
                                found = None
                                if clean_blk:
                                    for cand in gp_candidates:
                                        cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat = %s AND panchayat_samiti ILIKE %s LIMIT 1;", (rajasthan_dist.upper(), cand, f"%{clean_blk}%"))
                                        row = cur.fetchone()
                                        if row:
                                            found = row[0]
                                            break
                                if not found:
                                    for cand in gp_candidates:
                                        cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat = %s LIMIT 1;", (rajasthan_dist.upper(), cand))
                                        row = cur.fetchone()
                                        if row:
                                            found = row[0]
                                            break
                                if not found and clean_blk:
                                    cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat ILIKE %s AND panchayat_samiti ILIKE %s LIMIT 1;", (rajasthan_dist.upper(), f"%{g}%", f"%{clean_blk}%"))
                                    row = cur.fetchone()
                                    if row:
                                        found = row[0]
                                if not found:
                                    cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat ILIKE %s LIMIT 1;", (rajasthan_dist.upper(), f"%{g}%"))
                                    row = cur.fetchone()
                                    if row:
                                        found = row[0]
                                matched_gps.append(found or g)

                            if matched_gps:
                                # Check if specific wards are specified for any GPs
                                # Generated filter format: (district = %s AND ((gram_panchayat = 'A' AND ward_no IN (1, 2)) OR (gram_panchayat = 'B' AND ward_no IN (3, 4)) OR (gram_panchayat = 'C')))
                                has_specific_wards = False
                                gp_clauses = []
                                gp_sql_params = [rajasthan_dist.upper()]

                                norm_gp_wards = {}
                                if gp_wards and isinstance(gp_wards, dict):
                                    for k, v in gp_wards.items():
                                        if isinstance(v, (list, tuple, set)) and len(v) > 0:
                                            valid_w = [int(w) for w in v if str(w).strip().isdigit()]
                                            if valid_w:
                                                norm_gp_wards[str(k).strip().lower()] = valid_w

                                for orig_g, matched_g in zip((rajasthan_gps or [rajasthan_gp]), matched_gps):
                                    specific_w = norm_gp_wards.get(str(orig_g).strip().lower()) or norm_gp_wards.get(str(matched_g).strip().lower())
                                    if specific_w:
                                        has_specific_wards = True
                                        in_placeholders = ', '.join(['%s'] * len(specific_w))
                                        gp_clauses.append(f"(gram_panchayat = %s AND ward_no IN ({in_placeholders}))")
                                        gp_sql_params.append(matched_g)
                                        gp_sql_params.extend(specific_w)
                                    else:
                                        gp_clauses.append("(gram_panchayat = %s)")
                                        gp_sql_params.append(matched_g)

                                if has_specific_wards and gp_clauses:
                                    where_gp_filter = f"district = %s AND ({' OR '.join(gp_clauses)})"
                                    query_params = tuple(gp_sql_params)
                                else:
                                    where_gp_filter = "district = %s AND gram_panchayat = ANY(%s)"
                                    query_params = (rajasthan_dist.upper(), matched_gps)

                                cur.execute(f"""
                                    WITH raw_electors AS (
                                        SELECT
                                            {tenant_id} AS tenant_id,
                                            district,
                                            panchayat_samiti,
                                            COALESCE(NULLIF(TRIM(gram_panchayat), ''), 'General') AS gram_panchayat,
                                            COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, 1) AS ward_no,
                                            DENSE_RANK() OVER (
                                                ORDER BY COALESCE(NULLIF(TRIM(gram_panchayat), ''), 'General'),
                                                         COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, 1)
                                            ) AS part_no,
                                            COALESCE(sl_no, 1) AS sl_no,
                                            UPPER(TRIM(epic_no)) AS epic_no,
                                            COALESCE(NULLIF(TRIM(name_hi), ''), NULLIF(TRIM(name_en), ''), 'मतदाता ' || COALESCE(NULLIF(TRIM(epic_no), ''), id::text)) AS name_hi,
                                            CASE 
                                                WHEN name_en IS NOT NULL AND trim(name_en) <> '' AND name_en !~ '[\u0900-\u097F]' THEN trim(name_en) 
                                                ELSE NULL 
                                            END AS name_en,
                                            LOWER(TRIM(COALESCE(
                                                NULLIF(TRIM(CASE WHEN name_en IS NOT NULL AND trim(name_en) <> '' AND name_en !~ '[\u0900-\u097F]' THEN trim(name_en) ELSE NULL END), ''),
                                                NULLIF(TRIM(name_hi), ''),
                                                'voter'
                                            ))) AS name_norm,
                                            COALESCE(rel_type, 'पिता') AS rel_type,
                                            CASE
                                                WHEN rel_name_hi ~* 'available' THEN NULL
                                                ELSE TRIM(rel_name_hi)
                                            END AS rel_name_hi,
                                            CASE 
                                                WHEN rel_name_en IS NOT NULL AND trim(rel_name_en) <> '' AND rel_name_en !~ '[\u0900-\u097F]' AND rel_name_en !~* 'available' THEN trim(rel_name_en) 
                                                ELSE NULL 
                                            END AS rel_name_en,
                                            LOWER(TRIM(COALESCE(
                                                NULLIF(TRIM(CASE WHEN rel_name_en IS NOT NULL AND trim(rel_name_en) <> '' AND rel_name_en !~ '[\u0900-\u097F]' AND rel_name_en !~* 'available' THEN trim(rel_name_en) ELSE NULL END), ''),
                                                NULLIF(TRIM(rel_name_hi), ''),
                                                ''
                                            ))) AS rel_norm,
                                            TRIM(house_no) AS house_no,
                                            COALESCE(age, 18) AS age,
                                            COALESCE(gender, 'पुरुष') AS gender,
                                            TRIM(booth_name) AS booth_name,
                                            TRIM(booth_area) AS booth_area,
                                            mobile AS contact_phone,
                                            CASE WHEN mobile IS NOT NULL AND length(mobile) >= 10 THEN 'MASTER_MOBILE' ELSE NULL END AS phone_source_type,
                                            family_id,
                                            COALESCE(status, 'Active') AS status,
                                            ROW_NUMBER() OVER (
                                                PARTITION BY COALESCE(NULLIF(TRIM(gram_panchayat), ''), 'General'),
                                                             COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, 1),
                                                             COALESCE(sl_no, 1)
                                                ORDER BY (epic_no IS NOT NULL AND length(trim(epic_no)) > 0) DESC, id DESC
                                            ) AS dup_rank
                                        FROM voters_master
                                        WHERE {where_gp_filter}
                                          AND (
                                              (name_hi IS NOT NULL AND length(trim(name_hi)) > 0)
                                              OR (name_en IS NOT NULL AND length(trim(name_en)) > 0)
                                              OR (epic_no IS NOT NULL AND length(trim(epic_no)) > 0)
                                          )
                                    )
                                    INSERT INTO {part_tbl} (
                                        tenant_id, district, panchayat_samiti, gram_panchayat, ward_no, part_no, sl_no, epic_no, name_hi, name_en, name_norm,
                                        rel_type, rel_name_hi, rel_name_en, rel_norm, house_no, age, gender,
                                        booth_name, booth_area, contact_phone, phone_source_type, family_id, status
                                    )
                                    SELECT
                                        tenant_id, district, panchayat_samiti, gram_panchayat, ward_no, part_no, sl_no, epic_no, name_hi, name_en, name_norm,
                                        rel_type, rel_name_hi, rel_name_en, rel_norm, house_no, age, gender,
                                        booth_name, booth_area, contact_phone, phone_source_type, family_id, status
                                    FROM raw_electors
                                    WHERE dup_rank = 1
                                    ON CONFLICT (tenant_id, part_no, sl_no) DO UPDATE SET
                                        district = COALESCE(EXCLUDED.district, {part_tbl}.district),
                                        panchayat_samiti = COALESCE(NULLIF(EXCLUDED.panchayat_samiti, ''), {part_tbl}.panchayat_samiti),
                                        gram_panchayat = COALESCE(NULLIF(EXCLUDED.gram_panchayat, ''), {part_tbl}.gram_panchayat),
                                        epic_no = COALESCE(NULLIF(EXCLUDED.epic_no, ''), {part_tbl}.epic_no),
                                        name_hi = COALESCE(NULLIF(EXCLUDED.name_hi, ''), {part_tbl}.name_hi),
                                        name_en = COALESCE(NULLIF(EXCLUDED.name_en, ''), {part_tbl}.name_en),
                                        contact_phone = COALESCE(NULLIF(EXCLUDED.contact_phone, ''), {part_tbl}.contact_phone),
                                        phone_source_type = COALESCE(NULLIF(EXCLUDED.phone_source_type, ''), {part_tbl}.phone_source_type);
                                """, query_params)
                                voters_imported_count = cur.rowcount
                                ingest_conn.commit()

                                # Automated Two-Tier Kinship Household Propagation per booth
                                cur.execute(f"""
                                    WITH head_phones AS (
                                        SELECT DISTINCT ON (part_no, COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi))))
                                            part_no,
                                            COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi))) AS rel_key,
                                            contact_phone
                                        FROM {part_tbl}
                                        WHERE contact_phone IS NOT NULL
                                          AND (rel_norm IS NOT NULL OR rel_name_hi IS NOT NULL)
                                          AND length(COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi)))) >= 2
                                        ORDER BY part_no, rel_key, age DESC
                                    )
                                    UPDATE {part_tbl} t
                                    SET contact_phone = h.contact_phone,
                                        phone_source_type = 'RECONCILED_V2_KINSHIP'
                                    FROM head_phones h
                                    WHERE t.part_no = h.part_no
                                       AND COALESCE(NULLIF(t.rel_norm, ''), LOWER(TRIM(t.rel_name_hi))) = h.rel_key
                                       AND t.contact_phone IS NULL;
                                """)
                                ingest_conn.commit()
                        else:
                            gp_candidates = [rajasthan_gp, rajasthan_gp.upper(), rajasthan_gp.title(), rajasthan_gp.lower()]
                            matched_gp = None
                            if clean_blk:
                                for cand in gp_candidates:
                                    cur.execute("SELECT 1 FROM voters_master WHERE district = %s AND gram_panchayat = %s AND panchayat_samiti ILIKE %s LIMIT 1;", (rajasthan_dist.upper(), cand, f"%{clean_blk}%"))
                                    if cur.fetchone():
                                        matched_gp = cand
                                        break
                            if not matched_gp:
                                for cand in gp_candidates:
                                    cur.execute("SELECT 1 FROM voters_master WHERE district = %s AND gram_panchayat = %s LIMIT 1;", (rajasthan_dist.upper(), cand))
                                    if cur.fetchone():
                                        matched_gp = cand
                                        break
                            if not matched_gp and clean_blk:
                                cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat ILIKE %s AND panchayat_samiti ILIKE %s LIMIT 1;", (rajasthan_dist.upper(), f"%{rajasthan_gp}%", f"%{clean_blk}%"))
                                row = cur.fetchone()
                                if row:
                                    matched_gp = row[0]
                            if not matched_gp:
                                cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND (gram_panchayat ILIKE %s OR panchayat_samiti ILIKE %s) LIMIT 1;", (rajasthan_dist.upper(), f"%{rajasthan_gp}%", f"%{rajasthan_gp}%"))
                                row = cur.fetchone()
                                if row:
                                    matched_gp = row[0]

                            if matched_gp:
                                m_ward_filter = ""
                                m_query_params = [matched_gp, rajasthan_dist.upper(), matched_gp, f"%{clean_blk}%", clean_blk]
                                if election_type == 'MUNICIPAL':
                                    clean_wn = str(data.get('ward_no') or ward_no or '').strip()
                                    if clean_wn.isdigit():
                                        m_ward_filter = "AND (ward_no = %s OR substring(source_file from 'Ward_0*([0-9]+)')::integer = %s)"
                                        m_query_params.extend([int(clean_wn), int(clean_wn)])
                                    else:
                                        m_ward_filter = "AND FALSE"

                                cur.execute(f"""
                                    INSERT INTO {part_tbl} (
                                        tenant_id, district, panchayat_samiti, ward_no, part_no, sl_no, epic_no, name_hi, name_en, name_norm,
                                        rel_type, rel_name_hi, rel_name_en, rel_norm, house_no, age, gender,
                                        booth_name, booth_area, gram_panchayat, contact_phone, phone_source_type, family_id, status
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
                                            WHEN rel_name_hi ~* 'available' THEN NULL
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
                                        COALESCE(NULLIF(TRIM(gram_panchayat), ''), %s),
                                        mobile,
                                        CASE WHEN mobile IS NOT NULL AND length(mobile) >= 10 THEN 'MASTER_MOBILE' ELSE NULL END,
                                        family_id,
                                        COALESCE(status, 'Active')
                                    FROM voters_master
                                    WHERE district = %s AND gram_panchayat = %s
                                      AND (panchayat_samiti ILIKE %s OR panchayat_samiti IS NULL OR %s = '')
                                      {m_ward_filter}
                                      AND (
                                          (name_hi IS NOT NULL AND length(trim(name_hi)) > 0)
                                          OR (name_en IS NOT NULL AND length(trim(name_en)) > 0)
                                          OR (epic_no IS NOT NULL AND length(trim(epic_no)) > 0)
                                      )
                                    ORDER BY 
                                        COALESCE(ward_no, NULLIF(substring(source_file from 'Ward_0*([0-9]+)'), '')::integer, part_no, 1),
                                        COALESCE(sl_no, 1),
                                        (name_hi IS NOT NULL AND length(trim(name_hi)) > 0 AND name_hi NOT LIKE 'मतदाता%%') DESC,
                                        id DESC
                                    ON CONFLICT (tenant_id, part_no, sl_no) DO UPDATE SET
                                        district = COALESCE(EXCLUDED.district, {part_tbl}.district),
                                        panchayat_samiti = COALESCE(NULLIF(EXCLUDED.panchayat_samiti, ''), {part_tbl}.panchayat_samiti),
                                        gram_panchayat = COALESCE(NULLIF(EXCLUDED.gram_panchayat, ''), {part_tbl}.gram_panchayat),
                                        epic_no = COALESCE(NULLIF(EXCLUDED.epic_no, ''), {part_tbl}.epic_no),
                                        name_hi = CASE 
                                            WHEN EXCLUDED.name_hi NOT LIKE 'मतदाता%%' AND length(trim(EXCLUDED.name_hi)) > 0 THEN EXCLUDED.name_hi 
                                             ELSE {part_tbl}.name_hi 
                                         END,
                                         name_en = CASE 
                                            WHEN EXCLUDED.name_en IS NOT NULL AND EXCLUDED.name_en NOT LIKE 'Voter%%' AND length(trim(EXCLUDED.name_en)) > 0 AND EXCLUDED.name_en !~ '[\u0900-\u097F]' THEN EXCLUDED.name_en 
                                             ELSE {part_tbl}.name_en 
                                         END,
                                         contact_phone = COALESCE(NULLIF(EXCLUDED.contact_phone, ''), {part_tbl}.contact_phone),
                                         phone_source_type = COALESCE(NULLIF(EXCLUDED.phone_source_type, ''), {part_tbl}.phone_source_type);
                                """, tuple(m_query_params))
                                voters_imported_count = cur.rowcount
                                ingest_conn.commit()

                                # Automated Two-Tier Kinship Household Propagation for single GP
                                cur.execute(f"""
                                    WITH head_phones AS (
                                        SELECT DISTINCT ON (part_no, COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi))))
                                            part_no,
                                            COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi))) AS rel_key,
                                            contact_phone
                                        FROM {part_tbl}
                                        WHERE contact_phone IS NOT NULL
                                          AND (rel_norm IS NOT NULL OR rel_name_hi IS NOT NULL)
                                          AND length(COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi)))) >= 2
                                        ORDER BY part_no, rel_key, age DESC
                                    )
                                    UPDATE {part_tbl} t
                                    SET contact_phone = h.contact_phone,
                                        phone_source_type = 'RECONCILED_V2_KINSHIP'
                                    FROM head_phones h
                                    WHERE t.part_no = h.part_no
                                       AND COALESCE(NULLIF(t.rel_norm, ''), LOWER(TRIM(t.rel_name_hi))) = h.rel_key
                                       AND t.contact_phone IS NULL;
                                """)
                                ingest_conn.commit()

                    if voters_imported_count == 0:
                        from electionapp import rajasthan_catalog
                        import psycopg2.extras
                        all_gps = rajasthan_gps or [rajasthan_gp]
                        clean_wn = str(data.get('ward_no') or ward_no or '').strip()
                        target_wn = clean_wn if election_type == 'MUNICIPAL' else None
                        elector_records = rajasthan_catalog.load_gp_voter_records(rajasthan_dist, rajasthan_block, gps=all_gps, ward_no=target_wn)
                        if elector_records:
                            if is_multi_gp:
                                booth_buckets = {}
                                for r in elector_records:
                                    b_no = int(r.get('part_no') or r.get('booth_no') or 1)
                                    booth_buckets.setdefault(b_no, []).append(r)

                                indexed_records = []
                                for b_no, r_list in booth_buckets.items():
                                    r_list.sort(key=lambda x: (x.get('source_file') or '', x.get('ward_no') or 0, x.get('sl_no') or 0))
                                    for seq, rec in enumerate(r_list, 1):
                                        rec_copy = dict(rec)
                                        rec_copy['part_no'] = b_no
                                        rec_copy['sl_no'] = seq
                                        indexed_records.append(rec_copy)

                                seen_keys = set()
                                deduped_records = []
                                for rec in indexed_records:
                                    k = (rec.get('part_no') or 1, rec.get('sl_no') or 1)
                                    if k not in seen_keys:
                                        seen_keys.add(k)
                                        deduped_records.append(rec)
                                elector_records = deduped_records
                            else:
                                seen_keys = set()
                                deduped_records = []
                                for rec in elector_records:
                                    k = (rec.get('part_no') or 1, rec.get('sl_no') or 1)
                                    if k not in seen_keys:
                                        seen_keys.add(k)
                                        deduped_records.append(rec)
                                elector_records = deduped_records

                            upsert_sql = f"""
                                INSERT INTO {part_tbl} (
                                    tenant_id, ward_no, part_no, sl_no, epic_no, name_hi, name_en, name_norm,
                                    rel_type, rel_name_hi, rel_name_en, rel_norm, house_no, age, gender,
                                    booth_name, booth_area, gram_panchayat, status, source_file, pdf_page, contact_phone, phone_source_type, family_id
                                ) VALUES %s
                                ON CONFLICT (tenant_id, part_no, sl_no) DO UPDATE SET
                                    epic_no = COALESCE(NULLIF(EXCLUDED.epic_no, ''), {part_tbl}.epic_no),
                                    name_hi = COALESCE(NULLIF(EXCLUDED.name_hi, ''), {part_tbl}.name_hi),
                                    name_en = COALESCE(NULLIF(EXCLUDED.name_en, ''), {part_tbl}.name_en),
                                    gram_panchayat = COALESCE(NULLIF(EXCLUDED.gram_panchayat, ''), {part_tbl}.gram_panchayat),
                                    contact_phone = COALESCE(NULLIF(EXCLUDED.contact_phone, ''), {part_tbl}.contact_phone),
                                    phone_source_type = COALESCE(NULLIF(EXCLUDED.phone_source_type, ''), {part_tbl}.phone_source_type);
                            """
                            tuples = [
                                (
                                    tenant_id, r['ward_no'], r['part_no'], r['sl_no'], r['epic_no'],
                                    r['name_hi'], r['name_en'], r['name_norm'], r['rel_type'],
                                    r['rel_name_hi'], r['rel_name_en'], r['rel_norm'], r['house_no'],
                                    r['age'], r['gender'], r['booth_name'], r['booth_area'],
                                    r.get('gram_panchayat') or r.get('gp_name') or '',
                                    r['status'], r['source_file'], r['pdf_page'],
                                    r.get('contact_phone'), r.get('phone_source_type'), r.get('family_id')
                                )
                                for r in elector_records
                            ]
                            chunk_size = 500
                            for i in range(0, len(tuples), chunk_size):
                                chunk = tuples[i:i + chunk_size]
                                psycopg2.extras.execute_values(cur, upsert_sql, chunk, page_size=500)
                            ingest_conn.commit()
                            voters_imported_count = len(tuples)

                            if is_multi_gp:
                                cur.execute(f"""
                                    WITH head_phones AS (
                                        SELECT DISTINCT ON (part_no, COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi))))
                                            part_no,
                                            COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi))) AS rel_key,
                                            contact_phone
                                        FROM {part_tbl}
                                        WHERE contact_phone IS NOT NULL
                                          AND (rel_norm IS NOT NULL OR rel_name_hi IS NOT NULL)
                                          AND length(COALESCE(NULLIF(rel_norm, ''), LOWER(TRIM(rel_name_hi)))) >= 2
                                        ORDER BY part_no, rel_key, age DESC
                                    )
                                    UPDATE {part_tbl} t
                                    SET contact_phone = h.contact_phone,
                                        phone_source_type = 'RECONCILED_V2_KINSHIP'
                                    FROM head_phones h
                                    WHERE t.part_no = h.part_no
                                      AND COALESCE(NULLIF(t.rel_norm, ''), LOWER(TRIM(t.rel_name_hi))) = h.rel_key
                                      AND t.contact_phone IS NULL;
                                """)
                                ingest_conn.commit()

                    # Automated Multi-GP Self-Healing & Verification
                    if voters_imported_count > 0:
                        if is_multi_gp:
                            cur.execute(f"""
                                UPDATE {part_tbl} t
                                SET gram_panchayat = m.gram_panchayat
                                FROM voters_master m
                                WHERE t.epic_no = m.epic_no
                                  AND length(t.epic_no) > 5
                                  AND m.gram_panchayat IS NOT NULL
                                  AND (t.gram_panchayat IS NULL OR trim(t.gram_panchayat) = '');

                                WITH booth_gps AS (
                                    SELECT part_no, gram_panchayat, count(*) as cnt,
                                           ROW_NUMBER() OVER(PARTITION BY part_no ORDER BY count(*) DESC) as rn
                                    FROM {part_tbl}
                                    WHERE gram_panchayat IS NOT NULL AND trim(gram_panchayat) != ''
                                    GROUP BY part_no, gram_panchayat
                                )
                                UPDATE {part_tbl} t
                                SET gram_panchayat = b.gram_panchayat
                                FROM booth_gps b
                                WHERE t.part_no = b.part_no
                                  AND b.rn = 1
                                  AND (t.gram_panchayat IS NULL OR trim(t.gram_panchayat) = '');
                            """)
                            ingest_conn.commit()
                        elif rajasthan_gp:
                            cur.execute(f"""
                                UPDATE {part_tbl}
                                SET gram_panchayat = %s
                                WHERE (gram_panchayat IS NULL OR trim(gram_panchayat) = '');
                            """, (rajasthan_gp,))
                            ingest_conn.commit()

                    # Auto-discover booths from the ingested electors
                    if voters_imported_count > 0:
                        cur.execute(f"""
                            SELECT DISTINCT part_no, booth_name, booth_area
                            FROM {part_tbl}
                            WHERE part_no IS NOT NULL
                            ORDER BY part_no;
                        """)
                        b_rows = cur.fetchall()
                        discovered_booths = {}
                        for p_no, b_name, b_area in b_rows:
                            p_str = str(p_no)
                            discovered_booths[p_str] = {
                                'booth_no': p_str,
                                'name': b_name or f"बूथ {p_str}",
                                'area': b_area or '',
                                'maps_url': ''
                            }
                        if discovered_booths:
                            booths = discovered_booths
                            campaign_cfg['booths'] = discovered_booths
                            if election_type == 'PANCHAYAT' and (not data.get('ward_no') or str(data.get('ward_no')).strip() == '1'):
                                cur.execute(f"SELECT COUNT(DISTINCT ward_no) FROM {part_tbl} WHERE ward_no IS NOT NULL AND ward_no != 0;")
                                w_row = cur.fetchone()
                                w_cnt = w_row[0] if (w_row and w_row[0] > 0) else len(discovered_booths)
                                campaign_cfg['election']['ward_no'] = f"1-{w_cnt}"
                            with open(cfg_path, 'w', encoding='utf-8') as f:
                                json.dump(campaign_cfg, f, ensure_ascii=False, indent=2)

                    # Automated Universal Duplicate Voter Detection Engine
                    if voters_imported_count > 0:
                        try:
                            from scripts.detect_duplicate_voters import detect_duplicates_in_campaign
                            dedup_res = detect_duplicates_in_campaign(cur, tenant_id, slug, apply=True)
                            ingest_conn.commit()
                            print(f"[DEDUP] Flagged {dedup_res['detected_duplicates']} duplicates ({dedup_res['vetoed_citizens']} vetoed) for {slug}", flush=True)
                        except Exception as de:
                            print(f"[DEDUP_ERR] Duplicate detection failed for {slug}: {de}", flush=True)

                        # Automated Caste & Gotra Auto-Tagging Engine (Foolproof Family Consensus)
                        try:
                            from electionapp.caste_auto_tagger import auto_tag_tenant
                            caste_res = auto_tag_tenant(tenant_id, force=False, dry_run=False, use_ai=False)
                            print(f"[CASTE_AUTO_TAG] Tagged {caste_res['total_tagged']}/{caste_res['total_voters']} ({caste_res['coverage_pct']}%) voters for {slug}", flush=True)
                        except Exception as ce:
                            print(f"[CASTE_AUTO_TAG_ERR] Caste auto-tagging failed for {slug}: {ce}", flush=True)

                        # Update authoritative voter count and config in tenants table
                        try:
                            cur.execute(
                                f"UPDATE tenants SET total_voters = (SELECT count(*) FROM {part_tbl} WHERE LOWER(COALESCE(status, '')) <> 'deleted'), config = %s WHERE tenant_id = %s;",
                                (json.dumps(campaign_cfg), tenant_id)
                            )
                            ingest_conn.commit()
                        except Exception as te:
                            print(f"[TENANT_UPDATE_ERR] Failed to update total_voters in tenants table: {te}", flush=True)
            except Exception as ie:
                print(f"[AUTO_INGEST_ERR] {ie}", flush=True)
            finally:
                ingest_conn.close()


    invalidate_tenant_branding(slug)
    clear_tenant_config_cache(slug)

    base_url = str(data.get('base_url') or '').rstrip('/')
    if not base_url:
        base_url = "https://smartbooth.vedmap.com"
    direct_url = f"{base_url}/?tenant={slug}"

    # Generate 1-Click Magic Login URL for Campaign Admin
    admin_uid = created_user_info.get('admin_user_id') if created_user_info else None
    if not admin_uid:
        try:
            import user_manager
            admin_disp_user = created_user_info.get('admin_username') if created_user_info else admin_uname
            existing_u = user_manager.get_user_by_username(admin_disp_user)
            if existing_u:
                admin_uid = existing_u.get('user_id') or existing_u.get('id')
        except Exception:
            pass

    admin_magic_token = None
    magic_url = direct_url
    if admin_uid:
        try:
            import user_manager
            admin_magic_token = user_manager.get_or_create_user_magic_token(admin_uid)
            if admin_magic_token:
                magic_url = f"{base_url}/magic/{admin_magic_token}"
        except Exception as me:
            print(f"[MAGIC_TOKEN_WARN] Failed to get admin magic token: {me}", flush=True)

    # Ensure admin_magic_token is generated and returned as both magic_url and direct_url
    direct_url = magic_url

    # Automated WhatsApp Credential Dispatch (if candidate/admin phone provided)
    auto_wa_result = {
        'attempted': False,
        'success': False,
        'phone': None,
        'error': None
    }
    cand_phone = str(data.get('candidate_phone') or data.get('admin_phone') or '').strip()
    clean_digits = re.sub(r'\D', '', cand_phone)
    if len(clean_digits) >= 10:
        auto_wa_result['attempted'] = True
        auto_wa_result['phone'] = clean_digits
        try:
            from electionapp import whatsapp_quota
            import meta_whatsapp
            cand_display_name = cand_name_hi or cand_name_en or f"{slug} Admin"
            admin_disp_user = created_user_info.get('admin_username') if created_user_info else admin_uname
            admin_disp_pass = created_user_info.get('admin_password') if created_user_info else admin_pwd

            credit = whatsapp_quota.reserve(tenant_id, 1, performed_by=owner_user_id)
            try:
                with credit:
                    wa_ok, _wa_data, wa_err = meta_whatsapp.send_credentials_template(
                        phone=clean_digits,
                        full_name=cand_display_name,
                        username=admin_disp_user,
                        password=admin_disp_pass,
                        login_url=magic_url,
                        sender_label="SmartBooth Team",
                        credit=credit
                    )
                    auto_wa_result['success'] = bool(wa_ok)
                    auto_wa_result['error'] = wa_err if not wa_ok else None
            except Exception as w_exc:
                auto_wa_result['error'] = str(w_exc)
        except Exception as e:
            auto_wa_result['error'] = str(e)
            try:
                print(f"[AUTO_WA_DISPATCH_ERR] Auto WhatsApp dispatch failed for {slug}: {str(e).encode('ascii', errors='replace').decode('ascii')}", flush=True)
            except Exception:
                pass

    return True, {
        'success': True,
        'tenant_slug': slug,
        'campaign_slug': slug,
        'tenant_id': tenant_id,
        'election_type': election_type,
        'voting_method': resolved_voting_method,
        'direct_url': magic_url,
        'magic_url': magic_url,
        'magic_token': admin_magic_token,
        'portal_url': direct_url,
        'auto_whatsapp': auto_wa_result,
        'candidate_name': cand_name_hi or cand_name_en,
        'candidate_phone': str(data.get('candidate_phone') or data.get('admin_phone') or '').strip(),
        'admin_username': created_user_info.get('admin_username') if created_user_info else admin_uname,
        'admin_password': created_user_info.get('admin_password') if created_user_info else admin_pwd,
        'admin_user_id': created_user_info.get('admin_user_id') if created_user_info else None,
        'operator_username': created_user_info.get('operator_username') if created_user_info else op_uname,
        'operator_password': created_user_info.get('operator_password') if created_user_info else op_pwd,
        'operator_user_id': created_user_info.get('operator_user_id') if created_user_info else None,
        'storage_backend': 'postgres',
        'voters_imported': voters_imported_count,
        'booths_count': len(booths) if isinstance(booths, dict) else 0,
        'whatsapp_credits': wa_credits,
        'whatsapp_quota': {
            'granted': wa_credits,
            'consumed': 0,
            'available': wa_credits
        },
        'voice_credits': voice_credits,
        'voice_quota': {
            'granted': voice_credits,
            'consumed': 0,
            'available': voice_credits
        },
        'user_created': created_user_info,
        'user_creation_error': user_creation_error,
        'message': f"Campaign '{slug}' successfully provisioned with {voters_imported_count} voters and {len(booths) if isinstance(booths, dict) else 0} booths!"
    }, 201


def update_campaign(slug: str, data: Dict[str, Any], files: Any = None) -> Tuple[bool, Dict[str, Any], int]:
    """
    Updates an existing campaign's parameters dynamically:
    Candidate details, photos, party, symbol, ballot serial number, slogan, message, booths.
    Guarantees zero downtime and non-destructive partition preservation.
    """
    files = files or {}
    slug = sanitize_slug(slug)
    if not slug:
        return False, {'error': 'Campaign slug is required'}, 400

    try:
        tenant_dir = get_tenant_dir(slug)
    except ValueError:
        return False, {'error': f"Invalid campaign slug: '{slug}'"}, 400
    cfg_path = os.path.join(tenant_dir, 'campaign.json')
    if not os.path.exists(cfg_path):
        return False, {'error': f"Campaign '{slug}' not found — tenant directory missing or campaign.json absent"}, 404

    assets_dir = get_tenant_assets_dir(slug)
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        return False, {'error': f"Failed to read existing configuration: {e}"}, 500

    updated_items = []

    # 1. Update Candidate Photo
    if 'candidate_photo' in files:
        photo = files['candidate_photo']
        if photo and getattr(photo, 'filename', None):
            ext = os.path.splitext(photo.filename)[1].lower() or '.png'
            filename = f"candidate_photo{ext}"
            photo_dest = os.path.join(assets_dir, filename)
            _normalize_and_save_candidate_photo(photo, photo_dest)
            # Duplicate to persistent git-ignored data/<slug>/assets
            try:
                persistent_assets = os.path.join(DATA_DIR, slug, 'assets')
                os.makedirs(persistent_assets, exist_ok=True)
                shutil.copy2(photo_dest, os.path.join(persistent_assets, filename))
            except Exception:
                pass
            cfg.setdefault('candidate', {})['photo_filename'] = filename
            try:
                from electionapp.s3_storage import upload_campaign_asset
                s3_res = upload_campaign_asset(photo_dest, slug, filename)
                p_url = s3_res.get('thumb_url') or s3_res.get('url')
                if p_url:
                    cfg['candidate']['photo_url'] = p_url
            except Exception as e:
                logger.warning("S3 candidate photo update failed: %s", e)
            updated_items.append("Candidate Photo")

    # 2. Update Party Symbol
    sym = files.get('party_symbol') if files else None
    if sym and getattr(sym, 'filename', None) and str(sym.filename).strip():
        ext = os.path.splitext(sym.filename)[1].lower() or '.png'
        filename = f"symbol{ext}"
        sym_dest = os.path.join(assets_dir, filename)
        sym.save(sym_dest)
        # Duplicate to persistent git-ignored data/<slug>/assets
        try:
            persistent_assets = os.path.join(DATA_DIR, slug, 'assets')
            os.makedirs(persistent_assets, exist_ok=True)
            shutil.copy2(sym_dest, os.path.join(persistent_assets, filename))
        except Exception:
            pass
        cfg.setdefault('party', {})['symbol_filename'] = filename
        try:
            from electionapp.s3_storage import upload_campaign_asset
            s3_res = upload_campaign_asset(sym_dest, slug, filename, generate_thumb=False)
            s_url = s3_res.get('url')
            if s_url:
                cfg['party']['symbol_url'] = s_url
        except Exception as e:
            logger.warning("S3 party symbol update failed: %s", e)
        updated_items.append("Party Symbol")
    else:
        preset_candidate = data.get('preset_symbol') or data.get('symbol_name_hi') or data.get('symbol_name_en')
        if preset_candidate:
            src = _resolve_preset_symbol_path(preset_candidate)
            if src and os.path.exists(src):
                sym_dest = os.path.join(assets_dir, 'symbol.png')
                shutil.copy2(src, sym_dest)
                try:
                    persistent_assets = os.path.join(DATA_DIR, slug, 'assets')
                    os.makedirs(persistent_assets, exist_ok=True)
                    shutil.copy2(sym_dest, os.path.join(persistent_assets, 'symbol.png'))
                except Exception:
                    pass
                cfg.setdefault('party', {})['symbol_filename'] = 'symbol.png'
                try:
                    from electionapp.s3_storage import upload_campaign_asset
                    s3_res = upload_campaign_asset(sym_dest, slug, 'symbol.png', generate_thumb=False)
                    s_url = s3_res.get('url')
                    if s_url:
                        cfg['party']['symbol_url'] = s_url
                except Exception as e:
                    logger.warning("S3 preset symbol update failed: %s", e)
                cfg.setdefault('party', {})['symbol_filename'] = 'symbol.png'
                updated_items.append("Party Symbol (Preset)")

    # 3. Update Candidate Profile Details
    cand = cfg.setdefault('candidate', {})
    if 'candidate_name_hi' in data:
        cand['name_hi'] = str(data['candidate_name_hi']).strip()
        updated_items.append("Candidate Name (Hindi)")
    if 'candidate_name_en' in data:
        cand['name_en'] = str(data['candidate_name_en']).strip()
        updated_items.append("Candidate Name (English)")
    if 'designation' in data:
        cand['designation'] = str(data['designation']).strip()
    if 'candidate_phone' in data:
        cand['phone'] = str(data['candidate_phone']).strip()
    if 'office_address' in data:
        cand['office_address'] = str(data['office_address']).strip()

    # 4. Update Election & Ward Details
    elec = cfg.setdefault('election', {})
    if 'ward_no' in data:
        elec['ward_no'] = str(data['ward_no']).strip()
    if 'ward_name_hi' in data:
        elec['ward_name_hi'] = str(data['ward_name_hi']).strip()
        elec['app_title'] = f"{elec['ward_name_hi']} Smart Booth"
    if 'ward_name_en' in data:
        elec['ward_name_en'] = str(data['ward_name_en']).strip()
    if 'constituency' in data:
        elec['constituency'] = str(data['constituency']).strip()
    if 'city' in data:
        elec['city'] = str(data['city']).strip()
    if 'district' in data:
        elec['district'] = str(data['district']).strip()
    if 'state' in data:
        elec['state'] = str(data['state']).strip()
    if 'election_type' in data:
        elec['election_type'] = str(data['election_type']).upper().strip()

    # 5. Update Party, Ballot & Symbol Details
    party = cfg.setdefault('party', {})
    if 'party_name_hi' in data:
        party['name_hi'] = str(data['party_name_hi']).strip()
        updated_items.append("Party Name")
    if 'party_name_en' in data:
        party['name_en'] = str(data['party_name_en']).strip()
    if 'symbol_name_hi' in data:
        party['symbol_name_hi'] = str(data['symbol_name_hi']).strip()
        updated_items.append("Symbol Name")
    if 'symbol_name_en' in data:
        party['symbol_name_en'] = str(data['symbol_name_en']).strip()
    if 'ballot_serial' in data:
        party['ballot_serial'] = str(data['ballot_serial']).strip()
        updated_items.append("Ballot Serial Number")
    if 'ballot_color' in data:
        b_color = str(data['ballot_color'] or '').strip()
        party['ballot_color'] = b_color
        elec['ballot_color'] = b_color
        updated_items.append("Ballot Color")
    if 'voting_method' in data:
        v_meth = get_voting_method(elec.get('election_type', 'MUNICIPAL'), data.get('voting_method'))
        party['voting_method'] = v_meth
        elec['voting_method'] = v_meth
        updated_items.append("Voting Method")
    elif 'election_type' in data and 'voting_method' not in elec and 'voting_method' not in party:
        v_meth = get_voting_method(elec['election_type'])
        elec['voting_method'] = v_meth
        party['voting_method'] = v_meth
    if 'slogan' in data:
        party['slogan'] = str(data['slogan']).strip()
        updated_items.append("Slogan")
    if 'theme_color' in data:
        party['theme_color'] = str(data['theme_color']).strip()

    # 6. Update Campaign Message
    if 'campaign_message' in data and str(data['campaign_message']).strip():
        cfg['campaign_message'] = str(data['campaign_message']).strip()
        updated_items.append("WhatsApp Campaign Message")

    # 7. Update Booths
    if 'booths' in data or 'booths_data' in data:
        booths_raw = data.get('booths') or data.get('booths_data')
        parsed = parse_booths_data(booths_raw)
        if parsed:
            cfg['booths'] = parsed
            updated_items.append("Booths Directory")

    max_sub = None
    if 'max_sub_accounts' in data and data['max_sub_accounts'] is not None and str(data['max_sub_accounts']).strip() != '':
        try:
            max_sub = int(data['max_sub_accounts'])
            cfg['max_sub_accounts'] = max_sub
            updated_items.append("Max Sub Accounts Limit")
        except (ValueError, TypeError):
            pass

    new_status = None
    if 'status' in data and str(data['status']).strip():
        st = str(data['status']).strip().upper()
        if st in ('ACTIVE', 'PAUSED', 'CONCLUDED', 'ARCHIVED'):
            new_status = st
            cfg['status'] = st
            updated_items.append(f"Status ({st})")

    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 8. Synchronize PostgreSQL tenants metadata if active
    try:
        pg_conn = get_pg_connection()
        if pg_conn:
            with pg_conn.cursor() as cur:
                cur.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS config JSONB DEFAULT '{}'::jsonb;")
                cfg_json = json.dumps(cfg, ensure_ascii=False)
                cur.execute("""
                    UPDATE tenants
                    SET candidate_name = %s,
                        election_type = %s,
                        state = %s,
                        status = COALESCE(%s, status),
                        max_sub_accounts = COALESCE(%s, max_sub_accounts),
                        config = %s
                    WHERE campaign_slug = %s;
                """, (
                    cand.get('name_hi') or cand.get('name_en') or slug,
                    elec.get('election_type', 'MUNICIPAL'),
                    elec.get('state', 'Rajasthan'),
                    new_status,
                    max_sub,
                    cfg_json,
                    slug
                ))
                pg_conn.commit()
            pg_conn.close()
    except Exception:
        pass

    if new_status:
        try:
            import user_manager
            user_manager.sync_users_with_campaign_status(slug, new_status)
        except Exception:
            pass

    # Touch cross-worker fleet cache version sentinel
    try:
        fleet_ver = os.path.join(TENANTS_DIR, '.fleet_version')
        with open(fleet_ver, 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass

    # Invalidate in-memory branding cache AND purge disk-cached card PNGs.
    # The card generator caches rendered slip PNGs keyed by tenant+voter IDs.
    # Without purging these files, branding changes (name, ballot serial, symbol name)
    # are silently ignored on next dispatch — the old stale image is re-sent instead.
    invalidate_tenant_branding(slug)
    clear_tenant_config_cache(slug)
    purged_cards = purge_tenant_generated_cards(slug)

    return True, {
        'success': True,
        'tenant_slug': slug,
        'updated_items': updated_items,
        'purged_cards': purged_cards,
        'message': f"Successfully updated campaign '{slug}': {', '.join(updated_items) if updated_items else 'All settings saved'}"
    }, 200
