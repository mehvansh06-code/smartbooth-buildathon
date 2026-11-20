"""
slip.py
Multi-Tenant Voter Slip & Family Lookup Blueprint:
- /api/slip_text           — Generates WhatsApp voter slip text (single & family/combined)
- /api/family_smart        — Smart family & household discovery with scoring
- /api/voters_by_serials   — Quick lookup by roll serial numbers (supports ranges e.g. 12-18)
- /api/part_voters         — List all voters for a specific polling station part
- /api/slip_card_info      — Page count and contact phones for poster card generation
"""

import re
from typing import Optional, List, Dict, Any, Tuple
import psycopg2.extras
from flask import Blueprint, jsonify, request, current_app, g, render_template

from electionapp.config import load_tenant_config, is_panchayat_campaign, get_voting_method

slip_bp = Blueprint('slip', __name__)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

from electionapp.tenancy import resolve_tenant

def _get_tenant():
    """Resolve active tenant slug and repo from authoritative tenancy resolver."""
    return resolve_tenant()


def extract_surname(name: Optional[str]) -> str:
    if not name:
        return ""
    parts = re.findall(r'[a-zA-Z]+', name)
    if len(parts) >= 2:
        return parts[-1].lower()
    return ""


from electionapp.translit.normalizer import clean_devnagari_name, clean_english_name

def format_bilingual(name_en: Optional[str], name_hi: Optional[str]) -> str:
    hi = clean_devnagari_name(name_hi)
    en = clean_english_name(name_en, fallback_hi=hi)
    if en and hi and en.lower() != hi.lower():
        return f"{en} / {hi}"
    return en or hi


def clean_house(h: Optional[str]) -> str:
    if not h:
        return ""
    h = str(h).strip().lower()
    h = re.sub(r'\s*([/\-_,])\s*', r'\1', h)
    h = re.sub(r'\b0+(\d+)', r'\1', h)
    h = re.sub(r'\s+', ' ', h).strip()
    return h


def is_exact_house_match(target: Optional[str], candidate: Optional[str]) -> bool:
    t_clean = clean_house(target)
    c_clean = clean_house(candidate)
    if not t_clean or not c_clean:
        return False
    if t_clean == c_clean:
        return True
    t_alnum = re.sub(r'[^a-z0-9\u0900-\u097F]', '', t_clean)
    c_alnum = re.sub(r'[^a-z0-9\u0900-\u097F]', '', c_clean)
    if t_alnum and t_alnum == c_alnum:
        return True
    return False


STRUCK_OFF_ERROR = (
    "यह मतदाता मतदाता सूची से विलोपित है — इनके लिए पर्ची जारी नहीं की जा सकती। "
    "(This elector has been struck off the roll; no slip can be issued.)"
)


def is_struck_off(voter: Optional[Dict[str, Any]]) -> bool:
    """
    True if the elector's name has been struck off the roll (विलोपन).

    Struck-off electors stay searchable so the team can answer "why is my name
    gone?", but a slip would send them to a booth that will turn them away — so
    slip generation is refused server-side, not just hidden in the UI.
    """
    if not voter:
        return False
    return str(voter.get('status') or '').strip().lower() == 'deleted'


def parse_serial_numbers(text: Any) -> List[int]:
    """Extract list of positive integer serial numbers from text or ranges like 24-28."""
    if not text:
        return []
    if isinstance(text, list):
        out = []
        for item in text:
            out.extend(parse_serial_numbers(str(item)))
        return sorted(list(set(out)))

    text_str = str(text).strip()
    cleaned = re.sub(r'[,;\t\r\n]+', ' ', text_str)
    tokens = cleaned.split()
    serials = []
    for token in tokens:
        token = token.strip()
        range_match = re.match(r'^(\d+)\s*[-.]{1,2}\s*(\d+)$', token)
        if range_match:
            start_val = int(range_match.group(1))
            end_val = int(range_match.group(2))
            if start_val <= end_val and (end_val - start_val) <= 500:
                serials.extend(range(start_val, end_val + 1))
        elif token.isdigit():
            serials.append(int(token))
    return sorted(list(set(serials)))


# ---------------------------------------------------------------------------
# GET / POST /api/voters_by_serials
# ---------------------------------------------------------------------------

@slip_bp.route('/api/voters_by_serials', methods=['GET', 'POST'])
def api_voters_by_serials():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    if request.method == 'POST':
        data = request.get_json(silent=True) or request.form or {}
        raw_input = data.get('serials', '') or data.get('text', '') or data.get('raw_input', '') or data.get('q', '')
        part_no_raw = data.get('part_no') or data.get('part')
        ward_no_raw = data.get('ward_no') or data.get('ward')
        gender = str(data.get('gender', '')).strip()
        age_min = data.get('age_min')
        age_max = data.get('age_max')
    else:
        raw_input = request.args.get('serials', '') or request.args.get('text', '') or request.args.get('q', '')
        part_no_raw = request.args.get('part_no') or request.args.get('part')
        ward_no_raw = request.args.get('ward_no') or request.args.get('ward')
        gender = str(request.args.get('gender', '')).strip()
        age_min = request.args.get('age_min')
        age_max = request.args.get('age_max')

    part_no = None
    ward_no = None
    part_nos = None

    for raw_val in (part_no_raw, ward_no_raw):
        if not raw_val:
            continue
        s = str(raw_val).strip()
        m_compound = re.match(r'^(?:part|booth)[:\-_ ]*(\d+)[\-_ ]*ward[:\-_ ]*(\d+)$', s, flags=re.IGNORECASE)
        if m_compound:
            part_no = int(m_compound.group(1))
            ward_no = int(m_compound.group(2))
            break
        m_parts = re.match(r'^parts[:\-_ ]*([0-9, ]+)$', s, flags=re.IGNORECASE)
        if m_parts:
            parts_str = m_parts.group(1)
            parsed_parts = [int(p.strip()) for p in parts_str.split(',') if p.strip().isdigit()]
            if parsed_parts:
                part_nos = parsed_parts
            break

    if part_no is None and part_nos is None and part_no_raw:
        s_part = str(part_no_raw).strip()
        m_p = re.match(r'^(?:part|booth)[:\-_ ]*(\d+)$', s_part, flags=re.IGNORECASE)
        if m_p:
            part_no = int(m_p.group(1))
        elif s_part.isdigit():
            part_no = int(s_part)

    if ward_no is None and ward_no_raw:
        s_ward = str(ward_no_raw).strip()
        m_w = re.match(r'^ward[:\-_ ]*(\d+)$', s_ward, flags=re.IGNORECASE)
        if m_w:
            ward_no = int(m_w.group(1))
        elif s_ward.isdigit():
            ward_no = int(s_ward)

    serials = parse_serial_numbers(raw_input)
    if not serials:
        return jsonify({
            'success': False,
            'voters': [],
            'found_count': 0,
            'missing_serials': [],
            'message': 'No valid serial numbers entered'
        })

    from electionapp.search.two_stage_search import resolve_gender_variants
    gender_list = resolve_gender_variants(gender)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            sql = """
                SELECT * FROM voters
                WHERE tenant_id = %s AND sl_no = ANY(%s)
                  AND LOWER(COALESCE(status, '')) <> 'deleted'
            """
            params = [tid, serials]
            if part_no and str(part_no).isdigit():
                sql += " AND part_no = %s"
                params.append(int(part_no))
            elif part_nos:
                sql += " AND part_no = ANY(%s)"
                params.append(part_nos)
            if ward_no and str(ward_no).isdigit():
                sql += " AND ward_no = %s"
                params.append(int(ward_no))
            if gender_list:
                sql += " AND gender = ANY(%s)"
                params.append(gender_list)
            if age_min and str(age_min).isdigit():
                sql += " AND age >= %s"
                params.append(int(age_min))
            if age_max and str(age_max).isdigit():
                sql += " AND age <= %s"
                params.append(int(age_max))
            sql += " ORDER BY COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC"

            cur.execute(sql, params)
            rows = [repo._normalize_row(dict(r), tid) for r in cur.fetchall()]

    found_sls = set(r['sl_no'] for r in rows)
    missing = [s for s in serials if s not in found_sls]

    return jsonify({
        'success': True,
        'voters': rows,
        'found_count': len(rows),
        'searched_count': len(serials),
        'serials': serials,
        'missing_serials': missing,
        'message': f"Found {len(rows)} of {len(serials)} serial numbers"
    })


# ---------------------------------------------------------------------------
# GET /api/part_voters
# ---------------------------------------------------------------------------

@slip_bp.route('/api/part_voters')
def api_part_voters():
    tenant, repo = _get_tenant()
    part = request.args.get('part', type=int)
    if not part:
        return jsonify({'voters': [], 'total': 0})

    voters = repo.get_voters_by_part(tenant, part, limit=10000, offset=0)
    booths_dict = repo.get_booths_dict(tenant)
    booth = booths_dict.get(str(part)) or booths_dict.get(part, {})

    return jsonify({
        'part_no': part,
        'booth': booth,
        'voters': voters,
        'total': len(voters)
    })


# ---------------------------------------------------------------------------
# GET /api/family_smart
# ---------------------------------------------------------------------------
# Helper: Discover Family & Extended Household
# ---------------------------------------------------------------------------

def is_valid_family_id(f_id: Any) -> bool:
    """True if family_id is a non-empty, genuine identifier and not a placeholder or corruption."""
    if not f_id:
        return False
    s = str(f_id).strip().lower()
    if s in ('', 'none', 'null', 'none_', 'none_none', 'none_nohouse', '0', '0_0', 'none_0'):
        return False
    if s.startswith('none_') or s.endswith('_none') or s.endswith('_nohouse'):
        return False
    if re.match(r'^none_\d+', s):
        return False
    return True


def _clean_devanagari_skeleton(text: Optional[str]) -> str:
    """Normalizes Devanagari text by removing honorific particles, gotra suffixes, anusvara, and punctuation."""
    if not text:
        return ""
    s = re.sub(r'[\s\.\,\-\'\"]+', ' ', str(text)).strip()
    particles = [
        # Statewide Rajasthan Castes & Gotras
        'राजपुरोहित', 'बिश्नोई', 'चौधरी', 'शर्मा', 'मेघवाल', 'सुथार', 'माली',
        'पटेल', 'देवासी', 'रबारी', 'जाट', 'राजपूत', 'गुर्जर', 'मीणा', 'मीना',
        'बैरवा', 'सैन', 'प्रजापत', 'प्रजापति', 'कुम्हार', 'जांगिड़', 'चारण',
        'नाई', 'दर्जी', 'खटीक', 'वाल्मीकि', 'बलाई', 'कश्यप', 'खाती', 'बावरी',
        'रावत', 'धाकड़', 'लोधा', 'जोशी', 'व्यास', 'दाधीच', 'पंडित', 'तिवारी',
        # Suffixes & Honorifics
        'सिंह', 'सिह', 'देवी', 'कंवर', 'कुमार', 'कुमारी', 'लाल', 'राम',
        'बाई', 'जी', 'भाई', 'प्रसाद', 'दास', 'चंद्र', 'चन्द्र', 'पाल'
    ]
    words = s.split()
    while words and words[-1] in particles and len(words) > 1:
        words.pop()
    s = "".join(words)
    for p in particles:
        if s.endswith(p) and len(s) > len(p) + 2:
            s = s[:-len(p)]
    s = re.sub(r'[\u0901\u0902\u093c\u0964\u0965]', '', s)
    return s.strip()


def _clean_en_skeleton(text: Optional[str]) -> str:
    """Normalizes English transliterated names by removing suffixes, non-alpha, and phonetics."""
    if not text:
        return ""
    s = re.sub(r'[^a-zA-Z\s]', '', str(text)).lower().strip()
    particles = [
        # Statewide Rajasthan Castes & Gotras
        'rajpurohit', 'bishnoi', 'choudhary', 'chaudhary', 'sharma', 'meghwal',
        'suthar', 'mali', 'patel', 'dewasi', 'rabari', 'jat', 'rajput',
        'gurjar', 'gujjar', 'meena', 'mina', 'bairwa', 'sain', 'prajapat',
        'prajapati', 'kumhar', 'jangid', 'charan', 'nai', 'darji', 'khatik',
        'valmiki', 'balai', 'rawat', 'dhakar', 'lodha', 'joshi', 'vyas',
        # Suffixes & Honorifics
        'singh', 'sing', 'devi', 'kanwar', 'kumar', 'kumari', 'lal', 'ram',
        'bai', 'ji', 'bhai', 'das', 'prasad', 'pal', 'chand', 'chandra'
    ]
    words = s.split()
    while words and words[-1] in particles and len(words) > 1:
        words.pop()
    s = "".join(words)
    for p in particles:
        if s.endswith(p) and len(s) > len(p) + 2:
            s = s[:-len(p)]
    s = re.sub(r'[vw]', 'v', s)
    s = re.sub(r'ee|ea|ii', 'i', s)
    s = re.sub(r'oo|uu', 'u', s)
    s = re.sub(r'sh', 's', s)
    s = re.sub(r'th', 't', s)
    s = re.sub(r'kh', 'k', s)
    s = re.sub(r'gh', 'g', s)
    s = re.sub(r'dh', 'd', s)
    s = re.sub(r'ph', 'f', s)
    s = re.sub(r'bh', 'b', s)
    s = re.sub(r'(.)\1+', r'\1', s)
    return s.strip()


def _check_house_proximity(target_house: Optional[str], candidate_house: Optional[str]) -> Tuple[bool, int, str]:
    """
    Evaluates spatial relationship between two house numbers.
    Returns: (is_proximate: bool, distance: int, relation_type: str)
      relation_type in ('same', 'subhouse', 'adjacent_numeric', 'adjacent_subhouse', 'far', 'dummy')
    """
    t_clean = clean_house(target_house)
    c_clean = clean_house(candidate_house)
    if not t_clean or not c_clean or t_clean in ('0', '-', '') or c_clean in ('0', '-', ''):
        return False, 999, 'dummy'

    if t_clean == c_clean:
        return True, 0, 'same'

    # Check alphanumeric normalization match (e.g. 199 vs 199़ or 148 vs 148/A)
    t_alnum = re.sub(r'[^a-z0-9ऀ-ॿ]', '', t_clean)
    c_alnum = re.sub(r'[^a-z0-9ऀ-ॿ]', '', c_clean)
    if t_alnum and c_alnum and t_alnum == c_alnum:
        return True, 0, 'subhouse'

    # Pure numeric comparison
    if t_clean.isdigit() and c_clean.isdigit():
        diff = abs(int(t_clean) - int(c_clean))
        if diff <= 2:
            return True, diff, 'adjacent_numeric'
        return False, diff, 'far'

    # Prefix match for sub-houses (e.g. 148 and 148A, 199 and 199-1)
    m_t = re.match(r'^(\d+)', t_clean)
    m_c = re.match(r'^(\d+)', c_clean)
    if m_t and m_c:
        prefix_t = m_t.group(1)
        prefix_c = m_c.group(1)
        if prefix_t == prefix_c:
            return True, 0, 'subhouse'
        prefix_diff = abs(int(prefix_t) - int(prefix_c))
        if prefix_diff <= 1:
            return True, prefix_diff, 'adjacent_subhouse'

    return False, 999, 'far'


def _discover_family_for_voter(voter: Optional[Dict[str, Any]], part_no: int, house_no: str, tenant: str, repo: Any, tid: int):
    """Internal helper to discover official family, same house, and extended candidates for a given voter/house."""
    official_family = []
    f_id = str(voter.get('family_id') or '').strip() if voter else ''
    if is_valid_family_id(f_id):
        all_fam = repo.get_family_voters(tenant, f_id)
        if part_no:
            official_family = [r for r in all_fam if r.get('part_no') == part_no]
        else:
            official_family = all_fam

    if is_valid_family_id(f_id) and voter and not any(r['id'] == voter['id'] for r in official_family):
        official_family.insert(0, voter)

    official_ids = set(r['id'] for r in official_family)

    same_house = []
    if house_no:
        h_no_zero = house_no.lstrip('0') or '0'
        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT * FROM voters
                    WHERE tenant_id = %s AND part_no = %s
                      AND (house_no = %s OR house_no = %s OR LTRIM(house_no, '0') = %s)
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    ORDER BY sl_no
                """, (tid, part_no, house_no, h_no_zero, h_no_zero))
                raw_same_house = [repo._normalize_row(dict(r), tid) for r in cur.fetchall()]

        same_house = [
            r for r in raw_same_house
            if is_exact_house_match(house_no, r.get('house_no')) and r['id'] not in official_ids
        ]

    if not official_family and voter and not any(r['id'] == voter['id'] for r in same_house):
        same_house.insert(0, voter)

    household_ids = official_ids | set(r['id'] for r in same_house)
    direct = official_family + same_house

    # Extended candidates discovery with Devanagari normalization & biological guardrails
    related_candidates = []
    if voter and household_ids:
        id_list = list(household_ids)
        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # FIX: Handle NULL legacy_id properly using != ALL and IS NULL check
                cur.execute("""
                    SELECT * FROM voters
                    WHERE tenant_id = %s AND part_no = %s 
                      AND voter_id != ALL(%s)
                      AND (legacy_id IS NULL OR legacy_id != ALL(%s))
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                """, (tid, part_no, id_list, id_list))
                other_rows = [repo._normalize_row(dict(r), tid) for r in cur.fetchall()]

        # Prepare normalized metadata for primary household members
        household_profiles = []
        household_castes = set(r.get('caste_tag') for r in direct if r.get('caste_tag'))
        household_houses = set(clean_house(m.get('house_no')) for m in direct if m.get('house_no'))

        for m in direct:
            household_profiles.append({
                'name_hi': (m.get('name_hi') or '').strip(),
                'rel_hi': (m.get('rel_name_hi') or '').strip(),
                'rel_type': (m.get('rel_type') or '').strip(),
                'skel_hi': _clean_devanagari_skeleton(m.get('name_hi')),
                'rel_skel_hi': _clean_devanagari_skeleton(m.get('rel_name_hi')),
                'name_en': (m.get('name_en') or '').strip().lower(),
                'rel_en': (m.get('rel_name_en') or '').strip().lower(),
                'skel_en': _clean_en_skeleton(m.get('name_en')),
                'rel_skel_en': _clean_en_skeleton(m.get('rel_name_en')),
                'age': int(m.get('age') or 0),
                'gender': (m.get('gender') or '').strip(),
                'sl_no': int(m.get('sl_no') or 0),
                'house_no': str(m.get('house_no') or '').strip()
            })

        candidate_map = {}

        for row in other_rows:
            o_house = str(row.get('house_no') or '').strip()
            o_caste = row.get('caste_tag')

            # Hard Veto 1: Caste Tag Clash (if both known)
            if household_castes and o_caste and o_caste not in household_castes:
                continue

            # Hard Veto 2: Spatial Proximity Gate
            best_h_adj = False
            best_h_diff = 999
            best_h_type = 'far'

            for h_target in (household_houses or [clean_house(house_no)]):
                is_adj, h_diff, h_type = _check_house_proximity(h_target, o_house)
                if is_adj and h_diff < best_h_diff:
                    best_h_adj = True
                    best_h_diff = h_diff
                    best_h_type = h_type

            if not best_h_adj:
                if any(_check_house_proximity(h_target, o_house)[2] == 'dummy' for h_target in (household_houses or [clean_house(house_no)])) or not household_houses:
                    best_h_type = 'dummy'

            o_sl = int(row.get('sl_no') or 0)
            sl_diff = min(abs(p['sl_no'] - o_sl) for p in household_profiles) if household_profiles else 999
            is_dummy_house = (best_h_type == 'dummy')

            # Mandatory spatial gating: Must be either adjacent/sub house OR close serial if house is dummy
            if not best_h_adj:
                if is_dummy_house and sl_diff <= 15:
                    pass
                else:
                    # Beyond spatial neighborhood -> Hard reject!
                    continue

            o_name_hi = (row.get('name_hi') or '').strip()
            o_rel_hi = (row.get('rel_name_hi') or '').strip()
            o_rel_type = (row.get('rel_type') or '').strip()
            o_skel_hi = _clean_devanagari_skeleton(o_name_hi)
            o_rel_skel_hi = _clean_devanagari_skeleton(o_rel_hi)

            o_name_en = (row.get('name_en') or '').strip().lower()
            o_rel_en = (row.get('rel_name_en') or '').strip().lower()
            o_skel_en = _clean_en_skeleton(o_name_en)
            o_rel_skel_en = _clean_en_skeleton(o_rel_en)

            o_age = int(row.get('age') or 0)
            o_gender = (row.get('gender') or '').strip()

            best_score = 0
            best_reasons = []
            best_sl_diff = 9999

            for p in household_profiles:
                score = 0
                reasons = []

                # Spatial points
                if best_h_type == 'subhouse':
                    score += 35
                    reasons.append(f"उप-मकान #{o_house}")
                elif best_h_type == 'adjacent_numeric' and best_h_diff == 1:
                    score += 25
                    reasons.append(f"समीपस्थ मकान #{o_house}")
                elif best_h_type == 'adjacent_numeric' and best_h_diff == 2:
                    score += 15
                    reasons.append(f"निकटवर्ती मकान #{o_house}")
                elif best_h_type == 'adjacent_subhouse':
                    score += 20
                    reasons.append(f"समीपस्थ उप-मकान #{o_house}")
                elif is_dummy_house and sl_diff <= 10:
                    score += 15
                    reasons.append(f"निकटवर्ती क्र. {o_sl}")

                # Kinship & Relational Guardrails
                is_p_parent_rel = p['rel_type'] in ('पिता', 'Father', 'माता', 'Mother', '') or not p['rel_type']
                is_o_parent_rel = o_rel_type in ('पिता', 'Father', 'माता', 'Mother', '') or not o_rel_type

                # 1. Sibling Linkage (Both must share same father/relative; NEVER husband!)
                if is_p_parent_rel and is_o_parent_rel:
                    is_sib_hi = (p['rel_skel_hi'] and o_rel_skel_hi and p['rel_skel_hi'] == o_rel_skel_hi and len(p['rel_skel_hi']) >= 3)
                    is_sib_en = (p['rel_skel_en'] and o_rel_skel_en and p['rel_skel_en'] == o_rel_skel_en and len(p['rel_skel_en']) >= 3)
                    if is_sib_hi or is_sib_en:
                        if abs(p['age'] - o_age) <= 25:
                            score += 45
                            reasons.append(f"साझा पिता: {row.get('rel_name_hi') or row.get('rel_name_en')}")

                # 2. Junior Branch Linkage (Candidate is offspring of household member)
                if is_o_parent_rel:
                    is_child_hi = (p['skel_hi'] and o_rel_skel_hi and p['skel_hi'] == o_rel_skel_hi and len(p['skel_hi']) >= 3)
                    is_child_en = (p['skel_en'] and o_rel_skel_en and p['skel_en'] == o_rel_skel_en and len(p['skel_en']) >= 3)
                    if is_child_hi or is_child_en:
                        if p['age'] and o_age and (p['age'] - o_age >= 16):
                            score += 50
                            reasons.append(f"संभावित शाखा (पिता: {p['name_hi'] or p['name_en']})")

                # 3. Senior Branch Linkage (Household member is offspring of candidate)
                if is_p_parent_rel:
                    is_parent_hi = (o_skel_hi and p['rel_skel_hi'] and o_skel_hi == p['rel_skel_hi'] and len(o_skel_hi) >= 3)
                    is_parent_en = (o_skel_en and p['rel_skel_en'] and o_skel_en == p['rel_skel_en'] and len(o_skel_en) >= 3)
                    if is_parent_hi or is_parent_en:
                        if o_age and p['age'] and (o_age - p['age'] >= 16):
                            score += 50
                            reasons.append(f"संभावित वरिष्ठ शाखा (पिता: {o_name_hi or o_name_en})")

                # 4. Spouse / Bahu Linkage (Female candidate whose husband is in household)
                if o_gender in ('महिला', 'स्त्री', 'Female', 'F') and o_rel_type in ('पति', 'Husband'):
                    if p['gender'] in ('पुरुष', 'Male', 'M'):
                        is_spouse_hi = (p['skel_hi'] and o_rel_skel_hi and p['skel_hi'] == o_rel_skel_hi and len(p['skel_hi']) >= 3)
                        is_spouse_en = (p['skel_en'] and o_rel_skel_en and p['skel_en'] == o_rel_skel_en and len(p['skel_en']) >= 3)
                        if is_spouse_hi or is_spouse_en:
                            if -10 <= (p['age'] - o_age) <= 20:
                                score += 50
                                reasons.append(f"संभावित पारिवारिक सदस्य (पति: {p['name_hi'] or p['name_en']})")

                # Serial proximity bonus if candidate already has both spatial + relational match
                if score >= 60 and sl_diff <= 15:
                    score += 5

                if score > best_score:
                    best_score = score
                    best_reasons = reasons
                    best_sl_diff = sl_diff

            # Must satisfy both spatial proximity AND relational validity (Score >= 60)
            if best_score >= 60:
                v_id = row['id']
                if v_id not in candidate_map or candidate_map[v_id][0] < best_score:
                    row['relation_score'] = best_score
                    row['relation_reason'] = " · ".join(best_reasons)
                    candidate_map[v_id] = (best_score, best_sl_diff, row)

        related_candidates = list(candidate_map.values())
        related_candidates.sort(key=lambda x: (-x[0], x[1]))

    return official_family, same_house, direct, [x[2] for x in related_candidates[:15]]



# ---------------------------------------------------------------------------
# GET /api/family_smart
# ---------------------------------------------------------------------------

@slip_bp.route('/api/family_smart')
def api_family_smart():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    voter_ids_raw = request.args.get('voter_ids', '') or request.args.get('ids', '')
    voter_id = request.args.get('voter_id', type=int) or request.args.get('id', type=int)
    part_no = request.args.get('part', type=int)
    house_no = request.args.get('house', '').strip()

    id_list = []
    if voter_ids_raw:
        id_list = [int(x) for x in str(voter_ids_raw).split(',') if x.strip().isdigit()]
    elif voter_id:
        id_list = [voter_id]

    if not id_list and not (part_no and house_no):
        return jsonify({
            'official_family': [],
            'same_house': [],
            'direct': [],
            'related': [],
            'clusters': [],
            'total_direct': 0,
            'total_related': 0
        })

    # Retrieve all target voters
    target_voters = repo.get_voters_by_ids(tenant, id_list) if id_list else []
    booths_dict = repo.get_booths_dict(tenant)

    if not target_voters and part_no and house_no:
        # Fallback to direct house search without target voter
        off, same, direct, rel = _discover_family_for_voter(None, part_no, house_no, tenant, repo, tid)
        booth = booths_dict.get(str(part_no)) or booths_dict.get(part_no, {})
        return jsonify({
            'official_family': off,
            'same_house': same,
            'direct': direct,
            'related': rel,
            'clusters': [{
                'house_no': house_no,
                'part_no': part_no,
                'target_voters': [],
                'direct': direct,
                'related': rel,
                'booth': booth
            }],
            'total_direct': len(direct),
            'total_related': len(rel),
            'total_members': len(direct)
        })

    # Group target voters by household key: (part_no, clean_house(house_no))
    house_groups = {}
    for tv in target_voters:
        p = tv.get('part_no') or part_no or 1
        h = str(tv.get('house_no') or house_no or '').strip()
        k = (p, clean_house(h))
        if k not in house_groups:
            house_groups[k] = {'part_no': p, 'house_no': h, 'target_voters': []}
        house_groups[k]['target_voters'].append(tv)

    clusters = []
    aggregated_direct = []
    aggregated_related = []
    seen_direct_ids = set()
    seen_related_ids = set()

    for (p, _), g_info in house_groups.items():
        primary_voter = g_info['target_voters'][0]
        h_no = g_info['house_no']
        off, same, direct, rel = _discover_family_for_voter(primary_voter, p, h_no, tenant, repo, tid)
        booth = booths_dict.get(str(p)) or booths_dict.get(p, {})

        clusters.append({
            'part_no': p,
            'house_no': h_no,
            'target_voters': g_info['target_voters'],
            'official_family': off,
            'same_house': same,
            'direct': direct,
            'related': rel,
            'total_direct': len(direct),
            'total_related': len(rel),
            'booth': booth
        })

        for d in direct:
            did = d['id']
            if did not in seen_direct_ids:
                seen_direct_ids.add(did)
                aggregated_direct.append(d)

        for r in rel:
            rid = r['id']
            if rid not in seen_direct_ids and rid not in seen_related_ids:
                seen_related_ids.add(rid)
                aggregated_related.append(r)

    # For single cluster, populate top-level convenience keys
    first_cluster = clusters[0] if clusters else {}

    return jsonify({
        'mode': 'multi_cluster' if len(clusters) > 1 else 'single_cluster',
        'clusters': clusters,
        'official_family': first_cluster.get('official_family', []),
        'same_house': first_cluster.get('same_house', []),
        'direct': aggregated_direct,
        'related': aggregated_related,
        'total_direct': len(aggregated_direct),
        'total_related': len(aggregated_related),
        'total_members': len(aggregated_direct),
        'total_clusters': len(clusters),
        'booth': first_cluster.get('booth', {})
    })


# ---------------------------------------------------------------------------
# GET /api/slip_text
# ---------------------------------------------------------------------------

def sanitize_slip_emojis(text: str) -> str:
    """Normalizes composite ZWJ emojis and strips variation selectors for bulletproof WhatsApp Web and URL transport."""
    if not text:
        return text
    # Replace composite ZWJ emoji sequences (like man+ZWJ+boy) with universal stable emoji
    text = text.replace('👨‍👦', '👨')
    # Strip variation selector-16 (️) that breaks during URL re-encoding on WhatsApp Web
    text = text.replace('️', '')
    return text

@slip_bp.route('/api/slip_text')

def api_slip_text():
    tenant, repo = _get_tenant()
    cfg = load_tenant_config(tenant)
    is_panchayat = is_panchayat_campaign(cfg)
    election = cfg.get('election', {})
    party = cfg.get('party', {})
    election_type = str(election.get('election_type') or 'MUNICIPAL').strip().upper()
    voting_method = get_voting_method(
        election_type,
        party.get('voting_method') or cfg.get('voting_method') or election.get('voting_method')
    )
    is_ballot_election = (voting_method == 'BALLOT_PAPER')
    ballot_advice = "\n💡 *मतदान निर्देश:* मतदान केंद्र पर दी गई मुहर (ठप्पा) का उपयोग करें (अपनी पेन या अंगूठे का नहीं)। मुहर लगाने के बाद मतपत्र को पहले खड़े में मोड़ें ताकि स्याही दूसरी जगह न फैले।"

    place_name = election.get('ward_name_hi') or election.get('ward_name_en') or election.get('app_title', '')
    dist_name = election.get('district', '')
    slip_place = f"{place_name} ({dist_name})" if (place_name and dist_name) else (place_name or dist_name or "मतदाता सेवा")
    default_msg = cfg.get('campaign_message', '')
    prefix = (default_msg + "\n\n") if default_msg else ""
    booths_dict = repo.get_booths_dict(tenant)

    voter_id = request.args.get('id', type=int) or request.args.get('voter_id', type=int)
    family_ids = request.args.get('family_ids', '') or request.args.get('voter_ids', '')

    if family_ids:
        id_list = [int(x) for x in str(family_ids).split(',') if x.strip().isdigit()]
        if not id_list:
            return jsonify({'slip_text': ''})

        voters = repo.get_voters_by_ids(tenant, id_list)
        if not voters:
            return jsonify({'slip_text': ''})

        # Drop struck-off electors from family/combined slips; refuse outright if
        # that leaves nobody.
        struck_names = [format_bilingual(v.get('name_en'), v.get('name_hi'))
                        for v in voters if is_struck_off(v)]
        voters = [v for v in voters if not is_struck_off(v)]
        if not voters:
            return jsonify({'slip_text': '', 'error': STRUCK_OFF_ERROR, 'struck_off': struck_names}), 422

        # Keep original serial order
        voters.sort(key=lambda v: (v.get('part_no', 0), v.get('sl_no', 0)))
        first = voters[0]

        # If exactly 1 voter in family_ids, treat as Type 1: Single Slip
        if len(voters) == 1:
            v = voters[0]
            p = v.get('part_no')
            ward_raw = v.get('ward_no')
            has_explicit_ward = bool(is_panchayat and ward_raw and str(ward_raw).strip() not in ('0', '', 'None'))

            booth = booths_dict.get(str(p)) or booths_dict.get(p, {})
            b_name = booth.get('name') or v.get('booth_name', '')
            name_display = format_bilingual(v.get('name_en'), v.get('name_hi'))
            rel_display = format_bilingual(v.get('rel_name_en'), v.get('rel_name_hi'))
            gender_val = v.get('gender') or ''
            gender_str = "पुरुष" if gender_val in ['पुरुष', 'Male', 'M'] else "महिला"

            slip = prefix
            slip += f"🗳️ *मतदाता सूचना पर्ची · {slip_place}*\n\n"
            if has_explicit_ward:
                slip += f"📍 *वार्ड संख्या (Ward):* {ward_raw} | *भाग संख्या (Part):* {p}\n"
            else:
                slip += f"📍 *भाग संख्या (Part):* {p}\n"
            slip += f"🔢 *क्रम संख्या (Sl. No.):* {v.get('sl_no')}\n"
            slip += f"👤 *नाम (Name):* {name_display}\n"
            slip += f"👨‍👦 *{v.get('rel_type', 'संबंध')}:* {rel_display}\n"
            slip += f"🏠 *मकान नं. (House):* {v.get('house_no', '-')} | *आयु:* {v.get('age', '-')} वर्ष, {gender_str}\n"
            slip += f"🆔 *EPIC:* {v.get('epic_no', '-')}\n\n"
            slip += f"🏢 *मतदान केंद्र (Polling Booth):*\n{b_name}\n"
            slip += "\n🙏 *कृपया पहचान पत्र साथ लाएं व मतदान अवश्य करें!*"
            if is_ballot_election:
                slip += ballot_advice

            return jsonify({'slip_text': sanitize_slip_emojis(slip), 'voters': voters, 'voter': v, 'slip_type': 'Single'})

        # Type 2: Unified Family / Combined Slip (2+ Voters, No Google Maps)
        wards = sorted(list(set(str(v.get('ward_no')) for v in voters if v.get('ward_no') and str(v.get('ward_no')).strip() not in ('0', '', 'None')))) if is_panchayat else []
        has_wards = bool(wards)
        parts = sorted(list(set(v['part_no'] for v in voters if v.get('part_no'))))
        houses = sorted(list(set(str(v.get('house_no') or '').strip() for v in voters if v.get('house_no'))))
        multi_house = len(houses) > 1
        multi_division = len(wards) > 1 if has_wards else len(parts) > 1

        slip = prefix
        slip += f"🗳️ *परिवार / संयुक्त मतदाता पर्ची · {slip_place}*\n"
        if not multi_division:
            p = first['part_no']
            booth = booths_dict.get(str(p)) or booths_dict.get(p, {})
            b_name = booth.get('name') or first.get('booth_name', '')
            house_str = f" | 🏠 *मकान:* {houses[0]}" if (not multi_house and houses and houses[0]) else ""
            if has_wards:
                slip += f"📍 *वार्ड (Ward):* {wards[0]} | *भाग (Part):* {p}{house_str} | 👥 *कुल मतदाता:* {len(voters)}\n"
            else:
                slip += f"📍 *भाग (Part):* {p}{house_str} | 👥 *कुल मतदाता:* {len(voters)}\n"
            slip += f"🏢 *मतदान केंद्र:* {b_name}\n\n"
        else:
            if has_wards and len(parts) > 1:
                slip += f"📍 *वार्ड (Wards):* {', '.join(wards)} | *भाग (Parts):* {', '.join(str(p) for p in parts)} | 👥 *कुल मतदाता:* {len(voters)}\n\n"
            elif has_wards:
                slip += f"📍 *वार्ड (Wards):* {', '.join(wards)} | *भाग (Part):* {parts[0]} | 👥 *कुल मतदाता:* {len(voters)}\n\n"
            else:
                slip += f"📍 *भाग (Parts):* {', '.join(str(p) for p in parts)} | 👥 *कुल मतदाता:* {len(voters)}\n\n"
            slip += "🏢 *मतदान केंद्र विवरण (Polling Booths):*\n"
            for p in parts:
                p_b = booths_dict.get(str(p)) or booths_dict.get(p, {})
                slip += f"• *भाग {p}:* {p_b.get('name', '')}\n"
            slip += "\n"

        for idx, v in enumerate(voters, 1):
            name_display = format_bilingual(v.get('name_en'), v.get('name_hi'))
            gender_val = v.get('gender') or ''
            gender_short = "M" if gender_val in ['पुरुष', 'Male', 'M'] else "F"
            w_item = v.get('ward_no')
            has_w_item = w_item and str(w_item).strip() not in ('0', '', 'None')
            if len(wards) > 1 and has_w_item:
                div_tag = f"वार्ड {w_item} · "
            elif len(parts) > 1:
                div_tag = f"भाग {v.get('part_no')} · "
            else:
                div_tag = ""
            house_tag = f" [मकान #{v['house_no']}]" if (multi_house or multi_division) else ""
            slip += f"{idx}. *{div_tag}क्र.सं. {v['sl_no']}* · {name_display} ({v.get('age', '-')} Yrs, {gender_short}){house_tag} · EPIC: {v.get('epic_no', '-')}\n"

        slip += "\n🙏 *कृपया मतदान अवश्य करें!*"
        if is_ballot_election:
            slip += ballot_advice
        return jsonify({'slip_text': sanitize_slip_emojis(slip), 'voters': voters, 'voter': first, 'slip_type': 'FamilyCombined'})

    elif voter_id:
        v = repo.get_voter(tenant, voter_id)
        if not v:
            return jsonify({'slip_text': ''})
        if is_struck_off(v):
            return jsonify({
                'slip_text': '',
                'error': STRUCK_OFF_ERROR,
                'struck_off': [format_bilingual(v.get('name_en'), v.get('name_hi'))],
            }), 422

        p = v.get('part_no')
        ward_raw = v.get('ward_no')
        has_explicit_ward = bool(is_panchayat and ward_raw and str(ward_raw).strip() not in ('0', '', 'None'))

        booth = booths_dict.get(str(p)) or booths_dict.get(p, {})
        b_name = booth.get('name') or v.get('booth_name', '')
        name_display = format_bilingual(v.get('name_en'), v.get('name_hi'))
        rel_display = format_bilingual(v.get('rel_name_en'), v.get('rel_name_hi'))
        gender_val = v.get('gender') or ''
        gender_str = "पुरुष" if gender_val in ['पुरुष', 'Male', 'M'] else "महिला"

        slip = prefix
        slip += f"🗳️ *मतदाता सूचना पर्ची · {slip_place}*\n\n"
        if has_explicit_ward:
            slip += f"📍 *वार्ड संख्या (Ward):* {ward_raw} | *भाग संख्या (Part):* {p}\n"
        else:
            slip += f"📍 *भाग संख्या (Part):* {p}\n"
        slip += f"🔢 *क्रम संख्या (Sl. No.):* {v.get('sl_no')}\n"
        slip += f"👤 *नाम (Name):* {name_display}\n"
        slip += f"👨‍👦 *{v.get('rel_type', 'संबंध')}:* {rel_display}\n"
        slip += f"🏠 *मकान नं. (House):* {v.get('house_no', '-')} | *आयु:* {v.get('age', '-')} वर्ष, {gender_str}\n"
        slip += f"🆔 *EPIC:* {v.get('epic_no', '-')}\n\n"
        slip += f"🏢 *मतदान केंद्र (Polling Booth):*\n{b_name}\n"
        slip += "\n🙏 *कृपया पहचान पत्र साथ लाएं व मतदान अवश्य करें!*"
        if is_ballot_election:
            slip += ballot_advice

        return jsonify({'slip_text': sanitize_slip_emojis(slip), 'voter': v, 'slip_type': 'Single'})

    return jsonify({'slip_text': ''})


# ---------------------------------------------------------------------------
# GET /api/slip_card_info
# ---------------------------------------------------------------------------

@slip_bp.route('/api/slip_card_info')
def api_slip_card_info():
    tenant, repo = _get_tenant()
    voter_id = request.args.get('id', type=int) or request.args.get('voter_id', type=int)
    family_ids = request.args.get('family_ids', '') or request.args.get('voter_ids', '')
    page_size = request.args.get('page_size', 8, type=int) or 8

    target_voters = []
    if family_ids:
        id_list = [int(x) for x in str(family_ids).split(',') if x.strip().isdigit()]
        if id_list:
            target_voters = repo.get_voters_by_ids(tenant, id_list)
    elif voter_id:
        v = repo.get_voter(tenant, voter_id)
        if v:
            target_voters = [v]

    total_voters = len(target_voters)
    total_pages = max(1, (total_voters + page_size - 1) // page_size)

    family_phones = []
    for v in target_voters:
        p = (v.get('contact_phone') or '').strip()
        if not p:
            continue
        tokens = re.split(r'[,;\s/]+', p)
        for t in tokens:
            clean = re.sub(r'\D', '', t)
            if clean.startswith('91') and len(clean) == 12 and clean[2] in '6789':
                clean = clean[2:]
            elif clean.startswith('0') and len(clean) == 11 and clean[1] in '6789':
                clean = clean[1:]
            if len(clean) == 10 and clean[0] in '6789' and not any(fp['phone'] == clean for fp in family_phones):
                name_disp = v.get('name_hi') or v.get('name_en') or f"Sl {v.get('sl_no')}"
                family_phones.append({'phone': clean, 'name': name_disp, 'sl_no': v.get('sl_no')})

    return jsonify({
        'total_voters': total_voters,
        'total_pages': total_pages,
        'page_size': page_size,
        'pages': list(range(1, total_pages + 1)),
        'family_phones': family_phones
    })



# ---------------------------------------------------------------------------
# OFFICIAL A4 10-UP VOTER SLIPS (LEGAL POLLING DAY PRINT)
# ---------------------------------------------------------------------------

@slip_bp.route('/api/print_slips_10up')
def api_print_slips_10up():
    """
    Renders selected electors into an official, A4 10-Up cut-sheet printable view.
    Completely neutral: NO candidate name, NO symbol, NO candidate ballot serial.
    100% legal for distribution on polling day.
    """
    tenant, repo = _get_tenant()
    cfg = load_tenant_config(tenant) or {}
    
    voter_id = request.args.get('id', type=int) or request.args.get('voter_id', type=int)
    family_ids = request.args.get('family_ids', '') or request.args.get('voter_ids', '')
    
    target_voters = []
    if family_ids:
        id_list = [int(x) for x in str(family_ids).split(',') if x.strip().isdigit()]
        if id_list:
            target_voters = repo.get_voters_by_ids(tenant, id_list)
    elif voter_id:
        v = repo.get_voter(tenant, voter_id)
        if v:
            target_voters = [v]

    if not target_voters:
        return "<h2 style='font-family:sans-serif;padding:30px;color:#b91c1c;'>कोई मतदाता नहीं चुना गया (No electors selected for printing)</h2>", 404

    # Remove struck-off electors
    target_voters = [v for v in target_voters if not is_struck_off(v)]
    if not target_voters:
        return f"<h2 style='font-family:sans-serif;padding:30px;color:#b91c1c;'>{STRUCK_OFF_ERROR}</h2>", 422

    # Sort electors: Ward -> House No -> Sl No
    def elector_sort_key(v):
        w = v.get('ward_no') or v.get('part_no') or 1
        h = str(v.get('house_no') or '').strip()
        m = re.match(r'^(\d+)', h)
        h_num = int(m.group(1)) if m else 999999
        sl = int(v.get('sl_no') or 999999)
        return (w, h_num, sl)

    try:
        target_voters.sort(key=elector_sort_key)
    except Exception:
        pass

    booths_dict = repo.get_booths_dict(tenant) if hasattr(repo, 'get_booths_dict') else {}
    election_info = cfg.get('election', {})
    gp_name = election_info.get('ward_name_hi') or election_info.get('app_title') or f"ग्राम पंचायत"
    area_name = gp_name
    is_panchayat = is_panchayat_campaign(cfg)

    formatted_voters = []
    for v in target_voters:
        sl_no = v.get('sl_no') or '-'
        w_val = v.get('ward_no') or v.get('part_no') or 1
        ward_disp = f"वार्ड {w_val}" if (is_panchayat or v.get('ward_no')) else f"भाग {w_val}"
        name_hi = clean_devnagari_name(v.get('name_hi') or '')
        name_en = clean_english_name(v.get('name_en') or '', fallback_hi=name_hi)
        display_name = f"{name_hi} / {name_en}" if name_en and name_en.lower() != name_hi.lower() else (name_hi or name_en or '-')
        rel_type = v.get('rel_type') or 'पिता/पति'
        rel_name = v.get('rel_name_hi') or v.get('rel_name_en') or '—'
        house_no = v.get('house_no') or '—'
        age = v.get('age') or '—'
        gender = v.get('gender') or '—'
        epic_no = v.get('epic_no') or '—'
        
        # Booth resolution
        b_name = v.get('booth_name') or ''
        if not b_name:
            b_info = booths_dict.get(str(w_val)) or booths_dict.get(w_val) or {}
            b_name = b_info.get('name') or b_info.get('area') or 'राजकीय विद्यालय'

        formatted_voters.append({
            'sl_no': sl_no,
            'ward_display': ward_disp,
            'area_display': area_name,
            'display_name': display_name,
            'rel_type': rel_type,
            'rel_name': rel_name,
            'house_no': house_no,
            'age': age,
            'gender': gender,
            'epic_no': epic_no,
            'booth_name': b_name
        })

    # Chunk into pages of 10 electors strictly
    page_size = 10
    voter_pages = [formatted_voters[i:i + page_size] for i in range(0, len(formatted_voters), page_size)]
    total_pages = len(voter_pages)

    return render_template(
        'print_10up_official.html',
        voter_pages=voter_pages,
        total_voters=len(formatted_voters),
        total_pages=total_pages,
        area_name=area_name
    )
