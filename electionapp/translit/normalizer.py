# -*- coding: utf-8 -*-
"""Voter name cleaning, OCR anomaly correction, and transliteration normalizer.

Ensures no replacement characters (\\ufffd), OCR ligature dropouts, or truncated
English transliterations (e.g. Mahendr -> Mahendra, Dhamadr -> Dharmendra) enter the database.
"""
import re
from typing import Tuple, Optional, Any
try:
    from .rules import transliterate_name
except ImportError:
    from rules import transliterate_name

# Specific Devnagari OCR fixes for corrupted glyphs / missed ligatures
DEVNAGARI_REPLACEMENTS = [
    # Explicit known OCR corruptions in voter rolls
    (r'धम\ufffd+न्द्र', 'धर्मेन्द्र'),
    (r'धम\ufffd+द्र', 'धर्मेन्द्र'),
    (r'धम\ufffd+चन्द', 'धर्मचन्द'),
    (r'उ\ufffd+मला', 'उर्मिला'),
    (r'उ\ufffd+मल(?:\s|$)', 'उर्मिला '),
    (r'प्र\ufffd+लाद', 'प्रहलाद'),
    (r'नि\ufffd+मल(?:\s|$)', 'निर्मल '),
    (r'नि\ufffd+मला', 'निर्मला'),
    (r'वि\ufffd+\s*णु', 'विष्णु'),
    (r'वै\ufffd+णव', 'वैष्णव'),
    (r'वि\ufffd+ा', 'विद्या'),
    (r'प\ufffd+पू', 'पप्पू'),
    (r'प\ufffd+पु', 'पप्पू'),
    (r'पु\ufffd+पा', 'पुष्पा'),
    (r'पृ\ufffd+वी', 'पृथ्वी'),
    (r'स\ufffd+यनारायण', 'सत्यनारायण'),
    (r'ई\ufffd+वर', 'ईश्वर'),
    (r'रामे\ufffd+र', 'रामेश्वर'),
    (r'सव\ufffd+ेश्वर', 'सर्वेश्वर'),
    (r'सु\ufffd+डाराम', 'सुण्डाराम'),
    (r'सु\ufffd+डा\s*राम', 'सुण्डाराम'),
    (r'घम\ufffd+डाराम', 'घमंडाराम'),
    (r'घम\ufffd+डा(?:\s|$)', 'घमंडा '),
    (r'जि\ufffd+ेश', 'जिग्नेश'),
    (r'अ\ufffd+होत्री', 'अग्निहोत्री'),
    (r'मोह\ufffd+मद', 'मोहम्मद'),
    (r'मोहहम\ufffd+', 'मोहम्मद'),
    (r'दीनमो\ufffd*ंमद', 'दीन मोहम्मद'),
    (r'सु\ufffd+तान', 'सुल्तान'),
    (r'रा\ufffd+ड़ि?', 'राठौड़'),
    (r'मे\ufffd+वन्शी', 'मेघवंशी'),
    (r'मे\ufffd+वाल', 'मेघवाल'),
    (r'\ufffd+यारे\s*लाल', 'प्यारे लाल'),
    (r'\ufffd+यारी', 'प्यारी'),
    (r'राम\ufffd+यारी', 'रामप्यारी'),
    (r'\ufffd+काश(?:\s|$)', 'प्रकाश '),
    (r'\ufffd+सा[ददा]', 'प्रसाद'),
    (r'\ufffd+जापति', 'प्रजापति'),
    (r'चन्\ufffd+(?:\s|$)', 'चन्द्र '),
    (r'महेन्\ufffd+', 'महेन्द्र'),
    (r'राजेन्\ufffd+', 'राजेन्द्र'),
    (r'\ufffd+रकू\s*देवी', 'हरकू देवी'),
    (r'\ufffd+रकी\s*देवी', 'हरकी देवी'),
    (r'\ufffd+रकू', 'हरकू'),
    (r'\ufffd+रकी', 'हरकी'),
    (r'दुग\ufffd+श', 'दुर्गेश'),
    (r'धम\ufffd+श', 'धर्मेश'),
    (r'गु\ufffd+ड?[ूु]?', 'गुड्डू'),
    (r'(?:^|\s)\ufffd+श्वर्या', ' ऐश्वर्या'),
    (r'क\ufffd+मारी', 'कुमारी'),
    (r'काल\ufffd+राम', 'कालूराम'),
    (r'(?:दज|दर्ज)\ufffd+(?:\s|$)', 'दर्जी '),
    (r'स\ufffd+पत\s*राज', 'सम्पत राज'),
    (r'स\ufffd+पत(?:\s|$)', 'सम्पत '),
    (r'पू\ufffd+बया', 'पूरबिया'),
    (r'भु\ufffd+डाराम', 'भुण्दाराम'),
    (r'\ufffd+ैलीराम', 'छैलीराम'),
    (r'(?:^|\s)\ufffd+नीफ', ' हनीफ'),
    (r'अ\ufffd+बालाल', 'अम्बालाल'),
    (r'डि\ufffd+पल', 'डिम्पल'),
    (r'प\ufffd+डवाला', 'पण्डवाला'),
    (r'ध\ufffd+गड', 'धनगड'),
    (r'ल\ufffd+डु', 'लाड्डू'),
    (r'शरी\ufffd+', 'शरीफ'),
    (r'स\ufffd+ाम', 'सद्दाम'),
    (r'प\ufffd+ा\s*राम', 'पन्ना राम'),
    (r'प\ufffd+ा\s*दास', 'पन्ना दास'),
    (r'प\ufffd+ा(?:\s|$)', 'पन्ना '),
    (r'ध\ufffd+ा(?:\s|$)', 'धन्ना '),
    (r'मु\ufffd+ा(?:\s|$)', 'मुन्ना '),
    (r'\ufffd+कार', 'ओंकार'),
    (r'भ\ufffd+रु', 'भैरु'),
    (r'\ufffd+ान\s*सिंह', 'ज्ञान सिंह'),
    (r'\ufffd+ानसिंह', 'ज्ञानसिंह'),
    (r'\ufffd+मप्रकाश', 'ओमप्रकाश'),
    (r'स\ufffd+न\s*सिंह', 'सज्जन सिंह'),
    (r'क\ufffd+याण', 'कल्याण'),
    (r'क\ufffd+वर', 'कंवर'),
    (r'(?:^|\s)\ufffd+सैन', ' हुसैन'),
    (r'रा\ufffd+ल', 'राहुल'),
    (r'(?:^|\s)\ufffd+नश्याम', ' घनश्याम'),
    (r'र\ufffd+ुनाथ', 'रघुनाथ'),
    (r'र\ufffd+ुवीर', 'रघुवीर'),
    (r'र\ufffd+ु', 'रघु'),
    (r'ख\ufffd+ची', 'खींची'),
    (r'(?:^|\s)\ufffd+ेवर', ' घेवर'),
    (r'(?:^|\s)\ufffd+न्द्रा', ' इन्द्रा'),
    (r'चो\ufffd+ा', 'चोखा'),
    (r'महे\ufffd+(?:\s|$)', 'महेश '),
    (r'सुर\ufffd+ान', 'सुरज्ञान'),
    (r'तै\ufffd+यब', 'तैयब'),
    (r'स\ufffd+मुद्दीन', 'शमसुद्दीन'),
    (r'खुश\ufffd+द', 'खुर्शीद'),
    (r'ज़\ufffd+बेर', 'ज़ुबैर'),
    (r'सो\ufffd+ा', 'सोढा'),
    (r'ज़\ufffd+बी', 'ज़ैबी'),
    (r'दाडी\ufffd+\s*देवी', 'दाडमी देवी'),
    (r'\ufffd+कमा(?:\s|$)', 'हकमा'),
    (r'\ufffd+ुव\s*सिंह', 'ध्रुव सिंह'),
    (r'फ\ufffd+सी\s*देवी', 'फूसी देवी'),
    (r'रणछोडा\ufffd+\s*राम', 'रणछोड़ाराम'),
    (r'वज\ufffd+गाराम', 'वजीराराम'),
    (r'सि\ufffd+था\s*राम', 'सिमरथाराम'),
    (r'ना?\ufffd+रगा\s*राम', 'नरिगाराम'),
    (r'नर\ufffd+गाराम', 'नरिगाराम'),
    (r'भ\ufffd+ला\s*राम', 'भलाराम'),
    (r'मोडा\ufffd+\s*राम', 'मोडाराम'),
    (r'^\ufffd+ेपी$', 'केपी'),
    (r'^\ufffd+पी$', 'धूपी'),
    # Unmapped font glyph / dropped ligature patterns
    (r'(?:^|\s)ानी(?=\s|राम|दास|लाल|चंद|चन्द|सिंह|देवी|$)', ' ज्ञानी'),
    (r'(?:^|\s)ानीराम', ' ज्ञानीराम'),
    (r'(?:^|\s)ानचंद', ' ज्ञानचंद'),
    (r'(?:^|\s)ानसिंह', ' ज्ञानसिंह'),
    (r'(?:^|\s)ान(?=\s+(?:प्रकाश|कंवर|सिंह|चंद|चन्द))', ' ज्ञान'),
    (r'(?:^|\s)ानादेवी', ' ज्ञानादेवी'),
    (r'(?:^|\s)ारका\s*राम', ' द्वारका राम'),
    (r'(?:^|\s)ारकाराम', ' द्वारकाराम'),
    (r'(?:^|\s)ारका\s*प्रसाद', ' द्वारका प्रसाद'),
    (r'(?:^|\s)ारका\s*नाथ', ' द्वारका नाथ'),
    (r'(?:^|\s)ारका\s*दास', ' द्वारका दास'),
    (r'(?:^|\s)ारकादास', ' द्वारकादास'),
    (r'(?:^|\s)ूल(?=\s*(?:सिंह|चंद|चन्द|राम|दास))', ' मूल'),
    (r'(?:^|\s)ूलराम', ' मूलराम'),
    (r'(?:^|\s)ूलदास', ' मूलदास'),
    (r'(?:^|\s)ूलसिंह', ' मूलसिंह'),
    (r'(?:^|\s)ूलचन्द', ' मूलचन्द'),
    (r'(?:^|\s)ूलचंद', ' मूलचंद'),
    (r'(?:^|\s)ेम(?=\s*(?:देवी|कंवर|बाई|लता|सिंह|राज|कुमार|चन्द|चंद|प्रकाश|शंकर|सुख))', ' प्रेम'),
    (r'(?:^|\s)ेमलता', ' प्रेमलता'),
    (r'(?:^|\s)ेमसिंह', ' प्रेमसिंह'),
    (r'(?:^|\s)ेमराज', ' प्रेमराज'),
    (r'(?:^|\s)ेमचन्द', ' प्रेमचन्द'),
    (r'(?:^|\s)ेमचंद', ' प्रेमचंद'),
    (r'(?:^|\s)ुव(?=\s+(?:स्वामी|सिंह))', ' ध्रुव'),
    (r'(?:^|\s)परु\s*ूणमल', ' पूरणमल'),
    (r'(?:^|\s)डेडराज\s+गरु\s*ुजर', ' डेडराज गुर्जर'),
    (r'(?:^|\s)बगडावत\s+ांासिह', ' बगडावत सिंह'),
    (r'(?:^|\s)ुर्रप्यारसीलाल', ' प्यारसीलाल'),
    (r'(?:^|\s)ंसि[हं]+', ' सिंह'),
    (r'(?:^|\s)ंक[ंँ]?वर', ' कंवर'),
    (r'(?:^|\s)ंस[ंँ]?दीप', ' संदीप'),
    (r'(?:^|\s)ंहस(?=\s*राज)', ' हंस'),
    (r'(?:^|\s)ुपुनम', ' पूनम'),
    (r'(?:^|\s)ेदवी', ' देवी'),
    (r'(?:^|\s)ंहादा\s*राम', ' नहादा राम'),
    (r'(?:^|\s)ंहादाराम', ' नहादाराम'),
    (r'(?:^|\s)ानता(?:\s|$)', ' शांता '),
    (r'(?:^|\s)ानतादेवी', ' शांतादेवी'),
    # Space-fragmented Indic words from PDF font kerning/layout breaks
    (r'(?:^|\s)दीप\s+शिता(?:\s|$)', ' दीपशिता '),
    (r'(?:^|\s)सखलेचा(?:\s|$)', ' सकलेचा '),
    (r'(?:^|\s)कु\s+मा\s+र(?:\s|$)', ' कुमार '),
    (r'(?:^|\s)सो\s+नी(?:\s|$)', ' सोनी '),
    (r'(?:^|\s)का\s+ना\s+राम(?:\s|$)', ' कानाराम '),
    (r'(?:^|\s)अन्\s+नी(?:\s|$)', ' अन्नी '),
    (r'(?:^|\s)राय\s+चन्\s+द(?:\s|$)', ' रायचन्द '),
    (r'(?:^|\s)चन्\s+द(?:\s|$)', ' चन्द '),
    (r'(?:^|\s)बिस्मिल\s+ला(?:\s|$)', ' बिस्मिल्ला '),
    (r'(?:^|\s)मोहं\s+मद(?:\s|$)', ' मोहम्मद '),
    # Scrambled matra / layout fixes
    (r'खचि[ंँ]ी', 'खींची'),
    (r'खचिी', 'खींची'),
    # Unmapped font glyphs & dropped initial consonants
    (r'(?:^|\s)अा\s*राम(?=\s|$)', ' अम्रा राम'),
    (r'(?:^|\s)ूसा(?:\s*राम|राम)', ' फूसा राम'),
    (r'(?:^|\s)ुसाराम', ' फूसा राम'),
    (r'(?:^|\s)ुसी\s*देवी', ' फुसी देवी'),
    (r'(?:^|\s)ुसी(?=\s|$)', ' फुसी'),
    (r'(?:^|\s)सुा\s*राम', ' सुग्रा राम'),
    # Optical OCR character confusion fixes (Manikarnika, Hanuman, Ladhu)
    (r'(?:^|\s)मणिकहणका(?:\s|$)', ' मणिकर्णिका '),
    (r'(?:^|\s)कहणका(?:\s|$)', ' कर्णिका '),
    (r'(?:^|\s)हणमान(?:\s|$)', ' हनुमान '),
    (r'(?:^|\s)हणमानाराम(?:\s|$)', ' हनुमानाराम '),
    (r'(?:^|\s)हणमानराम(?:\s|$)', ' हनुमानराम '),
    (r'(?:^|\s)हणुताराम(?:\s|$)', ' हनुवंताराम '),
    (r'(?:^|\s)हणुता\s*राम(?:\s|$)', ' हनुवंताराम '),
    (r'(?:^|\s)लघु(?:\s|$)', ' लाधु '),
    (r'(?:^|\s)लघुराम(?:\s|$)', ' लाधुराम '),
    (r'(?:^|\s)लघुडी(?:\s|$)', ' लाधुडी '),
    (r'(?:^|\s)लघुकंवर(?:\s|$)', ' लाधु कंवर '),
    (r'(?:^|\s)गरीमासिह(?:\s|$)', ' गरिमा सिंह '),
    (r'(?:^|\s)गरिमासिह(?:\s|$)', ' गरिमा सिंह '),
    # Locality, institution, and booth Devanagari OCR fixes
    (r'वि\ufffd+ालय', 'विद्यालय'),
    (r'विधालय', 'विद्यालय'),
    (r'महावि\ufffd+ालय', 'महाविद्यालय'),
    (r'म\ufffd+ख्य', 'मुख्य'),
    (r'म\ufffd+हल्ला', 'मोहल्ला'),
    (r'मोह\ufffd+ला', 'मोहल्ला'),
    (r'मौह\ufffd+ला', 'मौहल्ला'),
    (r'रा\.उ\.\ufffd+ा\.वि\.', 'रा.उ.प्रा.वि.'),
    (r'रा\.उ\.ा\.वि\.', 'रा.उ.प्रा.वि.'),
    (r'उच्च\s+\ufffd*ाथमिक', 'उच्च प्राथमिक'),
    (r'प्रा\ufffd+थमिक', 'प्राथमिक'),
    (r'(?<=\s)ाथमिक(?=\s|$)', 'प्राथमिक'),
    (r'मा\ufffd+यमिक', 'माध्यमिक'),
    (r'उ\ufffd+च', 'उच्च'),
    (r'केन्\ufffd+', 'केन्द्र'),
    (r'ब्रा\ufffd*हण', 'ब्राह्मण'),
    (r'ब्रा\ufffd*ंमण', 'ब्राह्मण'),
    (r'वि\ufffd+ोई', 'विश्नोई'),
    (r'बि\ufffd+ोई', 'बिश्नोई'),
    (r'आर्तादी', 'आबादी'),
    (r'आर्बादी', 'आबादी'),
    (r'र्तीच(?=\s+की)', 'बीच'),
    (r'र्तस्ती', 'बस्ती'),
    (r'मे\ufffdवाल', 'मेघवाल'),
    (r'खेजडि\ufffd+या', 'खेजड़िया'),
    (r'स्टे\ufffd+ड', 'स्टैंड'),
    (r'स\ufffd+ओं\s*की\s*ढाणी', 'सांइयों की ढाणी'),
    (r'([क-ह]़?)\ufffd+\s+का\s+वास', r'\g<1>ों का वास'),
    (r'([क-ह]़?)\ufffd+\s+की\s+बस्ती', r'\g<1>ों की बस्ती'),
    (r'(?<=की\s)\ufffd+ाणी', 'ढाणी'),
    (r'(?<=के\s)\ufffd+ाणी', 'ढाणी'),
    (r'ढाणिया\ufffd+', 'ढाणियां'),
    # General cleanup
    (r'\ufffd', ''),                  # Strip any unhandled replacement character
    (r'[`\'"“”‘’]+', ''),           # Strip stray backticks and quotes
    (r'\s+', ' '),                  # Normalize spaces
]

# Canonical Devanagari vowel normalization:
# Convert illegal sequences of independent vowel 'अ' + vowel signs (matras),
# which occur from legacy font encoding (e.g. KrutiDev) and render with broken dotted circles.
CANONICAL_DEVANAGARI_VOWELS = [
    (r'\u0905\u093e\u0947', '\u0913'),   # अ + ा + े -> ओ
    (r'\u0905\u093e\u0948', '\u0914'),   # अ + ा + ै -> औ
    (r'\u0905\u093e\u0945', '\u0911'),   # अ + ा + ॅ -> ऑ
    (r'\u0905\u093e', '\u0906'),         # अ + ा -> आ
    (r'\u0905\u094b', '\u0913'),         # अ + ो -> ओ
    (r'\u0905\u094c', '\u0914'),         # अ + ौ -> औ
    (r'\u0905[\u0949\u0945]', '\u0911'), # अ + ॉ/ॅ -> ऑ
    (r'\u0905\u0948', '\u0910'),         # अ + ै -> ऐ
    (r'\u0905\u0947', '\u090f'),         # अ + े -> ए
    (r'\u0905\u093f', '\u0907'),         # अ + ि -> इ
    (r'\u0905\u0940', '\u0908'),         # अ + ी -> ई
    (r'\u0905\u0941', '\u0909'),         # अ + ु -> उ
    (r'\u0905\u0942', '\u090a'),         # अ + ू -> ऊ
]

# Common Rajasthani community/surnames that often get fused to given names without spaces
SURNAME_LIST = [
    'खतीक', 'खटीक', 'बैरवा', 'मेघवाल', 'शर्मा', 'वर्मा', 'जाट', 'गुर्जर', 'मीणा', 'राजपूत',
    'चौधरी', 'चौहान', 'राठौड़', 'सैनी', 'माली', 'बिश्नोई', 'विश्नोई', 'यादव', 'ब्राह्मण',
    'सुथार', 'लोहार', 'कुम्हार', 'नाई', 'सोनार', 'सोनी', 'जैन', 'अग्रवाल', 'खंडेलवाल',
    'जोशी', 'व्यास', 'दाधीच', 'तिवाड़ी', 'मिश्रा', 'पांडे', 'कस्वां', 'कसवां', 'गोदारा',
    'पुनिया', 'पूनिया', 'डूडी', 'बेनीवाल', 'धायल', 'झाझड़िया', 'शेखावत', 'तंवर', 'पंवार',
    'परिहार', 'भाटी', 'राणा', 'सोलंकी', 'सिसोदिया', 'खींची',
    'चूंडावत', 'चुंड़ावत', 'चुंडावत', 'शक्तावत'
]
FUSED_SURNAME_PATTERN = re.compile(
    rf'(?<=[\u0900-\u097f]{{3}})({"|".join(SURNAME_LIST)})(?=$|\s)'
)

# Common fused honorifics & gender markers (कमलादेवी -> कमला देवी, रामसिह -> राम सिंह)
FUSED_HONORIFIC_RULES = [
    (r'(?<=[\u0900-\u097f]{2})(?:देवी)(?=$|\s)', ' देवी'),
    (r'(?<=[\u0900-\u097f]{2})(?:बाई)(?=$|\s)', ' बाई'),
    (r'(?<=[\u0900-\u097f]{2})(?:कंवर|कँवर|कवर)(?=$|\s)', ' कंवर'),
    (r'(?<=[\u0900-\u097f]{2})(?:कुमारी)(?=$|\s)', ' कुमारी'),
    (r'(?<=[\u0900-\u097f]{2})(?:सिंह|सिह)(?=$|\s)', ' सिंह'),
    (r'(?<=[\u0900-\u097f]{2})(?:प्रसाद)(?=$|\s)', ' प्रसाद'),
]

# Truncated English cluster endings to expand to full inherent 'a'
ENGLISH_CLUSTER_FIXES = [
    (r'\b([A-Za-z]+)dr\b', r'\g<1>dra'),       # Mahendr -> Mahendra, Jitendr -> Jitendra
    (r'\bDhamadr\b', 'Dharmendra'),
    (r'\bSinh\b', 'Singh'),
    (r'\b([A-Za-z]+)sinh\b', r'\g<1> Singh'),
    (r'\bRathauda\b', 'Rathore'),
    (r'\bGauda\b', 'Gaur'),
    (r'([a-z]+)chaudhri\b', r'\g<1> Chaudhary'), # Unspaced compound e.g. Mafaramchaudhri -> Mafaram Chaudhary
    (r'([a-z]+)chaudhary\b', r'\g<1> Chaudhary'),
    (r'([a-z]+)rathod\b', r'\g<1> Rathore'),    # Unspaced compound e.g. Singhrathod -> Singh Rathore
    (r'([a-z]+)rathaur\b', r'\g<1> Rathore'),
    (r'\bRathaur\b', 'Rathore'),
    (r'\bRathau\b', 'Rathore'),
    (r'\bA Ram\b', 'Amra Ram'),
    (r'\bSa Ram\b', 'Fusa Ram'),
    (r'\bSaram\b', 'Fusa Ram'),
    (r'\bSi Devi\b', 'Fusi Devi'),
    (r'\bSu Ram\b', 'Sugra Ram'),
    (r'\bNi Ram\b', 'Gyani Ram'),
    (r'\bNiram\b', 'Gyaniram'),
    (r'\bJnani\b', 'Gyani'),
    (r'\bJnan\b', 'Gyan'),
    (r'\bChaudhri\b', 'Chaudhary'),
    (r'\bChoudhri\b', 'Chaudhary'),
    (r'\bGahlot\b', 'Gehlot'),
    (r'\bJangir\b', 'Jangid'),
    (r'\bKumavat\b', 'Kumawat'),
    (r'\bSisodiya\b', 'Sisodia'),
    (r'\bMul Singh\b', 'Mool Singh'),
    (r'\bMulsingh\b', 'Moolsingh'),
    (r'\bMulchand\b', 'Moolchand'),
    (r'\bMulchanda\b', 'Moolchand'),
    (r'\bDip\b', 'Deep'),
    (r'\bSakhlecha\b', 'Saklecha'),
    (r'\bJeetendr[a]?\b', 'Jitendra'),
    (r'\bLachanda\b', 'Moolchand'),
    (r'\bLa Singh\b', 'Mool Singh'),
    (r'\bLasingh\b', 'Moolsingh'),
    (r'\bRaka Prasad\b', 'Dwarka Prasad'),
    (r'\bMa Devi\b', 'Prem Devi'),
    (r'\bMa Ghumriya\b', 'Prem Ghumariya'),
    (r'\bMalta\b', 'Premlata'),
    (r'\bNachand\b', 'Gyanchand'),
    (r'\bNadevi\b', 'Gyanadevi'),
    (r'\bNa Kanwar\b', 'Gyan Kanwar'),
    (r'\bNa Prakash\b', 'Gyan Prakash'),
    (r'\bNata\b', 'Shanta'),
    (r'\bVa Svami\b', 'Dhruv Swami'),
    (r'\bParu Namal\b', 'Puranmal'),
    (r'\bJish\b', 'Jignesh'),
    (r'\bKama\b', 'Hakma'),
    (r'\bVa Singh\b', 'Dhruv Singh'),
    (r'\bFasi Devi\b', 'Foosi Devi'),
    (r'\bGhamda\b', 'Ghamanda'),
    (r'\bGhamdaram\b', 'Ghamandaram'),
    (r'\bVajgaram\b', 'Vajiraram'),
    (r'\bSitha Ram\b', 'Simratharam'),
    (r'\bNarga Ram\b', 'Narigaram'),
    (r'\bNargaram\b', 'Narigaram'),
    (r'\bDadmi\b', 'Dadmi'),
    (r'\bSo\b', 'Sodha'),                      # Rajput clan Sodha
    (r'\bBairva\b', 'Bairwa'),
    (r'\bMeghval\b', 'Meghwal'),
    (r'\bKhachin\b', 'Khinchi'),
    (r'\bKheenchi\b', 'Khinchi'),
    (r'\bHada Ram\b', 'Nahada Ram'),
    (r'\bHadaram\b', 'Nahadaram'),
    (r'\bSandip\b', 'Sandeep'),
    (r'\bSadip\b', 'Sandeep'),
    (r'\bKuldip\b', 'Kuldeep'),
    (r'\bPradip\b', 'Pradeep'),
    (r'\bHas Raj\b', 'Hansraj'),
    (r'\bRrapyarsilal\b', 'Pyarsilal'),
    (r'\bGaru Jar\b', 'Gurjar'),
    # --- Batch 2: Rajput & Marwar Clan -vat -> -wat Suffixes ---
    (r'\bRavat\b', 'Rawat'),
    (r'\bShekhavat\b', 'Shekhawat'),
    (r'\bRajavat\b', 'Rajawat'),
    (r'\bRanavat\b', 'Ranawat'),
    (r'\bChundavat\b', 'Chundawat'),
    (r'\bSarsvat\b', 'Saraswat'),
    (r'\bShaktavat\b', 'Shaktawat'),
    (r'\bChampavat\b', 'Champawat'),
    (r'\bNathavat\b', 'Nathawat'),
    (r'\bKanavat\b', 'Kanawat'),
    (r'\bUdavat\b', 'Udawat'),
    (r'\bKitavat\b', 'Kitawat'),
    # --- Batch 2: Merchant & Community -val -> -wal Suffixes ---
    (r'\bPaleeval\b', 'Paliwal'),
    (r'\bPalival\b', 'Paliwal'),
    (r'\bAgrval\b', 'Agarwal'),
    (r'\bBeneeval\b', 'Beniwal'),
    (r'\bBenival\b', 'Beniwal'),
    (r'\bKhandelval\b', 'Khandelwal'),
    (r'\bPosval\b', 'Poswal'),
    (r'\bMegval\b', 'Meghwal'),
    (r'\bJaysaval\b', 'Jaiswal'),
    (r'\bGadhval\b', 'Gadhwal'),
    (r'\bGothval\b', 'Gothwal'),
    (r'\bNitharval\b', 'Nitharwal'),
    (r'\bKharval\b', 'Kharwal'),
    (r'\bTetraval\b', 'Tetarwal'),
    (r'\bHaritval\b', 'Haritwal'),
    (r'\bDudhval\b', 'Dudhwal'),
    (r'\bSikhval\b', 'Sikhwal'),
    # --- Batch 2: Sanskrit Inherent 'a' Truncation -y -> -ya ---
    (r'\bAchary\b', 'Acharya'),
    (r'\bAry\b', 'Arya'),
    (r'\bMaury\b', 'Maurya'),
    (r'\bMory\b', 'Maurya'),
    # --- Batch 3: Phonetic / OCR Repair for 'w' -> 'v' and Suffix Anomalies ---
    (r'\bGauraw\b', 'Gaurav'),
    (r'\bSauraw\b', 'Saurav'),
    (r'\bBhairaw\b', 'Bhairav'),
    (r'\bArwind\b', 'Arvind'),
    (r'\bDhruw\b', 'Dhruv'),
    (r'\bAraw\b', 'Aarav'),
    (r'\bNeeraw\b', 'Neerav'),
    (r'\bWasudev\b', 'Vasudev'),
    (r'\bWajpeyi\b', 'Vajpayee'),
    (r'\bVajpeyi\b', 'Vajpayee'),
    (r'\bRajeshvri\b', 'Rajeshwari'),
    (r'\bMaheshvri\b', 'Maheshwari'),
    (r'\bAnnda\b', 'Anand'),
    (r'\bHemnta\b', 'Hemant'),
    (r'\bVasnta\b', 'Vasant'),
    (r'\bJaynta\b', 'Jayant'),
    (r'\bBalvanta\b', 'Balvant'),
    (r'\b([A-Za-z]+)chanda\b', r'\g<1>chand'),
    (r'\bChunravat\b', 'Chundawat'),
    (r'\bChundavat\b', 'Chundawat'),
    (r'\bKanvar\b', 'Kanwar'),
    (r'\bKnvar\b', 'Kanwar'),
    (r'\bKnchan\b', 'Kanchan'),
    (r'\bKavar\b', 'Kanwar'),
    (r'\bGareemasih\b', 'Garima Singh'),
    (r'\bGareema\s+Singh\b', 'Garima Singh'),
    (r'\bManikhanka\b', 'Manikarnika'),
    (r'\b([A-Za-z]+)sih\b', r'\g<1> Singh'),
]


def strip_fffd_artifacts(text: Optional[str]) -> str:
    """Generic replacement-character / OCR-glyph / dangling-matra cleanup for any
    Devanagari prose field (booth names, addresses, notes, etc.) that is not a
    person name and therefore should not go through name-specific handling like
    fused-surname splitting or title-casing.
    """
    if not text:
        return ''
    cleaned = str(text).strip()
    for pattern, repl in DEVNAGARI_REPLACEMENTS:
        cleaned = re.sub(pattern, repl, cleaned)
    for pattern, repl in CANONICAL_DEVANAGARI_VOWELS:
        cleaned = re.sub(pattern, repl, cleaned)
    # Strip any stray leading combining diacritics / orphan matras at word boundary
    cleaned = re.sub(r'(^|\s)[\u0901-\u0903\u093a-\u094f\u0962\u0963]+', r'\1', cleaned)
    # Reconnect split halants and detached matras
    cleaned = re.sub(r'्\s+', '्', cleaned)
    cleaned = re.sub(r'\s+([ा-्ंँः़ॅ-ौ])', r'\1', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()


def clean_text_or_structure(obj: Any) -> Any:
    """Recursively walks dicts/lists to sanitize all strings with strip_fffd_artifacts."""
    if isinstance(obj, str):
        return strip_fffd_artifacts(obj)
    elif isinstance(obj, dict):
        return {k: clean_text_or_structure(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_text_or_structure(item) for item in obj]
    return obj


REVERSE_NAME_LEXICON = {
    'arwind kumar': 'अरविंद कुमार',
    'arvind kumar': 'अरविंद कुमार',
    'amrit lal': 'अमृत लाल',
    'priya sharma': 'प्रिया शर्मा',
    'amit sharma': 'अमित शर्मा',
    'ramesh sharma': 'रमेश शर्मा',
    'narinder babar': 'नरेंद्र बब्बर',
    'wasudev babar': 'वासुदेव बब्बर',
    'akhil chawla': 'अखिल चावला',
    'chatar dass': 'चतर दास',
    'ravinder kumar': 'रविंदर कुमार',
    'om parkash': 'ओम प्रकाश',
    'aman kumar': 'अमन कुमार',
    'jeewan dass': 'जीवन दास',
    'rajnish': 'रजनीश',
    'guljari lal': 'गुलजारी लाल',
    'avtar singh': 'अवतार सिंह',
    'jangir singh': 'जांगिड़ सिंह',
    'puspa rani': 'पुष्पा रानी',
    'pushpa rani': 'पुष्पा रानी',
    'pushpa raani': 'पुष्पा रानी',
    'subhash kumar': 'सुभाष कुमार',
    'surjit singh': 'सुरजीत सिंह',
    'mukesh kumar': 'मुकेश कुमार',
    'seema rani': 'सीमा रानी',
    'bharat chand': 'भारत चंद',
    'shakuntla': 'शकुंतला',
    'hardiyal chand': 'हरदयाल चंद',
    'bansi lal': 'बंसी लाल',
}


def clean_devnagari_name(name_hi: Optional[str]) -> str:
    """Cleans OCR artifacts, restores ligatures, canonicalizes vowels, strips scraper leaks,
    and applies dictionary-gated reverse transliteration for verified Latin-only names.
    """
    if not name_hi:
        return ''
    raw = str(name_hi).strip()
    if re.match(r'(?i)^\s*(not\s+)?available\s*$', raw):
        return ''

    # Check verified reverse lexicon if input is pure Latin
    clean_latin_lookup = raw.lower().strip(' ,.-')
    if clean_latin_lookup in REVERSE_NAME_LEXICON:
        return REVERSE_NAME_LEXICON[clean_latin_lookup]

    cleaned = strip_fffd_artifacts(raw)
    cleaned = re.sub(r'(?i)\b(not\s+)?available\b', '', cleaned).strip()
    # Detach fused surnames
    cleaned = FUSED_SURNAME_PATTERN.sub(r' \1', cleaned)
    # Detach fused honorifics & suffixes (देवी, बाई, कंवर, सिंह)
    for pattern, repl in FUSED_HONORIFIC_RULES:
        cleaned = re.sub(pattern, repl, cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()


def clean_english_name(name_en: Optional[str], fallback_hi: Optional[str] = None) -> str:
    """Cleans English transliteration, expands -dr -> -dra, handles Devanagari contamination,
    or regenerates from Hindi.
    """
    val = str(name_en or '').strip()
    # Strip scraper leaks
    if not val or re.match(r'(?i)^\s*(not\s+)?available\s*$', val) or val in ('?', '-', '--', 'voter'):
        if fallback_hi:
            clean_hi = clean_devnagari_name(fallback_hi)
            return transliterate_name(clean_hi)
        return ''
    val = re.sub(r'(?i)\b(not\s+)?available\b', '', val).strip()

    # Strip corrupt chars
    val = val.replace('\ufffd', '').replace('`', '').strip()

    # Handle Devanagari characters in English field
    if re.search(r'[\u0900-\u097F]', val):
        deva_count = len(re.findall(r'[\u0900-\u097F]', val))
        latin_count = len(re.findall(r'[a-zA-Z]', val))
        if deva_count > latin_count or latin_count == 0:
            # Predominantly Devanagari (e.g. 'शक्ति सिंह', 'अलअमन कुरेशी')
            source_hi = clean_devnagari_name(fallback_hi or val)
            return transliterate_name(source_hi)
        else:
            # Stray combining diacritics/nuktas/viramas in Latin text (e.g. 'Laksh्mi' -> 'Lakshmi')
            val = re.sub(r'[\u0900-\u097F]', '', val).strip()

    # If empty or corrupted or contains Dhamadr, regenerate from clean Hindi
    if not val or 'dhamadr' in val.lower() or len(val) <= 1:
        if fallback_hi:
            clean_hi = clean_devnagari_name(fallback_hi)
            return transliterate_name(clean_hi)
        return ''

    # Apply English fixes & cluster expansions
    for pattern, repl in ENGLISH_CLUSTER_FIXES:
        val = re.sub(pattern, repl, val, flags=re.IGNORECASE)

    # Title-case each word properly
    val = ' '.join(w.capitalize() for w in val.split())
    return val


def normalize_voter_names(
    name_hi: Optional[str],
    name_en: Optional[str],
    rel_name_hi: Optional[str],
    rel_name_en: Optional[str]
) -> Tuple[str, str, str, str, str, str]:
    """
    Returns:
    (name_hi, name_en, name_norm, rel_name_hi, rel_name_en, rel_norm)
    all fully cleaned, synchronized, and idempotent.
    """
    c_name_hi = clean_devnagari_name(name_hi)
    c_rel_hi = clean_devnagari_name(rel_name_hi)

    bad_en_tokens = {
        'dhamadr', 'rathaur', 'rathau', 'rathod', 'chaudhri', 'choudhri',
        'jangir', 'kumavat', 'sisodiya', 'ni ram', 'niram', 'jnani',
        'la singh', 'lasingh', 'lachanda', 'raka prasad', 'ma devi',
        'ma singh', 'masingh', 'ma kumar', 'makumar', 'malta', 'lada davi',
        'hada ram', 'hadaram', 'sandip', 'sadip', 'kuldip', 'pradip',
        'gauraw', 'sauraw', 'bhairaw', 'arwind', 'dhruw', 'neeraw',
        'wasudev', 'wajpeyi', 'rajeshvri', 'maheshvri', 'annda', 'hemnta'
    }
    def needs_regen(en, hi, c_hi):
        if not en or '\ufffd' in str(en) or c_hi != (hi or '').strip():
            return True
        en_str = str(en).strip()
        if len(en_str) <= 1 or re.search(r'[\u0900-\u097F]', en_str):
            return True
        en_lower = en_str.lower()
        if en_lower in ('voter', 'available', 'not available', '?', '-', '--'):
            return True
        return any(t in en_lower for t in bad_en_tokens)

    # If Hindi was repaired or English was missing/corrupt, regenerate English
    if needs_regen(name_en, name_hi, c_name_hi):
        c_name_en = transliterate_name(c_name_hi)
    else:
        c_name_en = clean_english_name(name_en, fallback_hi=c_name_hi)
    c_name_en = clean_english_name(c_name_en, fallback_hi=c_name_hi)

    if needs_regen(rel_name_en, rel_name_hi, c_rel_hi):
        c_rel_en = transliterate_name(c_rel_hi)
    else:
        c_rel_en = clean_english_name(rel_name_en, fallback_hi=c_rel_hi)
    c_rel_en = clean_english_name(c_rel_en, fallback_hi=c_rel_hi)

    name_norm = c_name_en.lower() if c_name_en else c_name_hi.lower()
    rel_norm = c_rel_en.lower() if c_rel_en else c_rel_hi.lower()

    return c_name_hi, c_name_en, name_norm, c_rel_hi, c_rel_en, rel_norm


def build_search_doc(
    name_hi: Optional[str],
    name_en: Optional[str],
    name_norm: Optional[str],
    rel_name_hi: Optional[str],
    rel_name_en: Optional[str],
    rel_norm: Optional[str],
    house_no: Optional[str] = None,
    epic_no: Optional[str] = None
) -> str:
    """Builds standard search_doc for PostgreSQL pg_trgm GIN index."""
    parts = [
        (name_hi or '').strip(),
        (name_en or '').strip(),
        (name_norm or '').strip(),
        (rel_name_hi or '').strip(),
        (rel_name_en or '').strip(),
        (rel_norm or '').strip(),
        (str(house_no) if house_no else '').strip(),
        (epic_no or '').strip()
    ]
    doc = ' '.join(p for p in parts if p)
    return re.sub(r'\s+', ' ', doc).strip()

