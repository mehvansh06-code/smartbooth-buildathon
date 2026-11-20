"""
electionapp/caste_auto_tagger.py

Campaign-First Dynamic Caste & Community Analysis Engine for SmartBooth.

Architecture:
  - Step 1: Campaign Voter Name & Geographic Profiling
    Analyzes the specific campaign's voter names, parent names, and district/zone
    to discover active gotras and dynamically resolve ambiguous multi-caste surnames
    (e.g. 'Choudhary' = Kalbi/Patel in Sanchore vs Jat in Nagaur/Ajmer).
  - Step 2: Campaign-Calibrated Pattern Compilation
    Builds tailored detection rules based on the campaign's actual discovered vocabulary.
  - Step 3: Multi-Pass Demographics Pipeline
    - Pass 1: Direct Gotra / Surname regex with Devanagari boundary & composite exclusions.
    - Pass 2: Homogeneous Family ID cluster propagation.
    - Pass 3: Bidirectional Kinship Lineage (Parent/Spouse/Sibling link).
    - Pass 4: Homogeneous Co-located Household Anchor (Single-Caste House).

Preserves existing manual tags by default unless force=True.
"""

import os
import json
import subprocess
import shutil
import re
from collections import defaultdict, Counter
from typing import Dict, Any, List, Tuple, Optional
import psycopg2.extras

LEARNED_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'caste_ai_learned_gotras.json')

COMMON_HONORIFICS = {
    'राम', 'लाल', 'कुमार', 'सिंह', 'देवी', 'बाई', 'कंवर', 'जी', 'चन्द', 'चंद',
    'प्रसाद', 'प्रकाश', 'दान', 'बहादुर', 'नारायण', 'नाथ', 'दत्त', 'भाई', 'बेन',
    'सिह', 'सिंग', 'सिग', 'कुंवर', 'कवर', 'कमारी', 'कुमारी', 'प्रसाध', 'राव', 'बाबू'
}

_AI_CACHE = None


def _get_ai_cache() -> Dict[str, Optional[str]]:
    global _AI_CACHE
    if _AI_CACHE is None:
        _AI_CACHE = {}
        if os.path.exists(LEARNED_CACHE_FILE):
            try:
                with open(LEARNED_CACHE_FILE, 'r', encoding='utf-8') as f:
                    _AI_CACHE = json.load(f)
            except Exception:
                _AI_CACHE = {}
    return _AI_CACHE


def _save_ai_cache():
    if _AI_CACHE is None:
        return
    try:
        os.makedirs(os.path.dirname(LEARNED_CACHE_FILE), exist_ok=True)
        with open(LEARNED_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_AI_CACHE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def classify_novel_gotras_with_claude_cli(
    candidate_tokens: List[str],
    district: str = ''
) -> Dict[str, str]:
    """
    Classifies candidate gotra/surname tokens using Claude CLI (claude -p)
    grounded in Rajasthan demographics, updating the persistent learned gotras cache.
    Returns mapping of valid {gotra: community_name}.
    """
    cache = _get_ai_cache()

    uncached = [t for t in candidate_tokens if t not in cache]
    if uncached and shutil.which('claude'):
        for i in range(0, min(len(uncached), 80), 40):
            batch = uncached[i:i + 40]
            prompt = f"""
You are an expert on Rajasthan demographics and caste/gotra classifications.
Classify the following candidate tokens extracted from voter names in Rajasthan (District: {district or 'Rajasthan'}):
Candidate tokens: {json.dumps(batch, ensure_ascii=False)}

Valid standard communities:
- "राजपूत"
- "सुथार"
- "ब्राह्मण / पुरोहित"
- "मुस्लिम"
- "मेघवाल (SC)"
- "जाट"
- "चौधरी / पटेल"
- "प्रजापत (कुम्हार)"
- "देवासी (रबारी)"
- "माली"
- "भील (ST)"
- "मीणा (ST)"
- "गुर्जर"
- "जैन / ओसवाल"
- "नाई (सैन)"
- "सोनी"
- "दर्जी"
- "लोहार"
- "चारण"
- "कालबेलिया / नाथ"
- "साधु / स्वामी / वैष्णव"
- "रावणा राजपूत"
- "कीर"
- "बागरिया"
- "आदि धर्मी (SC)", "अहेरी (SC)", "बादी (SC)", "बागरी (SC)", "बजगर (SC)", "बलाई (SC)",
  "बांसफोड़ (SC)", "बावरी (SC)", "बरगी (SC)", "बेड़िया (SC)", "बहरूपिया (SC)",
  "हरिजन", "बिड़किया (SC)", "डबगर (SC)", "धनक (SC)",
  "धनकिया (SC)", "धोबी (SC)", "ढोली (SC)", "कबीरपंथी (SC)", "गंडिया (SC)", "गरांचा (SC)",
  "गरोड़ा (SC)", "गवरिया (SC)", "गोधी (SC)", "कामड़ (SC)", "कंजर (SC)", "सांसी (SC)",
  "खंगार (SC)", "कोली / कोरी (SC)", "कूचबंद (SC)", "कोरिया (SC)", "महार (SC)",
  "महयावंशी (SC)", "माझाबी (SC)", "मांग / मातंग (SC)", "नट (SC)", "पासी (SC)",
  "रावल (SC)", "सालवी (SC)", "संटिया (SC)", "सरभंगी (SC)", "सिंगीवाला (SC)",
  "थोरी (SC)", "तिरगर (SC)", "तुरी (SC)"
- "भील मीणा (ST)", "धनका (ST)", "कथोड़ी (ST)", "कोकना (ST)", "कोली ढोर (ST)",
  "नायकड़ा (ST)", "पटेलिया (ST)", "सहरिया (ST)"
- "अहीर (यादव)", "भाट / जागा", "भरभूजा", "बंजारा", "नगरची / दमामी", "धाकड़",
  "कहार / मल्लाह", "गाडरिया / घोसी", "घांची", "तेली", "हेला", "जनवा / सिरवी",
  "जुलाहा", "कच्छी / कुशवाहा", "कलाल", "कंदेरा / पिंजारा", "खारोल", "किरार",
  "लखेरा / मणिहार", "लोधी", "महाब्राह्मण", "मेर", "मीर / लंगा-मांगणियार", "मोगिया",
  "न्यारिया", "पटवा", "रावत", "सातिया-सिंधी", "सिकलीगर", "सिरकीवाल",
  "ठठेरा / कंसारा", "तमोली", "रायसिख", "कुरैशी", "डांगी", "मेव",
  "राइन", "सिंधी मुसलमान", "मदारी / बाज़ीगर"

CRITICAL CLASSIFICATION RULES:
1. If a token is a common personal given/first name (e.g. नाथू, कालू, मुकेश, धूकल, रमेश, सुरेश), an honorific, or not an established caste/gotra, you MUST map it to null.
2. Only map to a community if it is a recognized Rajasthani gotra / clan / caste surname (e.g. काबावत -> "राजपूत", सोढा -> "राजपूत", सांखला -> "राजपूत", पंचाल -> "सुथार", सिलावट -> "सुथार", दाधीच -> "ब्राह्मण / पुरोहित").

Return ONLY a valid JSON object mapping each token to either its community name string or null. No markdown fences, no explanatory text.
"""
            try:
                proc = subprocess.run(
                    ["claude", "-p", prompt],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=35
                )
                if proc.returncode == 0 and proc.stdout:
                    raw = proc.stdout.strip()
                    if raw.startswith("```"):
                        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                    parsed = json.loads(raw)
                    for k, v in parsed.items():
                        cache[k] = v
                    _save_ai_cache()
            except Exception:
                pass

    return {t: cache[t] for t in candidate_tokens if cache.get(t)}


def extract_campaign_candidate_tokens(
    voters: List[Tuple],
    known_tokens_set: set,
    min_freq: int = 2
) -> List[Tuple[str, int]]:
    """
    Scans voter names and relative names in the campaign to extract high-frequency
    potential surname/gotra candidate tokens that are not yet in the known registry.
    """
    counter = Counter()
    for v in voters:
        _, n_hi, r_hi, *_ = v
        for text in (n_hi, r_hi):
            if not text:
                continue
            words = [w.strip() for w in re.split(r'[\s,.\-_/]+', str(text)) if len(w.strip()) >= 3]
            if not words:
                continue
            cand = words[-1]
            if cand in COMMON_HONORIFICS and len(words) >= 2:
                cand = words[-2]
            if (cand not in COMMON_HONORIFICS 
                and cand.lower() not in known_tokens_set 
                and re.match(r'^[\u0900-\u097F]{3,}$', cand)):
                counter[cand] += 1
    return [(tok, cnt) for tok, cnt in counter.most_common(60) if cnt >= min_freq]


def _make_pattern(tokens: List[str]) -> re.Pattern:
    """Builds regex pattern supporting Hindi Devanagari and English with safe boundary matching."""
    escaped = '|'.join(re.escape(t.strip()) for t in tokens if t.strip())
    return re.compile(rf'(?:^|[^\w\u0900-\u097F])(?:{escaped})(?:$|[^\w\u0900-\u097F])', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Base Unambiguous Caste Gotra Registries
#
# Cross-referenced 2026-10-14 against the official Rajasthan Social Justice &
# Empowerment Dept (SJED) SC/ST/OBC caste lists — see
# electionapp/caste_official_lists_rajasthan.json for the sourced lists and
# URLs. Entries below marked (SC)/(ST) are net-new additions taken from those
# official lists; new OBC entries follow the file's existing no-suffix style.
# Intentionally excluded: official entries that are common standalone Hindi/
# English words (Bola, Mehar, Odd, Tak, Rao, Rana) — high false-positive risk
# even with boundary-safe regex — or too obscure/low-frequency to be useful
# (Kheldar, Chungar, Ratth, Multanies, Kotwal, Chobdaar, Orphan Children).
# ---------------------------------------------------------------------------
BASE_GOTRA_REGISTRY = {
    'बिश्नोई': [
        'बिश्नोई', 'विश्नोई', 'बिश्रोई', 'विशनोई', 'खिलेरी', 'मांझू', 'मांजू',
        'कड़वासरा', 'धतरवाल', 'खीचड़', 'पोटलिया', 'जानी', 'bishnoi', 'vishnoi',
        'khilery', 'khileri', 'manju', 'kheechad', 'potliya', 'jani'
    ],
    'देवासी (रबारी)': [
        'देवासी', 'रबारी', 'रायका', 'मालधारी', 'हूण', 'करमटा', 'समीठा', 'चोथिया',
        'dewasi', 'rabari', 'raika', 'maldhari'
    ],
    'चौधरी / पटेल': [
        'कलबी', 'आंजना', 'आंजणा', 'पाटीदार', 'पटेल', 'गेंवा', 'बोकण', 'सिंघाड़ा',
        'चधिरी', 'चोधरी', 'कणबी', 'कुलमी', 'दांगी पटेल',
        'kalbi', 'anjana', 'patel', 'genwa', 'bokan', 'kanbi', 'kulmi'
    ],
    'जाट': [
        'जाट', 'गोदारा', 'पूनिया', 'सहारण', 'सारण', 'बेनीवाल', 'जाखड़', 'डूडी',
        'भादू', 'खोजा', 'सियाग', 'कसवां', 'कस्वा', 'भाकर', 'रोझ', 'भीचर', 'भिचर', 'ढाका',
        'jat', 'godara', 'punia', 'saran', 'beniwal', 'jakhar', 'dudi', 'bhadu', 'bhichar', 'dhaka'
    ],
    'राजपूत': [
        'राजपूत', 'राठौड़', 'राठौड', 'चौहान', 'भाटी', 'सोलंकी', 'परमार', 'तंवर',
        'तोमर', 'शेखावत', 'कुम्पावत', 'चाम्पावत', 'जोधा', 'सिसोदिया', 'हाड़ा',
        'झाला', 'देवड़ा', 'कंवर', 'कुंवर', 'कवर', 'बड़गुर्जर', 'प्रतिहार',
        'सोढा', 'काबावत', 'सिह', 'सिंग', 'सिग',
        'rajput', 'rathore', 'rathod', 'chauhan', 'bhati', 'solanki', 'parmar',
        'tanwar', 'shekhawat', 'kanwar', 'kunwar', 'sodha'
    ],
    'ब्राह्मण / पुरोहित': [
        'ब्राह्मण', 'ब्राहमण', 'पुरोहित', 'राजपुरोहित', 'शर्मा', 'दवे', 'जोशी',
        'तिवारी', 'पंडित', 'व्यास', 'उपाध्याय', 'शुक्ला', 'मिश्रा', 'दाधीच',
        'गौड़', 'गौड', 'पंचारिया', 'पालीवाल', 'श्रीमाली', 'बोहरा', 'ओझा',
        'त्रिवेदी', 'चांडक', 'नागर', 'भट्ट', 'मेवाडा', 'brahmin', 'purohit', 'sharma',
        'dave', 'joshi', 'tiwari', 'vyas', 'shukla', 'mishra', 'upadhyay', 'shreemali'
    ],
    'मेघवाल (SC)': [
        'मेघवाल', 'बुनकर', 'मेघवंशी', 'मेघवन्शी', 'बैरवा', 'खटीक', 'रैगर',
        'रेगर', 'जाटव', 'चमार', 'गर्ग', 'भांभी', 'चंडालिया', 'सरगरा',
        'रामदासिया', 'जीनगर', 'नायक', 'नावरिया', 'पंकज', 'सामरिया', 'meghwal', 'bunkar', 'bairwa', 'khatik',
        'raigar', 'regar', 'jatav', 'garg', 'bhambhi', 'sargara', 'nayak', 'pankaj', 'navaria'
    ],
    'प्रजापत (कुम्हार)': [
        'प्रजापत', 'प्रजापति', 'कुम्हार', 'कुम्भार', 'कुंहार', 'मारू', 'घोड़ेला', 'सुआरा',
        'prajapat', 'prajapati', 'kumhar', 'suara'
    ],
    'सुथार': [
        'सुथार', 'जांगिड़', 'जांगिड', 'खाती', 'विश्वकर्मा', 'पंचाल', 'पांचाल',
        'suthar', 'jangid', 'khati', 'panchal'
    ],
    'माली': [
        'माली', 'सैनी', 'सैण', 'सुमन', 'mali', 'saini', 'suman'
    ],
    'जैन / ओसवाल': [
        'जैन', 'ओसवाल', 'शाह', 'मेहता', 'कोठारी', 'लोढ़ा', 'लोढा', 'मोदी',
        'बोथरा', 'संचेती', 'बाफना', 'गांधी', 'डूंगरवाल', 'सुरणा', 'राठी',
        'बघेरवाल', 'बगेरवाल', 'खंडेलवाल', 'माहेश्वरी',
        'jain', 'oswal', 'shah', 'mehta', 'kothari', 'lodha', 'modi', 'bothra', 'bafna', 'bagherwal', 'khandelwal', 'maheshwari'
    ],
    'मुस्लिम': [
        'खान', 'खां', 'खा', 'अली', 'मोहम्मद', 'मुहम्मद', 'मोहंमद', 'कुरैशी',
        'मंसूरी', 'शेख', 'पठान', 'मुसलमान', 'सिलावट', 'कायमखानी', 'बानो',
        'बानू', 'बेगम', 'अंसारी', 'सिद्दीकी', 'मकरानी', 'हुसैन',
        'khan', 'ali', 'mohammed', 'qureshi', 'mansuri', 'sheikh', 'pathan', 'bano'
    ],
    'भील (ST)': [
        'भील', 'भिल', 'गरासिया', 'डामोर', 'कटारा', 'पारगी', 'तवड़',
        'bheel', 'bhil', 'garasiya', 'damor'
    ],
    'मीणा (ST)': [
        'मीणा', 'मीना', 'meena', 'mina'
    ],
    'गुर्जर': [
        'गुर्जर', 'गूजर', 'पोसवाल', 'बैंसला', 'अवाना', 'खटाना', 'गोचर', 'gurjar', 'gujar', 'gochar'
    ],
    'सिख': [
        'सिख', 'कौर', 'sikh', 'kaur'
    ],
    'कालबेलिया / नाथ': [
        'कालबेलिया', 'सपेरा', 'जोगी', 'नाथ', 'भारती', 'पुरी', 'गिरी',
        'kalbeliya', 'nath', 'puri', 'giri', 'bharti'
    ],
    'साधु / स्वामी / वैष्णव': [
        'साधु', 'स्वामी', 'गोस्वामी', 'वैष्णव', 'रामानंदी', 'बैरागी', 'जंगम',
        'sadhu', 'swami', 'goswami', 'vaishnav', 'bairagi', 'jangam'
    ],
    'नाई (सैन)': [
        'नाई', 'सैन', 'सेन', 'nai', 'sain', 'sen'
    ],
    'सोनी': [
        'सोनी', 'स्वर्णकार', 'जौहरी', 'जड़िया', 'soni', 'swarnkar', 'jadia'
    ],
    'दर्जी': [
        'दर्जी', 'पिपावत', 'टेलर', 'darji', 'tailor'
    ],
    'लोहार': [
        'लोहार', 'लुहार', 'गाडोलिया', 'lohar'
    ],
    'रावणा राजपूत': [
        'रावणा', 'कच्छावा', 'दरोगा', 'rawana', 'daroga'
    ],
    'चारण': [
        'चारण', 'बारहट', 'गढवी', 'कविराज', 'दान', 'charan'
    ],
    'कीर': [
        'कीर', 'keer'
    ],
    'बागरिया': [
        'बागरिया', 'बावरिया', 'bagariya'
    ],

    # --- Net-new SC entries from the official SJED SC list ---
    'आदि धर्मी (SC)': ['आदि धर्मी', 'आदिधर्मी', 'adi dharmi'],
    'अहेरी (SC)': ['अहेरी', 'aheri'],
    'बादी (SC)': ['बादी', 'badi'],
    'बागरी (SC)': ['बागरी', 'बागड़ी', 'bagri', 'bagdi'],
    'बजगर (SC)': ['बजगर', 'bajgar'],
    'बलाई (SC)': ['बलाई', 'balai'],
    'बांसफोड़ (SC)': ['बांसफोड़', 'बांसफोर', 'bansphor', 'bansphod'],
    'बावरी (SC)': ['बावरी', 'baori'],
    'बरगी (SC)': ['बरगी', 'वरगी', 'बिरगी', 'bargi', 'vargi', 'birgi'],
    'बेड़िया (SC)': ['बेड़िया', 'bedia'],
    'बहरूपिया (SC)': ['बहरूपिया', 'भांड', 'bahurupiya', 'bhand'],
    'हरिजन': [
        'हरिजन', 'वाल्मीकि', 'बाल्मीकि', 'बालमीकि', 'मेहतर', 'मलकाना', 'हलालखोर', 'लालबेगी', 'भंगी',
        'harijan', 'valmiki', 'balmiki', 'mehtar', 'bhangi'
    ],
    'बिड़किया (SC)': ['बिड़किया', 'bidakia'],
    'डबगर (SC)': ['डबगर', 'dabgar'],
    'धनक (SC)': ['धनक', 'धनुक', 'dhanak', 'dhanuk'],
    'धनकिया (SC)': ['धनकिया', 'dhankia'],
    'धोबी (SC)': ['धोबी', 'dhobi'],
    'ढोली (SC)': ['ढोली'],
    'कबीरपंथी (SC)': ['कबीरपंथी', 'डोम', 'डोमा', 'dome', 'dom', 'kabirpanthi'],
    'गंडिया (SC)': ['गंडिया', 'gandia'],
    'गरांचा (SC)': ['गरांचा', 'गंचा', 'garancha', 'gancha'],
    'गरोड़ा (SC)': ['गारो', 'गरूड़ा', 'गुर्दा', 'गरोड़ा', 'garo', 'garura', 'garoda'],
    'गवरिया (SC)': ['गवरिया', 'gavaria'],
    'गोधी (SC)': ['गोधी', 'godhi'],
    'कामड़ (SC)': ['कामड़', 'कामड़िया', 'kamad', 'kamadia'],
    'कंजर (SC)': ['कंजर', 'कुंजर', 'kanjar', 'kunjar'],
    'सांसी (SC)': ['सांसी', 'कपाड़िया सांसी', 'sansi'],
    'खंगार (SC)': ['खंगार', 'khangar'],
    'कोली / कोरी (SC)': ['कोली', 'कोरी', 'महावर', 'koli', 'kori', 'mahawar'],
    'कूचबंद (SC)': ['कूचबंद', 'कुचबंद', 'kuchband'],
    'कोरिया (SC)': ['कोरिया', 'koria'],
    'महार (SC)': ['महार', 'तराल', 'mahar'],
    'महयावंशी (SC)': ['महयावंशी', 'वानकर', 'mahyavanshi', 'vankar'],
    'माझाबी (SC)': ['माझाबी', 'majhabi'],
    'मांग / मातंग (SC)': ['मांग', 'मातंग', 'मांग गारोड़ी', 'mang', 'matang', 'mang garodi'],
    'नट (SC)': ['नट', 'नट्ट', 'nat', 'nut'],
    'पासी (SC)': ['पासी', 'pasi'],
    'रावल (SC)': ['रावल', 'rawal'],
    'सालवी (SC)': ['सालवी', 'salvi'],
    'संटिया (SC)': ['संटिया', 'सतिया', 'santia', 'satia'],
    'सरभंगी (SC)': ['सरभंगी', 'sarbhangi'],
    'सिंगीवाला (SC)': ['सिंगीवाला', 'singiwala'],
    'थोरी (SC)': ['थोरी', 'thori'],
    'तिरगर (SC)': ['तिरगर', 'तिरबंदा', 'tirgar', 'tirbanda'],
    'तुरी (SC)': ['तुरी', 'turi'],

    # --- Net-new ST entries from the official SJED ST list ---
    'भील मीणा (ST)': ['भील मीणा', 'bhil mina', 'bhil meena'],
    'धनका (ST)': ['धनका', 'तड़वी', 'तेतरिया', 'dhanka', 'tadvi', 'tetaria', 'valvi'],
    'कथोड़ी (ST)': ['कथोड़ी', 'katkari', 'kathodi'],
    'कोकना (ST)': ['कोकना', 'कुकना', 'kokna', 'kokni', 'kukna'],
    'कोली ढोर (ST)': ['कोली ढोर', 'टोकरे कोली', 'कोलचा', 'कोलघा', 'koli dhor', 'kolcha', 'kolgha'],
    'नायकड़ा (ST)': ['नायकड़ा', 'naikda'],
    'पटेलिया (ST)': ['पटेलिया', 'patelia'],
    'सहरिया (ST)': ['सहरिया', 'सहारिया', 'seharia', 'sahariya'],

    # --- Net-new OBC entries from the official SJED state OBC list ---
    'अहीर (यादव)': ['अहीर', 'यादव', 'ahir', 'yadav'],
    'भाट / जागा': ['बडवा', 'भाट', 'जाचक', 'जागा', 'badwa', 'bhat', 'jachak'],
    'भरभूजा': ['भरभूजा', 'bharbhuja'],
    'बंजारा': ['बंजारा', 'बलाडिया', 'लबाना', 'banjara', 'baladia', 'labana'],
    'नगरची / दमामी': ['नगरची', 'दमामी', 'nagarchi', 'damami'],
    'धाकड़': ['धाकड़', 'धाकड', 'ढकड़', 'dhakad', 'dhakar'],
    'कहार / मल्लाह': [
        'कहार', 'भोई', 'मल्लाह', 'मेहरा', 'निषाद', 'भिश्ती', 'मछुआरा',
        'kahar', 'bhoi', 'mallah', 'nishad', 'bhisti', 'machhuara'
    ],
    'गाडरिया / घोसी': ['गाडरिया', 'गाड़री', 'गायरी', 'घोसी', 'ग्वाला', 'gadaria', 'gadri', 'gayri', 'ghosi'],
    'घांची': ['घांची', 'ghanchi'],
    'तेली': ['तेली', 'teli'],
    'हेला': ['हेला', 'hela'],
    'जनवा / सिरवी': ['जनवा', 'खारडिया', 'सिरवी', 'janwa', 'khardiya', 'sirvi'],
    'जुलाहा': ['जुलाहा', 'julaha'],
    'कच्छी / कुशवाहा': ['कच्छी', 'कुशवाहा', 'शाक्य', 'kachhi', 'kushwaha', 'shakya'],
    'कलाल': ['कलाल', 'kalal'],
    'कंदेरा / पिंजारा': ['कंदेरा', 'पिंजारा', 'पिनजारा', 'kandera', 'pinjara'],
    'खारोल': ['खारोल', 'खरवाल', 'kharol', 'kharwal'],
    'किरार': ['किरार', 'किराड़', 'kirar', 'kirad'],
    'लखेरा / मणिहार': ['लखेरा', 'कचेरा', 'मणिहार', 'lakhera', 'kachera', 'manihar'],
    'लोधी': ['लोधी', 'लोधा', 'lodhi'],
    'महाब्राह्मण': ['महाब्राह्मण', 'आचार्ज', 'mahabrahman', 'acharaj'],
    'मेर': ['मेर', 'मेहरात', 'mer'],
    'मीर / लंगा-मांगणियार': ['मीर', 'लंगा', 'मांगणियार', 'धाढ़ी', 'मिरासी', 'mir', 'langa', 'manganiyar', 'dhadi', 'mirasi'],
    'मोगिया': ['मोगिया', 'mogia'],
    'न्यारिया': ['न्यारिया', 'nyaria'],
    'पटवा': ['पटवा', 'patwa'],
    'रावत': ['रावत', 'rawat'],
    'सातिया-सिंधी': ['सातिया-सिंधी', 'satiya sindhi'],
    'सिकलीगर': ['सिकलीगर', 'बंदूकसाज़', 'sikligar'],
    'सिरकीवाल': ['सिरकीवाल', 'sirkiwal'],
    'ठठेरा / कंसारा': ['ठठेरा', 'कंसारा', 'भरावा', 'thathera', 'kansara'],
    'तमोली': ['तमोली', 'तांबोली', 'tamoli', 'tamboli'],
    'रायसिख': ['रायसिख', 'raisikh'],
    'कुरैशी': ['कुरैशी', 'हलाली', 'कसाई', 'qureshi', 'kureshi', 'halali', 'kasai'],
    'डांगी': ['डांगी', 'dangi'],
    'मेव': ['मेव', 'मेयो', 'mev', 'meo'],
    'राइन': ['राइन', 'रैन', 'कुंजड़ा', 'rayeen', 'raen', 'kunjada'],
    'सिंधी मुसलमान': ['सिंधी मुसलमान', 'sindhi musalman'],
    'मदारी / बाज़ीगर': ['मदारी', 'बाजीगर', 'madari', 'bazigar'],
}

# Flattened set of all known base registry tokens for fast exclusion during candidate extraction
ALL_KNOWN_REGISTRY_TOKENS = {
    t.strip().lower() for tokens in BASE_GOTRA_REGISTRY.values() for t in tokens if t.strip()
}

# Default compiled patterns for base gotras
COMPILED_PATTERNS = [(caste, _make_pattern(tokens)) for caste, tokens in BASE_GOTRA_REGISTRY.items()]


# False-positive guard tokens (e.g. "Anjali" must not trigger "Ali")
EXCLUSIONS = {
    'मुस्लिम': [
        re.compile(r'\b(anjali|swali|bali)\b', re.IGNORECASE),
        re.compile(r'(अंजलि|गीतांजलि|बाली|दिवाली)')
    ]
}


def _norm_str(s: Any) -> str:
    """Normalize string by removing whitespace, hyphens, dots."""
    return re.sub(r'[\s\-_.]', '', str(s or '').lower())


def analyze_and_calibrate_campaign(
    tenant_id: int,
    repo,
    voters: List[Tuple],
    use_ai: bool = True
) -> Dict[str, Any]:
    """
    Step 1: Campaign Voter Name & Geographic Profiling.
    Analyzes the specific campaign's voter names and district context to:
      1. Discover candidate gotras present in this specific campaign.
      2. Resolve ambiguous multi-caste tokens (e.g. 'Choudhary' = Kalbi in Sanchore vs Jat in Nagaur/Ajmer).
      3. Classify novel unrecognized gotras using Claude CLI (Rajasthan demographics expert).
      4. Return campaign-calibrated detection patterns.
    """
    # 1. Fetch Campaign Geographic Metadata
    district = ''
    ward_name = ''
    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT config, campaign_slug FROM tenants WHERE tenant_id = %s;", (tenant_id,))
                row = cur.fetchone()
                if row and row[0]:
                    cfg = row[0]
                    election = cfg.get('election', {})
                    district = (election.get('district') or '').strip()
                    ward_name = (election.get('ward_name_en') or '').strip()
    except Exception:
        pass

    dist_lower = district.lower()

    # 2. Extract and count distinct candidate gotras inside this campaign
    combined_corpus = []
    for v in voters:
        _, n_hi, r_hi, n_en, r_en, *_ = v
        combined_corpus.append(f"{n_hi or ''} {r_hi or ''} {n_en or ''} {r_en or ''}".lower())

    corpus_text = " ".join(combined_corpus)

    # 3. Analyze presence of specific community anchor gotras in this campaign
    kalbi_anchors = ['कलबी', 'आंजना', 'आंजणा', 'गेंवा', 'बोकण', 'सिंघाड़ा', 'kalbi', 'anjana', 'genwa', 'bokan']
    jat_anchors = ['भीचर', 'सहारण', 'सारण', 'बेनीवाल', 'जाखड़', 'डूडी', 'भादू', 'खोजा', 'कसवां', 'कस्वा', 'ढाका', 'bhichar', 'saran', 'beniwal', 'jakhar', 'dudi']
    tribal_anchors = ['भील', 'गरासिया', 'डामोर', 'कटारा', 'पारगी', 'bhil', 'garasiya', 'damor']

    kalbi_count = sum(len(re.findall(re.escape(w), corpus_text)) for w in kalbi_anchors)
    jat_count = sum(len(re.findall(re.escape(w), corpus_text)) for w in jat_anchors)
    tribal_count = sum(len(re.findall(re.escape(w), corpus_text)) for w in tribal_anchors)
    bishnoi_anchors = ['बिश्नोई', 'विश्नोई', 'विशनोई', 'खिलेरी', 'मांझू', 'मांजू', 'कड़वासरा', 'धतरवाल', 'खीचड़', 'पोटलिया', 'जानी', 'जाणी']
    bishnoi_count = sum(len(re.findall(re.escape(w), corpus_text)) for w in bishnoi_anchors)

    # 4. Campaign Geographic Zone & Disambiguation Rules
    disambiguations = {}

    # Rule A: Choudhary / Patel Disambiguation
    # In Sanchore, Jalore, Sirohi OR where Kalbi gotras dominate -> Kalbi/Patel
    # In Nagaur, Bikaner, Churu, Sikar, Ajmer, Tonk, Jaipur OR where Jat gotras dominate -> Jat
    is_marwar_south = any(d in dist_lower for d in ['sanchore', 'सांचौर', 'jalore', 'जालोर', 'sirohi', 'सिरोही'])
    is_jat_zone = any(d in dist_lower for d in ['nagaur', 'नागौर', 'bikaner', 'बीकानेर', 'churu', 'चूरू', 'sikar', 'सीकर', 'jhunjhunu', 'झुंझुनू', 'ajmer', 'अजमेर', 'kekri', 'केकड़ी', 'tonk', 'टोंक'])

    if is_marwar_south or (kalbi_count > jat_count and kalbi_count > 0):
        choudhary_target = 'चौधरी / पटेल'
        choudhary_reason = f"District {district or 'Marwar South'} / Kalbi Gotras found ({kalbi_count})"
    elif is_jat_zone or (jat_count > kalbi_count and jat_count > 0):
        choudhary_target = 'जाट'
        choudhary_reason = f"District {district or 'Jat Belt'} / Jat Gotras found ({jat_count})"
    else:
        choudhary_target = 'चौधरी / पटेल'
        choudhary_reason = "Default regional anchor"

    disambiguations['चौधरी / Choudhary'] = (choudhary_target, choudhary_reason)

    # Rule B: Rana Disambiguation
    # In Southern Tribal districts (Banswara, Dungarpur, Pratapgarh, Udaipur) or tribal heavy -> Bhil (ST)
    is_tribal_south = any(d in dist_lower for d in ['banswara', 'बांसवाड़ा', 'dungarpur', 'डूंगरपुर', 'pratapgarh', 'प्रतापगढ़', 'udaipur', 'उदयपुर'])
    if is_tribal_south or tribal_count >= 15:
        rana_target = 'भील (ST)'
        rana_reason = f"District {district or 'Tribal South'} / ST frequency ({tribal_count})"
    else:
        rana_target = 'राजपूत'
        rana_reason = "Traditional Rajput clan"

    disambiguations['राणा / Rana'] = (rana_target, rana_reason)

    # 5. Build Campaign-Calibrated Token Sets
    campaign_tokens = {caste: list(tokens) for caste, tokens in BASE_GOTRA_REGISTRY.items()}

    # Choudhary / Patel & Jat calibration
    kalbi_extras = ['सणवाल', 'लोल', 'ताडा', 'फागणा']
    for t in kalbi_extras:
        if t not in campaign_tokens['चौधरी / पटेल']:
            campaign_tokens['चौधरी / पटेल'].append(t)

    jat_extras = ['कास्वा']
    for t in jat_extras:
        if t not in campaign_tokens['जाट']:
            campaign_tokens['जाट'].append(t)

    if choudhary_target == 'चौधरी / पटेल':
        campaign_tokens['चौधरी / पटेल'].extend(['चौधरी', 'chaudhary', 'choudhary'])
    else:
        campaign_tokens['जाट'].extend(['चौधरी', 'chaudhary', 'choudhary'])

    # Rana calibration
    if rana_target == 'राजपूत':
        campaign_tokens['राजपूत'].extend(['राणा', 'rana'])
    else:
        campaign_tokens['भील (ST)'].extend(['राणा', 'rana'])

    # Bishnoi regional gotra calibration (Punia, Godara, Saran in Bishnoi belt)
    if is_marwar_south or bishnoi_count >= 5:
        bishnoi_extras = [
            'पूनिया', 'पुनिया', 'punia', 'puniya',
            'गोदारा', 'godara',
            'सारण', 'saran',
            'पंवार', 'panwar',
            'सियाग', 'siyag',
            'साऊ', 'sau',
            'भादू', 'bhadu',
            'जांगू', 'jangu',
            'जाणी', 'jani',
            'डेलू', 'delu',
            'बाबल', 'babal',
            'कूकणा', 'kukna'
        ]
        for g in bishnoi_extras:
            if g not in campaign_tokens['बिश्नोई']:
                campaign_tokens['बिश्नोई'].append(g)
        for g in ['पूनिया', 'पुनिया', 'punia', 'puniya', 'गोदारा', 'godara', 'सारण', 'saran', 'भादू', 'bhadu', 'सियाग', 'siyag']:
            if g in campaign_tokens.get('जाट', []):
                campaign_tokens['जाट'].remove(g)

    # 6. AI-Assisted Novel Gotra Discovery & Classification (Claude CLI)
    ai_discovered_summary = []
    if use_ai:
        candidate_tokens = extract_campaign_candidate_tokens(voters, ALL_KNOWN_REGISTRY_TOKENS, min_freq=2)
        if candidate_tokens:
            cand_token_strs = [tok for tok, _ in candidate_tokens[:40]]
            ai_classified = classify_novel_gotras_with_claude_cli(cand_token_strs, district)
            token_freq = dict(candidate_tokens)
            for gotra, comm in ai_classified.items():
                # Geographic disambiguation takes precedence for regional multi-caste tokens
                if gotra.lower() in ('चौधरी', 'chaudhary', 'choudhary', 'राणा', 'rana'):
                    continue
                if comm in campaign_tokens:
                    if gotra not in campaign_tokens[comm]:
                        campaign_tokens[comm].append(gotra)
                    ai_discovered_summary.append({
                        'gotra': gotra,
                        'community': comm,
                        'count': token_freq.get(gotra, 0),
                        'ai_discovered': True
                    })


    # Compile patterns
    campaign_patterns = [(caste, _make_pattern(tokens)) for caste, tokens in campaign_tokens.items()]

    # Discovered Gotra Summary in this campaign
    discovered_gotras = []
    all_check_tokens = [
        ('खिलेरी', 'बिश्नोई'), ('गोदारा', 'जाट/बिश्नोई'), ('सारण', 'जाट/बिश्नोई'),
        ('राठौड़', 'राजपूत'), ('चौहान', 'राजपूत'), ('भाटी', 'राजपूत'), ('सोलंकी', 'राजपूत'),
        ('कंवर', 'राजपूत'), ('पटेल', 'चौधरी/पटेल'), ('गेंवा', 'चौधरी/पटेल'), ('बोकण', 'चौधरी/पटेल'),
        ('भीचर', 'जाट'), ('बेनीवाल', 'जाट'), ('पूनिया', 'जाट'),
        ('शर्मा', 'ब्राह्मण'), ('पुरोहित', 'ब्राह्मण'), ('दवे', 'ब्राह्मण'), ('जोशी', 'ब्राह्मण'),
        ('मेघवाल', 'मेघवाल (SC)'), ('बुनकर', 'मेघवाल (SC)'), ('भांभी', 'मेघवाल (SC)'), ('बैरवा', 'मेघवाल (SC)'),
        ('प्रजापत', 'कुम्हार'), ('कुम्हार', 'कुम्हार'), ('सुथार', 'सुथार'), ('खाती', 'सुथार'),
        ('देवासी', 'देवासी'), ('रबारी', 'देवासी'), ('खान', 'मुस्लिम'), ('भील', 'भील (ST)'),
        ('मीणा', 'मीणा (ST)'), ('गुर्जर', 'गुर्जर'), ('सोनी', 'सोनी'), ('सैन', 'नाई')
    ]
    for g_tok, comm in all_check_tokens:
        c_matches = len(re.findall(re.escape(g_tok), corpus_text))
        if c_matches > 0:
            discovered_gotras.append({'gotra': g_tok, 'community': comm, 'count': c_matches, 'ai_discovered': False})

    for item in ai_discovered_summary:
        discovered_gotras.append(item)

    discovered_gotras.sort(key=lambda x: x['count'], reverse=True)

    return {
        'tenant_id': tenant_id,
        'district': district or 'Auto-Detected',
        'ward_name': ward_name,
        'kalbi_anchor_count': kalbi_count,
        'jat_anchor_count': jat_count,
        'tribal_anchor_count': tribal_count,
        'disambiguations': disambiguations,
        'discovered_gotras': discovered_gotras[:20],
        'ai_discovered_gotras': ai_discovered_summary,
        'campaign_patterns': campaign_patterns
    }



def auto_tag_tenant(
    tenant_id: int,
    repo=None,
    force: bool = False,
    dry_run: bool = False,
    batch_size: int = 1000,
    use_ai: bool = True
) -> Dict[str, Any]:

    """
    Executes the Campaign-First 4-pass Caste Auto-Tagging pipeline on the given tenant.
    
    Args:
        tenant_id: Target tenant id
        repo: Repository instance (resolves default if None)
        force: If True, overwrites existing caste_tag values
        dry_run: If True, calculates tags without executing UPDATE
        batch_size: Batch size for DB writes
        
    Returns:
        Structured dictionary with metrics, caste breakdown, method counts,
        and campaign-specific voter name analysis profile.
    """
    if repo is None:
        from electionapp.repo import get_repository
        repo = get_repository()

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            query = """
                SELECT voter_id, name_hi, rel_name_hi, name_en, rel_name_en, 
                       ward_no, part_no, house_no, family_id, caste_tag, sl_no
                FROM voters
                WHERE tenant_id = %s
                ORDER BY part_no, sl_no;
            """
            cur.execute(query, (tenant_id,))
            voters = cur.fetchall()

    total = len(voters)
    if not total:
        return {
            "tenant_id": tenant_id,
            "total_voters": 0,
            "already_tagged": 0,
            "direct_matched": 0,
            "newly_tagged": 0,
            "total_tagged": 0,
            "coverage_pct": 0.0,
            "caste_breakdown": {},
            "method_breakdown": {},
            "campaign_profile": {},
            "dry_run": dry_run
        }

    # -----------------------------------------------------------------------
    # Step 1: Analyze Campaign Voter Names & Calibrate Disambiguations
    # -----------------------------------------------------------------------
    campaign_profile = analyze_and_calibrate_campaign(tenant_id, repo, voters, use_ai=use_ai)
    compiled_patterns = campaign_profile['campaign_patterns']


    tagged = {}
    already_tagged_ids = set()

    # Pre-populate existing tags if not forcing overwrite
    for v in voters:
        vid = v[0]
        existing_tag = v[9] if len(v) > 9 else None
        if existing_tag and str(existing_tag).strip():
            already_tagged_ids.add(vid)
            if not force:
                tag_val = str(existing_tag).strip()
                if tag_val in ('GARG', 'garg', 'मेघवाल'):
                    tag_val = 'मेघवाल (SC)'
                tagged[vid] = (tag_val, 'existing')

    # -----------------------------------------------------------------------
    # Pass 1: Direct Gotra / Surname Pattern Matching
    # -----------------------------------------------------------------------
    for v in voters:
        vid, n_hi, r_hi, n_en, r_en, _, _, _, _, _, *_ = v
        if vid in tagged:
            continue
        combined = f"{n_hi or ''} {r_hi or ''} {n_en or ''} {r_en or ''}"
        for caste, pat in compiled_patterns:
            if caste in EXCLUSIONS:
                if any(ex.search(combined) for ex in EXCLUSIONS[caste]):
                    continue
            if pat.search(combined):
                tagged[vid] = (caste, 'direct_gotra')
                break

    direct_matched_count = len(tagged) - len(already_tagged_ids if not force else set())

    # -----------------------------------------------------------------------
    # Pass 2: Homogeneous Family ID Cluster Propagation
    # -----------------------------------------------------------------------
    family_caste_counts = defaultdict(lambda: defaultdict(int))
    for v in voters:
        vid, _, _, _, _, _, p_no, _, f_id, _, *_ = v
        clean_fid = _norm_str(f_id)
        if vid in tagged and clean_fid and clean_fid not in ('none', 'null', '0', ''):
            family_caste_counts[(p_no, clean_fid)][tagged[vid][0]] += 1

    for v in voters:
        vid, _, _, _, _, _, p_no, _, f_id, _, *_ = v
        clean_fid = _norm_str(f_id)
        if vid not in tagged and clean_fid and clean_fid not in ('none', 'null', '0', ''):
            c_dict = family_caste_counts.get((p_no, clean_fid), {})
            if len(c_dict) == 1:
                dominant = list(c_dict.keys())[0]
                tagged[vid] = (dominant, 'family_id')

    # -----------------------------------------------------------------------
    # Pass 3: Strict Local Household Kinship Lineage
    # -----------------------------------------------------------------------
    # Strictly isolated within the SAME house_no and part_no.
    # Never cross-pollute across booth. No matching on elector's own first name.
    voters_by_part_house = defaultdict(list)
    for v in voters:
        vid, n_hi, r_hi, _, _, _, p_no, h_no, f_id, _, *_ = v
        clean_h = _norm_str(h_no)
        if clean_h and clean_h not in ('0', '', 'none', 'null'):
            voters_by_part_house[(p_no, clean_h)].append(v)

    for (p_no, clean_h), h_voters in voters_by_part_house.items():
        if len(h_voters) > 25:
            continue
        house_castes = set()
        house_name_to_caste = {}
        for hv in h_voters:
            h_vid = hv[0]
            if h_vid in tagged:
                caste = tagged[h_vid][0]
                house_castes.add(caste)
                n_norm = _norm_str(hv[1])
                if len(n_norm) >= 3:
                    house_name_to_caste[n_norm] = caste

        # If house already has conflicting castes among tagged, do not propagate
        if len(house_castes) > 1:
            continue

        for hv in h_voters:
            h_vid = hv[0]
            if h_vid not in tagged:
                r_norm = _norm_str(hv[2])  # relative name
                if r_norm in house_name_to_caste:
                    tagged[h_vid] = (house_name_to_caste[r_norm], 'kinship_local')

    # -----------------------------------------------------------------------
    # Pass 4: Homogeneous Co-located Household Anchor (Single Caste House)
    # -----------------------------------------------------------------------
    house_caste_counts = defaultdict(lambda: defaultdict(int))
    for v in voters:
        vid, _, _, _, _, _, p_no, h_no, _, _, *_ = v
        h_clean = _norm_str(h_no)
        if vid in tagged and h_clean and h_clean not in ('0', '', 'none', 'null'):
            house_caste_counts[(p_no, h_clean)][tagged[vid][0]] += 1

    for v in voters:
        vid, _, _, _, _, _, p_no, h_no, _, _, *_ = v
        h_clean = _norm_str(h_no)
        if vid not in tagged and h_clean and h_clean not in ('0', '', 'none', 'null'):
            c_dict = house_caste_counts.get((p_no, h_clean), {})
            if len(c_dict) == 1 and len(voters_by_part_house.get((p_no, h_clean), [])) <= 20:
                dominant = list(c_dict.keys())[0]
                tagged[vid] = (dominant, 'household_house_no')

    # -----------------------------------------------------------------------
    # Step 5: Post-Pass Sanity & Intra-Family Invariant Enforcement
    # -----------------------------------------------------------------------
    # Circuit breaker: A single family or small house MUST NEVER have split auto-tags.
    # If any family has conflicting castes, strip all non-manual auto-tags for that family.
    family_tagged = defaultdict(set)
    for v in voters:
        vid, _, _, _, _, _, p_no, _, f_id, _, *_ = v
        clean_fid = _norm_str(f_id)
        if vid in tagged and clean_fid and clean_fid not in ('none', 'null', '0', ''):
            family_tagged[(p_no, clean_fid)].add(tagged[vid][0])

    conflicted_fids = {k for k, castes in family_tagged.items() if len(castes) > 1}
    if conflicted_fids:
        for v in voters:
            vid, _, _, _, _, _, p_no, _, f_id, _, *_ = v
            clean_fid = _norm_str(f_id)
            if (p_no, clean_fid) in conflicted_fids and vid in tagged:
                caste, method = tagged[vid]
                if method != 'existing':
                    del tagged[vid]

    # Similar check for immediate houses (<= 15 electors)
    house_tagged = defaultdict(set)
    for v in voters:
        vid, _, _, _, _, _, p_no, h_no, _, _, *_ = v
        h_clean = _norm_str(h_no)
        if vid in tagged and h_clean and h_clean not in ('0', '', 'none', 'null'):
            if len(voters_by_part_house.get((p_no, h_clean), [])) <= 15:
                house_tagged[(p_no, h_clean)].add(tagged[vid][0])

    conflicted_houses = {k for k, castes in house_tagged.items() if len(castes) > 1}
    if conflicted_houses:
        for v in voters:
            vid, _, _, _, _, _, p_no, h_no, _, _, *_ = v
            h_clean = _norm_str(h_no)
            if (p_no, h_clean) in conflicted_houses and vid in tagged:
                caste, method = tagged[vid]
                if method in ('kinship_local', 'household_house_no'):
                    del tagged[vid]

    # Calculate updates
    updates_to_perform = []
    method_counts = Counter()
    caste_counts = Counter()

    for vid, (caste, method) in tagged.items():
        caste_counts[caste] += 1
        method_counts[method] += 1
        if vid not in already_tagged_ids or force:
            updates_to_perform.append((caste, vid, tenant_id))

    # When force=True, clear any stale software tags that are now unclassified
    if force:
        for vid in already_tagged_ids:
            if vid not in tagged:
                updates_to_perform.append((None, vid, tenant_id))

    newly_tagged_count = len(updates_to_perform)
    total_tagged_count = len(tagged)

    # -----------------------------------------------------------------------
    # Database Persistence
    # -----------------------------------------------------------------------
    if not dry_run and updates_to_perform:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                update_sql = """
                    UPDATE voters
                    SET caste_tag = %s
                    WHERE voter_id = %s AND tenant_id = %s;
                """
                psycopg2.extras.execute_batch(
                    cur,
                    update_sql,
                    updates_to_perform,
                    page_size=batch_size
                )
            conn.commit()

    return {
        "tenant_id": tenant_id,
        "total_voters": total,
        "already_tagged": len(already_tagged_ids),
        "direct_matched": max(0, direct_matched_count),
        "newly_tagged": newly_tagged_count,
        "total_tagged": total_tagged_count,
        "coverage_pct": round(total_tagged_count / total * 100, 1) if total else 0.0,
        "caste_breakdown": dict(caste_counts.most_common(25)),
        "method_breakdown": dict(method_counts),
        "campaign_profile": {
            "district": campaign_profile['district'],
            "ward_name": campaign_profile['ward_name'],
            "disambiguations": {k: f"{v[0]} ({v[1]})" for k, v in campaign_profile['disambiguations'].items()},
            "discovered_gotras": campaign_profile['discovered_gotras'],
            "ai_discovered_gotras": campaign_profile.get('ai_discovered_gotras', [])
        },

        "dry_run": dry_run
    }
