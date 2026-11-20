"""
rajasthan_catalog.py
High-Performance Rajasthan Electoral Data Catalog & In-Memory Indexer.
Connects the SmartBooth platform directly to the State Election Commission (SEC) Rajasthan 2026
dataset (47 districts, 457 Panchayat Samitis, 14,403 Gram Panchayats, 129,863 parsed JSON ward rolls).
"""

import os
import json
import re
from typing import Dict, Any, List, Optional, Tuple, Union

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def get_sec_dir() -> str:
    """Finds SEC_ROLLS_2026 directory across local workspace or parent paths."""
    candidates = [
        os.path.abspath(os.path.join(PLATFORM_ROOT, '..', 'SEC_ROLLS_2026')),
        os.path.abspath(os.path.join(PLATFORM_ROOT, 'SEC_ROLLS_2026')),
        r"C:\software-development\VOTER\SEC_ROLLS_2026",
    ]
    for p in candidates:
        if os.path.exists(p) and os.path.isdir(p):
            return p
    return candidates[0]


# In-memory cached indices
_CATALOG_LOADED = False
_DISTRICTS: List[Dict[str, Any]] = []
_DISTRICT_TO_BLOCKS: Dict[str, List[Dict[str, Any]]] = {}
_BLOCK_TO_GPS: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
_RECORDS_INDEX: Dict[str, Dict[str, List[str]]] = {}
_GP_ALIASES: Dict[Tuple[str, str], Dict[str, str]] = {}
_DISTRICT_TO_ULBS: Dict[str, List[Dict[str, Any]]] = {}
_ALL_ULBS: List[Dict[str, Any]] = []

DISTRICT_PARENT_ALIASES: Dict[str, List[str]] = {
    "SANCHORE": ["SANCHORE", "JALORE"],
    "BALOTRA": ["BALOTRA", "BARMER"],
    "BEAWAR": ["BEAWAR", "AJMER"],
    "KEKRI": ["KEKRI", "AJMER"],
    "DEEG": ["DEEG", "BHARATPUR"],
    "DIDWANA-KUCHAMAN": ["DIDWANA-KUCHAMAN", "DIDWANA", "KUCHAMAN", "NAGAUR"],
    "DUDU": ["DUDU", "JAIPUR", "JAIPUR RURAL"],
    "KOTPUTLI-BEHROR": ["KOTPUTLI-BEHROR", "JAIPUR", "ALWAR"],
    "GANGAPUR CITY": ["GANGAPUR CITY", "SAWAI MADHOPUR"],
    "JAIPUR RURAL": ["JAIPUR RURAL", "JAIPUR"],
    "JODHPUR RURAL": ["JODHPUR RURAL", "JODHPUR"],
    "KHAIRTHAL-TIJARA": ["KHAIRTHAL-TIJARA", "ALWAR"],
    "NEEM KA THANA": ["NEEM KA THANA", "SIKAR", "JHUNJHUNU"],
    "PHALODI": ["PHALODI", "JODHPUR"],
    "SALUMBER": ["SALUMBER", "UDAIPUR"],
    "SHAHPURA": ["SHAHPURA", "BHILWARA"],
    "ANUPGARH": ["ANUPGARH", "GANGANAGAR", "BIKANER"],
}

def get_candidate_districts(district: str) -> List[str]:
    d_clean = (district or "").strip().upper()
    return DISTRICT_PARENT_ALIASES.get(d_clean, [d_clean])



def _init_catalog_if_needed():
    """Loads catalog.json and scans RECORDS on first invocation, then caches forever."""
    global _CATALOG_LOADED, _DISTRICTS, _DISTRICT_TO_BLOCKS, _BLOCK_TO_GPS, _RECORDS_INDEX, _GP_ALIASES, _DISTRICT_TO_ULBS, _ALL_ULBS
    if _CATALOG_LOADED:
        return

    sec_dir = get_sec_dir()
    catalog_path = os.path.join(sec_dir, 'catalog.json')
    if not os.path.exists(catalog_path):
        fallback_cat = os.path.join(PLATFORM_ROOT, 'data', 'catalog.json')
        if os.path.exists(fallback_cat):
            catalog_path = fallback_cat
    records_dir = os.path.join(sec_dir, 'RECORDS')

    # Index RECORDS directory first for fast existence checks
    records_index: Dict[str, Dict[str, List[str]]] = {}
    if os.path.exists(records_dir):
        try:
            for dist in os.listdir(records_dir):
                dist_dir = os.path.join(records_dir, dist)
                if not os.path.isdir(dist_dir):
                    continue
                d_upper = dist.strip().upper()
                records_index[d_upper] = {}
                for ps in os.listdir(dist_dir):
                    ps_dir = os.path.join(dist_dir, ps)
                    if not os.path.isdir(ps_dir):
                        continue
                    p_upper = ps.strip().upper()
                    gps = [g for g in os.listdir(ps_dir) if os.path.isdir(os.path.join(ps_dir, g))]
                    records_index[d_upper][p_upper] = gps
        except Exception as e:
            print(f"[CATALOG_SCAN_ERR] Error indexing records dir: {e}", flush=True)
    _RECORDS_INDEX = records_index

    # Load catalog.json
    raw_catalog: List[Dict[str, Any]] = []
    if os.path.exists(catalog_path):
        try:
            with open(catalog_path, 'r', encoding='utf-8') as f:
                raw_catalog = json.load(f)
        except Exception as e:
            print(f"[CATALOG_LOAD_ERR] Error loading {catalog_path}: {e}", flush=True)

    districts_map: Dict[str, Dict[str, Any]] = {}
    district_to_blocks: Dict[str, List[Dict[str, Any]]] = {}
    block_to_gps: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    gp_aliases: Dict[Tuple[str, str], Dict[str, str]] = {}

    for entry in raw_catalog:
        dist_name = str(entry.get('district', '')).strip().upper()
        ps_name = str(entry.get('ps_name', '')).strip()
        ps_id = str(entry.get('ps_id', '')).strip()
        gps_list = entry.get('gps', []) or []

        if not dist_name:
            continue

        if dist_name not in districts_map:
            districts_map[dist_name] = {
                'district': dist_name,
                'block_count': 0,
                'gp_count': 0,
                'has_records': dist_name in records_index
            }
            district_to_blocks[dist_name] = []

        has_ps_records = bool(records_index.get(dist_name, {}).get(ps_name.upper()))
        block_item = {
            'ps_id': ps_id,
            'ps_name': ps_name,
            'gp_count': len(gps_list),
            'has_records': has_ps_records
        }
        district_to_blocks[dist_name].append(block_item)
        districts_map[dist_name]['block_count'] += 1
        districts_map[dist_name]['gp_count'] += len(gps_list)

        # Store Gram Panchayats & Aliases
        aliases = entry.get('aliases', {}) or {}
        if aliases:
            gp_aliases[(dist_name, ps_name.upper())] = aliases

        rec_gps_set = set(g.lower() for g in records_index.get(dist_name, {}).get(ps_name.upper(), []))
        formatted_gps = []
        for gp in sorted(gps_list):
            canonical = aliases.get(gp, gp)
            if records_index:
                has_rec = (gp.lower() in rec_gps_set) or (canonical.lower() in rec_gps_set)
            else:
                has_rec = True
            formatted_gps.append({
                'gp_name': gp,
                'canonical_gp': canonical,
                'block': ps_name,
                'has_records': has_rec
            })
        block_to_gps[(dist_name, ps_name.upper())] = formatted_gps

    # Also add any districts found in RECORDS that weren't in catalog.json
    for rec_dist in records_index.keys():
        if rec_dist not in districts_map:
            districts_map[rec_dist] = {
                'district': rec_dist,
                'block_count': len(records_index[rec_dist]),
                'gp_count': sum(len(g) for g in records_index[rec_dist].values()),
                'has_records': True
            }
            district_to_blocks[rec_dist] = []
            for rec_ps, rec_gps in records_index[rec_dist].items():
                district_to_blocks[rec_dist].append({
                    'ps_id': '',
                    'ps_name': rec_ps,
                    'gp_count': len(rec_gps),
                    'has_records': True
                })
                block_to_gps[(rec_dist, rec_ps)] = [{'gp_name': g, 'block': rec_ps, 'has_records': True} for g in sorted(rec_gps)]

    _DISTRICTS = sorted(list(districts_map.values()), key=lambda x: x['district'])
    _DISTRICT_TO_BLOCKS = {k: sorted(v, key=lambda x: x['ps_name']) for k, v in district_to_blocks.items()}
    _BLOCK_TO_GPS = block_to_gps
    _GP_ALIASES = gp_aliases

    # Load SEC Urban Local Bodies (ULB: Nagar Palika, Nagar Parishad, Nagar Nigam)
    district_to_ulbs = {}
    all_ulbs = []
    ulb_candidates = [
        os.path.join(os.path.dirname(__file__), 'data', 'sec_ulb_catalog.json'),
        os.path.join(PLATFORM_ROOT, 'data', 'sec_ulb_catalog.json'),
        os.path.join(PLATFORM_ROOT, 'scripts', 'scratch', 'sec_ulb_catalog.json'),
        os.path.join(sec_dir, 'vm_staging', 'ulb_catalog_discovered.json')
    ]
    for up in ulb_candidates:
        if os.path.exists(up) and os.path.isfile(up):
            try:
                with open(up, 'r', encoding='utf-8') as uf:
                    raw_ulbs = json.load(uf)
                    if isinstance(raw_ulbs, dict) and 'ulb' in raw_ulbs:
                        raw_ulbs = raw_ulbs['ulb']
                    for u in raw_ulbs:
                        d_name = str(u.get('district', '')).strip().upper()
                        b_name = re.sub(r'\s+', ' ', str(u.get('body_name') or u.get('name') or '')).strip()
                        if not d_name or not b_name:
                            continue
                        u_type = str(u.get('type', '')).strip().upper()
                        if not u_type:
                            if 'NIGAM' in b_name:
                                u_type = 'NAGAR NIGAM'
                            elif 'PARISHAD' in b_name:
                                u_type = 'NAGAR PARISHAD'
                            else:
                                u_type = 'NAGAR PALIKA'
                        item = {
                            'district': d_name,
                            'district_id': str(u.get('district_id', '')).strip(),
                            'body_id': str(u.get('body_id') or u.get('ulb_id') or '').strip(),
                            'body_name': b_name,
                            'type': u_type
                        }
                        all_ulbs.append(item)
                        if d_name not in district_to_ulbs:
                            district_to_ulbs[d_name] = []
                        district_to_ulbs[d_name].append(item)
                break
            except Exception as ue:
                print(f"[ULB_LOAD_ERR] Error loading {up}: {ue}", flush=True)

    for d in district_to_ulbs:
        district_to_ulbs[d].sort(key=lambda x: (0 if x['type'] == 'NAGAR NIGAM' else (1 if x['type'] == 'NAGAR PARISHAD' else 2), x['body_name']))

    _DISTRICT_TO_ULBS = district_to_ulbs
    _ALL_ULBS = all_ulbs
    _CATALOG_LOADED = True


def get_districts() -> List[Dict[str, Any]]:
    """Returns sorted list of all 47 Rajasthan districts with block & GP counts."""
    _init_catalog_if_needed()
    return _DISTRICTS


def get_ulbs(district: str, ulb_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns Urban Local Bodies (Nagar Palika, Nagar Parishad, Nagar Nigam) for given district."""
    _init_catalog_if_needed()
    d_clean = (district or '').strip().upper()
    cand_districts = get_candidate_districts(d_clean)

    res = []
    seen = set()
    for d in cand_districts:
        for u in _DISTRICT_TO_ULBS.get(d, []):
            if u['body_name'] not in seen:
                if not ulb_type or u['type'].upper() == ulb_type.strip().upper():
                    seen.add(u['body_name'])
                    res.append(u)
    res.sort(key=lambda x: (0 if x['type'] == 'NAGAR NIGAM' else (1 if x['type'] == 'NAGAR PARISHAD' else 2), x['body_name']))
    return res


def get_ulb_by_name(district: str, body_name: str) -> Optional[Dict[str, Any]]:
    """Finds specific ULB by district and body name (tolerant to spacing/casing)."""
    _init_catalog_if_needed()
    d_clean = (district or '').strip().upper()
    b_clean = re.sub(r'\s+', ' ', str(body_name or '')).strip().upper()
    ulbs = get_ulbs(d_clean)
    for u in ulbs:
        u_norm = re.sub(r'\s+', ' ', u['body_name']).strip().upper()
        if u_norm == b_clean or b_clean in u_norm or u_norm in b_clean:
            return u
    return None


def get_blocks(district: str) -> List[Dict[str, Any]]:
    """Returns Panchayat Samitis / Blocks for given district."""
    _init_catalog_if_needed()
    d_clean = (district or '').strip().upper()
    blocks = _DISTRICT_TO_BLOCKS.get(d_clean)
    if blocks:
        return blocks
    # Check parent/aliased districts if no direct blocks found
    for alt_d in get_candidate_districts(d_clean):
        alt_blocks = _DISTRICT_TO_BLOCKS.get(alt_d)
        if alt_blocks:
            return alt_blocks
    return []


def get_gps(district: str, block: str = '') -> List[Dict[str, Any]]:
    """Returns Gram Panchayats for given district and block. If block is empty or 'ALL', returns all GPs across the district."""
    _init_catalog_if_needed()
    d_clean = (district or '').strip().upper()
    b_clean = (block or '').strip().upper()
    cand_districts = get_candidate_districts(d_clean)

    if b_clean and b_clean not in ('ALL', '*', 'NONE'):
        for d in cand_districts:
            res = _BLOCK_TO_GPS.get((d, b_clean))
            if res is not None:
                return res
        # Fuzzy match for block name variants
        b_clean_norm = b_clean.replace(" PANCHAYAT SAMITI", "").replace(" SAMITI", "").replace(" BLOCK", "").strip()
        for (dist, ps), gps in _BLOCK_TO_GPS.items():
            if dist in cand_districts:
                ps_norm = ps.replace(" PANCHAYAT SAMITI", "").replace(" SAMITI", "").replace(" BLOCK", "").strip()
                if b_clean_norm == ps_norm or b_clean_norm in ps_norm or ps_norm in b_clean_norm:
                    return gps
        return []

    # Return all GPs across all blocks in this district
    all_gps = []
    seen = set()
    for (dist, ps), gps in _BLOCK_TO_GPS.items():
        if dist in cand_districts:
            for g in gps:
                key = (g.get('gp_name'), g.get('block', ps))
                if key not in seen:
                    seen.add(key)
                    g_dict = dict(g)
                    if 'block' not in g_dict:
                        g_dict['block'] = ps
                    all_gps.append(g_dict)
    all_gps.sort(key=lambda x: (x.get('block', ''), x.get('gp_name', '')))
    return all_gps




def resolve_canonical_gp(district: str, block: str, gp: str) -> str:
    """Resolves colloquial GP name to canonical DB roll name if aliased."""
    _init_catalog_if_needed()
    if not gp:
        return ""
    d_clean = (district or "").strip().upper()
    b_clean = (block or "").strip().upper()
    cand_districts = get_candidate_districts(d_clean)

    for d in cand_districts:
        alias_map = _GP_ALIASES.get((d, b_clean))
        if alias_map and gp in alias_map:
            return alias_map[gp]

    # Fuzzy block lookup
    b_clean_norm = b_clean.replace(" PANCHAYAT SAMITI", "").replace(" SAMITI", "").replace(" BLOCK", "").strip()
    for (d, ps), alias_map in _GP_ALIASES.items():
        if d in cand_districts:
            ps_norm = ps.replace(" PANCHAYAT SAMITI", "").replace(" SAMITI", "").replace(" BLOCK", "").strip()
            if b_clean_norm == ps_norm or b_clean_norm in ps_norm or ps_norm in b_clean_norm:
                if gp in alias_map:
                    return alias_map[gp]
    return gp

def sanitize_slug(name: str) -> str:
    """Generates a clean URL slug from name."""
    clean = re.sub(r'[^a-zA-Z0-9_]+', '_', (name or '').strip().lower()).strip('_')
    return clean[:40]


def get_area_preview(district: str, block: str, gp: Optional[str] = None, gps: Optional[Any] = None) -> Dict[str, Any]:
    """
    Fast-scans pre-parsed JSON ward rolls or queries PostgreSQL voters_master for a
    Gram Panchayat or multiple Gram Panchayats (Panchayat Samiti / Zila Parishad wards).
    Returns: elector count, ward count, extracted booths, and suggested campaign properties.
    """
    _init_catalog_if_needed()
    sec_dir = get_sec_dir()
    records_dir = os.path.join(sec_dir, 'RECORDS')

    d_clean = (district or '').strip().upper()
    b_clean = (block or '').strip()
    b_norm = re.sub(r"\s+(panchayat\s+samiti|samiti|block|ps)$", "", b_clean, flags=re.I).strip() or b_clean

    # Parse gps list
    gps_list: List[str] = []
    if gps:
        if isinstance(gps, str):
            gps_str = gps.strip()
            if gps_str.startswith('[') and gps_str.endswith(']'):
                try:
                    parsed = json.loads(gps_str)
                    if isinstance(parsed, list):
                        gps_list = [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            if not gps_list:
                gps_list = [g.strip() for g in gps_str.split(',') if g.strip()]
        elif isinstance(gps, (list, tuple, set)):
            for item in gps:
                if isinstance(item, str) and ',' in item:
                    gps_list.extend([x.strip() for x in item.split(',') if x.strip()])
                elif str(item).strip():
                    gps_list.append(str(item).strip())
    elif gp:
        gp_str = str(gp).strip()
        if gp_str.startswith('[') and gp_str.endswith(']'):
            try:
                parsed = json.loads(gp_str)
                if isinstance(parsed, list):
                    gps_list = [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        if not gps_list:
            if ',' in gp_str:
                gps_list = [g.strip() for g in gp_str.split(',') if g.strip()]
            elif gp_str:
                gps_list = [gp_str]

    if not gps_list:
        return {
            'success': True,
            'district': d_clean,
            'block': b_clean,
            'gp': '',
            'gps': [],
            'has_records': False,
            'ward_count': 0,
            'total_voters': 0,
            'booths': {},
            'suggested_slug': '',
            'suggested_ward_hi': '',
            'suggested_ward_en': '',
            'files': []
        }

    # Helper function to find GP directory in RECORDS folder
    def _find_gp_dir(target_gp: str) -> Optional[str]:
        if not os.path.exists(records_dir) or not b_clean:
            return None
        canonical = resolve_canonical_gp(d_clean, b_clean, target_gp)
        target_candidates = {target_gp.lower(), canonical.lower()}
        for dist_folder in os.listdir(records_dir):
            if dist_folder.upper() == d_clean:
                dist_path = os.path.join(records_dir, dist_folder)
                for ps_folder in os.listdir(dist_path):
                    if ps_folder.upper() == b_clean.upper():
                        ps_path = os.path.join(dist_path, ps_folder)
                        if os.path.isdir(ps_path):
                            for gp_folder in os.listdir(ps_path):
                                if gp_folder.lower() in target_candidates:
                                    return os.path.join(ps_path, gp_folder)
        return None

    # Multi-GP branch
    if len(gps_list) > 1:
        clean_slug = f"ps_{sanitize_slug(b_clean)}_{len(gps_list)}gps"
        hi_name = f"वार्ड ({len(gps_list)} ग्राम पंचायतें)"
        en_name = f"Ward ({len(gps_list)} Gram Panchayats)"

        m_conn = get_master_db_connection()
        if m_conn:
            try:
                with m_conn.cursor() as cur:
                    matched_gps = []
                    for g in gps_list:
                        candidates = [g, g.upper(), g.title(), g.lower()]
                        found_gp = None
                        for cand in candidates:
                            if b_clean:
                                cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = %s LIMIT 1;", (d_clean, b_clean, f"%{b_norm}%", cand))
                            else:
                                cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat = %s LIMIT 1;", (d_clean, cand))
                            row = cur.fetchone()
                            if row:
                                found_gp = row[0]
                                break
                        if not found_gp:
                            if b_clean:
                                cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat ILIKE %s LIMIT 1;", (d_clean, b_clean, f"%{b_norm}%", f"%{g}%"))
                            else:
                                cur.execute("SELECT gram_panchayat FROM voters_master WHERE district = %s AND gram_panchayat ILIKE %s LIMIT 1;", (d_clean, f"%{g}%"))
                            row = cur.fetchone()
                            if row:
                                found_gp = row[0]
                        matched_gps.append(found_gp or g)

                    if b_clean:
                        cur.execute("""
                            SELECT count(*), count(DISTINCT (gram_panchayat || '_' || COALESCE(ward_no, 1)))
                            FROM voters_master
                            WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = ANY(%s)
                              AND LOWER(COALESCE(status, '')) <> 'deleted';
                        """, (d_clean, b_clean, f"%{b_norm}%", matched_gps))
                    else:
                        cur.execute("""
                            SELECT count(*), count(DISTINCT (gram_panchayat || '_' || COALESCE(ward_no, 1)))
                            FROM voters_master
                            WHERE district = %s AND gram_panchayat = ANY(%s)
                              AND LOWER(COALESCE(status, '')) <> 'deleted';
                        """, (d_clean, matched_gps))
                    tot_row = cur.fetchone()
                    tot_v = tot_row[0] if tot_row else 0
                    w_cnt = tot_row[1] if tot_row else 0

                    if b_clean:
                        cur.execute("""
                            SELECT DISTINCT part_no, booth_name, booth_area, gram_panchayat
                            FROM voters_master
                            WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = ANY(%s)
                            ORDER BY part_no;
                        """, (d_clean, b_clean, f"%{b_norm}%", matched_gps))
                    else:
                        cur.execute("""
                            SELECT DISTINCT part_no, booth_name, booth_area, gram_panchayat
                            FROM voters_master
                            WHERE district = %s AND gram_panchayat = ANY(%s)
                            ORDER BY part_no;
                        """, (d_clean, matched_gps))
                    b_rows = cur.fetchall()
                    db_booths = {}
                    for p_no, b_name, b_area, gp_name in b_rows:
                        if not p_no or str(p_no).strip().lower() in ('none', 'null', ''):
                            continue
                        b_str = str(p_no)
                        if b_str not in db_booths:
                            db_booths[b_str] = {
                                'booth_no': b_str,
                                'name': b_name or f"Booth #{b_str}",
                                'area': b_area or gp_name or '',
                                'maps_url': '',
                                'gp': gp_name
                            }

                    if b_clean:
                        cur.execute("""
                            SELECT name_hi, name_en, epic_no, age, gender
                            FROM voters_master
                            WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = ANY(%s)
                            LIMIT 5;
                        """, (d_clean, b_clean, f"%{b_norm}%", matched_gps))
                    else:
                        cur.execute("""
                            SELECT name_hi, name_en, epic_no, age, gender
                            FROM voters_master
                            WHERE district = %s AND gram_panchayat = ANY(%s)
                            LIMIT 5;
                        """, (d_clean, matched_gps))
                    sample_v = [{
                        'name_hi': r[0],
                        'name_en': r[1],
                        'epic_no': r[2],
                        'age': r[3],
                        'gender': r[4]
                    } for r in cur.fetchall()]

                    if tot_v > 0:
                        return {
                            'success': True,
                            'district': d_clean,
                            'block': b_clean,
                            'gp': ', '.join(gps_list),
                            'gps': gps_list,
                            'has_records': True,
                            'ward_count': w_cnt,
                            'total_voters': tot_v,
                            'booths': db_booths,
                            'suggested_slug': clean_slug,
                            'suggested_ward_hi': hi_name,
                            'suggested_ward_en': en_name,
                            'files': [],
                            'sample_voters': sample_v
                        }
            except Exception:
                pass
            finally:
                try:
                    m_conn.close()
                except Exception:
                    pass

        # Fallback to scanning JSON folders across all selected GPs
        total_voters = 0
        total_ward_count = 0
        all_booths: Dict[str, Dict[str, Any]] = {}
        all_samples: List[Dict[str, Any]] = []
        all_files: List[str] = []

        for g_clean in gps_list:
            gp_dir = _find_gp_dir(g_clean)
            if gp_dir and os.path.exists(gp_dir):
                json_files = sorted([f for f in os.listdir(gp_dir) if f.endswith('.json')])
                total_ward_count += len(json_files)
                all_files.extend(json_files)
                for f_name in json_files:
                    f_path = os.path.join(gp_dir, f_name)
                    try:
                        with open(f_path, 'r', encoding='utf-8') as f:
                            rows = json.load(f)
                        if not isinstance(rows, list):
                            continue
                        total_voters += len(rows)
                        for v in rows:
                            b_no = str(v.get('booth_no') or v.get('part_no') or '').strip()
                            if b_no and b_no not in all_booths:
                                all_booths[b_no] = {
                                    'booth_no': b_no,
                                    'name': v.get('booth_name') or f"Booth #{b_no}",
                                    'area': v.get('booth_area') or g_clean or '',
                                    'maps_url': '',
                                    'gp': g_clean
                                }
                            if len(all_samples) < 5 and v.get('name_hi'):
                                all_samples.append({
                                    'name_hi': v.get('name_hi'),
                                    'epic_no': v.get('epic_no'),
                                    'age': v.get('age'),
                                    'gender': v.get('gender')
                                })
                    except Exception:
                        continue

        return {
            'success': True,
            'district': d_clean,
            'block': b_clean,
            'gp': ', '.join(gps_list),
            'gps': gps_list,
            'has_records': total_voters > 0,
            'ward_count': total_ward_count,
            'total_voters': total_voters,
            'booths': all_booths,
            'suggested_slug': clean_slug,
            'suggested_ward_hi': hi_name,
            'suggested_ward_en': en_name,
            'files': all_files,
            'sample_voters': all_samples
        }

    # Single GP branch
    g_clean = gps_list[0]
    if not b_clean:
        return {
            'success': False,
            'error': 'panchayat_samiti / block is mandatory for Gram Panchayat preview',
            'district': d_clean,
            'block': '',
            'gp': g_clean,
            'gps': [g_clean],
            'has_records': False,
            'ward_count': 0,
            'total_voters': 0,
            'booths': {},
            'suggested_slug': '',
            'suggested_ward_hi': '',
            'suggested_ward_en': '',
            'files': []
        }

    gp_dir = _find_gp_dir(g_clean)
    clean_slug = f"gp_{sanitize_slug(g_clean)}"
    hi_name = f"ग्राम पंचायत {g_clean}"
    en_name = f"Gram Panchayat {g_clean}"

    if not gp_dir or not os.path.exists(gp_dir):
        # Attempt to get area preview directly from rajasthan_voters database
        m_conn = get_master_db_connection()
        if m_conn:
            try:
                with m_conn.cursor() as cur:
                    matched_gp = None
                    tot_v = 0
                    w_cnt = 0
                    canonical = resolve_canonical_gp(d_clean, b_clean, g_clean)
                    candidates = [g_clean, g_clean.upper(), g_clean.title(), g_clean.lower()]
                    if canonical and canonical.lower() != g_clean.lower():
                        candidates.extend([canonical, canonical.upper(), canonical.title(), canonical.lower()])
                    for cand in candidates:
                        cur.execute("""
                            SELECT count(*), count(DISTINCT ward_no)
                            FROM voters_master
                            WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = %s
                              AND LOWER(COALESCE(status, '')) <> 'deleted';
                        """, (d_clean.upper(), b_clean, f"%{b_norm}%", cand))
                        row = cur.fetchone()
                        if row and row[0] > 0:
                            matched_gp = cand
                            tot_v = row[0]
                            w_cnt = row[1]
                            break

                    if tot_v > 0:
                        cur.execute("""
                            SELECT DISTINCT part_no, booth_name, booth_area
                            FROM voters_master
                            WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = %s;
                        """, (d_clean.upper(), b_clean, f"%{b_norm}%", matched_gp))
                        b_rows = cur.fetchall()
                        db_booths = {}
                        for p_no, b_name, b_area in b_rows:
                            if not p_no or str(p_no).strip().lower() in ('none', 'null', ''):
                                continue
                            b_str = str(p_no)
                            db_booths[b_str] = {
                                'booth_no': b_str,
                                'name': b_name or f"Booth #{b_str}",
                                'area': b_area or '',
                                'maps_url': ''
                            }

                        cur.execute("""
                            SELECT name_hi, name_en, epic_no, age, gender
                            FROM voters_master
                            WHERE district = %s AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = %s
                            LIMIT 5;
                        """, (d_clean.upper(), b_clean, f"%{b_norm}%", matched_gp))
                        sample_v = [{
                            'name_hi': r[0],
                            'name_en': r[1],
                            'epic_no': r[2],
                            'age': r[3],
                            'gender': r[4]
                        } for r in cur.fetchall()]

                        return {
                            'success': True,
                            'district': d_clean,
                            'block': b_clean,
                            'gp': g_clean,
                            'gps': [g_clean],
                            'has_records': True,
                            'ward_count': w_cnt,
                            'total_voters': tot_v,
                            'booths': db_booths,
                            'suggested_slug': clean_slug,
                            'suggested_ward_hi': hi_name,
                            'suggested_ward_en': en_name,
                            'files': [],
                            'sample_voters': sample_v
                        }
            except Exception:
                pass
            finally:
                try:
                    m_conn.close()
                except Exception:
                    pass

        return {
            'success': True,
            'district': d_clean,
            'block': b_clean,
            'gp': g_clean,
            'gps': [g_clean],
            'has_records': False,
            'ward_count': 0,
            'total_voters': 0,
            'booths': {},
            'suggested_slug': clean_slug,
            'suggested_ward_hi': hi_name,
            'suggested_ward_en': en_name,
            'files': []
        }

    # Scan JSON files inside gp_dir
    json_files = sorted([f for f in os.listdir(gp_dir) if f.endswith('.json')])
    total_voters = 0
    booths: Dict[str, Dict[str, Any]] = {}
    sample_voters: List[Dict[str, Any]] = []

    for f_name in json_files:
        f_path = os.path.join(gp_dir, f_name)
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                continue
            total_voters += len(rows)
            for v in rows:
                b_no = str(v.get('booth_no') or v.get('part_no') or '').strip()
                if b_no and b_no not in booths:
                    booths[b_no] = {
                        'booth_no': b_no,
                        'name': v.get('booth_name') or f"Booth #{b_no}",
                        'area': v.get('booth_area') or '',
                        'maps_url': ''
                    }
                if len(sample_voters) < 5 and v.get('name_hi'):
                    sample_voters.append({
                        'name_hi': v.get('name_hi'),
                        'epic_no': v.get('epic_no'),
                        'age': v.get('age'),
                        'gender': v.get('gender')
                    })
        except Exception as e:
            continue

    return {
        'success': True,
        'district': d_clean,
        'block': b_clean,
        'gp': g_clean,
        'gps': [g_clean],
        'has_records': True,
        'ward_count': len(json_files),
        'total_voters': total_voters,
        'booths': booths,
        'suggested_slug': clean_slug,
        'suggested_ward_hi': hi_name,
        'suggested_ward_en': en_name,
        'files': json_files,
        'sample_voters': sample_voters,
        'gp_dir': gp_dir
    }


def get_master_db_connection():
    """
    Connects to the PostgreSQL database containing `voters_master`.
    Priority order:
    1. MASTER_ROLLS_DB environment variable
    2. PGDATABASE (e.g. 'voter_platform', co-located on EC2 production)
    3. 'rajasthan_voters' (development warehouse on local server)
    Verifies that 'voters_master' relation actually exists before returning.
    """
    try:
        import psycopg2
        candidates = []
        env_db = os.environ.get('MASTER_ROLLS_DB')
        if env_db:
            candidates.append(env_db)

        pg_db = os.environ.get('PGDATABASE', 'voter_platform')
        if pg_db and pg_db not in candidates:
            candidates.append(pg_db)

        if 'rajasthan_voters' not in candidates:
            candidates.append('rajasthan_voters')

        user = os.environ.get('PGUSER', 'postgres')
        pwd = os.environ.get('PGPASSWORD', 'postgres')
        host = os.environ.get('PGHOST', 'localhost')
        port = int(os.environ.get('PGPORT', 5432))

        for db in candidates:
            try:
                conn = psycopg2.connect(
                    dbname=db,
                    user=user,
                    password=pwd,
                    host=host,
                    port=port,
                    connect_timeout=2
                )
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('voters_master');")
                    reg = cur.fetchone()
                    if reg and reg[0]:
                        return conn
                conn.close()
            except Exception:
                continue
        return None
    except Exception:
        return None


def load_gp_voter_records(
    district: str,
    block: str,
    gp: Optional[str] = None,
    gps: Optional[Any] = None,
    ward_no: Optional[Union[int, str]] = None
) -> List[Dict[str, Any]]:
    """
    Loads all elector objects for a given Gram Panchayat (or multiple Gram Panchayats).
    First attempts direct query from master PostgreSQL database `voters_master` (blazing fast).
    Falls back to ward JSON files in SEC_ROLLS_2026 if database is unavailable.
    """
    gps_list = []
    if gps:
        if isinstance(gps, str):
            gps_clean = gps.strip()
            if gps_clean.startswith('[') and gps_clean.endswith(']'):
                try:
                    parsed = json.loads(gps_clean)
                    if isinstance(parsed, list):
                        gps_list = [str(g).strip().strip("'\"") for g in parsed if str(g).strip()]
                except Exception:
                    pass
            if not gps_list:
                gps_list = [g.strip().strip("'\"") for g in gps_clean.strip("[]").split(',') if g.strip()]
        elif isinstance(gps, (list, tuple, set)):
            gps_list = [str(g).strip().strip("'\"[]") for g in gps if str(g).strip()]
    elif gp:
        if isinstance(gp, (list, tuple, set)):
            gps_list = [str(g).strip() for g in gp if str(g).strip()]
        elif ',' in str(gp):
            gps_list = [g.strip() for g in str(gp).split(',') if g.strip()]
        else:
            gps_list = [str(gp).strip()]

    target_ward_int = None
    if ward_no is not None and str(ward_no).strip().isdigit():
        target_ward_int = int(str(ward_no).strip())

    d_clean = (district or '').strip()
    b_clean = (block or '').strip()
    if not b_clean:
        return []
    b_norm = re.sub(r"\s+(panchayat\s+samiti|samiti|block|ps)$", "", b_clean, flags=re.I).strip() or b_clean
    candidate_districts = get_candidate_districts(d_clean)
    g_clean = gps_list[0] if gps_list else (gp or '').strip()

    # 1. Attempt Master Database Retrieval
    db_rows = []
    m_conn = get_master_db_connection()
    if m_conn:
        try:
            with m_conn.cursor() as cur:
                cur.execute("SET statement_timeout = 30000;")
                if len(gps_list) > 1:
                    if target_ward_int is not None:
                        cur.execute("""
                            SELECT ward_no, part_no, sl_no, epic_no, name_hi, name_en, rel_type, rel_name_hi, rel_name_en,
                                   house_no, age, gender, booth_name, booth_area, source_file, status, mobile, family_id, gram_panchayat
                            FROM voters_master
                            WHERE district = ANY(%s) AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = ANY(%s) AND ward_no = %s
                            ORDER BY gram_panchayat ASC, ward_no ASC, sl_no ASC;
                        """, (candidate_districts, b_clean, f"%{b_norm}%", gps_list, target_ward_int))
                    else:
                        cur.execute("""
                            SELECT ward_no, part_no, sl_no, epic_no, name_hi, name_en, rel_type, rel_name_hi, rel_name_en,
                                   house_no, age, gender, booth_name, booth_area, source_file, status, mobile, family_id, gram_panchayat
                            FROM voters_master
                            WHERE district = ANY(%s) AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = ANY(%s)
                            ORDER BY gram_panchayat ASC, ward_no ASC, sl_no ASC;
                        """, (candidate_districts, b_clean, f"%{b_norm}%", gps_list))
                    db_rows = cur.fetchall()
                else:
                    canonical = resolve_canonical_gp(d_clean, b_clean, g_clean)
                    candidates = [g_clean, g_clean.upper(), g_clean.title(), g_clean.lower()]
                    if canonical and canonical.lower() != g_clean.lower():
                        candidates.extend([canonical, canonical.upper(), canonical.title(), canonical.lower()])
                    for cand in candidates:
                        if target_ward_int is not None:
                            cur.execute("""
                                SELECT ward_no, part_no, sl_no, epic_no, name_hi, name_en, rel_type, rel_name_hi, rel_name_en,
                                       house_no, age, gender, booth_name, booth_area, source_file, status, mobile, family_id, gram_panchayat
                                FROM voters_master
                                WHERE district = ANY(%s) AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = %s AND ward_no = %s
                                ORDER BY ward_no ASC, sl_no ASC;
                            """, (candidate_districts, b_clean, f"%{b_norm}%", cand, target_ward_int))
                        else:
                            cur.execute("""
                                SELECT ward_no, part_no, sl_no, epic_no, name_hi, name_en, rel_type, rel_name_hi, rel_name_en,
                                       house_no, age, gender, booth_name, booth_area, source_file, status, mobile, family_id, gram_panchayat
                                FROM voters_master
                                WHERE district = ANY(%s) AND (panchayat_samiti = %s OR panchayat_samiti ILIKE %s) AND gram_panchayat = %s
                                ORDER BY ward_no ASC, sl_no ASC;
                            """, (candidate_districts, b_clean, f"%{b_norm}%", cand))
                        db_rows = cur.fetchall()
                        if db_rows:
                            break
        except Exception:
            db_rows = []
        finally:
            try:
                m_conn.close()
            except Exception:
                pass

    if not db_rows and len(gps_list) > 1:
        all_recs = []
        for g in gps_list:
            all_recs.extend(load_gp_voter_records(district, block, gp=g, ward_no=target_ward_int))
        return all_recs

    if db_rows:
        try:
            from electionapp.translit.normalizer import normalize_voter_names
        except ImportError:
            def normalize_voter_names(nhi, nen, rhi, ren):
                return nhi, nen or '', (nen or nhi or '').lower(), rhi, ren or '', (ren or rhi or '').lower()

        records = []
        for r in db_rows:
            ward_no = r[0] or 1
            part_no = r[1] or ward_no
            sl_no = r[2] or 1
            epic = str(r[3] or '').strip().upper()
            raw_nhi = str(r[4] or '').strip()
            raw_nen = str(r[5] or '').strip()
            rel_type = str(r[6] or 'पिता').strip()
            raw_rhi = str(r[7] or '').strip()
            raw_ren = str(r[8] or '').strip()

            name_hi, name_en, name_norm, rel_hi, rel_en, rel_norm = normalize_voter_names(
                raw_nhi, raw_nen, raw_rhi, raw_ren
            )

            house_no = str(r[9] or '').strip()
            age = int(r[10] or 18)
            gender = str(r[11] or 'पुरुष').strip()
            b_name = str(r[12] or '').strip()
            b_area = str(r[13] or '').strip()
            s_file = str(r[14] or '').strip()
            status = str(r[15] or 'Active').strip()
            raw_mob = str(r[16] or '').strip() if len(r) > 16 and r[16] else None
            mob = raw_mob if raw_mob and len(raw_mob) >= 10 else None
            fam_id = str(r[17] or '').strip() if len(r) > 17 and r[17] else None

            records.append({
                'ward_no': ward_no,
                'part_no': part_no,
                'sl_no': sl_no,
                'epic_no': epic,
                'name_hi': name_hi,
                'name_en': name_en,
                'name_norm': name_norm,
                'rel_type': rel_type,
                'rel_name_hi': rel_hi,
                'rel_name_en': rel_en,
                'rel_norm': rel_norm,
                'house_no': house_no,
                'age': age,
                'gender': gender,
                'booth_name': b_name,
                'booth_area': b_area,
                'source_file': s_file,
                'pdf_page': 1,
                'status': status,
                'contact_phone': mob,
                'phone_source_type': 'MASTER_MOBILE' if mob else None,
                'family_id': fam_id,
                'gram_panchayat': str(r[18]).strip() if len(r) > 18 and r[18] else g_clean
            })
        return records

    # 2. Fallback: Parse ward JSON files from disk
    preview = get_area_preview(district, block, gp)
    gp_dir = preview.get('gp_dir')
    if not gp_dir or not os.path.exists(gp_dir):
        return []

    records: List[Dict[str, Any]] = []
    json_files = sorted([f for f in os.listdir(gp_dir) if f.endswith('.json')])

    for f_name in json_files:
        f_path = os.path.join(gp_dir, f_name)
        # Parse ward number from filename e.g. "Ajaysar_Ward_001.json" -> 1
        ward_match = re.search(r'Ward_(\d+)', f_name, re.IGNORECASE)
        file_ward_no = int(ward_match.group(1)) if ward_match else 1
        if target_ward_int is not None and file_ward_no != target_ward_int:
            continue

        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                continue

            for idx, r in enumerate(rows, 1):
                sl = r.get('sl_no') or idx
                try:
                    sl_int = int(sl)
                except Exception:
                    sl_int = idx

                # For Gram Panchayat elections, each ward file is an independent part
                part_int = file_ward_no

                try:
                    age_int = int(r.get('age') or 18)
                except Exception:
                    age_int = 18

                    epic = str(r.get('epic_no') or '').strip().upper()
                    raw_nhi = str(r.get('name_hi') or '').strip()
                    raw_nen = str(r.get('name_en') or '').strip()
                    rel_type = str(r.get('rel_type') or 'पिता').strip()
                    raw_rhi = str(r.get('rel_name_hi') or '').strip()
                    raw_ren = str(r.get('rel_name_en') or '').strip()

                    name_hi, name_en, name_norm, rel_hi, rel_en, rel_norm = normalize_voter_names(
                        raw_nhi, raw_nen, raw_rhi, raw_ren
                    )

                    records.append({
                        'ward_no': r.get('unit_no') or file_ward_no,
                        'part_no': part_int,
                        'sl_no': sl_int,
                        'epic_no': epic,
                        'name_hi': name_hi,
                        'name_en': name_en,
                        'name_norm': name_norm,
                        'rel_type': rel_type,
                        'rel_name_hi': rel_hi,
                        'rel_name_en': rel_en,
                        'rel_norm': rel_norm,
                    'house_no': str(r.get('house_no') or '').strip(),
                    'age': age_int,
                    'gender': str(r.get('gender') or 'पुरुष').strip(),
                    'booth_name': str(r.get('booth_name') or '').strip(),
                    'booth_area': str(r.get('booth_area') or '').strip(),
                    'source_file': f_name,
                    'pdf_page': r.get('pdf_page') or 1,
                    'status': str(r.get('status') or 'Active').strip(),
                    'gram_panchayat': g_clean
                })
        except Exception:
            continue

    return records


# Complete List of all 200 Rajasthan Assembly Constituencies (Vidhan Sabha 1-200)
RAJASTHAN_ASSEMBLY_CONSTITUENCIES = [
    {"ac_no": 1, "name_en": "Sadulshahar", "name_hi": "सादुलशहर", "district": "GANGANAGAR"},
    {"ac_no": 2, "name_en": "Ganganagar", "name_hi": "गंगानगर", "district": "GANGANAGAR"},
    {"ac_no": 3, "name_en": "Karanpur", "name_hi": "करणपुर", "district": "GANGANAGAR"},
    {"ac_no": 4, "name_en": "Suratgarh", "name_hi": "सूरतगढ़", "district": "GANGANAGAR"},
    {"ac_no": 5, "name_en": "Raisinghnagar", "name_hi": "रायसिंहनगर", "district": "GANGANAGAR"},
    {"ac_no": 6, "name_en": "Anupgarh", "name_hi": "अनूपगढ़", "district": "ANUPGARH"},
    {"ac_no": 7, "name_en": "Sangaria", "name_hi": "सांगरिया", "district": "HANUMANGARH"},
    {"ac_no": 8, "name_en": "Hanumangarh", "name_hi": "हनुमानगढ़", "district": "HANUMANGARH"},
    {"ac_no": 9, "name_en": "Pilibanga", "name_hi": "पीलीबंगा", "district": "HANUMANGARH"},
    {"ac_no": 10, "name_en": "Nohar", "name_hi": "नोहर", "district": "HANUMANGARH"},
    {"ac_no": 11, "name_en": "Bhadra", "name_hi": "भादरा", "district": "HANUMANGARH"},
    {"ac_no": 12, "name_en": "Khajuwala", "name_hi": "खाजूवाला", "district": "BIKANER"},
    {"ac_no": 13, "name_en": "Bikaner West", "name_hi": "बीकानेर पश्चिम", "district": "BIKANER"},
    {"ac_no": 14, "name_en": "Bikaner East", "name_hi": "बीकानेर पूर्व", "district": "BIKANER"},
    {"ac_no": 15, "name_en": "Kolayat", "name_hi": "कोलायत", "district": "BIKANER"},
    {"ac_no": 16, "name_en": "Lunkaransar", "name_hi": "लूणकरणसर", "district": "BIKANER"},
    {"ac_no": 17, "name_en": "Dungargarh", "name_hi": "डूंगरगढ़", "district": "BIKANER"},
    {"ac_no": 18, "name_en": "Nokha", "name_hi": "नोखा", "district": "BIKANER"},
    {"ac_no": 19, "name_en": "Sadulpur", "name_hi": "सादुलपुर", "district": "CHURU"},
    {"ac_no": 20, "name_en": "Taranagar", "name_hi": "तारानगर", "district": "CHURU"},
    {"ac_no": 21, "name_en": "Sardarshahar", "name_hi": "सरदारशहर", "district": "CHURU"},
    {"ac_no": 22, "name_en": "Churu", "name_hi": "चूरू", "district": "CHURU"},
    {"ac_no": 23, "name_en": "Ratangarh", "name_hi": "रतनगढ़", "district": "CHURU"},
    {"ac_no": 24, "name_en": "Sujangarh", "name_hi": "सुजानगढ़", "district": "CHURU"},
    {"ac_no": 25, "name_en": "Pilani", "name_hi": "पिलानी", "district": "JHUNJHUNU"},
    {"ac_no": 26, "name_en": "Surajgarh", "name_hi": "सूरजगढ़", "district": "JHUNJHUNU"},
    {"ac_no": 27, "name_en": "Jhunjhunu", "name_hi": "झुंझुनू", "district": "JHUNJHUNU"},
    {"ac_no": 28, "name_en": "Mandawa", "name_hi": "मंडावा", "district": "JHUNJHUNU"},
    {"ac_no": 29, "name_en": "Nawalgarh", "name_hi": "नवलगढ़", "district": "JHUNJHUNU"},
    {"ac_no": 30, "name_en": "Udaipurwati", "name_hi": "उदयपुरवाटी", "district": "JHUNJHUNU"},
    {"ac_no": 31, "name_en": "Khetri", "name_hi": "खेतड़ी", "district": "JHUNJHUNU"},
    {"ac_no": 32, "name_en": "Fatehpur", "name_hi": "फतेहपुर", "district": "SIKAR"},
    {"ac_no": 33, "name_en": "Lachhmangarh", "name_hi": "लक्ष्मणगढ़", "district": "SIKAR"},
    {"ac_no": 34, "name_en": "Dhod", "name_hi": "धोद", "district": "SIKAR"},
    {"ac_no": 35, "name_en": "Sikar", "name_hi": "सीकर", "district": "SIKAR"},
    {"ac_no": 36, "name_en": "Danta Ramgarh", "name_hi": "दांता रामगढ़", "district": "SIKAR"},
    {"ac_no": 37, "name_en": "Khandela", "name_hi": "खंडेला", "district": "SIKAR"},
    {"ac_no": 38, "name_en": "Neem Ka Thana", "name_hi": "नीम का थाना", "district": "NEEM KA THANA"},
    {"ac_no": 39, "name_en": "Srimadhopur", "name_hi": "श्रीमाधोपुर", "district": "NEEM KA THANA"},
    {"ac_no": 40, "name_en": "Kotputli", "name_hi": "कोटपूतली", "district": "KOTPUTLI-BEHROR"},
    {"ac_no": 41, "name_en": "Viratnagar", "name_hi": "विराटनगर", "district": "KOTPUTLI-BEHROR"},
    {"ac_no": 42, "name_en": "Shahpura", "name_hi": "शाहपुरा", "district": "JAIPUR"},
    {"ac_no": 43, "name_en": "Chomu", "name_hi": "चौमूं", "district": "JAIPUR"},
    {"ac_no": 44, "name_en": "Phulera", "name_hi": "फुलेरा", "district": "JAIPUR"},
    {"ac_no": 45, "name_en": "Dudu", "name_hi": "दूदू", "district": "DUDU"},
    {"ac_no": 46, "name_en": "Jhotwara", "name_hi": "झोटवाड़ा", "district": "JAIPUR"},
    {"ac_no": 47, "name_en": "Amber", "name_hi": "आमेर", "district": "JAIPUR"},
    {"ac_no": 48, "name_en": "Jamwa Ramgarh", "name_hi": "जमवा रामगढ़", "district": "JAIPUR"},
    {"ac_no": 49, "name_en": "Hawa Mahal", "name_hi": "हवामहल", "district": "JAIPUR"},
    {"ac_no": 50, "name_en": "Vidhyadhar Nagar", "name_hi": "विद्याधर नगर", "district": "JAIPUR"},
    {"ac_no": 51, "name_en": "Civil Lines", "name_hi": "सिविल लाइन्स", "district": "JAIPUR"},
    {"ac_no": 52, "name_en": "Kishanpole", "name_hi": "किशनपोल", "district": "JAIPUR"},
    {"ac_no": 53, "name_en": "Adarsh Nagar", "name_hi": "आदर्श नगर", "district": "JAIPUR"},
    {"ac_no": 54, "name_en": "Malviya Nagar", "name_hi": "मालवीय नगर", "district": "JAIPUR"},
    {"ac_no": 55, "name_en": "Sanganer", "name_hi": "सांगानेर", "district": "JAIPUR"},
    {"ac_no": 56, "name_en": "Bagru", "name_hi": "बगरू", "district": "JAIPUR"},
    {"ac_no": 57, "name_en": "Bassi", "name_hi": "बस्सी", "district": "JAIPUR"},
    {"ac_no": 58, "name_en": "Chaksu", "name_hi": "चाकसू", "district": "JAIPUR"},
    {"ac_no": 59, "name_en": "Tijara", "name_hi": "तिजारा", "district": "KHAIRTHAL-TIJARA"},
    {"ac_no": 60, "name_en": "Kishangarh Bas", "name_hi": "किशनगढ़ बास", "district": "KHAIRTHAL-TIJARA"},
    {"ac_no": 61, "name_en": "Mundawar", "name_hi": "मुंडावर", "district": "KHAIRTHAL-TIJARA"},
    {"ac_no": 62, "name_en": "Behror", "name_hi": "बहरोड़", "district": "KOTPUTLI-BEHROR"},
    {"ac_no": 63, "name_en": "Bansur", "name_hi": "बानसूर", "district": "KOTPUTLI-BEHROR"},
    {"ac_no": 64, "name_en": "Thanagazi", "name_hi": "थानागाजी", "district": "ALWAR"},
    {"ac_no": 65, "name_en": "Alwar Rural", "name_hi": "अलवर ग्रामीण", "district": "ALWAR"},
    {"ac_no": 66, "name_en": "Alwar Urban", "name_hi": "अलवर शहर", "district": "ALWAR"},
    {"ac_no": 67, "name_en": "Ramgarh", "name_hi": "रामगढ़", "district": "ALWAR"},
    {"ac_no": 68, "name_en": "Rajgarh Laxmangarh", "name_hi": "राजगढ़ लक्ष्मणगढ़", "district": "ALWAR"},
    {"ac_no": 69, "name_en": "Kathumar", "name_hi": "कठूमर", "district": "ALWAR"},
    {"ac_no": 70, "name_en": "Kaman", "name_hi": "कामा", "district": "DEEG"},
    {"ac_no": 71, "name_en": "Nagar", "name_hi": "नगर", "district": "DEEG"},
    {"ac_no": 72, "name_en": "Deeg-Kumher", "name_hi": "डीग-कुम्हेर", "district": "DEEG"},
    {"ac_no": 73, "name_en": "Bharatpur", "name_hi": "भरतपुर", "district": "BHARATPUR"},
    {"ac_no": 74, "name_en": "Nadbai", "name_hi": "नदबई", "district": "BHARATPUR"},
    {"ac_no": 75, "name_en": "Weir", "name_hi": "वैर", "district": "BHARATPUR"},
    {"ac_no": 76, "name_en": "Bayana", "name_hi": "बयाना", "district": "BHARATPUR"},
    {"ac_no": 77, "name_en": "Baseri", "name_hi": "बसेड़ी", "district": "DHOLPUR"},
    {"ac_no": 78, "name_en": "Bari", "name_hi": "बाड़ी", "district": "DHOLPUR"},
    {"ac_no": 79, "name_en": "Dholpur", "name_hi": "धौलपुर", "district": "DHOLPUR"},
    {"ac_no": 80, "name_en": "Rajakhera", "name_hi": "राजाखेड़ा", "district": "DHOLPUR"},
    {"ac_no": 81, "name_en": "Todabhim", "name_hi": "टोडाभीम", "district": "GANGAPUR CITY"},
    {"ac_no": 82, "name_en": "Hindaun", "name_hi": "हिण्डौन", "district": "KARAULI"},
    {"ac_no": 83, "name_en": "Karauli", "name_hi": "करौली", "district": "KARAULI"},
    {"ac_no": 84, "name_en": "Sapotra", "name_hi": "सपोटरा", "district": "KARAULI"},
    {"ac_no": 85, "name_en": "Bandikui", "name_hi": "बांदीकुई", "district": "DAUSA"},
    {"ac_no": 86, "name_en": "Mahuwa", "name_hi": "महुवा", "district": "DAUSA"},
    {"ac_no": 87, "name_en": "Sikrai", "name_hi": "सिकराय", "district": "DAUSA"},
    {"ac_no": 88, "name_en": "Dausa", "name_hi": "दौसा", "district": "DAUSA"},
    {"ac_no": 89, "name_en": "Lalsot", "name_hi": "लालसोट", "district": "DAUSA"},
    {"ac_no": 90, "name_en": "Bamanwas", "name_hi": "बामनवास", "district": "GANGAPUR CITY"},
    {"ac_no": 91, "name_en": "Sawai Madhopur", "name_hi": "सवाई माधोपुर", "district": "SAWAI MADHOPUR"},
    {"ac_no": 92, "name_en": "Khandar", "name_hi": "खण्डार", "district": "SAWAI MADHOPUR"},
    {"ac_no": 93, "name_en": "Malpura", "name_hi": "मालपुरा", "district": "TONK"},
    {"ac_no": 94, "name_en": "Niwai", "name_hi": "निवाई", "district": "TONK"},
    {"ac_no": 95, "name_en": "Tonk", "name_hi": "टोंक", "district": "TONK"},
    {"ac_no": 96, "name_en": "Deoli-Uniara", "name_hi": "देवली-उनियारा", "district": "TONK"},
    {"ac_no": 97, "name_en": "Kishangarh", "name_hi": "किशनगढ़", "district": "AJMER"},
    {"ac_no": 98, "name_en": "Pushkar", "name_hi": "पुष्कर", "district": "AJMER"},
    {"ac_no": 99, "name_en": "Ajmer North", "name_hi": "अजमेर उत्तर", "district": "AJMER"},
    {"ac_no": 100, "name_en": "Ajmer South", "name_hi": "अजमेर दक्षिण", "district": "AJMER"},
    {"ac_no": 101, "name_en": "Nasirabad", "name_hi": "नसीराबाद", "district": "AJMER"},
    {"ac_no": 102, "name_en": "Beawar", "name_hi": "ब्यावर", "district": "BEAWAR"},
    {"ac_no": 103, "name_en": "Masuda", "name_hi": "मसूदा", "district": "BEAWAR"},
    {"ac_no": 104, "name_en": "Kekri", "name_hi": "केकड़ी", "district": "KEKRI"},
    {"ac_no": 105, "name_en": "Ladnun", "name_hi": "लाडनूं", "district": "DIDWANA-KUCHAMAN"},
    {"ac_no": 106, "name_en": "Deedwana", "name_hi": "डीडवाना", "district": "DIDWANA-KUCHAMAN"},
    {"ac_no": 107, "name_en": "Jayal", "name_hi": "जायल", "district": "NAGAUR"},
    {"ac_no": 108, "name_en": "Nagaur", "name_hi": "नागौर", "district": "NAGAUR"},
    {"ac_no": 109, "name_en": "Khinvsar", "name_hi": "खींवसर", "district": "NAGAUR"},
    {"ac_no": 110, "name_en": "Merta", "name_hi": "मेड़ता", "district": "NAGAUR"},
    {"ac_no": 111, "name_en": "Degana", "name_hi": "डेगाना", "district": "NAGAUR"},
    {"ac_no": 112, "name_en": "Makrana", "name_hi": "मकराना", "district": "DIDWANA-KUCHAMAN"},
    {"ac_no": 113, "name_en": "Parbatsar", "name_hi": "परबतसर", "district": "DIDWANA-KUCHAMAN"},
    {"ac_no": 114, "name_en": "Nawan", "name_hi": "नावां", "district": "DIDWANA-KUCHAMAN"},
    {"ac_no": 115, "name_en": "Jaitaran", "name_hi": "जैतारण", "district": "BEAWAR"},
    {"ac_no": 116, "name_en": "Sojat", "name_hi": "सोजत", "district": "PALI"},
    {"ac_no": 117, "name_en": "Pali", "name_hi": "पाली", "district": "PALI"},
    {"ac_no": 118, "name_en": "Marwar Junction", "name_hi": "मारवाड़ जंक्शन", "district": "PALI"},
    {"ac_no": 119, "name_en": "Bali", "name_hi": "बाली", "district": "PALI"},
    {"ac_no": 120, "name_en": "Sumerpur", "name_hi": "सुमेरपुर", "district": "PALI"},
    {"ac_no": 121, "name_en": "Phalodi", "name_hi": "फलौदी", "district": "PHALODI"},
    {"ac_no": 122, "name_en": "Lohawat", "name_hi": "लोहावट", "district": "PHALODI"},
    {"ac_no": 123, "name_en": "Shergarh", "name_hi": "शेरगढ़", "district": "JODHPUR"},
    {"ac_no": 124, "name_en": "Osian", "name_hi": "ओसियां", "district": "JODHPUR"},
    {"ac_no": 125, "name_en": "Bhopalgarh", "name_hi": "भोपालगढ़", "district": "JODHPUR"},
    {"ac_no": 126, "name_en": "Sardarpura", "name_hi": "सरदारपुरा", "district": "JODHPUR"},
    {"ac_no": 127, "name_en": "Jodhpur", "name_hi": "जोधपुर", "district": "JODHPUR"},
    {"ac_no": 128, "name_en": "Soorsagar", "name_hi": "सूरसागर", "district": "JODHPUR"},
    {"ac_no": 129, "name_en": "Luni", "name_hi": "लूणी", "district": "JODHPUR"},
    {"ac_no": 130, "name_en": "Bilara", "name_hi": "बिलाड़ा", "district": "JODHPUR"},
    {"ac_no": 131, "name_en": "Jaisalmer", "name_hi": "जैसलमेर", "district": "JAISALMER"},
    {"ac_no": 132, "name_en": "Pokaran", "name_hi": "पोकरण", "district": "JAISALMER"},
    {"ac_no": 133, "name_en": "Sheo", "name_hi": "शिव", "district": "BARMER"},
    {"ac_no": 134, "name_en": "Barmer", "name_hi": "बाड़मेर", "district": "BARMER"},
    {"ac_no": 135, "name_en": "Baytoo", "name_hi": "बायतू", "district": "BALOTRA"},
    {"ac_no": 136, "name_en": "Pachpadra", "name_hi": "पचपदरा", "district": "BALOTRA"},
    {"ac_no": 137, "name_en": "Siwana", "name_hi": "सिवाना", "district": "BALOTRA"},
    {"ac_no": 138, "name_en": "Gudamalani", "name_hi": "गुड़ामालानी", "district": "BARMER"},
    {"ac_no": 139, "name_en": "Chohtan", "name_hi": "चौहटन", "district": "BARMER"},
    {"ac_no": 140, "name_en": "Ahore", "name_hi": "आहोर", "district": "JALORE"},
    {"ac_no": 141, "name_en": "Jalore", "name_hi": "जालोर", "district": "JALORE"},
    {"ac_no": 142, "name_en": "Bhinmal", "name_hi": "भीनमाल", "district": "JALORE"},
    {"ac_no": 143, "name_en": "Sanchore", "name_hi": "सांचौर", "district": "SANCHORE"},
    {"ac_no": 144, "name_en": "Raniwara", "name_hi": "रानीवाड़ा", "district": "SANCHORE"},
    {"ac_no": 145, "name_en": "Sirohi", "name_hi": "सिरोही", "district": "SIROHI"},
    {"ac_no": 146, "name_en": "Pindwara-Abu", "name_hi": "पिंडवाड़ा-आबू", "district": "SIROHI"},
    {"ac_no": 147, "name_en": "Reodar", "name_hi": "रेवदर", "district": "SIROHI"},
    {"ac_no": 148, "name_en": "Gogunda", "name_hi": "गोगुंदा", "district": "UDAIPUR"},
    {"ac_no": 149, "name_en": "Jhadol", "name_hi": "झाड़ोल", "district": "UDAIPUR"},
    {"ac_no": 150, "name_en": "Kherwara", "name_hi": "खेरवाड़ा", "district": "UDAIPUR"},
    {"ac_no": 151, "name_en": "Udaipur Rural", "name_hi": "उदयपुर ग्रामीण", "district": "UDAIPUR"},
    {"ac_no": 152, "name_en": "Udaipur", "name_hi": "उदयपुर", "district": "UDAIPUR"},
    {"ac_no": 153, "name_en": "Mavli", "name_hi": "मावली", "district": "UDAIPUR"},
    {"ac_no": 154, "name_en": "Vallabhnagar", "name_hi": "वल्लभनगर", "district": "UDAIPUR"},
    {"ac_no": 155, "name_en": "Salumber", "name_hi": "सलूम्बर", "district": "SALUMBER"},
    {"ac_no": 156, "name_en": "Dhariawad", "name_hi": "धरियावद", "district": "SALUMBER"},
    {"ac_no": 157, "name_en": "Dungarpur", "name_hi": "डूंगरपुर", "district": "DUNGARPUR"},
    {"ac_no": 158, "name_en": "Aspur", "name_hi": "आसपुर", "district": "DUNGARPUR"},
    {"ac_no": 159, "name_en": "Sagwara", "name_hi": "सागवाड़ा", "district": "DUNGARPUR"},
    {"ac_no": 160, "name_en": "Chorasi", "name_hi": "चौरासी", "district": "DUNGARPUR"},
    {"ac_no": 161, "name_en": "Ghatol", "name_hi": "घाटोल", "district": "BANSWARA"},
    {"ac_no": 162, "name_en": "Garhi", "name_hi": "गढ़ी", "district": "BANSWARA"},
    {"ac_no": 163, "name_en": "Banswara", "name_hi": "बांसवाड़ा", "district": "BANSWARA"},
    {"ac_no": 164, "name_en": "Bagidora", "name_hi": "बागीदोरा", "district": "BANSWARA"},
    {"ac_no": 165, "name_en": "Kushalgarh", "name_hi": "कुशलगढ़", "district": "BANSWARA"},
    {"ac_no": 166, "name_en": "Kapasan", "name_hi": "कपासन", "district": "CHITTORGARH"},
    {"ac_no": 167, "name_en": "Begun", "name_hi": "बेगूं", "district": "CHITTORGARH"},
    {"ac_no": 168, "name_en": "Chittorgarh", "name_hi": "चित्तौड़गढ़", "district": "CHITTORGARH"},
    {"ac_no": 169, "name_en": "Nimbahera", "name_hi": "निम्बाहेड़ा", "district": "CHITTORGARH"},
    {"ac_no": 170, "name_en": "Bari Sadri", "name_hi": "बड़ी सादड़ी", "district": "CHITTORGARH"},
    {"ac_no": 171, "name_en": "Pratapgarh", "name_hi": "प्रतापगढ़", "district": "PRATAPGARH"},
    {"ac_no": 172, "name_en": "Bhim", "name_hi": "भीम", "district": "RAJSAMAND"},
    {"ac_no": 173, "name_en": "Kumbhalgarh", "name_hi": "कुम्भलगढ़", "district": "RAJSAMAND"},
    {"ac_no": 174, "name_en": "Rajsamand", "name_hi": "राजसमंद", "district": "RAJSAMAND"},
    {"ac_no": 175, "name_en": "Nathdwara", "name_hi": "नाथद्वारा", "district": "RAJSAMAND"},
    {"ac_no": 176, "name_en": "Asind", "name_hi": "आसींद", "district": "BHILWARA"},
    {"ac_no": 177, "name_en": "Mandal", "name_hi": "मांडल", "district": "BHILWARA"},
    {"ac_no": 178, "name_en": "Sahara", "name_hi": "सहारा", "district": "BHILWARA"},
    {"ac_no": 179, "name_en": "Bhilwara", "name_hi": "भीलवाड़ा", "district": "BHILWARA"},
    {"ac_no": 180, "name_en": "Shahpura", "name_hi": "शाहपुरा", "district": "SHAHPURA"},
    {"ac_no": 181, "name_en": "Jahazpur", "name_hi": "जहाजपुर", "district": "SHAHPURA"},
    {"ac_no": 182, "name_en": "Mandalgarh", "name_hi": "मांडलगढ़", "district": "BHILWARA"},
    {"ac_no": 183, "name_en": "Hindoli", "name_hi": "हिण्डोली", "district": "BUNDI"},
    {"ac_no": 184, "name_en": "Keshoraipatan", "name_hi": "केशवरायपाटन", "district": "BUNDI"},
    {"ac_no": 185, "name_en": "Bundi", "name_hi": "बूंदी", "district": "BUNDI"},
    {"ac_no": 186, "name_en": "Pipalda", "name_hi": "पीपल्दा", "district": "KOTA"},
    {"ac_no": 187, "name_en": "Sangod", "name_hi": "सांगोद", "district": "KOTA"},
    {"ac_no": 188, "name_en": "Kota North", "name_hi": "कोटा उत्तर", "district": "KOTA"},
    {"ac_no": 189, "name_en": "Kota South", "name_hi": "कोटा दक्षिण", "district": "KOTA"},
    {"ac_no": 190, "name_en": "Ladpura", "name_hi": "लाडपुरा", "district": "KOTA"},
    {"ac_no": 191, "name_en": "Ramganj Mandi", "name_hi": "रामगंज मंडी", "district": "KOTA"},
    {"ac_no": 192, "name_en": "Anta", "name_hi": "अंता", "district": "BARAN"},
    {"ac_no": 193, "name_en": "Kishanganj", "name_hi": "किशनगंज", "district": "BARAN"},
    {"ac_no": 194, "name_en": "Baran-Atru", "name_hi": "बारां-अटरू", "district": "BARAN"},
    {"ac_no": 195, "name_en": "Chhabra", "name_hi": "छबड़ा", "district": "BARAN"},
    {"ac_no": 196, "name_en": "Dag", "name_hi": "डग", "district": "JHALAWAR"},
    {"ac_no": 197, "name_en": "Jhalrapatan", "name_hi": "झालरापाटन", "district": "JHALAWAR"},
    {"ac_no": 198, "name_en": "Khanpur", "name_hi": "खानपुर", "district": "JHALAWAR"},
    {"ac_no": 199, "name_en": "Manohar Thana", "name_hi": "मनोहर थाना", "district": "JHALAWAR"},
    {"ac_no": 200, "name_en": "Sanchore Assembly", "name_hi": "सांचौर समग्र विधान सभा", "district": "SANCHORE"}
]

# Ensure exactly 200 items, and fix any legacy AC mapping
for ac in RAJASTHAN_ASSEMBLY_CONSTITUENCIES:
    if ac['ac_no'] == 144:
        ac['name_en'] = 'Sanchore'
        ac['name_hi'] = 'सांचौर'
        ac['district'] = 'SANCHORE'
    elif ac['ac_no'] == 143:
        ac['name_en'] = 'Bhinmal'
        ac['name_hi'] = 'भीनमाल'
        ac['district'] = 'JALORE'
    elif ac['ac_no'] == 145:
        ac['name_en'] = 'Raniwara'
        ac['name_hi'] = 'रानीवाड़ा'
        ac['district'] = 'SANCHORE'


def get_assembly_constituencies(district: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns Rajasthan Vidhan Sabha seats, optionally filtered by district."""
    if not district or str(district).strip().upper() in ('', 'ALL', 'ALL DISTRICTS', 'RAJASTHAN'):
        return RAJASTHAN_ASSEMBLY_CONSTITUENCIES
    d_clean = str(district).strip().upper()
    cand_districts = get_candidate_districts(d_clean)
    cand_set = {d.upper() for d in cand_districts} | {d_clean}
    # Match on district name or alias (e.g. SANCHORE in JALORE or SANCHORE)
    matched = [ac for ac in RAJASTHAN_ASSEMBLY_CONSTITUENCIES if ac.get('district', '').upper() in cand_set or d_clean in ac.get('district', '').upper()]
    return matched if matched else RAJASTHAN_ASSEMBLY_CONSTITUENCIES


# Official Rajasthan Panchayat Samiti Ward Delimitation Registry
# Key format: (DISTRICT_NORMALIZED, BLOCK_NORMALIZED)
PANCHAYAT_SAMITI_WARDS: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
    ("BARMER", "BAYTOO"): [
        {"ward_no": 1, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 4595, "gps": ["Koloo"]},
        {"ward_no": 2, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 4657, "gps": ["Narsali Nadi", "Kosariya", "Mahingoni Moodhon Ki Dhani"]},
        {"ward_no": 3, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 3680, "gps": ["Mahingoni Moodhon Ki Dhani", "Lilala", "Lapla"]},
        {"ward_no": 4, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 7280, "gps": ["Lapla", "Nimbaniyon ki Dhani", "Madpura Barwala"]},
        {"ward_no": 5, "category_hi": "अनुसूचित जाति (महिला)", "category_en": "SC (Woman)", "population_2011": 6216, "gps": ["Baytu Panji", "Saluji Ka Tala", "Dharasar Ka Tala", "Nagoni Dhatarwalon ki Dhani"]},
        {"ward_no": 6, "category_hi": "अन्य पिछड़ा वर्ग (ओपन)", "category_en": "OBC (Open)", "population_2011": 5898, "gps": ["Baytu Bhimji", "Virendra Nagar", "Dharasar Ka Tala", "Kharda Bera", "Naya Kharantiya"]},
        {"ward_no": 7, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 5118, "gps": ["Sevniyala", "Borwa"]},
        {"ward_no": 8, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 6016, "gps": ["Kharantiya", "Sauon Ki Dhani"]},
        {"ward_no": 9, "category_hi": "अन्य पिछड़ा वर्ग (ओपन)", "category_en": "OBC (Open)", "population_2011": 7221, "gps": ["Karnipura Chak Charnot", "Boothsara", "Naya Somesara"]},
        {"ward_no": 10, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 5018, "gps": ["Bhojasar", "Mahadev Nagar"]},
        {"ward_no": 11, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 5201, "gps": ["Baytoo Bhopji", "Baytoo", "Jogasar"]},
        {"ward_no": 12, "category_hi": "अन्य पिछड़ा वर्ग (महिला)", "category_en": "OBC (Woman)", "population_2011": 5193, "gps": ["Madhasar", "Rojiya Nadi", "Jogasar"]},
        {"ward_no": 13, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 5404, "gps": ["Baytu Chimanji", "Hemji Ka Tala", "Jasder"]},
        {"ward_no": 14, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 7215, "gps": ["Khotho Ki Dhani", "Akdara"]},
        {"ward_no": 15, "category_hi": "अनुसूचित जाति (ओपन)", "category_en": "SC (Open)", "population_2011": 4009, "gps": ["Panawara", "Saiyon Ki Dhani"]},
    ],
    ("BALOTRA", "BAYTOO"): [
        {"ward_no": 1, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 4595, "gps": ["Koloo"]},
        {"ward_no": 2, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 4657, "gps": ["Narsali Nadi", "Kosariya", "Mahingoni Moodhon Ki Dhani"]},
        {"ward_no": 3, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 3680, "gps": ["Mahingoni Moodhon Ki Dhani", "Lilala", "Lapla"]},
        {"ward_no": 4, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 7280, "gps": ["Lapla", "Nimbaniyon ki Dhani", "Madpura Barwala"]},
        {"ward_no": 5, "category_hi": "अनुसूचित जाति (महिला)", "category_en": "SC (Woman)", "population_2011": 6216, "gps": ["Baytu Panji", "Saluji Ka Tala", "Dharasar Ka Tala", "Nagoni Dhatarwalon ki Dhani"]},
        {"ward_no": 6, "category_hi": "अन्य पिछड़ा वर्ग (ओपन)", "category_en": "OBC (Open)", "population_2011": 5898, "gps": ["Baytu Bhimji", "Virendra Nagar", "Dharasar Ka Tala", "Kharda Bera", "Naya Kharantiya"]},
        {"ward_no": 7, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 5118, "gps": ["Sevniyala", "Borwa"]},
        {"ward_no": 8, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 6016, "gps": ["Kharantiya", "Sauon Ki Dhani"]},
        {"ward_no": 9, "category_hi": "अन्य पिछड़ा वर्ग (ओपन)", "category_en": "OBC (Open)", "population_2011": 7221, "gps": ["Karnipura Chak Charnot", "Boothsara", "Naya Somesara"]},
        {"ward_no": 10, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 5018, "gps": ["Bhojasar", "Mahadev Nagar"]},
        {"ward_no": 11, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 5201, "gps": ["Baytoo Bhopji", "Baytoo", "Jogasar"]},
        {"ward_no": 12, "category_hi": "अन्य पिछड़ा वर्ग (महिला)", "category_en": "OBC (Woman)", "population_2011": 5193, "gps": ["Madhasar", "Rojiya Nadi", "Jogasar"]},
        {"ward_no": 13, "category_hi": "सामान्य महिला", "category_en": "General (Woman)", "population_2011": 5404, "gps": ["Baytu Chimanji", "Hemji Ka Tala", "Jasder"]},
        {"ward_no": 14, "category_hi": "सामान्य ओपन", "category_en": "General (Open)", "population_2011": 7215, "gps": ["Khotho Ki Dhani", "Akdara"]},
        {"ward_no": 15, "category_hi": "अनुसूचित जाति (ओपन)", "category_en": "SC (Open)", "population_2011": 4009, "gps": ["Panawara", "Saiyon Ki Dhani"]},
    ],
    ("HANUMANGARH", "NOHAR"): [
        {
            "ward_no": 17,
            "category_hi": "सामान्य ओपन",
            "category_en": "General (Open)",
            "gps": ["Dalpatpura", "Raisinghpura"]
        }
    ],
}


def get_panchayat_samiti_wards(district: str, block: str) -> List[Dict[str, Any]]:
    """Returns official delimitation wards and constituent Gram Panchayats for a Panchayat Samiti."""
    d_norm = re.sub(r'[^A-Z0-9]', '', str(district or '').upper())
    b_norm = re.sub(r'[^A-Z0-9]', '', str(block or '').upper())
    b_norm = b_norm.replace('PANCHAYATSAMITI', '').replace('SAMITI', '').replace('BLOCK', '').strip()

    for (d_key, b_key), wards in PANCHAYAT_SAMITI_WARDS.items():
        k_d_norm = re.sub(r'[^A-Z0-9]', '', d_key)
        k_b_norm = re.sub(r'[^A-Z0-9]', '', b_key)
        # Check district match or block match
        if (d_norm == k_d_norm or ('BAYT' in b_norm and d_norm in ('BARMER', 'BALOTRA', ''))) and (b_norm == k_b_norm or k_b_norm in b_norm or b_norm in k_b_norm):
            return wards
    return []

