"""
two_stage_search.py
Two-stage Indic voter search engine for multi-tenant PostgreSQL 17 + SIMD rapidfuzz.

Stage 1: Accelerated PostgreSQL candidate retrieval using GIN pg_trgm index on precomputed search_doc
         strictly scoped by tenant_id for partition pruning.
Stage 2: In-memory SIMD rapidfuzz ranking across Indic & English voter tokens.
"""

import time
import string
import unicodedata
import re
from typing import List, Dict, Any, Optional, Tuple
import rapidfuzz.fuzz

from electionapp.translit.bilingual_search import get_bilingual_search_variants

STAGE1_CANDIDATE_LIMIT = 200

GENDER_MAP = {
    'M': ['M', 'Male', 'male', 'MALE', 'पुरुष', 'पुरूष'],
    'F': ['F', 'Female', 'female', 'FEMALE', 'स्त्री', 'महिला'],
    'O': ['O', 'Other', 'other', 'OTHER', 'तृतीय लिंग', 'अन्य']
}

def resolve_gender_variants(gender: Optional[str]) -> Optional[List[str]]:
    """Normalizes gender queries across bilingual schemas ('पुरुष'/'स्त्री', 'Male'/'Female', 'M'/'F')."""
    if not gender:
        return None
    g = gender.strip().upper()
    if g in ('M', 'MALE', 'पुरुष', 'पुरूष'):
        return GENDER_MAP['M']
    elif g in ('F', 'FEMALE', 'स्त्री', 'महिला'):
        return GENDER_MAP['F']
    elif g in ('O', 'OTHER', 'तृतीय लिंग', 'अन्य'):
        return GENDER_MAP['O']
    return [gender.strip()]

# Canonical compound suffixes common in Indian names and speech-to-text outputs
HINDI_COMPOUND_SUFFIXES = [
    'प्रसाद', 'प्रकाश', 'चन्द्र', 'चन्द', 'कुमार', 'कुमारी', 
    'सिंह', 'लाल', 'राम', 'कान्त', 'भान', 'देव', 'दीन', 'नाथ', 'दत्त', 'राज', 'शर्मा'
]

ENGLISH_COMPOUND_SUFFIXES = [
    'prasad', 'prakash', 'chandra', 'chand', 'kumar', 'kumari',
    'singh', 'lal', 'ram', 'kant', 'bhan', 'dev', 'deen', 'nath', 'dutt', 'raj', 'sharma'
]

def split_indic_compound(token: str) -> List[str]:
    """
    Decompounds joined Indian name tokens (common in speech-to-text Hindi outputs).
    e.g., 'शिवकुमार' -> ['शिव', 'कुमार'], 'shivkumar' -> ['shiv', 'kumar'].
    Safeguards short stems and preserves non-compound names.
    """
    if not token or len(token) < 5:
        return [token]

    # 1. Hindi de-compounding
    for suf in HINDI_COMPOUND_SUFFIXES:
        if token.endswith(suf) and len(token) > len(suf):
            prefix = token[:-len(suf)]
            if len(prefix) >= 2 and not prefix.endswith('्'):
                return [prefix, suf]

    # 2. English de-compounding
    t_lower = token.lower()
    for suf in ENGLISH_COMPOUND_SUFFIXES:
        if t_lower.endswith(suf) and len(t_lower) > len(suf):
            prefix = token[:-len(suf)]
            if len(prefix) >= 2:
                return [prefix, token[-len(suf):]]

    return [token]

def expand_search_tokens(query_str: str) -> List[str]:
    """
    Tokenizes input query and expands any compound tokens.
    Returns list of distinct query tokens.
    """
    if not query_str:
        return []
    words = [w.strip() for w in query_str.split() if w.strip()]
    expanded = []
    for w in words:
        expanded.extend(split_indic_compound(w))
    return expanded

_PUNCT_AND_SYMBOLS = set(string.whitespace + string.punctuation + '।॥`~^+=<>|\ufffd')

_PHONETIC_DEV_FOLD = {
    'ण': 'न',
    'ब': 'व',
    'श': 'स',
    'ष': 'स',
    'घ': 'ध',
    'ी': 'ि',
    'ू': 'ु',
    'ं': 'न',
    'ँ': 'न',
    'ज़': 'ज',
    'फ़': 'फ',
    'ड़': 'ड',
    'ढ़': 'ढ',
    'क़': 'क',
    'ख़': 'ख',
    'ग़': 'ग',
}

_LATIN_PHONETIC_FOLD = [
    ('sh', 's'),
    ('ee', 'i'),
    ('oo', 'u'),
    ('w', 'v'),
    ('b', 'v'),
]

def fold_indic_name(s: str) -> str:
    """
    Collapses whitespace and non-letter/non-matra punctuation for canonical name matching.
    Preserves Devanagari vowel matras and combining characters.
    'शिव कुमार सैनी' -> 'शिवकुमारसैनी'
    'शिवकुमार सैनी' -> 'शिवकुमारसैनी'
    """
    if not s:
        return ""
    return "".join(
        c.lower() for c in s
        if not c.isspace()
        and c not in _PUNCT_AND_SYMBOLS
        and not unicodedata.category(c).startswith('P')
    )

def fold_phonetic_indic_name(s: str, collapse_spaces: bool = True) -> str:
    """
    Folds Devanagari and Latin names into canonical phonetic equivalence classes:
    - ण <-> न (गणपत <-> गनपत)
    - व <-> ब (विश्नोई <-> बिश्नोई)
    - श / ष <-> स (सुरेश <-> सुरेस)
    - ध <-> घ (लाधु <-> लाघु)
    - Anusvara <-> Half Nasal virama (चम्पालाल <-> चंपालाल, सन्तोष <-> संतोष)
    """
    if not s:
        return ""
    val = re.sub(r'[मनण]्', 'ं', str(s).lower())
    for lat_src, lat_dst in _LATIN_PHONETIC_FOLD:
        val = val.replace(lat_src, lat_dst)
    
    chars = []
    for c in val:
        if c.isspace():
            if not collapse_spaces:
                chars.append(' ')
            continue
        if c in _PUNCT_AND_SYMBOLS or unicodedata.category(c).startswith('P'):
            continue
        chars.append(_PHONETIC_DEV_FOLD.get(c, c))
    
    res = "".join(chars)
    if not collapse_spaces:
        res = " ".join(res.split())
    return res

def build_stage1_query(
    tenant_id: int,
    query: Optional[str] = "",
    candidate_limit: int = STAGE1_CANDIDATE_LIMIT,
    part_no: Optional[int] = None,
    ward_no: Optional[int] = None,
    gender: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    caste: Optional[str] = None,
    category: Optional[Any] = None,
    name: Optional[str] = None,
    relative: Optional[str] = None,
    part_nos: Optional[List[int]] = None,
    ward_nos: Optional[List[int]] = None,
    villages: Optional[List[str]] = None,
    **kwargs
) -> Tuple[str, dict]:
    """
    Constructs the parameterized Stage 1 SQL query and parameters.
    Guarantees tenant_id predicate is strictly present for PostgreSQL partition pruning.
    Applies Two-Box filters (name & relative) with surname-omission tolerance and optional filter predicates.
    """
    extra_conditions = []
    if kwargs.get("include_deleted") is False:
        extra_conditions.append("LOWER(COALESCE(status, '')) <> 'deleted'")
    params = {"tenant_id": tenant_id, "limit": candidate_limit}

    if part_no is not None:
        extra_conditions.append("""(
            (booth_name ILIKE %(booth_pfx1)s OR booth_name ILIKE %(booth_pfx2)s)
            OR (part_no = %(part_no)s AND (booth_name IS NULL OR substring(booth_name from '^([0-9]+)\\\\s*[-:]') IS NULL))
        )""")
        params["part_no"] = part_no
        params["booth_pfx1"] = f"{part_no} - %"
        params["booth_pfx2"] = f"{part_no} %"
    elif part_nos:
        clean_parts = [int(p) for p in part_nos if str(p).isdigit()]
        if clean_parts:
            extra_conditions.append("part_no = ANY(%(part_nos)s)")
            params["part_nos"] = clean_parts

    if ward_no is not None:
        extra_conditions.append("ward_no = %(ward_no)s")
        params["ward_no"] = ward_no
    elif ward_nos:
        clean_wards = [int(w) for w in ward_nos if str(w).isdigit()]
        if clean_wards:
            extra_conditions.append("ward_no = ANY(%(ward_nos)s)")
            params["ward_nos"] = clean_wards

    if villages:
        v_list = [f"%{str(v).strip()}%" for v in villages if str(v).strip()]
        if v_list:
            extra_conditions.append("(booth_area ILIKE ANY(%(villages)s) OR booth_name ILIKE ANY(%(villages)s))")
            params["villages"] = v_list

    target_gp = kwargs.get("gp") or kwargs.get("gram_panchayat")
    if target_gp and str(target_gp).strip():
        extra_conditions.append("gram_panchayat = %(target_gp)s")
        params["target_gp"] = str(target_gp).strip()

    gender_list = resolve_gender_variants(gender)
    if gender_list:
        extra_conditions.append("gender = ANY(%(gender_list)s)")
        params["gender_list"] = gender_list

    if age_min is not None:
        extra_conditions.append("age >= %(age_min)s")
        params["age_min"] = age_min

    if age_max is not None:
        extra_conditions.append("age <= %(age_max)s")
        params["age_max"] = age_max

    if caste:
        extra_conditions.append("caste_tag ILIKE %(caste)s")
        params["caste"] = f"%{caste.strip()}%"

    if category is not None:
        extra_conditions.append("voter_category = %(category)s")
        params["category"] = category

    cleaned_name = (name or "").strip()
    cleaned_rel = (relative or "").strip()
    cleaned_query = (query or "").strip()

    # 1. Two-Box Search Mode (when name or relative is explicitly provided)
    if cleaned_name or cleaned_rel:
        order_by_clause = "COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC"
        if cleaned_name:
            name_words = [w for w in cleaned_name.split() if w]
            name_tokens = expand_search_tokens(cleaned_name)
            name_subconds = []

            # EPIC match if pattern matches voter ID format
            if len(cleaned_name) >= 5 and any(c.isalpha() for c in cleaned_name) and any(c.isdigit() for c in cleaned_name):
                name_subconds.append("epic_no ILIKE %(name_epic)s")
                params["name_epic"] = f"%{cleaned_name}%"

            # Serial / House match if numeric
            if cleaned_name.isdigit() and len(cleaned_name) <= 5:
                name_subconds.append("(sl_no = %(name_sl)s OR house_no = %(name_house)s)")
                params["name_sl"] = int(cleaned_name)
                params["name_house"] = cleaned_name

            n_vars = get_bilingual_search_variants(cleaned_name)
            n_hi_pats = n_vars.get('hi_patterns') or [f"%{cleaned_name}%"]
            n_en_pats = n_vars.get('en_patterns') or [f"%{cleaned_name}%"]
            n_all_pats = n_vars.get('all_patterns') or [f"%{cleaned_name}%"]

            params["name_hi_pats"] = n_hi_pats
            params["name_en_pats"] = n_en_pats
            params["name_all_pats"] = n_all_pats
            params["name_full"] = f"%{cleaned_name}%"
            params["name_raw"] = cleaned_name
            name_subconds.extend([
                "name_en ILIKE ANY(%(name_en_pats)s)",
                "name_hi ILIKE ANY(%(name_hi_pats)s)",
                "name_norm ILIKE ANY(%(name_en_pats)s)",
                "search_doc ILIKE ANY(%(name_all_pats)s)",
                "name_hi %% %(name_raw)s"
            ])

            # Match multi-token compounds across search_doc using GIN trigram index
            if len(name_tokens) >= 2:
                tok_conds = []
                for idx, tok in enumerate(name_tokens):
                    k = f"name_tok_{idx}"
                    tok_conds.append(f"search_doc ILIKE %({k})s")
                    params[k] = f"%{tok}%"
                name_subconds.append(f"({' AND '.join(tok_conds)})")

            # If multi-word (e.g. "Vansh Mehta" or "Damini Parihar"), also match first token so we never miss voters whose surname was omitted in official roll
            first_tok = name_tokens[0] if name_tokens else (name_words[0] if name_words else "")
            if first_tok and (len(name_words) >= 2 or len(name_tokens) >= 2):
                ft_vars = get_bilingual_search_variants(first_tok)
                ft_hi_pats = ft_vars.get('hi_patterns') or [f"%{first_tok}%"]
                ft_en_pats = ft_vars.get('en_patterns') or [f"%{first_tok}%"]
                params["name_ft_hi"] = ft_hi_pats
                params["name_ft_en"] = ft_en_pats
                name_subconds.extend([
                    "name_en ILIKE ANY(%(name_ft_en)s)",
                    "name_hi ILIKE ANY(%(name_ft_hi)s)",
                    "name_norm ILIKE ANY(%(name_ft_en)s)"
                ])
            extra_conditions.append(f"({' OR '.join(name_subconds)})")
            order_by_clause = """
                CASE 
                    WHEN name_hi ILIKE ANY(%(name_hi_pats)s) OR name_en ILIKE ANY(%(name_en_pats)s) THEN 1
                    WHEN name_hi %% %(name_raw)s THEN 2
                    ELSE 3
                END ASC,
                similarity(name_hi, %(name_raw)s) DESC,
                COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC
            """

        if cleaned_rel:
            rel_words = [w for w in cleaned_rel.split() if w]
            rel_tokens = expand_search_tokens(cleaned_rel)
            r_vars = get_bilingual_search_variants(cleaned_rel)
            r_hi_pats = r_vars.get('hi_patterns') or [f"%{cleaned_rel}%"]
            r_en_pats = r_vars.get('en_patterns') or [f"%{cleaned_rel}%"]
            r_all_pats = r_vars.get('all_patterns') or [f"%{cleaned_rel}%"]

            params["rel_hi_pats"] = r_hi_pats
            params["rel_en_pats"] = r_en_pats
            params["rel_all_pats"] = r_all_pats
            params["rel_full"] = f"%{cleaned_rel}%"
            params["rel_raw"] = cleaned_rel
            rel_subconds = [
                "rel_name_en ILIKE ANY(%(rel_en_pats)s)",
                "rel_name_hi ILIKE ANY(%(rel_hi_pats)s)",
                "rel_norm ILIKE ANY(%(rel_en_pats)s)",
                "search_doc ILIKE ANY(%(rel_all_pats)s)",
                "rel_name_hi %% %(rel_raw)s"
            ]
            if len(rel_tokens) >= 2:
                rel_tok_conds = []
                for idx, tok in enumerate(rel_tokens):
                    rt_vars = get_bilingual_search_variants(tok)
                    rt_pats = rt_vars.get('all_patterns') or [f"%{tok}%"]
                    k = f"rel_tok_{idx}"
                    rel_tok_conds.append(f"search_doc ILIKE ANY(%({k})s)")
                    params[k] = rt_pats
                rel_subconds.append(f"({' AND '.join(rel_tok_conds)})")

            first_rel_tok = rel_tokens[0] if rel_tokens else (rel_words[0] if rel_words else "")
            if first_rel_tok and (len(rel_words) >= 2 or len(rel_tokens) >= 2):
                fr_vars = get_bilingual_search_variants(first_rel_tok)
                fr_hi_pats = fr_vars.get('hi_patterns') or [f"%{first_rel_tok}%"]
                fr_en_pats = fr_vars.get('en_patterns') or [f"%{first_rel_tok}%"]
                params["rel_ft_hi"] = fr_hi_pats
                params["rel_ft_en"] = fr_en_pats
                rel_subconds.extend([
                    "rel_name_en ILIKE ANY(%(rel_ft_en)s)",
                    "rel_name_hi ILIKE ANY(%(rel_ft_hi)s)",
                    "rel_norm ILIKE ANY(%(rel_ft_en)s)"
                ])
            extra_conditions.append(f"({' OR '.join(rel_subconds)})")
            if not cleaned_name:
                order_by_clause = """
                    CASE 
                        WHEN rel_name_hi ILIKE ANY(%(rel_hi_pats)s) OR rel_name_en ILIKE ANY(%(rel_en_pats)s) THEN 1
                        WHEN rel_name_hi %% %(rel_raw)s THEN 2
                        ELSE 3
                    END ASC,
                    similarity(rel_name_hi, %(rel_raw)s) DESC,
                    COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC
                """

        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions
        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT *
            FROM voters
            WHERE {where_clause}
            ORDER BY {order_by_clause}
            LIMIT %(limit)s;
        """
        return sql, params

    # 2. Legacy / Single-Query Mode
    words = expand_search_tokens(cleaned_query)
    if not words:
        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions
        where_clause = " AND ".join(conditions)
        sort_by = str(kwargs.get("sort") or kwargs.get("order_by") or "").strip().lower()
        if sort_by in ("name", "alpha", "a-z", "name_asc"):
            order_by_sql = "LOWER(COALESCE(NULLIF(TRIM(name_en), ''), TRIM(name_hi), '')) ASC, sl_no ASC NULLS LAST"
        else:
            order_by_sql = "part_no ASC, COALESCE(NULLIF(ward_no, 0), 1) ASC, sl_no ASC NULLS LAST"
        sql = f"""
            SELECT *
            FROM voters
            WHERE {where_clause}
            ORDER BY {order_by_sql}
            LIMIT %(limit)s;
        """
        return sql, params

    if len(words) == 1:
        w_vars = get_bilingual_search_variants(words[0])
        w_pats = w_vars.get("all_patterns") or [f"%{words[0]}%"]
        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions + ["search_doc ILIKE ANY(%(pattern)s)"]
        where_clause = " AND ".join(conditions)
        params["pattern"] = w_pats
        params["raw_q"] = words[0]
        sql = f"""
            SELECT *
            FROM voters
            WHERE {where_clause}
            ORDER BY similarity(name_hi, %(raw_q)s) DESC, COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC
            LIMIT %(limit)s;
        """
        return sql, params
    else:
        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions
        for i, word in enumerate(words):
            w_vars = get_bilingual_search_variants(word)
            w_pats = w_vars.get("all_patterns") or [f"%{word}%"]
            key = f"p{i}"
            conditions.append(f"search_doc ILIKE ANY(%({key})s)")
            params[key] = w_pats

        where_clause = " AND ".join(conditions)
        params["raw_q"] = cleaned_query
        sql = f"""
            SELECT *
            FROM voters
            WHERE {where_clause}
            ORDER BY similarity(name_hi, %(raw_q)s) DESC, COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC
            LIMIT %(limit)s;
        """
        return sql, params

def build_count_query(
    tenant_id: int,
    query: Optional[str] = "",
    part_no: Optional[int] = None,
    ward_no: Optional[int] = None,
    gender: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    caste: Optional[str] = None,
    category: Optional[Any] = None,
    name: Optional[str] = None,
    relative: Optional[str] = None,
    part_nos: Optional[List[int]] = None,
    ward_nos: Optional[List[int]] = None,
    villages: Optional[List[str]] = None,
    **kwargs
) -> Tuple[str, dict]:
    """Constructs parameterized count query matching Stage 1 filter predicates."""
    extra_conditions = []
    if kwargs.get("include_deleted") is False:
        extra_conditions.append("LOWER(COALESCE(status, '')) <> 'deleted'")
    params = {"tenant_id": tenant_id}

    if part_no is not None:
        extra_conditions.append("""(
            (booth_name ILIKE %(booth_pfx1)s OR booth_name ILIKE %(booth_pfx2)s)
            OR (part_no = %(part_no)s AND (booth_name IS NULL OR substring(booth_name from '^([0-9]+)\\\\s*[-:]') IS NULL))
        )""")
        params["part_no"] = part_no
        params["booth_pfx1"] = f"{part_no} - %"
        params["booth_pfx2"] = f"{part_no} %"
    elif part_nos:
        clean_parts = [int(p) for p in part_nos if str(p).isdigit()]
        if clean_parts:
            extra_conditions.append("part_no = ANY(%(part_nos)s)")
            params["part_nos"] = clean_parts

    if ward_no is not None:
        extra_conditions.append("ward_no = %(ward_no)s")
        params["ward_no"] = ward_no
    elif ward_nos:
        clean_wards = [int(w) for w in ward_nos if str(w).isdigit()]
        if clean_wards:
            extra_conditions.append("ward_no = ANY(%(ward_nos)s)")
            params["ward_nos"] = clean_wards

    if villages:
        v_list = [f"%{str(v).strip()}%" for v in villages if str(v).strip()]
        if v_list:
            extra_conditions.append("(booth_area ILIKE ANY(%(villages)s) OR booth_name ILIKE ANY(%(villages)s))")
            params["villages"] = v_list

    target_gp = kwargs.get("gp") or kwargs.get("gram_panchayat")
    if target_gp and str(target_gp).strip():
        extra_conditions.append("gram_panchayat = %(target_gp)s")
        params["target_gp"] = str(target_gp).strip()

    gender_list = resolve_gender_variants(gender)
    if gender_list:
        extra_conditions.append("gender = ANY(%(gender_list)s)")
        params["gender_list"] = gender_list

    if age_min is not None:
        extra_conditions.append("age >= %(age_min)s")
        params["age_min"] = age_min

    if age_max is not None:
        extra_conditions.append("age <= %(age_max)s")
        params["age_max"] = age_max

    if caste:
        extra_conditions.append("caste_tag ILIKE %(caste)s")
        params["caste"] = f"%{caste.strip()}%"

    if category is not None:
        extra_conditions.append("voter_category = %(category)s")
        params["category"] = category

    cleaned_name = (name or "").strip()
    cleaned_rel = (relative or "").strip()
    cleaned_query = (query or "").strip()

    if cleaned_name or cleaned_rel:
        if cleaned_name:
            name_words = [w for w in cleaned_name.split() if w]
            name_tokens = expand_search_tokens(cleaned_name)
            name_subconds = []

            if len(cleaned_name) >= 5 and any(c.isalpha() for c in cleaned_name) and any(c.isdigit() for c in cleaned_name):
                name_subconds.append("epic_no ILIKE %(name_epic)s")
                params["name_epic"] = f"%{cleaned_name}%"

            if cleaned_name.isdigit() and len(cleaned_name) <= 5:
                name_subconds.append("(sl_no = %(name_sl)s OR house_no = %(name_house)s)")
                params["name_sl"] = int(cleaned_name)
                params["name_house"] = cleaned_name

            n_vars = get_bilingual_search_variants(cleaned_name)
            n_hi_pats = n_vars.get('hi_patterns') or [f"%{cleaned_name}%"]
            n_en_pats = n_vars.get('en_patterns') or [f"%{cleaned_name}%"]
            n_all_pats = n_vars.get('all_patterns') or [f"%{cleaned_name}%"]

            params["name_hi_pats"] = n_hi_pats
            params["name_en_pats"] = n_en_pats
            params["name_all_pats"] = n_all_pats
            params["name_full"] = f"%{cleaned_name}%"
            params["name_raw"] = cleaned_name
            name_subconds.extend([
                "name_en ILIKE ANY(%(name_en_pats)s)",
                "name_hi ILIKE ANY(%(name_hi_pats)s)",
                "name_norm ILIKE ANY(%(name_en_pats)s)",
                "search_doc ILIKE ANY(%(name_all_pats)s)",
                "name_hi %% %(name_raw)s"
            ])

            if len(name_tokens) >= 2:
                tok_conds = []
                for idx, tok in enumerate(name_tokens):
                    k = f"name_tok_{idx}"
                    tok_conds.append(f"search_doc ILIKE %({k})s")
                    params[k] = f"%{tok}%"
                name_subconds.append(f"({' AND '.join(tok_conds)})")

            first_tok = name_tokens[0] if name_tokens else (name_words[0] if name_words else "")
            if first_tok and (len(name_words) >= 2 or len(name_tokens) >= 2):
                ft_vars = get_bilingual_search_variants(first_tok)
                ft_hi_pats = ft_vars.get('hi_patterns') or [f"%{first_tok}%"]
                ft_en_pats = ft_vars.get('en_patterns') or [f"%{first_tok}%"]
                params["name_ft_hi"] = ft_hi_pats
                params["name_ft_en"] = ft_en_pats
                name_subconds.extend([
                    "name_en ILIKE ANY(%(name_ft_en)s)",
                    "name_hi ILIKE ANY(%(name_ft_hi)s)",
                    "name_norm ILIKE ANY(%(name_ft_en)s)"
                ])
            extra_conditions.append(f"({' OR '.join(name_subconds)})")

        if cleaned_rel:
            rel_words = [w for w in cleaned_rel.split() if w]
            rel_tokens = expand_search_tokens(cleaned_rel)
            r_vars = get_bilingual_search_variants(cleaned_rel)
            r_hi_pats = r_vars.get('hi_patterns') or [f"%{cleaned_rel}%"]
            r_en_pats = r_vars.get('en_patterns') or [f"%{cleaned_rel}%"]
            r_all_pats = r_vars.get('all_patterns') or [f"%{cleaned_rel}%"]

            params["rel_hi_pats"] = r_hi_pats
            params["rel_en_pats"] = r_en_pats
            params["rel_all_pats"] = r_all_pats
            params["rel_full"] = f"%{cleaned_rel}%"
            params["rel_raw"] = cleaned_rel
            rel_subconds = [
                "rel_name_en ILIKE ANY(%(rel_en_pats)s)",
                "rel_name_hi ILIKE ANY(%(rel_hi_pats)s)",
                "rel_norm ILIKE ANY(%(rel_en_pats)s)",
                "search_doc ILIKE ANY(%(rel_all_pats)s)",
                "rel_name_hi %% %(rel_raw)s"
            ]
            if len(rel_tokens) >= 2:
                rel_tok_conds = []
                for idx, tok in enumerate(rel_tokens):
                    rt_vars = get_bilingual_search_variants(tok)
                    rt_pats = rt_vars.get('all_patterns') or [f"%{tok}%"]
                    k = f"rel_tok_{idx}"
                    rel_tok_conds.append(f"search_doc ILIKE ANY(%({k})s)")
                    params[k] = rt_pats
                rel_subconds.append(f"({' AND '.join(rel_tok_conds)})")

            first_rel_tok = rel_tokens[0] if rel_tokens else (rel_words[0] if rel_words else "")
            if first_rel_tok and (len(rel_words) >= 2 or len(rel_tokens) >= 2):
                fr_vars = get_bilingual_search_variants(first_rel_tok)
                fr_hi_pats = fr_vars.get('hi_patterns') or [f"%{first_rel_tok}%"]
                fr_en_pats = fr_vars.get('en_patterns') or [f"%{first_rel_tok}%"]
                params["rel_ft_hi"] = fr_hi_pats
                params["rel_ft_en"] = fr_en_pats
                rel_subconds.extend([
                    "rel_name_en ILIKE ANY(%(rel_ft_en)s)",
                    "rel_name_hi ILIKE ANY(%(rel_ft_hi)s)",
                    "rel_norm ILIKE ANY(%(rel_ft_en)s)"
                ])
            extra_conditions.append(f"({' OR '.join(rel_subconds)})")

        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions
        where_clause = " AND ".join(conditions)
        sql = f"SELECT count(*) FROM voters WHERE {where_clause};"
        return sql, params

    words = expand_search_tokens(cleaned_query)
    if not words:
        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions
    elif len(words) == 1:
        w_vars = get_bilingual_search_variants(words[0])
        w_pats = w_vars.get("all_patterns") or [f"%{words[0]}%"]
        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions + ["search_doc ILIKE ANY(%(pattern)s)"]
        params["pattern"] = w_pats
    else:
        conditions = ["tenant_id = %(tenant_id)s"] + extra_conditions
        for i, word in enumerate(words):
            w_vars = get_bilingual_search_variants(word)
            w_pats = w_vars.get("all_patterns") or [f"%{word}%"]
            key = f"p{i}"
            conditions.append(f"search_doc ILIKE ANY(%({key})s)")
            params[key] = w_pats

    where_clause = " AND ".join(conditions)
    sql = f"SELECT count(*) FROM voters WHERE {where_clause};"
    return sql, params

def count_stage1_voters(
    conn,
    tenant_id: int,
    query: Optional[str] = "",
    part_no: Optional[int] = None,
    ward_no: Optional[int] = None,
    gender: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    caste: Optional[str] = None,
    category: Optional[Any] = None,
    name: Optional[str] = None,
    relative: Optional[str] = None,
    part_nos: Optional[List[int]] = None,
    ward_nos: Optional[List[int]] = None,
    villages: Optional[List[str]] = None,
    **kwargs
) -> int:
    """Executes count query against PostgreSQL with partition pruning and filters."""
    sql, params = build_count_query(
        tenant_id,
        query,
        part_no=part_no,
        ward_no=ward_no,
        gender=gender,
        age_min=age_min,
        age_max=age_max,
        caste=caste,
        category=category,
        name=name,
        relative=relative,
        part_nos=part_nos,
        ward_nos=ward_nos,
        villages=villages,
        **kwargs
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else 0

def execute_stage1(
    conn,
    tenant_id: int,
    query: Optional[str] = "",
    candidate_limit: int = STAGE1_CANDIDATE_LIMIT,
    part_no: Optional[int] = None,
    ward_no: Optional[int] = None,
    gender: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    caste: Optional[str] = None,
    category: Optional[Any] = None,
    name: Optional[str] = None,
    relative: Optional[str] = None,
    part_nos: Optional[List[int]] = None,
    ward_nos: Optional[List[int]] = None,
    villages: Optional[List[str]] = None,
    **kwargs
) -> List[Dict[str, Any]]:
    """Executes Stage 1 against PostgreSQL with partition pruning and filters."""
    sql, params = build_stage1_query(
        tenant_id,
        query,
        candidate_limit=candidate_limit,
        part_no=part_no,
        ward_no=ward_no,
        gender=gender,
        age_min=age_min,
        age_max=age_max,
        caste=caste,
        category=category,
        name=name,
        relative=relative,
        part_nos=part_nos,
        ward_nos=ward_nos,
        villages=villages,
        **kwargs
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        candidates = [dict(zip(cols, row)) for row in rows]
        
    # If query returned 0 candidates, attempt trigram similarity fallback
    cleaned_q = (name or query or "").strip()
    if not candidates and cleaned_q:
        fb_conditions = ["tenant_id = %(tenant_id)s"]
        if kwargs.get("include_deleted") is False:
            fb_conditions.append("LOWER(COALESCE(status, '')) <> 'deleted'")
        fb_params = {"tenant_id": tenant_id, "q": cleaned_q, "limit": candidate_limit}
        if part_no is not None:
            fb_conditions.append("""(
                (booth_name ILIKE %(booth_pfx1)s OR booth_name ILIKE %(booth_pfx2)s)
                OR (part_no = %(part_no)s AND (booth_name IS NULL OR substring(booth_name from '^([0-9]+)\\\\s*[-:]') IS NULL))
            )""")
            fb_params["part_no"] = part_no
            fb_params["booth_pfx1"] = f"{part_no} - %"
            fb_params["booth_pfx2"] = f"{part_no} %"

        if ward_no is not None:
            fb_conditions.append("ward_no = %(ward_no)s")
            fb_params["ward_no"] = ward_no
        gender_list = resolve_gender_variants(gender)
        if gender_list:
            fb_conditions.append("gender = ANY(%(gender_list)s)")
            fb_params["gender_list"] = gender_list
        if age_min is not None:
            fb_conditions.append("age >= %(age_min)s")
            fb_params["age_min"] = age_min
        if age_max is not None:
            fb_conditions.append("age <= %(age_max)s")
            fb_params["age_max"] = age_max
        if caste:
            fb_conditions.append("caste_tag ILIKE %(caste)s")
            fb_params["caste"] = f"%{caste.strip()}%"
        if category is not None:
            fb_conditions.append("voter_category = %(category)s")
            fb_params["category"] = category

        fb_conditions.append("(name_hi %% %(q)s OR rel_name_hi %% %(q)s OR name_en %% %(q)s)")
        where_clause = " AND ".join(fb_conditions)
        fallback_sql = f"""
            SELECT *
            FROM voters
            WHERE {where_clause}
            ORDER BY similarity(name_hi, %(q)s) DESC
            LIMIT %(limit)s;
        """
        with conn.cursor() as cur:
            try:
                cur.execute(fallback_sql, fb_params)
                cols = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                candidates = [dict(zip(cols, row)) for row in rows]
            except Exception:
                pass
                
    return candidates

def stage2_rapidfuzz_rank(
    candidates: List[Dict[str, Any]],
    query: Optional[str] = "",
    limit: int = 50,
    name: Optional[str] = "",
    relative: Optional[str] = ""
) -> List[Dict[str, Any]]:
    """
    Ranks candidates using SIMD rapidfuzz scoring across Devanagari and English tokens.
    Handles Two-Box composite scoring (name & relative) with exact and prefix boosts.
    """
    if not candidates:
        return []

    q_name = (name or "").strip()
    q_rel = (relative or "").strip()
    q_legacy = (query or "").strip()

    if not q_name and not q_rel and not q_legacy:
        return candidates[:limit]

    # Pre-allocate dictionary lookups and cache function references for hot loop
    ratio = rapidfuzz.fuzz.token_set_ratio

    if q_name or q_rel:
        # Two-Box composite ranking
        q_name_lower = q_name.lower()
        q_rel_lower = q_rel.lower()
        q_name_folded = fold_indic_name(q_name)
        q_rel_folded = fold_indic_name(q_rel)
        q_name_phonetic = fold_phonetic_indic_name(q_name, collapse_spaces=True)
        q_rel_phonetic = fold_phonetic_indic_name(q_rel, collapse_spaces=True)
        q_name_phonetic_tokens = fold_phonetic_indic_name(q_name, collapse_spaces=False)
        q_rel_phonetic_tokens = fold_phonetic_indic_name(q_rel, collapse_spaces=False)

        name_weight = 0.55
        rel_weight = 0.45
        has_name = bool(q_name)
        has_rel = bool(q_rel)
        has_both = has_name and has_rel

        # In-place sort approach: list of tuples (score, part_no, sl_no, cand)
        scored_candidates = []

        for cand in candidates:
            cand_name_hi = cand.get("name_hi") or ""
            cand_name_en = cand.get("name_en") or ""
            cand_name_en_lower = cand_name_en.lower() if cand_name_en else ""
            cand_name_norm = cand.get("name_norm") or ""
            cand_name_norm_lower = cand_name_norm.lower() if cand_name_norm else ""

            cand_rel_hi = cand.get("rel_name_hi") or ""
            cand_rel_en = cand.get("rel_name_en") or ""
            cand_rel_en_lower = cand_rel_en.lower() if cand_rel_en else ""
            cand_rel_norm = cand.get("rel_norm") or ""
            cand_rel_norm_lower = cand_rel_norm.lower() if cand_rel_norm else ""

            epic_no = cand.get("epic_no") or ""
            epic_no_lower = epic_no.lower() if epic_no else ""

            name_score = 0.0
            if has_name:
                cand_name_hi_folded = fold_indic_name(cand_name_hi)
                cand_name_en_folded = fold_indic_name(cand_name_en)
                cand_name_hi_phonetic = fold_phonetic_indic_name(cand_name_hi, collapse_spaces=True)
                cand_name_en_phonetic = fold_phonetic_indic_name(cand_name_en, collapse_spaces=True)
                cand_name_hi_phonetic_tokens = fold_phonetic_indic_name(cand_name_hi, collapse_spaces=False)

                score_hi = ratio(q_name, cand_name_hi)
                score_en = ratio(q_name_lower, cand_name_en_lower)
                score_norm = ratio(q_name_lower, cand_name_norm_lower)
                score_phonetic = ratio(q_name_phonetic_tokens, cand_name_hi_phonetic_tokens) if q_name_phonetic_tokens and cand_name_hi_phonetic_tokens else 0.0
                name_score = max(score_hi, score_en, score_norm, score_phonetic)

                if epic_no_lower:
                    if q_name_lower == epic_no_lower:
                        name_score += 150.0
                    elif epic_no_lower.startswith(q_name_lower):
                        name_score += 80.0

                if (
                    cand_name_en_lower == q_name_lower
                    or cand_name_hi == q_name
                    or (q_name_folded and (cand_name_hi_folded == q_name_folded or cand_name_en_folded == q_name_folded))
                    or (q_name_phonetic and (cand_name_hi_phonetic == q_name_phonetic or cand_name_en_phonetic == q_name_phonetic))
                ):
                    name_score = max(name_score, 100.0) + 50.0
                elif cand_name_en_lower.startswith(q_name_lower) or q_name_lower.startswith(cand_name_en_lower):
                    name_score += 30.0
                elif cand_name_hi.startswith(q_name) or q_name.startswith(cand_name_hi):
                    name_score += 30.0
                elif q_name_folded and (cand_name_hi_folded.startswith(q_name_folded) or q_name_folded.startswith(cand_name_hi_folded)):
                    name_score += 30.0
                elif q_name_phonetic and (cand_name_hi_phonetic.startswith(q_name_phonetic) or q_name_phonetic.startswith(cand_name_hi_phonetic)):
                    name_score += 30.0

            rel_score = 0.0
            if has_rel:
                cand_rel_hi_folded = fold_indic_name(cand_rel_hi)
                cand_rel_en_folded = fold_indic_name(cand_rel_en)
                cand_rel_hi_phonetic = fold_phonetic_indic_name(cand_rel_hi, collapse_spaces=True)
                cand_rel_en_phonetic = fold_phonetic_indic_name(cand_rel_en, collapse_spaces=True)
                cand_rel_hi_phonetic_tokens = fold_phonetic_indic_name(cand_rel_hi, collapse_spaces=False)

                score_r_hi = ratio(q_rel, cand_rel_hi)
                score_r_en = ratio(q_rel_lower, cand_rel_en_lower)
                score_r_norm = ratio(q_rel_lower, cand_rel_norm_lower)
                score_r_phonetic = ratio(q_rel_phonetic_tokens, cand_rel_hi_phonetic_tokens) if q_rel_phonetic_tokens and cand_rel_hi_phonetic_tokens else 0.0
                rel_score = max(score_r_hi, score_r_en, score_r_norm, score_r_phonetic)

                if (
                    cand_rel_en_lower == q_rel_lower
                    or cand_rel_hi == q_rel
                    or (q_rel_folded and (cand_rel_hi_folded == q_rel_folded or cand_rel_en_folded == q_rel_folded))
                    or (q_rel_phonetic and (cand_rel_hi_phonetic == q_rel_phonetic or cand_rel_en_phonetic == q_rel_phonetic))
                ):
                    rel_score = max(rel_score, 100.0) + 50.0
                elif cand_rel_en_lower.startswith(q_rel_lower) or q_rel_lower.startswith(cand_rel_en_lower):
                    rel_score += 30.0
                elif cand_rel_hi.startswith(q_rel) or q_rel.startswith(cand_rel_hi):
                    rel_score += 30.0
                elif q_rel_folded and (cand_rel_hi_folded.startswith(q_rel_folded) or q_rel_folded.startswith(cand_rel_hi_folded)):
                    rel_score += 30.0
                elif q_rel_phonetic and (cand_rel_hi_phonetic.startswith(q_rel_phonetic) or q_rel_phonetic.startswith(cand_rel_hi_phonetic)):
                    rel_score += 30.0

            if has_both:
                final_score = (name_score * name_weight) + (rel_score * rel_weight)
            elif has_name:
                final_score = name_score
            else:
                final_score = rel_score

            ward_no = cand.get("ward_no") or 0
            part_no = cand.get("part_no") or 0
            sl_no = cand.get("sl_no") or 0
            sort_ward = ward_no if ward_no > 0 else part_no
            scored_candidates.append((final_score, sort_ward, part_no, sl_no, cand))

        scored_candidates.sort(key=lambda x: (-x[0], x[1], x[2], x[3]))

        result = []
        for i in range(min(limit, len(scored_candidates))):
            score, _, _, _, cand = scored_candidates[i]
            c = dict(cand)
            c["search_score"] = round(score, 2)
            result.append(c)
        return result
    else:
        # Legacy single-query ranking
        q_lower = q_legacy.lower()
        q_legacy_folded = fold_indic_name(q_legacy)
        q_legacy_phonetic = fold_phonetic_indic_name(q_legacy, collapse_spaces=True)
        q_legacy_phonetic_tokens = fold_phonetic_indic_name(q_legacy, collapse_spaces=False)
        scored_candidates = []
        for cand in candidates:
            name_hi = cand.get("name_hi") or ""
            name_en = cand.get("name_en") or ""
            name_en_lower = name_en.lower() if name_en else ""
            name_norm = cand.get("name_norm") or ""
            name_norm_lower = name_norm.lower() if name_norm else ""

            rel_hi = cand.get("rel_name_hi") or ""
            rel_en = cand.get("rel_name_en") or ""
            rel_en_lower = rel_en.lower() if rel_en else ""

            house_no = cand.get("house_no") or ""
            house_no_lower = house_no.lower() if house_no else ""

            epic_no = cand.get("epic_no") or ""
            epic_no_lower = epic_no.lower() if epic_no else ""

            name_hi_phonetic_tokens = fold_phonetic_indic_name(name_hi, collapse_spaces=False)
            score_name_hi = ratio(q_legacy, name_hi)
            if q_legacy_phonetic_tokens and name_hi_phonetic_tokens:
                score_phonetic = ratio(q_legacy_phonetic_tokens, name_hi_phonetic_tokens)
                if score_phonetic > score_name_hi:
                    score_name_hi = score_phonetic

            score_name_en = ratio(q_lower, name_en_lower)
            score_name_norm = ratio(q_lower, name_norm_lower)

            rel_hi_phonetic_tokens = fold_phonetic_indic_name(rel_hi, collapse_spaces=False)
            score_rel_hi = ratio(q_legacy, rel_hi)
            if q_legacy_phonetic_tokens and rel_hi_phonetic_tokens:
                score_rel_phonetic = ratio(q_legacy_phonetic_tokens, rel_hi_phonetic_tokens)
                if score_rel_phonetic > score_rel_hi:
                    score_rel_hi = score_rel_phonetic

            score_rel_en = ratio(q_lower, rel_en_lower)
            score_rel = score_rel_hi if score_rel_hi > score_rel_en else score_rel_en

            boost = 0.0
            if epic_no_lower:
                if q_lower == epic_no_lower:
                    boost += 150.0
                elif epic_no_lower.startswith(q_lower):
                    boost += 80.0

            if house_no_lower and q_lower == house_no_lower:
                boost += 50.0

            if name_en_lower and name_en_lower.startswith(q_lower):
                boost += 30.0
            if name_norm_lower and name_norm_lower.startswith(q_lower):
                boost += 30.0
            if name_hi and name_hi.startswith(q_legacy):
                boost += 30.0

            if q_legacy_folded:
                name_hi_folded = fold_indic_name(name_hi)
                name_en_folded = fold_indic_name(name_en)
                name_hi_phonetic = fold_phonetic_indic_name(name_hi, collapse_spaces=True)
                name_en_phonetic = fold_phonetic_indic_name(name_en, collapse_spaces=True)
                if (
                    name_hi_folded == q_legacy_folded
                    or name_en_folded == q_legacy_folded
                    or (q_legacy_phonetic and (name_hi_phonetic == q_legacy_phonetic or name_en_phonetic == q_legacy_phonetic))
                ):
                    boost += 50.0
                    score_name_hi = max(score_name_hi, 100.0)
                    score_name_en = max(score_name_en, 100.0)
                elif (
                    name_hi_folded.startswith(q_legacy_folded)
                    or q_legacy_folded.startswith(name_hi_folded)
                    or (q_legacy_phonetic and (name_hi_phonetic.startswith(q_legacy_phonetic) or q_legacy_phonetic.startswith(name_hi_phonetic)))
                ):
                    boost += 30.0

            final_score = score_name_hi
            if score_name_en > final_score: final_score = score_name_en
            if score_name_norm > final_score: final_score = score_name_norm
            score_rel_scaled = score_rel * 0.9
            if score_rel_scaled > final_score: final_score = score_rel_scaled

            final_score += boost

            ward_no = cand.get("ward_no") or 0
            part_no = cand.get("part_no") or 0
            sl_no = cand.get("sl_no") or 0
            sort_ward = ward_no if ward_no > 0 else part_no
            scored_candidates.append((final_score, sort_ward, part_no, sl_no, cand))

        scored_candidates.sort(key=lambda x: (-x[0], x[1], x[2], x[3]))

        result = []
        for i in range(min(limit, len(scored_candidates))):
            score, _, _, _, cand = scored_candidates[i]
            c = dict(cand)
            c["search_score"] = round(score, 2)
            result.append(c)
        return result

def search_voters(
    conn,
    tenant_id: int,
    query: Optional[str] = "",
    limit: int = 50,
    candidate_limit: int = STAGE1_CANDIDATE_LIMIT,
    part_no: Optional[int] = None,
    ward_no: Optional[int] = None,
    gender: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    caste: Optional[str] = None,
    category: Optional[Any] = None,
    name: Optional[str] = None,
    relative: Optional[str] = None,
    part_nos: Optional[List[int]] = None,
    ward_nos: Optional[List[int]] = None,
    villages: Optional[List[str]] = None,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Two-stage Indic search:
      Stage 1: Scoped PostgreSQL partition query (uses GIN trigram index, pruned by tenant_id) with Two-Box filters
      Stage 2: SIMD rapidfuzz composite scoring and ranking
    """
    cand_limit = max(candidate_limit, limit)
    candidates = execute_stage1(
        conn,
        tenant_id,
        query=query,
        candidate_limit=cand_limit,
        part_no=part_no,
        ward_no=ward_no,
        gender=gender,
        age_min=age_min,
        age_max=age_max,
        caste=caste,
        category=category,
        name=name,
        relative=relative,
        part_nos=part_nos,
        ward_nos=ward_nos,
        villages=villages,
        **kwargs
    )
    results = stage2_rapidfuzz_rank(candidates, query=query, limit=limit, name=name, relative=relative)
    return results

def explain_search(conn, tenant_id: int, query: str) -> dict:
    """
    Runs EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) on the Stage 1 search query
    to inspect execution plan and verify partition pruning.
    """
    sql, params = build_stage1_query(tenant_id, query, candidate_limit=STAGE1_CANDIDATE_LIMIT)
    explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
    with conn.cursor() as cur:
        cur.execute(explain_sql, params)
        plan_json = cur.fetchone()[0]
    return plan_json[0]
