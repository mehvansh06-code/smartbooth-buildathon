import os
import json
import re
import time
import copy
from typing import Dict, Any, List, Optional, Tuple

PLATFORM_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TENANTS_DIR = os.path.join(PLATFORM_ROOT, 'tenants')
DATA_DIR = os.path.join(PLATFORM_ROOT, 'data')

import threading

_TENANT_CONFIG_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_AVAILABLE_TENANTS_CACHE: Tuple[float, List[str]] = (0.0, [])
_AVAILABLE_TENANTS_TTL = 60.0  # seconds
_WARD_HIERARCHY_CACHE: Dict[str, Tuple[float, Dict[str, Any], Dict[str, Any]]] = {}
_WARD_HIERARCHY_TTL = 300.0  # 5 minutes
_PANCHAYAT_HIERARCHY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PANCHAYAT_HIERARCHY_TTL = 300.0  # 5 minutes
_COMMUNITY_TAGS_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
_COMMUNITY_TAGS_TTL = 300.0  # 5 minutes
_TENANT_STATUS_CACHE: Dict[str, Tuple[float, str]] = {}
_TENANT_STATUS_LOCK = threading.Lock()
_TENANT_STATUS_TTL = 10.0  # 10 seconds TTL


def clear_community_tags_cache(tenant_slug: Optional[str] = None) -> None:
    """Clears cached community tags."""
    if tenant_slug:
        _COMMUNITY_TAGS_CACHE.pop(tenant_slug, None)
    else:
        _COMMUNITY_TAGS_CACHE.clear()


def clear_ward_hierarchy_cache(tenant_slug: Optional[str] = None) -> None:
    """Clears cached ward hierarchy and panchayat hierarchy."""
    if tenant_slug:
        _WARD_HIERARCHY_CACHE.pop(tenant_slug, None)
        _PANCHAYAT_HIERARCHY_CACHE.pop(tenant_slug, None)
    else:
        _WARD_HIERARCHY_CACHE.clear()
        _PANCHAYAT_HIERARCHY_CACHE.clear()



def invalidate_tenant_status(tenant_slug: Optional[str] = None) -> None:
    """Evicts cached tenant status."""
    with _TENANT_STATUS_LOCK:
        if tenant_slug:
            _TENANT_STATUS_CACHE.pop(str(tenant_slug).strip().lower(), None)
        else:
            _TENANT_STATUS_CACHE.clear()


def get_tenant_status(tenant_slug: Optional[str]) -> str:
    """
    Returns normalized campaign status ('ACTIVE', 'PAUSED', 'CONCLUDED', 'ARCHIVED').
    Thread-safe in-memory cache with PostgreSQL query and campaign.json fallback.
    """
    if not tenant_slug:
        return 'ACTIVE'
    clean = str(tenant_slug).strip().lower()
    if not clean or clean == '*':
        return 'ACTIVE'

    now = time.time()
    with _TENANT_STATUS_LOCK:
        if clean in _TENANT_STATUS_CACHE:
            ts, val = _TENANT_STATUS_CACHE[clean]
            if now - ts < _TENANT_STATUS_TTL:
                return val

    status = None
    # 1. Authoritative PostgreSQL check if available
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute("SELECT status FROM tenants WHERE LOWER(campaign_slug) = %s", (clean,))
                row = cur.fetchone()
                if row and row[0]:
                    status = str(row[0]).strip().upper()
        except Exception:
            pass
        finally:
            try:
                pg_conn.close()
            except Exception:
                pass

    # 2. Filesystem campaign.json fallback if not determined from database
    if not status:
        try:
            cfg_file = os.path.join(TENANTS_DIR, clean, 'campaign.json')
            if os.path.exists(cfg_file):
                with open(cfg_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    st = cfg.get('status')
                    if st:
                        status = str(st).strip().upper()
        except Exception:
            pass

    if not status or status not in ('ACTIVE', 'PAUSED', 'CONCLUDED', 'ARCHIVED'):
        status = 'ACTIVE'

    with _TENANT_STATUS_LOCK:
        _TENANT_STATUS_CACHE[clean] = (now, status)
    return status


def clear_tenant_config_cache(tenant_slug: Optional[str] = None) -> None:
    """Clears in-memory config and tenant cache."""
    global _AVAILABLE_TENANTS_CACHE
    if tenant_slug:
        _TENANT_CONFIG_CACHE.pop(tenant_slug, None)
        _WARD_HIERARCHY_CACHE.pop(tenant_slug, None)
        _COMMUNITY_TAGS_CACHE.pop(tenant_slug, None)
    else:
        _TENANT_CONFIG_CACHE.clear()
        _WARD_HIERARCHY_CACHE.clear()
        _COMMUNITY_TAGS_CACHE.clear()
    _AVAILABLE_TENANTS_CACHE = (0.0, [])
    invalidate_tenant_status(tenant_slug)



def list_available_tenants() -> List[str]:
    """Returns a list of valid tenant slugs found in the tenants directory with TTL caching."""
    global _AVAILABLE_TENANTS_CACHE
    now = time.time()
    last_time, cached_list = _AVAILABLE_TENANTS_CACHE
    if now - last_time < _AVAILABLE_TENANTS_TTL and cached_list:
        return list(cached_list)

    if not os.path.isdir(TENANTS_DIR):
        tenants = []
    else:
        tenants = []
        for entry in os.listdir(TENANTS_DIR):
            candidate_dir = os.path.join(TENANTS_DIR, entry)
            if os.path.isdir(candidate_dir) and os.path.exists(os.path.join(candidate_dir, 'campaign.json')):
                tenants.append(entry)
    fixtures_dir = os.path.join(PLATFORM_ROOT, 'tests', 'fixtures')
    if os.path.isdir(fixtures_dir):
        for entry in os.listdir(fixtures_dir):
            if entry not in tenants and os.path.exists(os.path.join(fixtures_dir, entry, 'campaign.json')):
                tenants.append(entry)
    result = sorted(tenants)
    _AVAILABLE_TENANTS_CACHE = (now, result)
    return list(result)


def get_tenant_dir(tenant_slug: str) -> str:
    """Returns absolute path to tenant directory."""
    if not tenant_slug or not re.match(r'^[a-zA-Z0-9_-]+$', tenant_slug):
        raise ValueError(f"Invalid tenant slug: '{tenant_slug}'")
    tdir = os.path.join(TENANTS_DIR, tenant_slug)
    if not os.path.isdir(tdir):
        fixture_dir = os.path.join(PLATFORM_ROOT, 'tests', 'fixtures', tenant_slug)
        if os.path.isdir(fixture_dir):
            return fixture_dir
    return tdir


def get_tenant_assets_dir(tenant_slug: str) -> str:
    """Returns absolute path to tenant assets directory."""
    tdir = get_tenant_dir(tenant_slug)
    assets_dir = os.path.join(tdir, 'assets')
    if not os.path.isdir(assets_dir):
        fixture_assets = os.path.join(PLATFORM_ROOT, 'tests', 'fixtures', tenant_slug, 'assets')
        if os.path.isdir(fixture_assets):
            return fixture_assets
    os.makedirs(assets_dir, exist_ok=True)
    return assets_dir


BALLOT_PAPER_ELECTION_TYPES = {
    'PANCHAYAT', 'GRAM_PANCHAYAT', 'SARPANCH', 'WARD_PANCH'
}

EVM_ELECTION_TYPES = {
    'ASSEMBLY', 'VIDHAN_SABHA', 'PARLIAMENTARY', 'LOK_SABHA',
    'MUNICIPAL', 'NAGAR_PALIKA', 'NAGAR_PARISHAD', 'NAGAR_NIGAM',
    'ZILA_PARISHAD', 'ZP',
    'PANCHAYAT_SAMITI', 'PS'
}


def get_voting_method(election_type: Optional[str] = None, custom_override: Optional[str] = None) -> str:
    """
    Returns the voting method ('BALLOT_PAPER' or 'EVM') for an election type.
    Allows custom override if explicitly passed.
    """
    if custom_override is not None and str(custom_override).strip():
        return str(custom_override).strip().upper()

    et = str(election_type or '').strip().upper()
    if et in BALLOT_PAPER_ELECTION_TYPES:
        return 'BALLOT_PAPER'
    if et in EVM_ELECTION_TYPES:
        return 'EVM'

    # Heuristic matching: Panchayat Samiti, Zila Parishad, Municipal, and Assembly are EVM
    if any(k in et for k in ('ZILA', 'PARISHAD', 'SAMITI', 'ASSEMBLY', 'VIDHAN', 'LOK_SABHA', 'PARLIAMENT', 'MUNICIPAL', 'NAGAR', 'CORP', 'EVM')):
        return 'EVM'
    # Only rural Gram Panchayat (Sarpanch / Ward Panch) use physical paper ballot
    if any(k in et for k in ('SARPANCH', 'WARD_PANCH', 'PANCHAYAT', 'GRAM', 'PANCH', 'PRI')):
        return 'BALLOT_PAPER'

    return 'BALLOT_PAPER'


def validate_tenant_config(cfg: Dict[str, Any], tenant_slug: str) -> None:
    """
    Strict validation for campaign configuration schema.
    Ensures candidate, election, party, and booths sections are complete and valid.
    Prevents malformed configs from rendering corrupted UI or leaking default tenant branding.
    """
    if not isinstance(cfg, dict):
        raise ValueError(f"Tenant '{tenant_slug}' campaign configuration must be a JSON object")

    required_sections = ['candidate', 'election']
    for sec in required_sections:
        if sec not in cfg or not isinstance(cfg[sec], dict):
            raise ValueError(f"Tenant '{tenant_slug}' campaign.json missing required section: '{sec}'")

    # 1. Candidate validation
    candidate = cfg['candidate']
    for key in ['name_hi', 'name_en']:
        if key not in candidate or not isinstance(candidate[key], str):
            raise ValueError(f"Tenant '{tenant_slug}' campaign.json candidate missing required field: '{key}'")

    # 2. Election validation
    election = cfg['election']
    for key in ['ward_name_hi', 'ward_name_en', 'app_title']:
        if key not in election or not str(election[key]).strip():
            raise ValueError(f"Tenant '{tenant_slug}' campaign.json election missing required field: '{key}'")
    for key in ['voting_method', 'ballot_color']:
        if key in election and election[key] is not None and not isinstance(election[key], str):
            raise ValueError(f"Tenant '{tenant_slug}' campaign.json election field '{key}' must be a string")

    # 3. Party validation (optional, e.g. for independent / panchayat campaigns)
    if 'party' in cfg and cfg['party'] is not None:
        if not isinstance(cfg['party'], dict):
            raise ValueError(f"Tenant '{tenant_slug}' campaign.json 'party' must be a mapping")
        party = cfg['party']
        string_keys = ['name_hi', 'name_en', 'symbol_name_hi', 'symbol_name_en', 'symbol_filename', 'theme_color', 'slogan', 'ballot_color', 'voting_method']
        for key in string_keys:
            if key in party and party[key] is not None and not isinstance(party[key], str):
                raise ValueError(f"Tenant '{tenant_slug}' campaign.json party field '{key}' must be a string")
        if 'ballot_serial' in party and party['ballot_serial'] is not None:
            if not isinstance(party['ballot_serial'], (str, int)):
                raise ValueError(f"Tenant '{tenant_slug}' campaign.json party field 'ballot_serial' must be a string or integer")

    # 4. Booths validation
    if 'booths' in cfg and cfg['booths'] is not None:
        if not isinstance(cfg['booths'], (dict, list)):
            raise ValueError(f"Tenant '{tenant_slug}' campaign.json 'booths' must be a mapping or list")


def get_pg_connection():
    """Helper to connect to PostgreSQL if available."""
    try:
        import psycopg2
        import psycopg2.extras
        return psycopg2.connect(
            dbname=os.environ.get('PGDATABASE', 'voter_platform'),
            user=os.environ.get('PGUSER', 'postgres'),
            password=os.environ.get('PGPASSWORD', 'postgres'),
            host=os.environ.get('PGHOST', 'localhost'),
            port=int(os.environ.get('PGPORT', 5432)),
            connect_timeout=3
        )
    except Exception:
        return None


def _deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in source.items():
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            _deep_merge(target[k], v)
        else:
            target[k] = copy.deepcopy(v)
    return target


def load_tenant_config(tenant_slug: str) -> Dict[str, Any]:
    """
    Loads campaign configuration dynamically by tenant slug.
    Blends static filesystem baseline with PostgreSQL authoritative runtime config.
    Memoized using file mtime to eliminate redundant filesystem I/O.
    """
    tdir = get_tenant_dir(tenant_slug)
    cfg_file = os.path.join(tdir, 'campaign.json')

    if not os.path.exists(cfg_file):
        raise FileNotFoundError(f"Campaign config for tenant '{tenant_slug}' not found at: {cfg_file}")

    mtime = os.path.getmtime(cfg_file)
    cached = _TENANT_CONFIG_CACHE.get(tenant_slug)
    if cached and cached[0] == mtime:
        return copy.deepcopy(cached[1])

    try:
        with open(cfg_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse campaign.json for tenant '{tenant_slug}': {e}") from e

    cfg['tenant_slug'] = tenant_slug

    # Overlay authoritative PostgreSQL configuration if active
    pg_conn = get_pg_connection()
    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT candidate_name, status, config FROM tenants WHERE campaign_slug = %s",
                    (tenant_slug,)
                )
                row = cur.fetchone()
                if row:
                    db_cand_name, db_status, db_cfg = row[0], row[1], row[2]
                    if db_status:
                        cfg['status'] = str(db_status).strip().upper()
                    if db_cfg and isinstance(db_cfg, dict) and len(db_cfg) > 0:
                        _deep_merge(cfg, db_cfg)
                    cand_clean = str(db_cand_name or '').strip()
                    if cand_clean and '?' not in cand_clean:
                        is_deva = any('\u0900' <= char <= '\u097F' for char in cand_clean)
                        if is_deva:
                            cfg.setdefault('candidate', {})['name_hi'] = cand_clean
                        else:
                            cfg.setdefault('candidate', {})['name_en'] = cand_clean
            pg_conn.close()
        except Exception:
            pass

    try:
        from electionapp.translit.normalizer import clean_text_or_structure
        cfg = clean_text_or_structure(cfg)
    except Exception:
        pass

    validate_tenant_config(cfg, tenant_slug)
    _TENANT_CONFIG_CACHE[tenant_slug] = (mtime, cfg)
    return copy.deepcopy(cfg)


def get_tenant_db_path(tenant_slug: str, config: Optional[Dict[str, Any]] = None) -> str:
    """
    Resolves the SQLite database file path for a tenant.
    Resolution order:
    1. VOTER_DB_PATH environment variable (explicit override)
    2. PLATFORM/data/<tenant_slug>/<db_filename>
    3. PLATFORM/data/<tenant_slug>/voters.db
    4. PLATFORM/<db_filename>
    5. Default to PLATFORM/data/<tenant_slug>/<db_filename>
    """
    env_override = os.environ.get('VOTER_DB_PATH')
    if env_override:
        return os.path.abspath(env_override)

    cfg = config or load_tenant_config(tenant_slug)
    db_filename = cfg.get('database', {}).get('db_filename', f"voters_{tenant_slug}.db")

    if os.path.isabs(db_filename):
        return db_filename

    # Option 2: data/<slug>/<db_filename>
    p2 = os.path.join(DATA_DIR, tenant_slug, db_filename)
    if os.path.exists(p2):
        return p2

    # Option 3: data/<slug>/voters.db
    p3 = os.path.join(DATA_DIR, tenant_slug, 'voters.db')
    if os.path.exists(p3):
        return p3

    # Option 4: PLATFORM/<db_filename>
    p4 = os.path.join(PLATFORM_ROOT, db_filename)
    if os.path.exists(p4):
        return p4

    # Default location
    return os.path.join(DATA_DIR, tenant_slug, db_filename)


def get_booths_dict(tenant_config: Dict[str, Any], db_path: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
    """
    Returns booth definitions mapping part_no (int) to booth metadata.
    If explicitly declared in campaign.json, uses those.
    """
    cfg_booths = tenant_config.get('booths') or {}
    if cfg_booths:
        formatted = {}
        for k, v in cfg_booths.items():
            try:
                p_num = int(k)
                formatted[p_num] = {
                    'booth_no': v.get('booth_no', p_num),
                    'name': v.get('name', f'भाग {p_num}'),
                    'name_en': v.get('name_en', f'Part {p_num}'),
                    'area': v.get('area', ''),
                    'maps_url': v.get('maps_url', '')
                }
            except Exception:
                continue
        if formatted:
            return formatted

    return {}


def is_ward_campaign(cfg: Dict[str, Any]) -> bool:
    """Returns True if the tenant campaign represents a local body election (Gram Panchayat / Sarpanch,
    Zila Parishad, Panchayat Samiti, or Nagar Nikay / Municipal) where electors are organized by Wards.
    Only State Assembly (Vidhan Sabha) and Parliamentary (Lok Sabha) elections are strictly Booth-based.
    """
    if not cfg or not isinstance(cfg, dict):
        return False
    elec = cfg.get('election', {}) if isinstance(cfg.get('election'), dict) else {}
    etype = str(elec.get('election_type') or cfg.get('election_type') or '').upper().strip()

    # 1. Authoritative check: State Assembly & Parliamentary seats are strictly Booth-based
    if etype in ('ASSEMBLY', 'VIDHAN_SABHA', 'PARLIAMENTARY', 'LOK_SABHA'):
        return False
    w_hi = str(elec.get('ward_name_hi') or cfg.get('ward_name_hi') or '')
    w_en = str(elec.get('ward_name_en') or cfg.get('ward_name_en') or '').lower()
    constituency = str(elec.get('constituency') or cfg.get('constituency') or '').lower()
    if 'विधानसभा' in w_hi or 'विधान सभा' in w_hi or 'ac-' in w_en or 'vidhan' in w_en or 'ac-' in constituency or 'assembly' in constituency:
        return False

    # 2. Local body elections are Ward-based
    if etype in ('PANCHAYAT', 'GRAM_PANCHAYAT', 'SARPANCH', 'WARD_PANCH', 'ZILA_PARISHAD', 'ZP', 'PANCHAYAT_SAMITI', 'PS', 'MUNICIPAL', 'NAGAR_NIGAM', 'NAGAR_PARISHAD', 'NAGAR_PALIKA', 'PARSHAD', 'WARD'):
        return True
    if elec.get('is_ward_election') or cfg.get('is_ward_election') or elec.get('is_panchayat') or cfg.get('is_panchayat'):
        return True
    if 'ग्राम पंचायत' in w_hi or 'वार्ड' in w_hi or 'ward' in w_en:
        return True
    cand_obj = cfg.get('candidate', {}) if isinstance(cfg.get('candidate'), dict) else {}
    cand_en = str(cand_obj.get('name_en') or cfg.get('candidate_name_en') or cfg.get('candidate_name') or '').lower()
    cand_hi = str(cand_obj.get('name_hi') or cfg.get('candidate_name_hi') or cfg.get('candidate_name') or '')
    if 'sarpanch' in cand_en or 'सरपंच' in cand_hi or 'पार्षद' in cand_hi or 'parshad' in cand_en:
        return True
    return False


def is_panchayat_campaign(cfg: Dict[str, Any]) -> bool:
    """Returns True if the tenant campaign represents a Gram Panchayat / Sarpanch or local body ward election.
    Maintained for backward compatibility with existing callers.
    """
    return is_ward_campaign(cfg)


def get_cached_ward_hierarchy(repo: Any, tenant_slug: str, cfg: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Returns (wards_dict, booths_with_wards) with 5-minute TTL memoization.
    Guarantees both core.py and template context processor use the exact same cache.
    """
    if not is_ward_campaign(cfg) or not repo or not hasattr(repo, 'get_wards_dict'):
        return {}, {}
    now = time.time()
    cached = _WARD_HIERARCHY_CACHE.get(tenant_slug)
    if cached and (now - cached[0] < _WARD_HIERARCHY_TTL):
        return cached[1], cached[2]
    wards: Dict[str, Any] = {}
    booths_with_wards: Dict[str, Any] = {}
    try:
        found_wards = repo.get_wards_dict(tenant_slug) if hasattr(repo, 'get_wards_dict') else {}
        if len(found_wards) >= 1:
            wards = found_wards
            if hasattr(repo, 'get_booths_with_wards'):
                booths_with_wards = repo.get_booths_with_wards(tenant_slug)
        else:
            # Fallback for single-ward campaigns (e.g. Ward 82, ZP Ward 23) where table ward_no was not populated
            elec = cfg.get('election', {}) if isinstance(cfg.get('election'), dict) else {}
            w_no_cfg = elec.get('ward_no') or cfg.get('ward_no')
            if w_no_cfg and str(w_no_cfg).strip():
                w_str = str(w_no_cfg).strip()
                if w_str.isdigit():
                    booths_dict = repo.get_booths_dict(tenant_slug) if hasattr(repo, 'get_booths_dict') else {}
                    if not booths_dict:
                        booths_dict = dict(cfg.get('booths') or {})
                    tot_voters = sum(int(b.get('total_voters', b.get('total', b.get('voter_count', 0))) or 0) for b in booths_dict.values())
                    b_list = []
                    b_with_w = {}
                    for p_no, b_info in booths_dict.items():
                        b_obj = dict(b_info) if isinstance(b_info, dict) else {'name': str(b_info)}
                        b_cnt = int(b_obj.get('total_voters', b_obj.get('total', b_obj.get('voter_count', 0))) or 0)
                        b_list.append({
                            "part_no": p_no,
                            "booth_no": str(b_obj.get('booth_no') or p_no),
                            "booth_name": b_obj.get('name') or b_obj.get('booth_name') or f"Booth {p_no}",
                            "voter_count": b_cnt
                        })
                        b_with_w[str(p_no)] = {
                            "part_no": p_no,
                            "booth_no": str(b_obj.get('booth_no') or p_no),
                            "booth_name": b_obj.get('name') or b_obj.get('booth_name') or f"Booth {p_no}",
                            "wards": [{"ward_no": int(w_str), "voter_count": b_cnt}],
                            "total_voters": b_cnt
                        }
                    wards = {
                        w_str: {
                            "ward_no": int(w_str),
                            "part_no": b_list[0]["part_no"] if b_list else 1,
                            "booth_no": ", ".join(dict.fromkeys(b["booth_no"] for b in b_list)),
                            "booth_name": f"{len(b_list)} मतदान केंद्र" if len(b_list) > 1 else (b_list[0]["booth_name"] if b_list else ""),
                            "voter_count": tot_voters,
                            "total": tot_voters,
                            "booths": b_list
                        }
                    }
                    booths_with_wards = b_with_w

        _WARD_HIERARCHY_CACHE[tenant_slug] = (now, wards, booths_with_wards)
    except Exception:
        pass
    return wards, booths_with_wards


def get_cached_panchayat_hierarchy(repo: Any, tenant_slug: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Returns structured panchayat hierarchy with 5-minute TTL memoization.
    Guarantees both core.py and template context processors receive structured
    Gram Panchayat information for multi-GP campaigns.
    """
    if not is_ward_campaign(cfg) or not repo or not hasattr(repo, 'get_panchayat_hierarchy'):
        return {"is_multi_gp": False, "panchayats": [], "total_voters": 0}
    now = time.time()
    cached = _PANCHAYAT_HIERARCHY_CACHE.get(tenant_slug)
    if cached and (now - cached[0] < _PANCHAYAT_HIERARCHY_TTL):
        return cached[1]
    hierarchy: Dict[str, Any] = {"is_multi_gp": False, "panchayats": [], "total_voters": 0}
    try:
        hierarchy = repo.get_panchayat_hierarchy(tenant_slug)
        _PANCHAYAT_HIERARCHY_CACHE[tenant_slug] = (now, hierarchy)
    except Exception:
        pass
    return hierarchy


def parse_location_filter(ward_arg: Any = None, part_arg: Any = None) -> Tuple[Optional[int], Optional[int], Optional[List[int]]]:
    """
    Robustly parses ward_no, part_no, and part_nos from location parameters.
    Maintains backward compatibility with 3-element tuple unpacking.
    """
    w, p, ps, _, _ = parse_location_filter_extended(ward_arg, part_arg)
    return w, p, ps


def parse_location_filter_extended(ward_arg: Any = None, part_arg: Any = None) -> Tuple[Optional[int], Optional[int], Optional[List[int]], Optional[str], Optional[List[int]]]:
    """
    Robustly parses ward_no, part_no, part_nos, and gram_panchayat (gp) from location parameters.
    Supports:
    - gp:IRAS_ward:1, gp:IRAS
    - parts:14,15_ward:1, part:9_ward:1
    - parts:9,10, ward:1, part:82
    """
    clean_ward = None
    clean_part = None
    clean_parts = None
    clean_gp = None
    clean_wards = None

    for val, default_type in [(ward_arg, 'ward'), (part_arg, 'part')]:
        if val is None:
            continue
        s = str(val).strip()
        if not s or s.lower() in ('null', 'none', 'all', '', 'undefined'):
            continue

        # 1. GP with Ward compound: e.g. gp:IRAS_ward:1, gp:BADAGAV SARVAR_ward:7
        m_gp_w = re.match(r'^gp:(.+?)[_\- ]+ward[:\-_ ]*(\d+)$', s, flags=re.IGNORECASE)
        if m_gp_w:
            clean_gp = m_gp_w.group(1).strip()
            clean_ward = int(m_gp_w.group(2))
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        # 2. GP alone: e.g. gp:IRAS, gp:BADAGAV SARVAR
        m_gp = re.match(r'^gp:(.+)$', s, flags=re.IGNORECASE)
        if m_gp:
            clean_gp = m_gp.group(1).strip()
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        # 3. Compound multi-part with ward: parts:14,15_ward:1
        m_parts_w = re.match(r'^parts[:\-_ ]*([0-9, ]+)[\-_ ]*ward[:\-_ ]*(\d+)$', s, flags=re.IGNORECASE)
        if m_parts_w:
            parts_str = m_parts_w.group(1)
            parsed_parts = [int(p.strip()) for p in parts_str.split(',') if p.strip().isdigit()]
            if parsed_parts:
                clean_parts = parsed_parts
            clean_ward = int(m_parts_w.group(2))
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        # 4. Compound single part with ward: part:9_ward:1 or 9_ward:1
        m_compound = re.match(r'^(?:(?:part|booth)[:\-_ ]*)?(\d+)[\-_ ]*ward[:\-_ ]*(\d+)$', s, flags=re.IGNORECASE)
        if m_compound:
            clean_part = int(m_compound.group(1))
            clean_ward = int(m_compound.group(2))
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        # 5. Multi-ward: wards:5,6,7 or wards:5, 6, 7
        m_wards = re.match(r'^wards[:\-_ ]*([0-9, ]+)$', s, flags=re.IGNORECASE)
        if m_wards:
            wards_str = m_wards.group(1)
            parsed_wards = [int(w.strip()) for w in wards_str.split(',') if w.strip().isdigit()]
            if parsed_wards:
                clean_wards = parsed_wards
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        # 6. Multi-part: parts:9,10
        m_parts = re.match(r'^parts[:\-_ ]*([0-9, ]+)$', s, flags=re.IGNORECASE)
        if m_parts:
            parts_str = m_parts.group(1)
            parsed_parts = [int(p.strip()) for p in parts_str.split(',') if p.strip().isdigit()]
            if parsed_parts:
                clean_parts = parsed_parts
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        # 7. Raw comma-separated numbers
        if ',' in s and all(p.strip().isdigit() for p in s.split(',') if p.strip()):
            nums = [int(p.strip()) for p in s.split(',') if p.strip().isdigit()]
            if default_type == 'ward':
                clean_wards = nums
            else:
                clean_parts = nums
            return clean_ward, clean_part, clean_parts, clean_gp, clean_wards

        m_ward = re.match(r'^ward[:\-_ ]*(\d+)$', s, flags=re.IGNORECASE)
        m_part = re.match(r'^(?:part|booth)[:\-_ ]*(\d+)$', s, flags=re.IGNORECASE)
        if m_ward:
            clean_ward = int(m_ward.group(1))
        elif m_part:
            clean_part = int(m_part.group(1))
        elif s.isdigit():
            if default_type == 'ward':
                clean_ward = int(s)
            else:
                clean_part = int(s)

    return clean_ward, clean_part, clean_parts, clean_gp, clean_wards



# ===========================================================================
# CAMPAIGN-SPECIFIC COMMUNITY BADGES & SYNONYM CANONICALIZATION
# ===========================================================================

COMMUNITY_SYNONYMS: Dict[str, str] = {
    'jat': 'जाट', 'jaat': 'जाट', 'जाट': 'जाट',
    'bishnoi': 'बिश्नोई', 'vishnoi': 'बिश्नोई', 'बिश्नोई': 'बिश्नोई',
    'dewasi': 'देवासी', 'rabari': 'देवासी', 'raika': 'देवासी', 'maldhari': 'देवासी', 'देवासी': 'देवासी', 'रबारी': 'देवासी',
    'choudhary': 'चौधरी', 'chaudhary': 'चौधरी', 'चौधरी': 'चौधरी',
    'patel': 'पटेल', 'kalbi': 'पटेल', 'anjana': 'पटेल', 'sau': 'पटेल', 'पटेल': 'पटेल', 'कलबी': 'पटेल',
    'rajput': 'राजपूत', 'rathore': 'राजपूत', 'chauhan': 'राजपूत', 'bhati': 'राजपूत', 'solanki': 'राजपूत',
    'parmar': 'राजपूत', 'shekhawat': 'राजपूत', 'राजपूत': 'राजपूत', 'राठौड़': 'राजपूत', 'चौहान': 'राजपूत', 'भाटी': 'राजपूत', 'परमार': 'राजपूत',
    'purohit': 'पुरोहित', 'rajpurohit': 'पुरोहित', 'पुरोहित': 'पुरोहित',
    'brahmin': 'ब्राह्मण', 'sharma': 'ब्राह्मण', 'dave': 'ब्राह्मण', 'joshi': 'ब्राह्मण', 'tiwari': 'ब्राह्मण', 'pandit': 'ब्राह्मण', 'ब्राह्मण': 'ब्राह्मण', 'शर्मा': 'ब्राह्मण',
    'meghwal': 'मेघवाल', 'bunkar': 'मेघवाल', 'garg': 'मेघवाल', 'nayak': 'मेघवाल', 'मेघवाल': 'मेघवाल', 'बुनकर': 'मेघवाल', 'भाम्भी': 'भाम्भी',
    'prajapat': 'प्रजापत', 'kumhar': 'कुम्हार', 'प्रजापत': 'प्रजापत', 'कुम्हार': 'कुम्हार',
    'suthar': 'सुथार', 'jangid': 'सुथार', 'khati': 'खाती', 'lohar': 'लोहार', 'सुथार': 'सुथार', 'जांगिड़': 'सुथार', 'खाती': 'खाती',
    'jain': 'जैन', 'oswal': 'जैन', 'shah': 'जैन', 'mehta': 'जैन', 'lodha': 'जैन', 'जैन': 'जैन', 'ओसवाल': 'जैन',
    'muslim': 'मुस्लिम', 'khan': 'मुस्लिम', 'ali': 'मुस्लिम', 'qureshi': 'मुस्लिम', 'sheikh': 'मुस्लिम', 'मुस्लिम': 'मुस्लिम', 'खान': 'मुस्लिम',
    'bheel': 'भील', 'bhil': 'भील', 'garasiya': 'गरासिया', 'भील': 'भील', 'गरासिया': 'गरासिया',
    'meena': 'मीणा', 'mina': 'मीणा', 'मीणा': 'मीणा',
    'gurjar': 'गुर्जर', 'gujjar': 'गुर्जर', 'गुर्जर': 'गुर्जर',
    'mali': 'माली', 'saini': 'माली', 'माली': 'माली',
    'bairwa': 'बैरवा', 'बैरवा': 'बैरवा',
    'daroga': 'दरोगा', 'दरोगा': 'दरोगा',
    'dhakad': 'धाकड़', 'dhakar': 'धाकड़', 'धाकड़': 'धाकड़',
    'rawat': 'रावत', 'रावत': 'रावत',
    'khatik': 'खटीक', 'खटीक': 'खटीक',
    'panara': 'पनारा', 'पनारा': 'पनारा'
}

COMMUNITY_LABELS: Dict[str, str] = {
    'जाट': 'जाट (Jat)',
    'भील': 'भील (Bheel)',
    'गुर्जर': 'गुर्जर (Gurjar)',
    'कुम्हार': 'कुम्हार (Kumhar)',
    'बैरवा': 'बैरवा (Bairwa)',
    'भाम्भी': 'भाम्भी (Bhambhi)',
    'राजपूत': 'राजपूत (Rajput)',
    'दरोगा': 'दरोगा (Daroga)',
    'माली': 'माली (Mali)',
    'खाती': 'खाती (Khati/Jangid)',
    'खटीक': 'खटीक (Khatik)',
    'पनारा': 'पनारा (Panara)',
    'ब्राह्मण': 'ब्राह्मण (Brahmin)',
    'मुस्लिम': 'मुस्लिम (Muslim)',
    'जैन': 'जैन (Jain)',
    'बिश्नोई': 'बिश्नोई (Bishnoi)',
    'देवासी': 'देवासी (Dewasi/Rabari)',
    'चौधरी': 'चौधरी (Choudhary)',
    'पटेल': 'पटेल (Patel)',
    'पुरोहित': 'पुरोहित (Purohit)',
    'मेघवाल': 'मेघवाल (Meghwal)',
    'सुथार': 'सुथार (Suthar)',
    'प्रजापत': 'प्रजापत (Prajapat)',
    'मीणा': 'मीणा (Meena)',
    'गरासिया': 'गरासिया (Garasiya)',
    'धाकड़': 'धाकड़ (Dhakad)',
    'रावत': 'रावत (Rawat)',
    'लोहार': 'लोहार (Lohar)'
}

DEFAULT_COMMUNITY_CHIPS: List[Dict[str, str]] = [
    {'tag': 'जाट', 'label': 'जाट (Jat)'},
    {'tag': 'भील', 'label': 'भील (Bheel)'},
    {'tag': 'गुर्जर', 'label': 'गुर्जर (Gurjar)'},
    {'tag': 'कुम्हार', 'label': 'कुम्हार (Kumhar)'},
    {'tag': 'बैरवा', 'label': 'बैरवा (Bairwa)'},
    {'tag': 'राजपूत', 'label': 'राजपूत (Rajput)'},
    {'tag': 'माली', 'label': 'माली (Mali)'},
    {'tag': 'ब्राह्मण', 'label': 'ब्राह्मण (Brahmin)'},
    {'tag': 'मुस्लिम', 'label': 'मुस्लिम (Muslim)'},
    {'tag': 'जैन', 'label': 'जैन (Jain)'}
]


def get_cached_community_tags(repo: Any, tenant_slug: str, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Returns campaign-specific community tags auto-derived with 5-minute TTL memoization.
    Sources in order:
    1. Explicit campaign.json / tenants.config 'community_tags' if specified.
    2. Previously assigned caste_tag values in the campaign's voter table (canonicalized and deduped).
    3. Auto-discovered communities from voter roll surnames matched against surname synonyms.
    4. Fallback default chips if completely empty.
    """
    if cfg and isinstance(cfg, dict) and cfg.get('community_tags'):
        explicit = cfg['community_tags']
        formatted: List[Dict[str, str]] = []
        for item in explicit:
            if isinstance(item, dict) and item.get('tag'):
                t = str(item['tag']).strip()
                formatted.append({
                    'tag': t,
                    'label': str(item.get('label') or COMMUNITY_LABELS.get(t, t)).strip()
                })
            elif isinstance(item, str) and item.strip():
                clean_tag = item.strip()
                formatted.append({
                    'tag': clean_tag,
                    'label': COMMUNITY_LABELS.get(clean_tag, clean_tag)
                })
        if formatted:
            return formatted

    now = time.time()
    cached = _COMMUNITY_TAGS_CACHE.get(tenant_slug)
    if cached and (now - cached[0] < _COMMUNITY_TAGS_TTL):
        return cached[1]

    tags: List[Dict[str, str]] = []
    seen: set = set()

    if repo and tenant_slug:
        try:
            tid = repo._resolve_tenant(tenant_slug)[0] if hasattr(repo, '_resolve_tenant') else 1
            tagged_rows = []
            with repo.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT caste_tag, count(*) AS cnt
                        FROM voters
                        WHERE tenant_id = %s AND caste_tag IS NOT NULL AND length(trim(caste_tag)) > 0
                        GROUP BY caste_tag
                        ORDER BY cnt DESC
                        LIMIT 15;
                    """, (tid,))
                    tagged_rows = cur.fetchall()

            for row in tagged_rows:
                raw_tag = str(row[0] or '').strip()
                if not raw_tag:
                    continue
                canon = COMMUNITY_SYNONYMS.get(raw_tag.lower(), raw_tag)
                if canon not in seen:
                    seen.add(canon)
                    tags.append({
                        'tag': canon,
                        'label': COMMUNITY_LABELS.get(canon, canon)
                    })

            # Cold-start inference from voter names ONLY if zero tags found in database
            if not tags:
                name_rows = []
                with repo.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT name_en, name_hi, rel_name_en, rel_name_hi
                            FROM voters
                            WHERE tenant_id = %s
                            LIMIT 1000;
                        """, (tid,))
                        name_rows = cur.fetchall()

                inferred_counts: Dict[str, int] = {}
                for r in name_rows:
                    for text in r:
                        if not text:
                            continue
                        tokens = re.findall(r'[\w\u0900-\u097F]+', str(text).lower())
                        for tok in tokens:
                            if tok in COMMUNITY_SYNONYMS:
                                canon = COMMUNITY_SYNONYMS[tok]
                                inferred_counts[canon] = inferred_counts.get(canon, 0) + 1

                for canon, score in sorted(inferred_counts.items(), key=lambda x: -x[1]):
                    if canon not in seen and score >= 2:
                        seen.add(canon)
                        tags.append({
                            'tag': canon,
                            'label': COMMUNITY_LABELS.get(canon, canon)
                        })
                        if len(tags) >= 8:
                            break
        except Exception:
            pass

    # Strictly campaign-specific: do NOT pad or fallback to DEFAULT_COMMUNITY_CHIPS
    _COMMUNITY_TAGS_CACHE[tenant_slug] = (now, tags)
    return tags


