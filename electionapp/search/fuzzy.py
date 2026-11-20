import re
import difflib
from typing import Tuple, List, Optional, Any

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True

    def compute_string_similarity(s1: str, s2: str) -> float:
        """
        Computes string similarity using RapidFuzz ratio normalized to 0.0 - 1.0.
        """
        if not s1 or not s2:
            return 0.0
        return fuzz.ratio(s1, s2) / 100.0

except ImportError:
    RAPIDFUZZ_AVAILABLE = False

    def compute_string_similarity(s1: str, s2: str) -> float:
        """
        Fallback similarity computation using standard library difflib.
        """
        if not s1 or not s2:
            return 0.0
        return difflib.SequenceMatcher(None, s1, s2).ratio()


def clean_search_token(s: Any) -> str:
    """Removes non-alphanumeric characters and lowercases."""
    if not s:
        return ""
    return re.sub(r'[^a-zA-Z0-9]', '', str(s)).lower()


def normalize_hindi(text: str) -> str:
    """
    Normalizes Hindi devanagari strings:
    - Removes nukta (U+093C)
    - Replaces chandrabindu with anusvara (U+0901 -> U+0902)
    - Normalizes matras and spaces
    """
    if not text:
        return ""
    # Remove nukta
    text = text.replace('\u093c', '')
    # Chandrabindu to anusvara
    text = text.replace('\u0901', '\u0902')
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_phonetic(text: str) -> str:
    """
    Phonetic normalizer for Indian voter names (v/w, b/v, ee/i, oo/u, sh/s, etc.).
    """
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r'[vw]', 'v', t)
    t = re.sub(r'ee', 'i', t)
    t = re.sub(r'oo', 'u', t)
    t = re.sub(r'sh', 's', t)
    t = re.sub(r'th', 't', t)
    t = re.sub(r'dh', 'd', t)
    t = re.sub(r'kh', 'k', t)
    t = re.sub(r'gh', 'g', t)
    t = re.sub(r'ch', 'c', t)
    t = re.sub(r'ph', 'f', t)
    t = re.sub(r'bh', 'b', t)
    t = re.sub(r'jh', 'j', t)
    t = re.sub(r'(.)\1+', r'\1', t)  # collapse duplicate letters
    return t


def fuzzy_head_match(q_clean: str, n_clean: str, threshold: float = 0.85) -> Tuple[bool, float, str]:
    """
    Tier 1H / Suggestive fuzzy head match from Rajpura search engine.
    Matches prefix slice (e.g. "vitbahadur" -> "virbahadur").
    Returns (matched, similarity_ratio, matched_slice).
    """
    if not q_clean or not n_clean or len(q_clean) < 4:
        return False, 0.0, ""

    q_len = len(q_clean)
    n_len = len(n_clean)
    if n_len < q_len:
        return False, 0.0, ""

    head_slice = n_clean[:q_len]
    ratio = compute_string_similarity(q_clean, head_slice)
    if ratio >= threshold:
        return True, ratio, head_slice
    return False, ratio, head_slice


def fuzzy_word_match(q_clean: str, words: List[str], threshold: float = 0.80) -> Tuple[bool, float, str]:
    """
    Checks if any word in candidate name matches query token within similarity threshold.
    """
    if not q_clean or not words or len(q_clean) < 4:
        return False, 0.0, ""

    q_len = len(q_clean)
    best_ratio = 0.0
    best_word = ""

    for w in words:
        w_clean = clean_search_token(w)
        if len(w_clean) >= 4 and abs(len(w_clean) - q_len) <= 2:
            ratio = compute_string_similarity(q_clean, w_clean)
            if ratio > best_ratio:
                best_ratio = ratio
                best_word = w_clean

    if best_ratio >= threshold:
        return True, best_ratio, best_word
    return False, best_ratio, best_word


def fuzzy_full_match(q_clean: str, n_clean: str, threshold: float = 0.78) -> Tuple[bool, float]:
    """
    Checks full string similarity ratio between query and candidate.
    """
    if not q_clean or not n_clean:
        return False, 0.0
    if abs(len(n_clean) - len(q_clean)) > 3:
        return False, 0.0

    ratio = compute_string_similarity(q_clean, n_clean)
    if ratio >= threshold:
        return True, ratio
    return False, ratio
