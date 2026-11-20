"""
caste.py
Multi-Caste Census, Surname Synonyms, Family Cluster Expansion & Export Blueprint:
- /api/caste/presets — Pre-configured caste & community presets with sample surnames
- /api/caste/search, /api/caste/census — High-precision multi-caste & family cluster search
- /api/caste/export — Export caste census report with family clusters to UTF-8 CSV
"""

import io
import csv
import re
from collections import defaultdict
from typing import Optional, Any, Tuple, List
import psycopg2.extras
from flask import Blueprint, jsonify, request, send_file, session, g

from electionapp.config import load_tenant_config
from electionapp.tenancy import resolve_tenant

caste_bp = Blueprint('caste', __name__)

_DELETED_EXCLUSION = "LOWER(COALESCE(status, '')) <> 'deleted'"


def _get_tenant():
    """Resolve active tenant slug and repo using authoritative tenancy resolver."""
    return resolve_tenant()


# ===========================================================================
# CASTE CENSUS & COMMUNITY CLUSTERS
# ===========================================================================

CASTE_PRESETS = {
    "choudhary_patel": {
        "title": "Choudhary / Patel (चौधरी / कलबी / पटेल समाज)",
        "surnames": [
            "choudhary", "patel", "kalbi", "anjana", "chaudhary", "sau",
            "चौधरी", "पटेल", "कलबी", "आंजना", "साऊ"
        ]
    },
    "rajput_darbar": {
        "title": "Rajput / Darbar (राजपूत / दरबार / चौहान)",
        "surnames": [
            "singh", "kanwar", "kunwar", "chauhan", "rathore", "bhati", "solanki", "parmar",
            "सिंह", "कंवर", "कुंवर", "चौहान", "राठौड़", "भाटी", "सोलंकी", "परमार"
        ]
    },
    "dewasi_rabari": {
        "title": "Dewasi / Rabari (देवासी / रबारी / मालधारी समाज)",
        "surnames": [
            "dewasi", "rabari", "maldhari", "raika",
            "देवासी", "रबारी", "मालधारी", "रायका"
        ]
    },
    "bishnoi": {
        "title": "Bishnoi (बिश्नोई समाज)",
        "surnames": [
            "bishnoi", "vishnoi", "khilery", "dhaka", "saran", "godara", "punia", "jani",
            "बिश्नोई", "विश्नोई", "खीचड़", "सहारण", "गोदारा", "पूनिया", "जानी"
        ]
    },
    "purohit_brahmin": {
        "title": "Purohit / Brahmin (पुरोहित / ब्राह्मण समाज)",
        "surnames": [
            "purohit", "sharma", "dave", "joshi", "tiwari", "pandit", "shastri",
            "पुरोहित", "शर्मा", "दवे", "जोशी", "तिवारी", "पंडित"
        ]
    },
    "jain_oswal": {
        "title": "Jain / Oswal / Mahajan (जैन / ओसवाल समाज)",
        "surnames": [
            "jain", "oswal", "shah", "mehta", "chand", "modi", "lodha",
            "जैन", "ओसवाल", "शाह", "मेहता", "चंद", "लोढ़ा"
        ]
    },
    "sc_meghwal": {
        "title": "SC / Meghwal / Bunkar (मेघवाल / बुनकर / अनुसूचित जाति)",
        "surnames": [
            "meghwal", "bunkar", "garg", "chamar", "nayak", "ramdasia",
            "मेघवाल", "बुनकर", "गर्ग", "नायक", "रामदासिया"
        ]
    },
    "obc_artisan": {
        "title": "Prajapat / Suthar / Lohar (प्रजापत / सुथार / लोहार / कुम्हार)",
        "surnames": [
            "prajapat", "suthar", "lohar", "kumhar", "mali", "jangid",
            "प्रजापत", "सुथार", "लोहार", "कुम्हार", "माली", "जांगिड़"
        ]
    },
    "muslim": {
        "title": "Muslim (मुस्लिम समुदाय)",
        "surnames": [
            "khan", "ali", "mohammed", "qureshi", "mansuri", "sheikh",
            "खान", "अली", "मोहम्मद", "कुरैशी", "मंसूरी", "शेख"
        ]
    },
    "st_bheel": {
        "title": "ST / Bheel / Garasiya (भील / गरासिया / मीणा / भील समाज)",
        "surnames": [
            "bheel", "bhil", "garasiya", "garasia", "meena", "mina", "damor", "rana",
            "भील", "गरासिया", "मीणा", "मीना", "डामोर", "राणा"
        ]
    }
}

SURNAME_SYNONYMS = {
    # Punjab / North Communities
    "jagga": ["jagga", "जग्गा"],
    "जग्गा": ["jagga", "जग्गा"],
    "arora": ["arora", "अरोड़ा", "अरोरा"],
    "अरोड़ा": ["arora", "अरोड़ा", "अरोरा"],
    "chawla": ["chawla", "चावला"],
    "चावला": ["chawla", "चावला"],
    "grover": ["grover", "ग्रोवर"],
    "ग्रोवर": ["grover", "ग्रोवर"],
    "batra": ["batra", "बत्रा"],
    "बत्रा": ["batra", "बत्रा"],
    "chhabra": ["chhabra", "छाबड़ा"],
    "छाबड़ा": ["chhabra", "छाबड़ा"],
    "taneja": ["taneja", "तनेजा"],
    "तनेजा": ["taneja", "तनेजा"],
    "sethi": ["sethi", "सेठी"],
    "सेठी": ["sethi", "सेठी"],
    "sidhu": ["sidhu", "सिद्धू"],
    "सिद्धू": ["sidhu", "सिद्धू"],
    "dhillon": ["dhillon", "ढिल्लों"],
    "ढिल्लों": ["dhillon", "ढिल्लों"],
    "gill": ["gill", "गिल"],
    "गिल": ["gill", "गिल"],
    "sandhu": ["sandhu", "संधू"],
    "संधू": ["sandhu", "संधू"],
    "grewal": ["grewal", "ग्रेवाल"],
    "ग्रेवाल": ["grewal", "ग्रेवाल"],
    "brar": ["brar", "बराड़"],
    "बराड़": ["brar", "बराड़"],
    "mann": ["mann", "मान"],
    "मान": ["mann", "मान"],
    "bajwa": ["bajwa", "बाजवा"],
    "बाजवा": ["bajwa", "बाजवा"],
    "cheema": ["cheema", "चीमा"],
    "चीमा": ["cheema", "चीमा"],
    "saini": ["saini", "सैनी"],
    "सैनी": ["saini", "सैनी"],
    "kamboj": ["kamboj", "कंबोज"],
    "कंबोज": ["kamboj", "कंबोज"],
    "dhiman": ["dhiman", "धीमान"],
    "धीमान": ["dhiman", "धीमान"],
    "ramgarhia": ["ramgarhia", "रामगढ़िया"],
    "रामगढ़िया": ["ramgarhia", "रामगढ़िया"],

    # Common North / Central
    "sharma": ["sharma", "शर्मा"],
    "शर्मा": ["sharma", "शर्मा"],
    "gupta": ["gupta", "गुप्ता"],
    "गुप्ता": ["gupta", "गुप्ता"],
    "bansal": ["bansal", "बंसल"],
    "बंसल": ["bansal", "बंसल"],
    "goyal": ["goyal", "गोयल"],
    "गोयल": ["goyal", "गोयल"],
    "mittal": ["mittal", "मित्तल"],
    "मित्तल": ["mittal", "मित्तल"],
    "garg": ["garg", "गर्ग"],
    "गर्ग": ["garg", "गर्ग"],
    "aggarwal": ["aggarwal", "agarwal", "अग्रवाल"],
    "अग्रवाल": ["aggarwal", "agarwal", "अग्रवाल"],
    "bania": ["bania", "बनिया"],
    "बनिया": ["bania", "बनिया"],
    "pandit": ["pandit", "पंडित"],
    "पंडित": ["pandit", "पंडित"],

    # Rajasthan / Rajput / Darbar
    "singh": ["singh", "सिंह"],
    "सिंह": ["singh", "सिंह"],
    "kanwar": ["kanwar", "कंवर"],
    "कंवर": ["kanwar", "कंवर"],
    "kunwar": ["kunwar", "कुंवर"],
    "कुंवर": ["kunwar", "कुंवर"],
    "rathore": ["rathore", "राठौड़"],
    "राठौड़": ["rathore", "राठौड़"],
    "bhati": ["bhati", "भाटी"],
    "भाटी": ["bhati", "भाटी"],
    "chauhan": ["chauhan", "चौहान"],
    "चौहान": ["chauhan", "चौहान"],
    "solanki": ["solanki", "सोलंकी"],
    "सोलंकी": ["solanki", "सोलंकी"],
    "parmar": ["parmar", "परमार"],
    "परमार": ["parmar", "परमार"],
    "shekhawat": ["shekhawat", "शेखावत"],
    "शेखावत": ["shekhawat", "शेखावत"],
    "gehlot": ["gehlot", "गेहलोत"],
    "गेहलोत": ["gehlot", "गेहलोत"],
    "panwar": ["panwar", "पंवार"],
    "पंवार": ["panwar", "पंवार"],
    "rajput": ["rajput", "राजपूत"],
    "राजपूत": ["rajput", "राजपूत"],

    # Jaat / Choudhary / Patel / Bishnoi
    "choudhary": ["choudhary", "chaudhary", "चौधरी"],
    "चौधरी": ["choudhary", "chaudhary", "चौधरी"],
    "jat": ["jat", "jaat", "जाट"],
    "jaat": ["jat", "jaat", "जाट"],
    "जाट": ["jat", "jaat", "जाट"],
    "patel": ["patel", "पटेल"],
    "पटेल": ["patel", "पटेल"],
    "kalbi": ["kalbi", "कलबी"],
    "कलबी": ["kalbi", "कलबी"],
    "anjana": ["anjana", "आंजना"],
    "आंजना": ["anjana", "आंजना"],
    "godara": ["godara", "गोदारा"],
    "गोदारा": ["godara", "गोदारा"],
    "punia": ["punia", "पूनिया", "पूनियां"],
    "पूनिया": ["punia", "पूनिया", "पूनियां"],
    "saran": ["saran", "सहारण", "सारण"],
    "सहारण": ["saran", "सहारण", "सारण"],
    "dhaka": ["dhaka", "ढाका"],
    "ढाका": ["dhaka", "ढाका"],
    "sau": ["sau", "साऊ"],
    "साऊ": ["sau", "साऊ"],
    "bishnoi": ["bishnoi", "vishnoi", "बिश्नोई", "विश्नोई"],
    "vishnoi": ["bishnoi", "vishnoi", "बिश्नोई", "विश्नोई"],
    "बिश्नोई": ["bishnoi", "vishnoi", "बिश्नोई", "विश्नोई"],
    "विश्नोई": ["bishnoi", "vishnoi", "बिश्नोई", "विश्नोई"],
    "kheechad": ["kheechad", "khilery", "खीचड़", "खिलेरी"],
    "खीचड़": ["kheechad", "khilery", "खीचड़", "खिलेरी"],
    "jani": ["jani", "जानी"],
    "जानी": ["jani", "जानी"],

    # Dewasi / Rabari
    "dewasi": ["dewasi", "देवासी"],
    "देवासी": ["dewasi", "देवासी"],
    "rabari": ["rabari", "रबारी"],
    "रबारी": ["rabari", "रबारी"],
    "raika": ["raika", "रायका"],
    "रायका": ["raika", "रायका"],
    "maldhari": ["maldhari", "मालधारी"],
    "मालधारी": ["maldhari", "मालधारी"],

    # Brahmin / Purohit
    "purohit": ["purohit", "पुरोहित"],
    "पुरोहित": ["purohit", "पुरोहित"],
    "dave": ["dave", "दवे"],
    "दवे": ["dave", "दवे"],
    "joshi": ["joshi", "जोशी"],
    "जोशी": ["joshi", "जोशी"],
    "tiwari": ["tiwari", "तिवारी"],
    "तिवारी": ["tiwari", "तिवारी"],
    "vyas": ["vyas", "व्यास"],
    "व्यास": ["vyas", "व्यास"],
    "trivedi": ["trivedi", "त्रिवेदी"],
    "त्रिवेदी": ["trivedi", "त्रिवेदी"],
    "shastri": ["shastri", "शास्त्री"],
    "शास्त्री": ["shastri", "शास्त्री"],
    "brahmin": ["brahmin", "ब्राह्मण"],
    "ब्राह्मण": ["brahmin", "ब्राह्मण"],

    # Jain / Oswal / Mahajan
    "jain": ["jain", "जैन"],
    "जैन": ["jain", "जैन"],
    "oswal": ["oswal", "ओसवाल"],
    "ओसवाल": ["oswal", "ओसवाल"],
    "mehta": ["mehta", "मेहता"],
    "मेहता": ["mehta", "मेहता"],
    "lodha": ["lodha", "लोढ़ा"],
    "लोढ़ा": ["lodha", "लोढ़ा"],
    "shah": ["shah", "शाह"],
    "शाह": ["shah", "शाह"],
    "modi": ["modi", "मोदी"],
    "मोदी": ["modi", "मोदी"],
    "maheshwari": ["maheshwari", "माहेश्वरी"],
    "माहेश्वरी": ["maheshwari", "माहेश्वरी"],

    # Artisans & OBC
    "prajapat": ["prajapat", "kumhar", "प्रजापत", "कुम्हार"],
    "kumhar": ["prajapat", "kumhar", "प्रजापत", "कुम्हार"],
    "प्रजापत": ["prajapat", "kumhar", "प्रजापत", "कुम्हार"],
    "कुम्हार": ["prajapat", "kumhar", "प्रजापत", "कुम्हार"],
    "suthar": ["suthar", "सुथार", "जांगिड़"],
    "सुथार": ["suthar", "सुथार", "जांगिड़"],
    "jangid": ["jangid", "जांगिड़"],
    "जांगिड़": ["jangid", "जांगिड़"],
    "lohar": ["lohar", "लोहार"],
    "लोहार": ["lohar", "लोहार"],
    "sain": ["sain", "sen", "सैन", "सेन", "नाई"],
    "सैन": ["sain", "sen", "सैन", "सेन", "नाई"],
    "mali": ["mali", "माली"],
    "माली": ["mali", "माली"],
    "khati": ["khati", "खाती", "suthar", "सुथार", "jangid", "जांगिड़"],
    "खाती": ["khati", "खाती", "suthar", "सुथार", "jangid", "जांगिड़"],
    "khatik": ["khatik", "खटीक"],
    "खटीक": ["khatik", "खटीक"],
    "khatri": ["khatri", "खत्री"],
    "खत्री": ["khatri", "खत्री"],
    "daroga": ["daroga", "दरोगा", "rajput", "राजपूत"],
    "दरोगा": ["daroga", "दरोगा", "rajput", "राजपूत"],
    "panara": ["panara", "पनारा"],
    "पनारा": ["panara", "पनारा"],
    "bhambhi": ["bhambhi", "bambhi", "भाम्भी", "मेघवाल"],
    "भाम्भी": ["bhambhi", "bambhi", "भाम्भी", "मेघवाल"],
    "bisayati": ["bisayati", "बिसायती"],
    "बिसायती": ["bisayati", "बिसायती"],
    "bhalot": ["bhalot", "भालोट"],
    "भालोट": ["bhalot", "भालोट"],
    "ghaswa": ["ghaswa", "घसवा"],
    "घसवा": ["ghaswa", "घसवा"],
    "bagariya": ["bagariya", "bagaria", "बागरिया"],
    "बागरिया": ["bagariya", "bagaria", "बागरिया"],
    "soni": ["soni", "सोनी", "swarnkar", "स्वर्णकार"],
    "सोनी": ["soni", "सोनी", "swarnkar", "स्वर्णकार"],
    "sadhu": ["sadhu", "साधू", "साधु"],
    "साधू": ["sadhu", "साधू", "साधु"],
    "साधु": ["sadhu", "साधू", "साधु"],
    "dundi": ["dundi", "डुंडी"],
    "डुंडी": ["dundi", "डुंडी"],
    "kalal": ["kalal", "कलाल"],
    "कलाल": ["kalal", "कलाल"],
    "chad": ["chad", "चाड"],
    "चाड": ["chad", "चाड"],
    "riyad": ["riyad", "रियाड"],
    "रियाड": ["riyad", "रियाड"],
    "darji": ["darji", "darzi", "दर्जी"],
    "दर्जी": ["darji", "darzi", "दर्जी"],
    "ghuwar": ["ghuwar", "घुवार"],
    "घुवार": ["ghuwar", "घुवार"],
    "dholi": ["dholi", "ढोली"],
    "ढोली": ["dholi", "ढोली"],
    "keer": ["keer", "kir", "कीर"],
    "कीर": ["keer", "kir", "कीर"],
    "babar": ["babar", "बाबर"],
    "बाबर": ["babar", "बाबर"],
    "bhadu": ["bhadu", "भादू"],
    "भादू": ["bhadu", "भादू"],
    "dhobi": ["dhobi", "धोबी"],
    "धोबी": ["dhobi", "धोबी"],
    "sod": ["sod", "सोड़", "सोड"],
    "सोड़": ["sod", "सोड़", "सोड"],
    "kalbeliya": ["kalbeliya", "kalbelia", "कालबेलिया"],
    "कालबेलिया": ["kalbeliya", "kalbelia", "कालबेलिया"],
    "chhipa": ["chhipa", "छीपा"],
    "छीपा": ["chhipa", "छीपा"],
    "harijan": ["harijan", "हरीजन", "हरिजन"],
    "हरीजन": ["harijan", "हरीजन", "हरिजन"],
    "regar": ["regar", "raigar", "रेगर"],
    "रेगर": ["regar", "raigar", "रेगर"],
    "nat": ["nat", "नट"],
    "नट": ["nat", "नट"],
    "bhichar": ["bhichar", "भीचर", "जाट"],
    "भीचर": ["bhichar", "भीचर", "जाट"],

    # SC / Dalit / Meghwal
    "meghwal": ["meghwal", "मेघवाल"],
    "मेघवाल": ["meghwal", "मेघवाल"],
    "bairwa": ["bairwa", "बैरवा"],
    "बैरवा": ["bairwa", "बैरवा"],
    "bunkar": ["bunkar", "बुनकर"],
    "बुनकर": ["bunkar", "बुनकर"],
    "valmiki": ["valmiki", "वाल्मीकि", "वाल्मीक"],
    "वाल्मीकि": ["valmiki", "वाल्मीकि", "वाल्मीक"],
    "nayak": ["nayak", "नायक"],
    "नायक": ["nayak", "नायक"],
    "dalit": ["dalit", "दलित"],
    "दलित": ["dalit", "दलित"],

    # Muslim
    "khan": ["khan", "खान"],
    "खान": ["khan", "खान"],
    "ali": ["ali", "अली"],
    "अली": ["ali", "अली"],
    "mohammed": ["mohammed", "mohammad", "मोहम्मद"],
    "मोहम्मद": ["mohammed", "mohammad", "मोहम्मद"],
    "qureshi": ["qureshi", "कुरैशी"],
    "कुरैशी": ["qureshi", "कुरैशी"],
    "mansuri": ["mansuri", "मंसूरी"],
    "मंसूरी": ["mansuri", "मंसूरी"],
    "sheikh": ["sheikh", "शेख"],
    "शेख": ["sheikh", "शेख"],
    "pathan": ["pathan", "पठान"],
    "पठान": ["pathan", "पठान"],

    # ST / Bheel / Tribal Communities
    "bheel": ["bheel", "bhil", "भील"],
    "bhil": ["bheel", "bhil", "भील"],
    "भील": ["bheel", "bhil", "भील"],
    "garasiya": ["garasiya", "garasia", "गरासिया"],
    "garasia": ["garasiya", "garasia", "गरासिया"],
    "गरासिया": ["garasiya", "garasia", "गरासिया"],
    "meena": ["meena", "mina", "मीणा", "मीना"],
    "mina": ["meena", "mina", "मीणा", "मीना"],
    "मीणा": ["meena", "mina", "मीणा", "मीना"],
    "मीना": ["meena", "mina", "मीणा", "मीना"],
    "damor": ["damor", "डामोर"],
    "डामोर": ["damor", "डामोर"]
}

COMPOSITE_EXCLUSIONS = {
    "ali": [
        re.compile(r'\banjali\b', re.IGNORECASE),
        re.compile(r'\bswali\b', re.IGNORECASE),
        re.compile(r'अंजलि'),
        re.compile(r'गीतांजलि')
    ]
}


def _first_name_token(name):
    """First whitespace-delimited token of a name, lowercased. Used to compare
    a confirmed match's own given name against a relative's declared
    father/husband name field."""
    name = (name or '').strip()
    if not name:
        return ''
    return re.split(r'\s+', name)[0].strip().lower()


def _household_token_pattern(tok):
    if not tok:
        return None
    if tok.isascii():
        return re.compile(r'\b' + re.escape(tok) + r'\b', re.IGNORECASE)
    return re.compile(r'(?:^|[\s,.\-_/])' + re.escape(tok) + r'(?:$|[\s,.\-_/])')


def _is_declared_family_member(m_dict, confirmed_rows, compiled_patterns):
    """
    A voter sharing a family_id/house number with a direct caste match is only
    counted as a real family member if the record itself declares a link —
    never because they merely share an address. Evidence accepted, in order:
      1. Their own name/relative-name text matches a searched surname token.
      2. Their declared father/husband name matches a confirmed match's own
         first name (they are that confirmed match's child/spouse).
      2b. A confirmed match's declared father/husband name matches their own
         first name (they are that confirmed match's parent/spouse).
      3. They declare the same father/husband name as a confirmed match
         (siblings), even if neither carries the searched surname.
    Living at the same house number with none of the above is not evidence.
    """
    m_n_en = m_dict.get('name_en') or ''
    m_n_hi = m_dict.get('name_hi') or ''
    m_r_en = m_dict.get('rel_name_en') or ''
    m_r_hi = m_dict.get('rel_name_hi') or ''

    for _, pat in compiled_patterns:
        if pat.search(m_n_en) or pat.search(m_n_hi) or pat.search(m_r_en) or pat.search(m_r_hi):
            return True, 'Family Member (Name Match)'

    cand_first_en = _first_name_token(m_n_en)
    cand_first_hi = _first_name_token(m_n_hi)
    cand_rel_en = m_r_en.strip().lower()
    cand_rel_hi = m_r_hi.strip().lower()

    for c in confirmed_rows:
        c_first_en = _first_name_token(c.get('name_en'))
        c_first_hi = _first_name_token(c.get('name_hi'))
        c_rel_en = (c.get('rel_name_en') or '').strip().lower()
        c_rel_hi = (c.get('rel_name_hi') or '').strip().lower()

        for tok in (c_first_en, c_first_hi):
            if len(tok) < 3:
                continue
            pat = _household_token_pattern(tok)
            if pat and (pat.search(m_r_en) or pat.search(m_r_hi)):
                return True, 'Family Member (Declared Parent/Spouse Link)'

        for tok in (cand_first_en, cand_first_hi):
            if len(tok) < 3:
                continue
            pat = _household_token_pattern(tok)
            if pat and (pat.search(c_rel_en) or pat.search(c_rel_hi)):
                return True, 'Family Member (Declared Parent/Spouse Link)'

        if (cand_rel_en and c_rel_en and cand_rel_en == c_rel_en) or \
           (cand_rel_hi and c_rel_hi and cand_rel_hi == c_rel_hi):
            return True, 'Family Member (Shared Declared Parent)'

    return False, None


def _parse_location_filter(ward_arg: Any = None, part_arg: Any = None) -> Tuple[Optional[int], Optional[int], Optional[List[int]], Optional[str], Optional[List[int]]]:
    from electionapp.config import parse_location_filter_extended
    return parse_location_filter_extended(ward_arg, part_arg)


UNTAGGED_CASTE_SYNONYMS = {
    'अन्य / गैर-टैग', 'अन्य/गैर-टैग', 'अन्य', 'गैर-टैग', 'अनिर्धारित', 'अनिर्धारित / गैर-टैग',
    'untagged', 'other', 'unknown', 'none', 'null'
}


def _is_untagged_caste_query(val: Optional[str]) -> bool:
    if not val:
        return False
    return str(val).strip().lower() in UNTAGGED_CASTE_SYNONYMS


def _search_caste_family_clusters(
    tid: int, 
    caste_tags=None, 
    include_family: bool = True, 
    part_no: Optional[int] = None, 
    ward_no: Optional[int] = None, 
    repo = None,
    caste_filter: Optional[str] = None,
    text_query: Optional[str] = None
):
    ward_no, part_no, part_nos, gp, ward_nos = _parse_location_filter(ward_no, part_no)

    caste_filter = (caste_filter or '').strip()
    text_query = (text_query or '').strip()

    is_untagged_filter = False
    if _is_untagged_caste_query(caste_filter):
        is_untagged_filter = True
        caste_filter = 'अन्य / गैर-टैग'
        if _is_untagged_caste_query(text_query) or text_query == caste_filter:
            text_query = ''
    elif not caste_filter and _is_untagged_caste_query(text_query):
        is_untagged_filter = True
        caste_filter = 'अन्य / गैर-टैग'
        text_query = ''
    elif not caste_filter and not text_query and isinstance(caste_tags, str) and _is_untagged_caste_query(caste_tags):
        is_untagged_filter = True
        caste_filter = 'अन्य / गैर-टैग'
        text_query = ''

    # Determine tokens for caste filtering
    caste_tokens = set()
    if caste_filter and not is_untagged_filter:
        c_low = caste_filter.lower()
        caste_tokens.add(c_low)
        caste_tokens.add(caste_filter)
        if c_low in CASTE_PRESETS:
            caste_tokens.update(CASTE_PRESETS[c_low]['surnames'])
            caste_tokens.add(CASTE_PRESETS[c_low]['title'].lower())
        if c_low in SURNAME_SYNONYMS:
            caste_tokens.update(SURNAME_SYNONYMS[c_low])

    # Determine tokens for free-text search (surnames, names)
    search_terms = []
    if text_query:
        search_terms = [t.strip().lower() for t in re.split(r'[,|;/\n]+', text_query) if t.strip()]
    elif not caste_filter and not is_untagged_filter and caste_tags:
        if isinstance(caste_tags, str):
            search_terms = [t.strip().lower() for t in re.split(r'[,|;/\n]+', caste_tags) if t.strip()]
        elif caste_tags:
            search_terms = [str(t).strip().lower() for t in caste_tags if str(t).strip()]

    expanded_tokens = set(search_terms)
    for t in search_terms:
        if t in CASTE_PRESETS:
            expanded_tokens.update(CASTE_PRESETS[t]['surnames'])
        if t in SURNAME_SYNONYMS:
            expanded_tokens.update(SURNAME_SYNONYMS[t])

    # Target caste tokens is either explicit caste filter tokens or expanded search tokens
    target_caste_tokens = set() if is_untagged_filter else (caste_tokens if caste_filter else expanded_tokens)

    compiled_patterns = []
    if not is_untagged_filter:
        for tok in target_caste_tokens:
            if tok.isascii():
                pat = re.compile(r'\b' + re.escape(tok) + r'\b', re.IGNORECASE)
            else:
                pat = re.compile(r'(?:^|[\s,.\-_/])' + re.escape(tok) + r'(?:$|[\s,.\-_/])')
            compiled_patterns.append((tok, pat))

    active_exclusions = []
    if not is_untagged_filter:
        for key, excl_list in COMPOSITE_EXCLUSIONS.items():
            if key in target_caste_tokens:
                active_exclusions.extend(excl_list)

    if repo is None:
        from electionapp.tenancy import resolve_tenant
        _, repo = resolve_tenant()

    ward_caste_summary = {}
    direct_rows = []
    direct_ids = set()

    with repo.get_connection() as conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        except (TypeError, Exception):
            cur = conn.cursor()

        with cur:
            if gp and ward_no is not None and str(ward_no).isdigit():
                w_int = int(ward_no)
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND gram_panchayat = %s AND ward_no = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, gp, w_int))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }
            elif gp:
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND gram_panchayat = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, gp))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }
            elif ward_no is not None and str(ward_no).isdigit() and part_no is not None and str(part_no).isdigit():
                w_int = int(ward_no)
                p_int = int(part_no)
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND ward_no = %s AND part_no = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, w_int, p_int))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }
            elif ward_no is not None and str(ward_no).isdigit():
                w_int = int(ward_no)
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND (ward_no = %s OR (COALESCE(ward_no, 0) = 0 AND part_no = %s)) AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, w_int, w_int))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }
            elif part_no is not None and str(part_no).isdigit():
                p_int = int(part_no)
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND part_no = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, p_int))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }
            elif ward_nos:
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND ward_no = ANY(%s) AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, ward_nos))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }
            elif part_nos:
                sum_sql = """
                    SELECT COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') AS caste, COUNT(*) AS count
                    FROM voters
                    WHERE tenant_id = %s AND part_no = ANY(%s) AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """
                cur.execute(sum_sql, (tid, part_nos))
                ward_caste_summary = {
                    str(row['caste'] if isinstance(row, dict) else row[0]): int(row['count'] if isinstance(row, dict) else row[1])
                    for row in cur.fetchall()
                }

            like_patterns = [f"%{tok}%" for tok in expanded_tokens if len(tok) >= 2]
            caste_tag_list = list(expanded_tokens)

            sql = """
                SELECT 
                    voter_id, legacy_id, ward_no, part_no, sl_no, name_en, name_hi, rel_name_en, rel_name_hi,
                    rel_type, house_no, epic_no, age, gender, contact_phone, voter_category,
                    is_voted, family_id, caste_tag
                FROM voters
                WHERE tenant_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
            """
            params = [tid]

            if gp and ward_no is not None:
                sql += " AND gram_panchayat = %s AND ward_no = %s"
                params.extend([gp, ward_no])
            elif gp:
                sql += " AND gram_panchayat = %s"
                params.append(gp)
            elif ward_no is not None and part_no is not None:
                sql += " AND ward_no = %s AND part_no = %s"
                params.extend([ward_no, part_no])
            elif ward_no is not None:
                w_int = ward_no
                sql += " AND (ward_no = %s OR (COALESCE(ward_no, 0) = 0 AND part_no = %s))"
                params.extend([w_int, w_int])
            elif part_no is not None:
                sql += " AND part_no = %s"
                params.append(part_no)
            elif ward_nos:
                sql += " AND ward_no = ANY(%s)"
                params.append(ward_nos)
            elif part_nos:
                sql += " AND part_no = ANY(%s)"
                params.append(part_nos)


            if is_untagged_filter:
                sql += " AND (caste_tag IS NULL OR length(trim(caste_tag)) = 0 OR lower(trim(caste_tag)) IN ('अन्य / गैर-टैग', 'अन्य/गैर-टैग', 'अन्य', 'गैर-टैग', 'अनिर्धारित', 'untagged', 'other', 'unknown', 'none', 'null'))"
                if text_query:
                    sql += """ AND (
                        name_en ILIKE %s OR name_hi ILIKE %s
                        OR rel_name_en ILIKE %s OR rel_name_hi ILIKE %s
                        OR epic_no ILIKE %s OR house_no ILIKE %s
                        OR sl_no::text = %s
                    )"""
                    t_pat = f"%{text_query}%"
                    params.extend([t_pat, t_pat, t_pat, t_pat, t_pat, t_pat, text_query])
            elif caste_filter:
                caste_tag_matches = list(target_caste_tokens)
                sql += """ AND (
                    lower(trim(COALESCE(caste_tag, ''))) = lower(%s)
                    OR lower(trim(COALESCE(caste_tag, ''))) = ANY(%s)
                    OR caste_tag ILIKE %s
                )"""
                params.extend([caste_filter, caste_tag_matches, f"%{caste_filter}%"])

                if text_query:
                    sql += """ AND (
                        name_en ILIKE %s OR name_hi ILIKE %s
                        OR rel_name_en ILIKE %s OR rel_name_hi ILIKE %s
                        OR epic_no ILIKE %s OR house_no ILIKE %s
                        OR sl_no::text = %s
                    )"""
                    t_pat = f"%{text_query}%"
                    params.extend([t_pat, t_pat, t_pat, t_pat, t_pat, t_pat, text_query])
            elif expanded_tokens:
                like_patterns = [f"%{tok}%" for tok in expanded_tokens if len(tok) >= 2]
                caste_tag_list = list(expanded_tokens)
                if like_patterns:
                    sql += """ AND (
                        lower(trim(COALESCE(caste_tag, ''))) = ANY(%s)
                        OR caste_tag ILIKE ANY(%s)
                        OR (
                            (caste_tag IS NULL OR length(trim(caste_tag)) = 0 OR lower(trim(caste_tag)) IN ('अन्य / गैर-टैग', 'other', 'unknown'))
                            AND (
                                name_en ILIKE ANY(%s)
                                OR rel_name_en ILIKE ANY(%s)
                                OR name_hi ILIKE ANY(%s)
                                OR rel_name_hi ILIKE ANY(%s)
                            )
                        )
                    )"""
                    params.extend([caste_tag_list, like_patterns, like_patterns, like_patterns, like_patterns, like_patterns])
                else:
                    sql += " AND (lower(caste_tag) = ANY(%s) OR caste_tag ILIKE ANY(%s))"
                    params.extend([caste_tag_list, [f"%{tok}%" for tok in expanded_tokens]])
            else:
                # When no query is specified:
                # If neither ward_no nor part_no is specified, only fetch voters with a caste_tag to keep result manageable.
                # If a specific ward_no or part_no is selected, fetch all voters in that ward/part so full ward bifurcation and all ward electors are returned!
                if ward_no is None and part_no is None and not ward_nos and not part_nos:
                    sql += " AND caste_tag IS NOT NULL AND length(trim(caste_tag)) > 0"

            sql += " ORDER BY COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC"
            cur.execute(sql, tuple(params))
            candidates = [dict(r) for r in cur.fetchall()]

            for r in candidates:
                n_en = r.get('name_en') or ''
                n_hi = r.get('name_hi') or ''
                r_en = r.get('rel_name_en') or ''
                r_hi = r.get('rel_name_hi') or ''
                raw_c_tag = (r.get('caste_tag') or '').strip()
                c_tag = raw_c_tag.lower()

                matched_tok = None
                is_tagged_match = False
                if is_untagged_filter:
                    matched_tok = 'अन्य / गैर-टैग'
                    is_tagged_match = False
                elif caste_filter:
                    matched_tok = raw_c_tag or caste_filter
                    is_tagged_match = True
                elif not expanded_tokens:
                    matched_tok = raw_c_tag or 'अन्य / गैर-टैग'
                    is_tagged_match = bool(raw_c_tag)
                else:
                    # Free-text search with expanded_tokens
                    if c_tag and (
                        c_tag in target_caste_tokens 
                        or any(c_tag == tok or tok in c_tag or pat.search(c_tag) for tok, pat in compiled_patterns)
                    ):
                        matched_tok = raw_c_tag
                        is_tagged_match = True
                    elif not c_tag or c_tag in ('अन्य / गैर-टैग', 'other', 'unknown'):
                        # Only untagged electors can match by surname/name!
                        for tok, pat in compiled_patterns:
                            # Relative name has surname
                            if pat.search(r_en) or pat.search(r_hi):
                                matched_tok = tok
                                break
                            # Multi-word name has surname (not a single given name)
                            words_en = [w for w in re.split(r'\s+', n_en) if w]
                            words_hi = [w for w in re.split(r'\s+', n_hi) if w]
                            if (len(words_en) > 1 and any(pat.search(w) for w in words_en[1:])) or \
                               (len(words_hi) > 1 and any(pat.search(w) for w in words_hi[1:])):
                                matched_tok = tok
                                break
                    else:
                        # c_tag is present and belongs to another caste -> CANNOT MATCH!
                        matched_tok = None

                if not matched_tok:
                    continue

                is_excluded = False
                for excl_pat in active_exclusions:
                    if (excl_pat.search(n_en) or excl_pat.search(n_hi)) and not any(
                        p.search(r_en) or p.search(r_hi) or
                        (p.search(n_en) and not excl_pat.search(n_en)) or
                        (p.search(n_hi) and not excl_pat.search(n_hi))
                        for _, p in compiled_patterns
                    ):
                        is_excluded = True
                        break

                if is_excluded:
                    continue

                v_id = r.get('legacy_id') or r.get('voter_id')
                r['id'] = v_id
                r['sl_no'] = r.get('sl_no')
                r['serial_no'] = r.get('sl_no')
                r['ward_no'] = r.get('ward_no') or (r.get('part_no') if (ward_no is not None and ward_no == r.get('part_no')) else None)
                r['rel_name_hi'] = r.get('rel_name_hi')
                r['relative_name_hi'] = r.get('rel_name_hi')
                r['rel_name_en'] = r.get('rel_name_en')
                r['relative_name_en'] = r.get('rel_name_en')
                r['phone'] = r.get('contact_phone') or ''
                r['contact_phone'] = r.get('contact_phone') or ''
                r['is_voted'] = 1 if r.get('is_voted') else 0
                if is_untagged_filter:
                    r['caste_match_type'] = 'untagged'
                    r['match_tag'] = '⚪ अनिर्धारित / गैर-टैग'
                elif not expanded_tokens:
                    r['caste_match_type'] = 'tagged' if is_tagged_match else 'ward_elector'
                    r['match_tag'] = f"Updated Caste: {r.get('caste_tag')}" if is_tagged_match else 'वार्ड मतदाता'
                else:
                    r['caste_match_type'] = 'tagged' if is_tagged_match else 'direct'
                    r['match_tag'] = f"Updated Caste: {r.get('caste_tag')}" if is_tagged_match else 'Direct Surname / Name Match'
                r['is_expanded'] = False
                direct_rows.append(r)
                direct_ids.add(v_id)

            all_results = list(direct_rows)
            expanded_rows = []

            # Step 2: Family cluster expansion
            if include_family and direct_rows and not is_untagged_filter:
                family_ids_by_part = defaultdict(set)
                confirmed_by_family = defaultdict(list)
                for r in direct_rows:
                    p_no = r['part_no']
                    f_id = r.get('family_id')
                    if f_id and str(f_id).strip() and str(f_id).strip().lower() not in ('none', 'null', '0', ''):
                        clean_fid = str(f_id).strip()
                        family_ids_by_part[p_no].add(clean_fid)
                        confirmed_by_family[(p_no, clean_fid)].append(r)

                for p_no, f_ids in family_ids_by_part.items():
                    exp_sql = """
                        SELECT
                            voter_id, legacy_id, ward_no, part_no, sl_no, name_en, name_hi, rel_name_en, rel_name_hi,
                            rel_type, house_no, epic_no, age, gender, contact_phone, voter_category,
                            is_voted, family_id, caste_tag
                        FROM voters
                        WHERE tenant_id = %s AND part_no = %s AND family_id = ANY(%s)
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                    """
                    exp_params = [tid, p_no, list(f_ids)]
                    if ward_no is not None and str(ward_no).isdigit():
                        w_int = int(ward_no)
                        exp_sql += " AND (ward_no = %s OR (COALESCE(ward_no, 0) = 0 AND part_no = %s))"
                        exp_params.extend([w_int, w_int])

                    exp_sql += " ORDER BY sl_no"
                    cur.execute(exp_sql, tuple(exp_params))
                    for m in cur.fetchall():
                        m_dict = dict(m)
                        mv_id = m_dict.get('legacy_id') or m_dict.get('voter_id')
                        if mv_id in direct_ids:
                            continue

                        # Guard: Family member MUST NOT have a conflicting caste tag!
                        m_raw_c_tag = (m_dict.get('caste_tag') or '').strip()
                        m_c_tag = m_raw_c_tag.lower()
                        if target_caste_tokens and m_c_tag and m_c_tag not in ('अन्य / गैर-टैग', 'other', 'unknown'):
                            is_compat = (
                                m_c_tag in target_caste_tokens
                                or any(m_c_tag == tok or tok in m_c_tag or pat.search(m_c_tag) for tok, pat in compiled_patterns)
                            )
                            if not is_compat:
                                continue

                        m_fid = str(m_dict.get('family_id') or '').strip()
                        confirmed_rows = confirmed_by_family.get((p_no, m_fid), [])
                        verified, reason = _is_declared_family_member(m_dict, confirmed_rows, compiled_patterns)
                        if not verified:
                            continue

                        m_dict['id'] = mv_id
                        m_dict['sl_no'] = m_dict.get('sl_no')
                        m_dict['serial_no'] = m_dict.get('sl_no')
                        m_dict['ward_no'] = m_dict.get('ward_no') or (m_dict.get('part_no') if (ward_no is not None and ward_no == m_dict.get('part_no')) else None)
                        m_dict['rel_name_hi'] = m_dict.get('rel_name_hi')
                        m_dict['relative_name_hi'] = m_dict.get('rel_name_hi')
                        m_dict['rel_name_en'] = m_dict.get('rel_name_en')
                        m_dict['relative_name_en'] = m_dict.get('rel_name_en')
                        m_dict['phone'] = m_dict.get('contact_phone') or ''
                        m_dict['contact_phone'] = m_dict.get('contact_phone') or ''
                        m_dict['is_voted'] = 1 if m_dict.get('is_voted') else 0
                        m_dict['caste_match_type'] = 'family_cluster'
                        m_dict['is_expanded'] = True
                        m_dict['match_tag'] = reason
                        direct_ids.add(mv_id)
                        expanded_rows.append(m_dict)

                all_results.extend(expanded_rows)

    all_results.sort(key=lambda x: (
        x.get('ward_no') if (x.get('ward_no') and x.get('ward_no') > 0) else x.get('part_no', 0),
        x.get('part_no', 0),
        str(x.get('house_no', '')),
        x.get('sl_no', 0)
    ))

    total_count = len(all_results)
    direct_count = len(direct_rows)
    family_expanded_count = len(expanded_rows)

    ward_breakdown = defaultdict(int)
    gender_breakdown = defaultdict(int)
    sentiment_breakdown = defaultdict(int)
    voted_count = 0
    phone_count = 0

    has_wards = any((r.get('ward_no') and r.get('ward_no') > 0) for r in all_results)
    for r in all_results:
        key = r.get('ward_no') if (r.get('ward_no') and r.get('ward_no') > 0) else r['part_no']
        ward_breakdown[key] += 1
        g_val = r.get('gender')
        g_label = 'Female' if g_val in ('स्त्री', 'Female', 'F', 'महिला') else 'Male'
        gender_breakdown[g_label] += 1
        cat = str(r.get('voter_category') or 'unclassified')
        sentiment_breakdown[cat] += 1
        if r.get('is_voted') == 1:
            voted_count += 1
        if r.get('contact_phone'):
            phone_count += 1

    caste_breakdown = defaultdict(int)
    for r in all_results:
        c_tag = (r.get('caste_tag') or '').strip()
        if c_tag:
            caste_breakdown[c_tag] += 1
        else:
            caste_breakdown['अन्य / गैर-टैग'] += 1

    sorted_caste_breakdown = dict(sorted(caste_breakdown.items(), key=lambda x: x[1], reverse=True))

    return {
        "success": True,
        "query_tags": ['अन्य / गैर-टैग'] if is_untagged_filter else list(expanded_tokens),
        "total_voters": total_count,
        "direct_matches": direct_count,
        "family_expanded": family_expanded_count,
        "voted_count": voted_count,
        "turnout_pct": round((voted_count / total_count * 100), 1) if total_count > 0 else 0.0,
        "phone_count": phone_count,
        "phone_pct": round((phone_count / total_count * 100), 1) if total_count > 0 else 0.0,
        "ward_breakdown": dict(ward_breakdown),
        "booth_breakdown": dict(ward_breakdown),
        "booth_counts": dict(ward_breakdown),
        "caste_breakdown": sorted_caste_breakdown,
        "ward_caste_breakdown": ward_caste_summary,
        "is_ward_view": bool(has_wards or (ward_no is not None)),
        "gender_breakdown": dict(gender_breakdown),
        "sentiment_breakdown": dict(sentiment_breakdown),
        "voters": all_results
    }


def _export_caste_list_to_csv(tid: int, slug: str, caste_tags=None, include_family: bool = True, part_no: Optional[int] = None, ward_no: Optional[int] = None, repo = None, caste_filter: Optional[str] = None, text_query: Optional[str] = None):
    res = _search_caste_family_clusters(tid, caste_tags=caste_tags, include_family=include_family, part_no=part_no, ward_no=ward_no, repo=repo, caste_filter=caste_filter, text_query=text_query)
    if not res.get('success'):
        return None

    voters = res.get('voters', [])
    output = io.StringIO()
    writer = csv.writer(output)

    cfg = load_tenant_config(slug)
    ward_title = cfg.get('election', {}).get('ward_name_en') or f"AC-{slug.title()}"

    writer.writerow([f"{ward_title} - Community / Caste Census Report ({', '.join(res.get('query_tags', []))})"])
    writer.writerow([f"Total Voters: {res['total_voters']} (Direct: {res['direct_matches']}, Family Expanded: {res['family_expanded']})"])
    writer.writerow([])

    headers = [
        "Ward No", "Part No", "Sl. No", "Name (English)", "Name (Hindi)",
        "Relation Type", "Relative Name", "House No", "EPIC No",
        "Age", "Gender", "Phone", "Cluster Match Type"
    ]
    writer.writerow(headers)

    for v in voters:
        if v.get('caste_match_type') == 'tagged':
            match_type = f"Tagged Caste ({v.get('caste_tag') or ''})"
        elif v.get('caste_match_type') == 'direct':
            match_type = "Direct Surname"
        else:
            match_type = "Family"
        rel_name = v.get('rel_name_hi') or v.get('rel_name_en') or ''
        is_female = v.get('gender') in ['स्त्री', 'Female', 'F', 'महिला']
        gender_hi = 'स्त्री' if is_female else 'पुरुष'
        rel_type = v.get('rel_type') or 'पिता'
        phone = v.get('contact_phone') or v.get('phone') or ''
        clean_phone = re.sub(r'\D', '', str(phone)) if phone else ''

        writer.writerow([
            v.get('ward_no') or v.get('part_no'),
            v.get('part_no'),
            v.get('sl_no'),
            v.get('name_en') or '',
            v.get('name_hi') or '',
            rel_type,
            rel_name,
            v.get('house_no') or '',
            v.get('epic_no') or '',
            v.get('age') or '',
            gender_hi,
            clean_phone,
            match_type
        ])

    return output.getvalue()


@caste_bp.route('/api/caste/presets', methods=['GET'])
def api_caste_presets():
    """Returns authentic caste and community preset definitions along with tenant's tagged castes."""
    tenant, repo = _get_tenant()
    req_tenant = request.args.get('tenant')
    if req_tenant:
        user_role = session.get('role') or getattr(g, 'current_user', {}).get('role')
        if user_role in ('super_admin', 'admin', 'operator') or session.get('tenant_slug') in ('*', req_tenant) or not session.get('user_id'):
            tenant = req_tenant
    tid, _ = repo._resolve_tenant(tenant)

    short_titles = {
        "choudhary_patel": "चौधरी / पटेल",
        "rajput_darbar": "राजपूत / दरबार",
        "dewasi_rabari": "देवासी / रबारी",
        "bishnoi": "बिश्नोई",
        "purohit_brahmin": "ब्राह्मण / पुरोहित",
        "jain_oswal": "जैन / ओसवाल",
        "sc_meghwal": "मेघवाल / बुनकर",
        "obc_artisan": "प्रजापत / सुथार / कुम्हार",
        "muslim": "मुस्लिम",
        "st_bheel": "भील / गरासिया / मीणा",
    }

    presets = []
    for k, v in CASTE_PRESETS.items():
        presets.append({
            'key': k,
            'title': v['title'],
            'short_title': short_titles.get(k, v['title'].split('(')[0].strip()),
            'search_query': k,
            'sample_surnames': v['surnames'][:4]
        })

    tagged_castes = []
    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') as caste_label,
                        COUNT(*) as voter_count
                    FROM voters
                    WHERE tenant_id = %s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY 1
                    ORDER BY voter_count DESC, caste_label ASC
                    LIMIT 60;
                """, (tid,))
                for row in cur.fetchall():
                    tagged_castes.append({
                        'tag': str(row[0]).strip(),
                        'count': int(row[1])
                    })
    except Exception:
        pass

    return jsonify({
        'success': True,
        'presets': presets,
        'tagged_castes': tagged_castes
    })


@caste_bp.route('/api/caste/analytics', methods=['GET', 'POST'])
def api_caste_analytics():
    """
    Returns pre-aggregated caste & community demographic analysis.
    Supports whole campaign breakdown or filtered by ward_no / part_no.
    """
    tenant, repo = _get_tenant()
    req_tenant = request.args.get('tenant') or ((request.get_json(silent=True) or {}).get('tenant') if request.is_json else None)
    if req_tenant:
        user_role = session.get('role') or getattr(g, 'current_user', {}).get('role')
        if user_role in ('super_admin', 'admin', 'operator') or session.get('tenant_slug') in ('*', req_tenant) or not session.get('user_id'):
            tenant = req_tenant
    tid, slug = repo._resolve_tenant(tenant)

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or request.form or {}
        ward_no = data.get('ward_no') or data.get('ward')
        part_no = data.get('part_no') or data.get('part')
    else:
        ward_no = request.args.get('ward_no') or request.args.get('ward')
        part_no = request.args.get('part_no') or request.args.get('part')

    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                where_clause = "WHERE tenant_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'"
                params = [tid]
                w_filter = None
                if ward_no is not None and str(ward_no).strip() != '' and str(ward_no) != 'ALL':
                    w_filter = int(ward_no) if str(ward_no).isdigit() else str(ward_no)
                    where_clause += " AND (ward_no = %s OR (COALESCE(ward_no, 0) = 0 AND part_no = %s))"
                    params.extend([w_filter, w_filter])
                elif part_no is not None and str(part_no).strip() != '' and str(part_no) != 'ALL':
                    p_filter = int(part_no) if str(part_no).isdigit() else str(part_no)
                    where_clause += " AND part_no = %s"
                    params.append(p_filter)

                # 1. Summary metrics
                cur.execute(f"""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(CASE WHEN caste_tag IS NOT NULL AND length(trim(caste_tag)) > 0 THEN 1 END) as tagged,
                        COUNT(CASE WHEN contact_phone IS NOT NULL AND length(trim(contact_phone)) >= 10 THEN 1 END) as phones,
                        COUNT(CASE WHEN is_voted IS TRUE OR is_voted::text = '1' THEN 1 END) as voted
                    FROM voters
                    {where_clause}
                """, tuple(params))
                row = cur.fetchone()
                total_voters = int(row['total'] if isinstance(row, dict) else row[0])
                tagged_voters = int(row['tagged'] if isinstance(row, dict) else row[1])
                phones_count = int(row['phones'] if isinstance(row, dict) else row[2])
                voted_count = int(row['voted'] if isinstance(row, dict) else row[3])

                # 2. Caste-wise distribution with phone reach
                cur.execute(f"""
                    SELECT 
                        COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') as caste,
                        COUNT(*) as count,
                        COUNT(CASE WHEN contact_phone IS NOT NULL AND length(trim(contact_phone)) >= 10 THEN 1 END) as phone_count
                    FROM voters
                    {where_clause}
                    GROUP BY 1
                    ORDER BY 2 DESC, 1 ASC;
                """, tuple(params))
                caste_rows = cur.fetchall()

                # 3. Ward-by-ward distribution per caste (for whole-campaign scope)
                ward_dist_by_caste = defaultdict(list)
                if not w_filter:
                    cur.execute("""
                        SELECT 
                            COALESCE(NULLIF(trim(caste_tag), ''), 'अन्य / गैर-टैग') as caste,
                            COALESCE(NULLIF(ward_no, 0), part_no) as ward,
                            COUNT(*) as count
                        FROM voters
                        WHERE tenant_id = %s
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                        GROUP BY 1, 2
                        ORDER BY 1 ASC, 3 DESC;
                    """, (tid,))
                    for w_row in cur.fetchall():
                        c_name = str(w_row['caste'] if isinstance(w_row, dict) else w_row[0]).strip()
                        w_num = int(w_row['ward'] if isinstance(w_row, dict) else w_row[1])
                        cnt = int(w_row['count'] if isinstance(w_row, dict) else w_row[2])
                        ward_dist_by_caste[c_name].append({'ward': w_num, 'count': cnt})

                communities = []
                for cr in caste_rows:
                    c_name = str(cr['caste'] if isinstance(cr, dict) else cr[0]).strip()
                    cnt = int(cr['count'] if isinstance(cr, dict) else cr[1])
                    p_cnt = int(cr['phone_count'] if isinstance(cr, dict) else cr[2])
                    pct = round((cnt / total_voters * 100), 1) if total_voters > 0 else 0.0
                    p_pct = round((p_cnt / cnt * 100)) if cnt > 0 else 0
                    is_untagged = (c_name == 'अन्य / गैर-टैग')

                    communities.append({
                        'caste': c_name,
                        'count': cnt,
                        'pct': pct,
                        'phone_count': p_cnt,
                        'phone_pct': p_pct,
                        'is_untagged': is_untagged,
                        'wards': ward_dist_by_caste.get(c_name, [])
                    })

                # 4. Ward summaries (for whole-campaign scope)
                ward_summaries = []
                if not w_filter:
                    cur.execute("""
                        SELECT 
                            COALESCE(NULLIF(ward_no, 0), part_no) as ward,
                            COUNT(*) as total_voters,
                            COUNT(CASE WHEN contact_phone IS NOT NULL AND length(trim(contact_phone)) >= 10 THEN 1 END) as phones
                        FROM voters
                        WHERE tenant_id = %s
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                        GROUP BY 1
                        ORDER BY 1 ASC;
                    """, (tid,))
                    for wr in cur.fetchall():
                        w_num = int(wr['ward'] if isinstance(wr, dict) else wr[0])
                        w_tot = int(wr['total_voters'] if isinstance(wr, dict) else wr[1])
                        w_phn = int(wr['phones'] if isinstance(wr, dict) else wr[2])
                        ward_summaries.append({
                            'ward_no': w_num,
                            'total_voters': w_tot,
                            'phone_count': w_phn,
                            'phone_pct': round((w_phn / w_tot * 100)) if w_tot > 0 else 0
                        })

                return jsonify({
                    'success': True,
                    'scope': f'WARD_{w_filter}' if w_filter else 'ALL',
                    'filter_ward': w_filter,
                    'total_voters': total_voters,
                    'tagged_voters': tagged_voters,
                    'unclassified_voters': total_voters - tagged_voters,
                    'tagged_pct': round((tagged_voters / total_voters * 100), 1) if total_voters > 0 else 0.0,
                    'phone_count': phones_count,
                    'phone_pct': round((phones_count / total_voters * 100), 1) if total_voters > 0 else 0.0,
                    'voted_count': voted_count,
                    'turnout_pct': round((voted_count / total_voters * 100), 1) if total_voters > 0 else 0.0,
                    'communities': communities,
                    'ward_summaries': ward_summaries
                })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@caste_bp.route('/api/caste/search', methods=['GET', 'POST'])
@caste_bp.route('/api/caste/census', methods=['GET', 'POST'])
def api_caste_search():
    """High-precision multi-caste & family cluster search with tenant isolation."""
    tenant, repo = _get_tenant()
    req_tenant = request.args.get('tenant') or ((request.get_json(silent=True) or {}).get('tenant') if request.is_json else None)
    if req_tenant:
        user_role = session.get('role') or getattr(g, 'current_user', {}).get('role')
        if user_role in ('super_admin', 'admin', 'operator') or session.get('tenant_slug') in ('*', req_tenant) or not session.get('user_id'):
            tenant = req_tenant
    tid, _ = repo._resolve_tenant(tenant)

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or request.form or {}
        caste = (data.get('caste') or data.get('caste_tag') or data.get('caste_filter') or '').strip()
        tags = (data.get('tags') or data.get('query') or data.get('q') or '').strip()
        include_family = data.get('include_family', True)
        part_no = data.get('part_no') or data.get('part')
        ward_no = data.get('ward_no') or data.get('ward')
    else:
        caste = (request.args.get('caste') or request.args.get('caste_tag') or request.args.get('caste_filter') or '').strip()
        tags = (request.args.get('tags') or request.args.get('query') or request.args.get('q') or '').strip()
        include_family = request.args.get('include_family', '1').lower() in ['1', 'true', 'yes']
        part_no = request.args.get('part_no') or request.args.get('part')
        ward_no = request.args.get('ward_no') or request.args.get('ward')

    if not caste and _is_untagged_caste_query(tags):
        caste = 'अन्य / गैर-टैग'
        tags = ''
    elif _is_untagged_caste_query(caste):
        caste = 'अन्य / गैर-टैग'
        if _is_untagged_caste_query(tags) or tags == caste:
            tags = ''

    res = _search_caste_family_clusters(
        tid, 
        caste_tags=tags, 
        include_family=include_family, 
        part_no=part_no, 
        ward_no=ward_no, 
        repo=repo,
        caste_filter=caste,
        text_query=tags
    )
    return jsonify(res)


@caste_bp.route('/api/caste/export', methods=['GET', 'POST'])
def api_caste_export():
    """Exports multi-caste census report with family clusters to UTF-8 BOM CSV."""
    tenant, repo = _get_tenant()
    tid, slug = repo._resolve_tenant(tenant)

    if request.method == 'POST':
        data = request.get_json(force=True, silent=True) or request.form or {}
        caste = (data.get('caste') or data.get('caste_tag') or data.get('caste_filter') or '').strip()
        tags = (data.get('tags') or data.get('query') or data.get('q') or '').strip()
        include_family = data.get('include_family', True)
        part_no = data.get('part_no') or data.get('part')
        ward_no = data.get('ward_no') or data.get('ward')
    else:
        caste = (request.args.get('caste') or request.args.get('caste_tag') or request.args.get('caste_filter') or '').strip()
        tags = (request.args.get('tags') or request.args.get('query') or request.args.get('q') or '').strip()
        include_family = request.args.get('include_family', '1').lower() in ['1', 'true', 'yes']
        part_no = request.args.get('part_no') or request.args.get('part')
        ward_no = request.args.get('ward_no') or request.args.get('ward')

    if not caste and _is_untagged_caste_query(tags):
        caste = 'अन्य / गैर-टैग'
        tags = ''
    elif _is_untagged_caste_query(caste):
        caste = 'अन्य / गैर-टैग'
        if _is_untagged_caste_query(tags) or tags == caste:
            tags = ''

    csv_data = _export_caste_list_to_csv(
        tid, 
        slug, 
        caste_tags=tags, 
        include_family=include_family, 
        part_no=part_no, 
        ward_no=ward_no, 
        repo=repo,
        caste_filter=caste,
        text_query=tags
    )
    if not csv_data:
        return jsonify({'success': False, 'error': 'No data to export'}), 400

    bio = io.BytesIO(csv_data.encode('utf-8-sig'))
    display_tag = caste or tags or 'census'
    tag_slug = re.sub(r'[^a-zA-Z0-9]+', '_', display_tag)[:30] or 'census'
    return send_file(
        bio,
        mimetype='text/csv; charset=utf-8',
        as_attachment=True,
        download_name=f'{slug.title()}_Caste_Report_{tag_slug}.csv'
    )


@caste_bp.route('/api/caste/auto-tag', methods=['POST'])
def api_caste_auto_tag():
    """
    Triggers automated software-side caste & community tagging for the active tenant
    using the foolproof family-anchored consensus engine.
    Accepts JSON or Form data: { "force": bool, "dry_run": bool, "tenant": str }
    """
    tenant, repo = _get_tenant()
    req_tenant = request.args.get('tenant') or ((request.get_json(silent=True) or {}).get('tenant') if request.is_json else None)
    if req_tenant:
        user_role = session.get('role') or getattr(g, 'current_user', {}).get('role')
        if user_role in ('super_admin', 'admin', 'operator') or session.get('tenant_slug') in ('*', req_tenant) or not session.get('user_id'):
            tenant = req_tenant
    tid, slug = repo._resolve_tenant(tenant)

    data = request.get_json(force=True, silent=True) or request.form or {}
    force = bool(data.get('force', False))
    dry_run = bool(data.get('dry_run', False))

    from electionapp.caste_auto_tagger import auto_tag_tenant
    try:
        res = auto_tag_tenant(tid, repo=repo, force=force, dry_run=dry_run, use_ai=False)
        return jsonify({
            'success': True,
            'tenant': slug,
            'data': res
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
