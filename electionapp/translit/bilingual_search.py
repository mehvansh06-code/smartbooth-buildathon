# -*- coding: utf-8 -*-
"""
bilingual_search.py
Bilingual English & Indic (Hindi / Devanagari) Search Engine Helper:
- Transliterates English / Roman search terms into Devanagari candidates
  using Google Input Tools API with thread-safe caching and offline phonetic fallback.
- Transliterates Devanagari names into English / Latin using canonical rules.
- Builds multi-script SQL search patterns for two-stage and relational family queries.
"""

import functools
import re
import requests
from typing import List, Dict, Any, Optional

from electionapp.translit.rules import transliterate_name

# Common phonetic mapping for offline English -> Hindi fallback
OFFLINE_CONSONANTS = [
    ('chh', 'छ'), ('kh', 'ख'), ('gh', 'घ'), ('ch', 'च'), ('jh', 'झ'),
    ('th', 'थ'), ('dh', 'ध'), ('ph', 'फ'), ('bh', 'भ'), ('sh', 'श'),
    ('k', 'क'), ('g', 'ग'), ('j', 'ज'), ('t', 'त'), ('d', 'द'),
    ('n', 'न'), ('p', 'प'), ('f', 'फ'), ('b', 'ब'), ('m', 'म'),
    ('y', 'य'), ('r', 'र'), ('l', 'ल'), ('v', 'व'), ('w', 'व'),
    ('s', 'स'), ('h', 'ह'), ('z', 'ज़'), ('q', 'क')
]

OFFLINE_VOWELS = [
    ('aa', 'ा'), ('ee', 'ी'), ('oo', 'ू'), ('ai', 'ै'), ('au', 'ौ'),
    ('i', 'ि'), ('u', 'ु'), ('e', 'े'), ('o', 'ो')
]


def expand_devanagari_phonetic_variants(text: str, max_variants: int = 12) -> List[str]:
    """
    Expands Devanagari text with Rajasthani & Indic phonetic equivalences:
    - ण <-> न (Retroflex nasal vs Dental nasal: गणपत <-> गनपत, नारायण <-> नारायन, जाणी <-> जानी, राणा <-> राना)
    - व <-> ब (v/w <-> b: बिश्नोई <-> विश्नोई, देवी <-> देबी, वचना <-> बचना, विकास <-> बिकास)
    - श / ष <-> स (Sibilants: सुरेश <-> सुरेस, किशोर <-> किसोर, रमेश <-> रमेस, शांति <-> सांति)
    - ध <-> घ (Dialect & OCR: लाधु <-> लाघु, राधेश्याम <-> राघेश्याम)
    - Anusvara <-> Nasal virama (चम्पालाल <-> चंपालाल, सन्तोष <-> संतोष)
    - Matras: ी <-> ि, ू <-> ु
    - Nuktas: ज़ <-> ज, फ़ <-> फ, ड़ <-> ड, ढ़ <-> ढ
    """
    t = (text or '').strip()
    if not t:
        return []
    variants = [t]

    # 1. Anusvara <-> Nasal before consonants
    if 'ं' in t:
        sub_m = re.sub(r'ं([पफबभम])', r'म्\1', t)
        if sub_m != t and sub_m not in variants:
            variants.append(sub_m)
        sub_n = re.sub(r'ं([तथदधन])', r'न्\1', t)
        if sub_n != t and sub_n not in variants:
            variants.append(sub_n)
    else:
        sub_anusvara = re.sub(r'[मनण]्', 'ं', t)
        if sub_anusvara != t and sub_anusvara not in variants:
            variants.append(sub_anusvara)

    # 2. Single consonant substitutions
    for i, ch in enumerate(t):
        alts = []
        if ch == 'ण':
            alts.append('न')
        elif ch == 'न' and i > 0:
            alts.append('ण')
        elif ch == 'व':
            alts.append('ब')
        elif ch == 'ब':
            alts.append('व')
        elif ch in ('श', 'ष'):
            alts.append('स')
        elif ch == 'स':
            alts.append('श')
        elif ch == 'ध':
            alts.append('घ')
        elif ch == 'घ':
            alts.append('ध')
        elif ch == 'ज़':
            alts.append('ज')
        elif ch == 'फ़':
            alts.append('फ')

        for a in alts:
            v = t[:i] + a + t[i+1:]
            if v not in variants:
                variants.append(v)
            if len(variants) >= max_variants:
                break
        if len(variants) >= max_variants:
            break

    # 3. Cross-combinations for top variants (e.g. विश्नोई -> बिश्नोई -> विस्नोई -> बिसनोई)
    curr_batch = list(variants)
    for base in curr_batch[1:4]:
        for i, ch in enumerate(base):
            alts = []
            if ch == 'ण':
                alts.append('न')
            elif ch == 'न' and i > 0:
                alts.append('ण')
            elif ch == 'व':
                alts.append('ब')
            elif ch == 'ब':
                alts.append('व')
            elif ch in ('श', 'ष'):
                alts.append('स')
            elif ch == 'स':
                alts.append('श')
            elif ch == 'ध':
                alts.append('घ')
            elif ch == 'घ':
                alts.append('ध')
            for a in alts:
                v = base[:i] + a + base[i+1:]
                if v not in variants:
                    variants.append(v)
                if len(variants) >= max_variants:
                    break
            if len(variants) >= max_variants:
                break
        if len(variants) >= max_variants:
            break

    # 4. Vowel matras if space permits (ी <-> ि, ू <-> ु)
    if len(variants) < max_variants:
        for i, ch in enumerate(t):
            alts = []
            if ch == 'ी':
                alts.append('ि')
            elif ch == 'ि':
                alts.append('ी')
            elif ch == 'ू':
                alts.append('ु')
            elif ch == 'ु':
                alts.append('ू')
            for a in alts:
                v = t[:i] + a + t[i+1:]
                if v not in variants:
                    variants.append(v)
                if len(variants) >= max_variants:
                    break
            if len(variants) >= max_variants:
                break

    return variants[:max_variants]


def offline_phonetic_translit(word: str) -> str:
    """Best-effort offline Latin -> Devanagari transliteration fallback."""
    w = word.strip().lower()
    if not w:
        return ""
    
    res = []
    i = 0
    n = len(w)
    prev_was_consonant = False

    while i < n:
        # Initial independent vowels
        if not prev_was_consonant:
            if w.startswith('aa', i):
                res.append('आ')
                i += 2
                continue
            elif w.startswith('a', i):
                res.append('अ')
                i += 1
                continue
            elif w.startswith('ee', i) or w.startswith('i', i):
                res.append('इ')
                i += 2 if w.startswith('ee', i) else 1
                continue
            elif w.startswith('oo', i) or w.startswith('u', i):
                res.append('उ')
                i += 2 if w.startswith('oo', i) else 1
                continue

        matched = False
        # Check consonants
        for latin, dev in OFFLINE_CONSONANTS:
            if w.startswith(latin, i):
                res.append(dev)
                i += len(latin)
                matched = True
                prev_was_consonant = True
                break
        if matched:
            continue

        # Check vowels
        for latin, dev in OFFLINE_VOWELS:
            if w.startswith(latin, i):
                res.append(dev)
                i += len(latin)
                matched = True
                prev_was_consonant = False
                break
        if matched:
            continue

        # Single inherent 'a' after consonant
        if w[i] == 'a':
            i += 1
            prev_was_consonant = False
            continue

        # Unknown character: skip or pass through
        i += 1
        prev_was_consonant = False
        
    return "".join(res)


@functools.lru_cache(maxsize=4096)
def fetch_google_translit(text: str) -> List[List[str]]:
    """
    Calls Google Input Tools API for English -> Hindi transliteration.
    Returns list of candidate lists, one per token.
    Cached in memory for maximum speed.
    """
    t = (text or '').strip()
    if not t:
        return []
        
    try:
        resp = requests.get(
            'https://inputtools.google.com/request',
            params={'text': t, 'itc': 'hi-t-i0-und', 'num': 5},
            timeout=1.5
        )
        if resp.status_code == 200:
            body = resp.json()
            if len(body) > 1 and body[0] == 'SUCCESS' and body[1]:
                candidates = []
                for item in body[1]:
                    if len(item) > 1 and item[1]:
                        candidates.append(item[1])
                return candidates
    except Exception:
        pass
        
    return []


SEARCH_STOPWORDS = {
    'ka', 'ki', 'ke', 'ko', 'se', 'me', 'mein', 'par', 'ji', 'ghar',
    'wale', 'wala', 'wali', 'dhani', 'da', 'di',
    'का', 'की', 'के', 'को', 'से', 'में', 'पर', 'जी', 'घर',
    'वाले', 'वाला', 'वाली', 'ढाणी'
}


def is_substantive_search_word(word: str) -> bool:
    """Returns True if the word is not a stopword and has substantive length (> 2)."""
    w = (word or '').strip()
    if not w:
        return False
    if w.lower() in SEARCH_STOPWORDS or w in SEARCH_STOPWORDS:
        return False
    if len(w) <= 2:
        return False
    return True


def get_bilingual_search_variants(query: str) -> Dict[str, Any]:
    """
    Expands an input search query into bilingual English & Hindi search variants.
    Filters out non-substantive stopwords and short particles from multi-word queries
    while retaining full phrases and substantive individual keywords.
    
    Returns:
    {
        'original': str,
        'primary_hi': str,
        'is_latin': bool,
        'is_devanagari': bool,
        'hi_variants': List[str],
        'en_variants': List[str],
        'hi_patterns': List[str],
        'en_patterns': List[str],
        'all_patterns': List[str]
    }
    """
    q = (query or '').strip()
    if not q:
        return {
            'original': '',
            'primary_hi': '',
            'is_latin': False,
            'is_devanagari': False,
            'hi_variants': [],
            'en_variants': [],
            'hi_patterns': [],
            'en_patterns': [],
            'all_patterns': []
        }

    is_latin = bool(re.search(r'[a-zA-Z]', q))
    is_devanagari = bool(re.search(r'[\u0900-\u097F]', q))

    hi_variants = []
    en_variants = []

    words = [w.strip() for w in q.split() if w.strip()]
    is_multi_word = len(words) > 1

    # If query contains Latin (English) characters
    if is_latin:
        en_variants.append(q)
        if is_multi_word:
            en_variants.extend([w for w in words if is_substantive_search_word(w)])
        else:
            en_variants.extend(words)

        token_candidates = fetch_google_translit(q)
        if token_candidates:
            # 1. Full phrase combined from top candidate of each token
            primary = ' '.join(opts[0] for opts in token_candidates if opts)
            if primary:
                hi_variants.append(primary)
                hi_variants.extend(expand_devanagari_phonetic_variants(primary))

            # 2. Add individual token candidates and expand phonetically (only substantive if multi-word)
            substantive_candidates = []
            for idx, opts in enumerate(token_candidates):
                w_orig = words[idx] if idx < len(words) else ''
                if is_multi_word and not is_substantive_search_word(w_orig):
                    continue
                filtered_opts = [opt for opt in opts if (not is_multi_word or is_substantive_search_word(opt))]
                if filtered_opts:
                    substantive_candidates.append(filtered_opts)
                for opt in filtered_opts[:4]:
                    hi_variants.append(opt)
                    hi_variants.extend(expand_devanagari_phonetic_variants(opt))

            # 3. If multi-word with substantive tokens, cross-combine top variants
            if len(substantive_candidates) == 2:
                top_w1 = list(dict.fromkeys(
                    [substantive_candidates[0][0]] + expand_devanagari_phonetic_variants(substantive_candidates[0][0])[:2]
                ))
                top_w2 = list(dict.fromkeys(
                    [substantive_candidates[1][0]] + expand_devanagari_phonetic_variants(substantive_candidates[1][0])[:2]
                ))
                for w1 in top_w1[:2]:
                    for w2 in top_w2[:2]:
                        hi_variants.append(f"{w1} {w2}")
        else:
            # Offline fallback
            offline_tokens = [(w, offline_phonetic_translit(w)) for w in words]
            offline_combined = ' '.join(t for _, t in offline_tokens if t)
            if offline_combined:
                hi_variants.append(offline_combined)
                hi_variants.extend(expand_devanagari_phonetic_variants(offline_combined))

            substantive_offline = []
            for w, t in offline_tokens:
                if t:
                    if is_multi_word and (not is_substantive_search_word(w) or not is_substantive_search_word(t)):
                        continue
                    substantive_offline.append(t)
                    hi_variants.append(t)
                    hi_variants.extend(expand_devanagari_phonetic_variants(t))

            if len(substantive_offline) == 2:
                top_w1 = list(dict.fromkeys([substantive_offline[0]] + expand_devanagari_phonetic_variants(substantive_offline[0])[:2]))
                top_w2 = list(dict.fromkeys([substantive_offline[1]] + expand_devanagari_phonetic_variants(substantive_offline[1])[:2]))
                for w1 in top_w1[:2]:
                    for w2 in top_w2[:2]:
                        hi_variants.append(f"{w1} {w2}")

    # If query contains Devanagari (Hindi) characters
    if is_devanagari:
        q_phonetic = expand_devanagari_phonetic_variants(q)
        hi_variants.extend(q_phonetic)

        substantive_hi_words = []
        for w in words:
            if is_multi_word and not is_substantive_search_word(w):
                continue
            substantive_hi_words.append(w)
            w_phonetic = expand_devanagari_phonetic_variants(w)
            hi_variants.extend(w_phonetic)

        if len(substantive_hi_words) == 2:
            w1_vars = expand_devanagari_phonetic_variants(substantive_hi_words[0])[:3]
            w2_vars = expand_devanagari_phonetic_variants(substantive_hi_words[1])[:3]
            for v1 in w1_vars:
                for v2 in w2_vars:
                    hi_variants.append(f"{v1} {v2}")

        # English transliteration from primary Devanagari variants
        for hv in q_phonetic[:3]:
            try:
                en_translit = transliterate_name(hv)
                if en_translit:
                    en_variants.append(en_translit)
                    if is_multi_word:
                        en_variants.extend([tw for tw in en_translit.split() if is_substantive_search_word(tw)])
                    else:
                        en_variants.extend(en_translit.split())
            except Exception:
                pass

    # If no variants collected yet (e.g. numeric, phone, or symbols), use raw query
    if not hi_variants and not en_variants and q:
        hi_variants.append(q)
        en_variants.append(q)

    # Deduplicate preserving insertion order
    hi_clean = list(dict.fromkeys(v.strip() for v in hi_variants if v and v.strip()))
    en_clean = list(dict.fromkeys(v.strip() for v in en_variants if v and v.strip()))

    # Final multi-word filter safeguard: remove standalone single-token stopwords and words with len <= 2
    if is_multi_word:
        hi_clean = [v for v in hi_clean if (' ' in v) or is_substantive_search_word(v)]
        en_clean = [v for v in en_clean if (' ' in v) or is_substantive_search_word(v)]

    primary_hi = hi_clean[0] if (hi_clean and any('\u0900' <= c <= '\u097F' for c in hi_clean[0])) else ''

    hi_patterns = [f"%{v}%" for v in hi_clean]
    en_patterns = [f"%{v}%" for v in en_clean]
    all_patterns = list(dict.fromkeys(hi_patterns + en_patterns))

    return {
        'original': q,
        'primary_hi': primary_hi,
        'is_latin': is_latin,
        'is_devanagari': is_devanagari,
        'hi_variants': hi_clean,
        'en_variants': en_clean,
        'hi_patterns': hi_patterns,
        'en_patterns': en_patterns,
        'all_patterns': all_patterns
    }
