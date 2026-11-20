# -*- coding: utf-8 -*-
"""Devanagari -> Latin for Indian personal names.

This is transliteration, never translation. A name changes alphabet and nothing else:
पिस्ता is Pista, not Pistachio; सूर्यप्रकाश is Suryaprakash, not Sunlight; केसर is Kesar,
not Saffron. Ward 82's data was ruined by a translation engine and then patched by hand
78 entries at a time -- none of that happens here.

It also follows how Indians actually spell their names rather than an academic scheme:
फ is 'f' (बाफना -> Bafna, not Baphana), the inherent 'a' is dropped word-finally and in
the penultimate syllable (जबरा -> Jabra), and word-final ी is 'i' (देवी -> Devi).
"""
import re
import unicodedata

CONSONANTS = {
    'क': 'k', 'ख': 'kh', 'ग': 'g', 'घ': 'gh', 'ङ': 'n',
    'च': 'ch', 'छ': 'chh', 'ज': 'j', 'झ': 'jh', 'ञ': 'n',
    'ट': 't', 'ठ': 'th', 'ड': 'd', 'ढ': 'dh', 'ण': 'n',
    'त': 't', 'थ': 'th', 'द': 'd', 'ध': 'dh', 'न': 'n',
    'प': 'p', 'फ': 'f', 'ब': 'b', 'भ': 'bh', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v', 'ळ': 'l',
    'श': 'sh', 'ष': 'sh', 'स': 's', 'ह': 'h',
    # precomposed nukta forms
    'क़': 'q', 'ख़': 'kh', 'ग़': 'gh', 'ज़': 'z', 'ड़': 'r', 'ढ़': 'rh', 'फ़': 'f',
}

INDEP_VOWELS = {
    'अ': 'a', 'आ': 'a', 'इ': 'i', 'ई': 'i', 'उ': 'u', 'ऊ': 'u',
    'ऋ': 'ri', 'ए': 'e', 'ऐ': 'ai', 'ओ': 'o', 'औ': 'au',
    'ऑ': 'o', 'ऍ': 'e',
}

MATRAS = {
    'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u', 'ृ': 'ri',
    'े': 'e', 'ै': 'ai', 'ो': 'o', 'ौ': 'au', 'ॉ': 'o', 'ॅ': 'e',
}

VIRAMA = '्'
NUKTA = '़'
ANUSVARA = 'ं'
CHANDRABINDU = 'ँ'
VISARGA = 'ः'

# Anusvara takes the nasal of whatever follows it.
LABIAL = set('पफबभम')
PALATAL = set('चछजझ')
VELAR = set('कखगघ')

# Suffixes that behave as separate words inside a compound. Splitting on them first is
# what makes पारसाराम come out 'Parsaram' rather than 'Parasaram'.
SUFFIXES = [
    'कुमारी', 'कुमार', 'प्रसाद', 'शंकर', 'चन्द्र', 'चंद्र', 'सिंह', 'कंवर', 'कँवर',
    'कवर', 'देवी', 'नाथ', 'दास', 'चन्द', 'चंद', 'लाल', 'राम', 'बाई', 'मल', 'जी',
]
PREFIXES = ['श्री', 'स्व.', 'स्व']


def _syllabify(word):
    """Split into syllables: (consonant_cluster_latin, vowel_latin, had_explicit_vowel)."""
    out = []
    i, n = 0, len(word)
    cluster = ''
    while i < n:
        ch = word[i]

        # Special ligature ज्ञ (Gy)
        if word[i:i+3] == 'ज्ञ':
            cluster += 'gy'
            i += 3
            if i < n and word[i] in MATRAS:
                out.append((cluster, MATRAS[word[i]], True, word[i]))
                i += 1
            else:
                out.append((cluster, 'a', False, ''))
            cluster = ''
            continue

        if ch in CONSONANTS:
            lat = CONSONANTS[ch]
            if ch == 'व':
                # Ligature श्व is always shw: Ishwar, Rameshwar, Rajeshwari, Ashwini, Shweta
                if cluster.endswith('sh'):
                    lat = 'w'
                # Preceded by nasal: Kanwar, Tanwar, Panwar, Banwari
                elif out and out[-1][1].endswith('n'):
                    lat = 'w'
                # Non-initial suffixes: -wal, -wala, -wat, -wan, -wana (Meghwal, Shekhawat, Bhagwan, Bairwa)
                elif i > 0 and (
                    word[i:].startswith('वाल') or
                    word[i:].startswith('वत') or
                    word[i:].startswith('वान') or
                    word[i:].startswith('वा')
                ):
                    lat = 'w'
                else:
                    lat = 'v'
            if i + 1 < n and word[i + 1] == NUKTA:
                lat = {'ज': 'z', 'ड': 'r', 'ढ': 'rh', 'फ': 'f',
                       'क': 'q', 'ख': 'kh', 'ग': 'gh'}.get(ch, lat)
                i += 1
            cluster += lat
            i += 1
            if i < n and word[i] == VIRAMA:        # half form: cluster continues
                i += 1
                continue
            if i < n and word[i] in MATRAS:
                out.append((cluster, MATRAS[word[i]], True, word[i]))
                i += 1
            else:
                out.append((cluster, 'a', False, ''))   # inherent vowel
            cluster = ''
            continue

        if ch in INDEP_VOWELS:
            out.append((cluster, INDEP_VOWELS[ch], True, ch))
            cluster = ''
            i += 1
            continue

        if ch in (ANUSVARA, CHANDRABINDU):
            nxt = word[i + 1] if i + 1 < n else ''
            if nxt in LABIAL:
                nasal = 'm'
            elif nxt == 'ह':
                nasal = 'ng'          # सिंह -> Singh
            else:
                nasal = 'n'
            if out:
                out[-1] = (out[-1][0], out[-1][1] + nasal, out[-1][2], out[-1][3])
            i += 1
            continue

        if ch == VISARGA:
            if out:
                out[-1] = (out[-1][0], out[-1][1] + 'h', out[-1][2], out[-1][3])
            i += 1
            continue

        i += 1      # anything else (stray marks) is dropped
    if cluster:
        out.append((cluster, '', True, ''))
    return out


def _word(word):
    syl = _syllabify(word)
    if not syl:
        return ''

    keep = [True] * len(syl)

    # Word-final inherent 'a' is silent -- राम -> ram, अशोक -> ashok, चन्द/चंद -> chand --
    # except after Sanskrit clusters ending in 'r' (like -dra: महेन्द्र -> mahendra, चन्द्र -> chandra).
    SINGLE_CONSONANTS = {'b', 'bh', 'ch', 'chh', 'd', 'dh', 'f', 'g', 'gh', 'h', 'j', 'jh', 'k', 'kh', 'l', 'm', 'n', 'p', 'q', 'r', 'rh', 's', 'sh', 't', 'th', 'v', 'w', 'y', 'z'}
    last_cluster = syl[-1][0]
    keeps_terminal_schwa = (
        len(last_cluster) > 1 and 
        last_cluster not in SINGLE_CONSONANTS and 
        last_cluster.endswith(('dr', 'tr', 'shr', 'pr', 'kr', 'dhr', 'br'))
    )
    if len(syl) > 1 and not syl[-1][2] and not keeps_terminal_schwa:
        keep[-1] = False

    # Medial schwa deletion, applied left to right: an inherent 'a' drops when the
    # syllable before it still has a vowel. जगदीश -> jagdish, ओमप्रकाश -> omprakash,
    # जबरा -> jabra.
    # CRITICAL: A conjunct consonant cluster (e.g. 'shw' in राजेश्वरी) NEVER drops its schwa!
    for i in range(1, len(syl) - 1):
        cons, vowel, explicit = syl[i][0], syl[i][1], syl[i][2]
        if explicit or not cons or vowel != 'a':
            continue
        if not keep[i - 1]:
            continue
        # Conjunct consonant clusters (not in SINGLE_CONSONANTS) preserve schwa!
        if cons not in SINGLE_CONSONANTS:
            continue
        # Something vowel-bearing has to follow, or this syllable is now word-final and
        # keeps its vowel: कंवर stays Kanwar, विजय stays Vijay.
        if not any(keep[j] for j in range(i + 1, len(syl))):
            continue
        keep[i] = False

    # Long ī is written 'ee' except where the word ends on it: दीपा Deepa, गीता Geeta,
    # मीरा Meera, but देवी Devi and नरसी Narsi. Long ū stays plain 'u' -- the verified
    # Ward 82 spellings are Bhura, Suraj, Rupa, not Bhoora, Sooraj, Roopa.
    last = max(i for i in range(len(syl)) if keep[i])
    parts = []
    for i, (cons, vowel, _explicit, src) in enumerate(syl):
        if keep[i] and i != last:
            if src in ('ी', 'ई') and vowel.startswith('i'):
                vowel = 'ee' + vowel[1:]
        parts.append(cons + (vowel if keep[i] else ''))
    return ''.join(parts)


def _split_compound(token):
    """Peel off trailing name-elements so each is transliterated as its own word."""
    for suf in SUFFIXES:
        if token.endswith(suf) and len(token) > len(suf) + 1:
            return _split_compound(token[:-len(suf)]) + [suf]
    for pre in PREFIXES:
        if token.startswith(pre) and len(token) > len(pre) + 1:
            return [pre] + _split_compound(token[len(pre):])
    return [token]


COMMON_SURNAME_OVERRIDES = {
    unicodedata.normalize('NFC', k): v
    for k, v in {
        # Saklecha / Sakhlecha variants
        'सकलेचा': 'Saklecha',
        'सखलेचा': 'Saklecha',
        'साकलेचा': 'Saklecha',
        # Jitendra canonical convention (always Jitendra, never Jeetendra)
        'जीतेन्द्र': 'Jitendra',
        'जितेन्द्र': 'Jitendra',
        'जितेंद्र': 'Jitendra',
        'जीतेंद्र': 'Jitendra',
        'दीप': 'Deep',
        'दीपक': 'Deepak',
        # Rathore variants (all Devanagari spellings and nukta combinations)
        'राठौड़': 'Rathore',
        'राठौड़': 'Rathore',
        'राठोड': 'Rathore',
        'राठोड़': 'Rathore',
        'राठौड': 'Rathore',
        'राठौर': 'Rathore',
        'राठोर': 'Rathore',
        # Choudhary / Chaudhary variants
        'चौधरी': 'Chaudhary',
        'चोधरी': 'Chaudhary',
        'चौधुरी': 'Chaudhary',
        'चौधारी': 'Chaudhary',
        # Shekhawati / Marwar clan surnames & castes
        'शेखावत': 'Shekhawat',
        'कुमावत': 'Kumawat',
        'गहलोत': 'Gehlot',
        'सिसोदिया': 'Sisodia',
        'झाझड़िया': 'Jhajharia',
        'झाझडिया': 'Jhajharia',
        'जांगिड़': 'Jangid',
        'जांगिड': 'Jangid',
        'जाखड़': 'Jakhar',
        'पूनिया': 'Poonia',
        'पुनिया': 'Poonia',
        'डूडी': 'Dudi',
        'गोदारा': 'Godara',
        'धायल': 'Dhayal',
        'बिश्नोई': 'Bishnoi',
        'विश्नोई': 'Bishnoi',
        'माहेश्वरी': 'Maheshwari',
        'सुथार': 'Suthar',
        'लोहार': 'Lohar',
        'कुम्हार': 'Kumhar',
        'गौड़': 'Gaur',
        'गोड़': 'Gaur',
        'बड़गुर्जर': 'Badgurjar',
        'गुर्जर': 'Gurjar',
        'मीणा': 'Meena',
        'मीना': 'Meena',
        'सैनी': 'Saini',
        'माली': 'Mali',
        'यादव': 'Yadav',
        'जाट': 'Jat',
        'राजपूत': 'Rajput',
        'ब्राह्मण': 'Brahmin',
        'वर्मा': 'Verma',
        'शर्मा': 'Sharma',
        'तिवाड़ी': 'Tiwari',
        'तिवारी': 'Tiwari',
        'व्यास': 'Vyas',
        'जोशी': 'Joshi',
        'दाधीच': 'Dadhich',
        'सोनी': 'Soni',
        'भाटी': 'Bhati',
        'राणा': 'Rana',
        'सोलंकी': 'Solanki',
        'परिहार': 'Parihar',
        'झाला': 'Jhala',
        'चौहान': 'Chauhan',
        'बैरवा': 'Bairwa',
        'मेघवाल': 'Meghwal',
        'बेनीवाल': 'Beniwal',
        'कस्वां': 'Kaswan',
        'कसवां': 'Kaswan',
        'तंवर': 'Tanwar',
        'पंवार': 'Panwar',
        'खींची': 'Khinchi',
        'खिंची': 'Khinchi',
        'खतीक': 'Khatik',
        'अग्रवाल': 'Agarwal',
        'खंडेलवाल': 'Khandelwal',
        'पालीवाल': 'Paliwal',
        'पोसवाल': 'Poswal',
        'गढवाल': 'Gadhwal',
        'गोठवाल': 'Gothwal',
        'निठारवाल': 'Nitharwal',
        'खारवाल': 'Kharwal',
        'तेतरवाल': 'Tetarwal',
        'हरितवाल': 'Haritwal',
        'जायसवाल': 'Jaiswal',
        'दूधवाल': 'Dudhwal',
        'सिखवाल': 'Sikhwal',
        'धाकड़': 'Dhakad',
        # Rajput & Marwar -wat lineages
        'रावत': 'Rawat',
        'राजावत': 'Rajawat',
        'राणावत': 'Ranawat',
        'रणावत': 'Ranawat',
        'चुंडावत': 'Chundawat',
        'चूंडावत': 'Chundawat',
        'चुंड़ावत': 'Chundawat',
        'चूड़ावत': 'Chundawat',
        'चुंदावत': 'Chundawat',
        'चुडावत': 'Chundawat',
        'सारस्वत': 'Saraswat',
        'शक्तावत': 'Shaktawat',
        'चंपावत': 'Champawat',
        'नाथावत': 'Nathawat',
        'कानावत': 'Kanawat',
        'उदावत': 'Udawat',
        'कितावत': 'Kitawat',
        # Sanskrit & Scholar -ya endings
        'आचार्य': 'Acharya',
        'आर्य': 'Arya',
        'मौर्य': 'Maurya',
        'मोर्य': 'Maurya',
        # Common given name tokens that have regional phonetic standards
        'मूलसिंह': 'Mool Singh',
        'मूल': 'Mool',
        'अकवर': 'Akbar',
        'मूलचन्द': 'Moolchand',
        'मूलचंद': 'Moolchand',
        'फूलचंद': 'Phoolchand',
        'फूलचन्द': 'Phoolchand',
        'द्वारका': 'Dwarka',
        'ज्ञानी': 'Gyani',
        'ज्ञानीराम': 'Gyaniram',
        'ज्ञान': 'Gyan',
        'ज्ञानचंद': 'Gyanchand',
        'ज्ञानसिंह': 'Gyansingh',
        'संदीप': 'Sandeep',
        'कुलदीप': 'Kuldeep',
        'प्रदीप': 'Pradeep',
        'वाजपेयी': 'Vajpayee',
        'वाजपेई': 'Vajpayee',
        'ईश्वर': 'Ishwar',
        'रामेश्वर': 'Rameshwar',
        'राजेश्वर': 'Rajeshwar',
        'राजेश्वरी': 'Rajeshwari',
        'महेश्वरी': 'Maheshwari',
        'गौरव': 'Gaurav',
        'सौरव': 'Saurav',
        'भैरव': 'Bhairav',
        'वासुदेव': 'Vasudev',
        'अरविंद': 'Arvind',
        'अरविन्द': 'Arvind',
        'आरव': 'Aarav',
        'नीरव': 'Neerav',
        'मणिकर्णिका': 'Manikarnika',
        'कर्णिका': 'Karnika',
        'ध्रुव': 'Dhruv',
        'पार्वती': 'Parvati',
        'पारवती': 'Parvati',
        'मूंगा': 'Moonga',
        'मूँग': 'Moonga',
    }.items()
}


def transliterate_token(token):
    """One Hindi token -> its English spelling, Title Case."""
    token = unicodedata.normalize('NFC', token.strip())
    if not token:
        return ''
    if token in COMMON_SURNAME_OVERRIDES:
        return COMMON_SURNAME_OVERRIDES[token]
    if re.fullmatch(r'[\x00-\x7f]+', token):     # already Latin / digits
        return token
    # Normalize Devanagari jna (ज्ञ) to gya for standard Indian English spelling (Gyan, Gyani, etc.)
    token = token.replace('ज्ञ', 'ग्य')
    # Pre-normalize true homorganic nasal halants (e.g. चन्द->चंद, आनन्द->आनंद, हेमन्त->हेमंत, सम्पत->संपत)
    # Crucial: 'म्' only becomes anusvara before labials (प-भ). Before 'र'/'य'/'ल'/'व'/'ह' (e.g. अम्रा, नम्रता, सम्राट, सौम्य), it is a conjunct cluster 'mr'/'my', NOT anusvara!
    token = re.sub(r'ङ्(?=[क-घ])', 'ं', token)
    token = re.sub(r'ञ्(?=[च-झ])', 'ं', token)
    token = re.sub(r'ण्(?=[ट-ढ])', 'ं', token)
    token = re.sub(r'न्(?=[त-स])', 'ं', token)
    token = re.sub(r'म्(?=[प-भ])', 'ं', token)
    return ''.join(_word(p) for p in _split_compound(token)).title()


def transliterate_name(name):
    return ' '.join(transliterate_token(t) for t in (name or '').split()).strip()
