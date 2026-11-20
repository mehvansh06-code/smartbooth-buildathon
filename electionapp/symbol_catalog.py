"""
electionapp/symbol_catalog.py
Authoritative Rajasthan State Election Commission (SEC) Free Symbols Catalog:
- SARPANCH_SYMBOLS: 40 official symbols for Gram Panchayat Sarpanch elections
- WARD_PANCH_SYMBOLS: 40 official symbols for Ward Panch elections
- POPULAR_FREE_SYMBOLS: Additional widely-used free symbols (Tractor, Handpump, etc.)
- POLITICAL_PARTY_SYMBOLS: Recognized state and national political parties
- COMPREHENSIVE_SYMBOL_MAP: High-recall mapping supporting Hindi, English, and phonetic aliases
"""

from typing import List, Dict, Any, Optional
import os

SARPANCH_SYMBOLS: List[Dict[str, Any]] = [
    {"id": 1, "name_hi": "प्रेस", "en_name": "Press / Iron", "file": "press_iron.png", "keywords": "प्रेस इस्त्री iron press kapde"},
    {"id": 2, "name_hi": "स्लेट", "en_name": "Slate", "file": "slate.png", "keywords": "स्लेट slate patti takhti"},
    {"id": 3, "name_hi": "कटहल", "en_name": "Jackfruit", "file": "jackfruit.png", "keywords": "कटहल jackfruit kathal phal"},
    {"id": 4, "name_hi": "जुराबें", "en_name": "Socks", "file": "socks.png", "keywords": "जुराबें मोजे socks moje jurab"},
    {"id": 5, "name_hi": "केतली", "en_name": "Kettle", "file": "kettle.png", "keywords": "केतली kettle chai ketli"},
    {"id": 6, "name_hi": "सोफा", "en_name": "Sofa", "file": "sofa.png", "keywords": "सोफा sofa couch seat"},
    {"id": 7, "name_hi": "भिंडी", "en_name": "Ladyfinger", "file": "ladyfinger.png", "keywords": "भिंडी ladyfinger okra bhindi sabzi"},
    {"id": 8, "name_hi": "हेलमेट", "en_name": "Helmet", "file": "helmet.png", "keywords": "हेलमेट helmet topa"},
    {"id": 9, "name_hi": "लेडी पर्स", "en_name": "Ladies Purse", "file": "ladies_purse.png", "keywords": "लेडी पर्स पर्स handbag purse batua"},
    {"id": 10, "name_hi": "स्टूल", "en_name": "Stool", "file": "stool.png", "keywords": "स्टूल stool tipai baithak"},
    {"id": 11, "name_hi": "कुण्डी", "en_name": "Door Latch", "file": "door_latch.png", "keywords": "कुण्डी सांकल kundi latch sankal darwaja"},
    {"id": 12, "name_hi": "झूला", "en_name": "Swing", "file": "swing.png", "keywords": "झूला swing jhula jhulaa"},
    {"id": 13, "name_hi": "लैटर बॉक्स", "en_name": "Letter Box", "file": "letter_box.png", "keywords": "लैटर बॉक्स डाक पेटी letterbox postbox dak peti"},
    {"id": 14, "name_hi": "सिरिंज", "en_name": "Syringe", "file": "syringe.png", "keywords": "सिरिंज सुई syringe injection sui"},
    {"id": 15, "name_hi": "माचिस की डिब्बी", "en_name": "Matchbox", "file": "matchbox.png", "keywords": "माचिस की डिब्बी माचिस matchbox marchis diya salai"},
    {"id": 16, "name_hi": "मेज", "en_name": "Table", "file": "table.png", "keywords": "मेज टेबल table mej"},
    {"id": 17, "name_hi": "माइक", "en_name": "Mic", "file": "mic.png", "keywords": "माइक माईक mic microphone bonga"},
    {"id": 18, "name_hi": "टैंट", "en_name": "Tent", "file": "tent.png", "keywords": "टैंट टेंट तंबू tent tambu"},
    {"id": 19, "name_hi": "कढ़ाई", "en_name": "Cauldron / Pan", "file": "cauldron_pan.png", "keywords": "कढ़ाई pan kadhai wok"},
    {"id": 20, "name_hi": "कैमरा", "en_name": "Camera", "file": "camera.png", "keywords": "कैमरा camera photo"},
    {"id": 21, "name_hi": "नाशपाती", "en_name": "Pear", "file": "pear.png", "keywords": "नाशपाती pear nashpati phal"},
    {"id": 22, "name_hi": "चिमटा", "en_name": "Tongs", "file": "tongs.png", "keywords": "चिमटा tongs chimta roti"},
    {"id": 23, "name_hi": "ट्यूब लाइट", "en_name": "Tube Light", "file": "tube_light.png", "keywords": "ट्यूब लाइट tubelight tube light batti"},
    {"id": 24, "name_hi": "दांत ब्रश", "en_name": "Toothbrush", "file": "toothbrush.png", "keywords": "दांत ब्रश टूथब्रश toothbrush brush dant"},
    {"id": 25, "name_hi": "मूसल और खरल", "en_name": "Mortar & Pestle", "file": "mortar_pestle.png", "keywords": "मूसल और खरल ओखली mortar pestle okhli danka"},
    {"id": 26, "name_hi": "टूथपेस्ट", "en_name": "Toothpaste", "file": "toothpaste.png", "keywords": "टूथपेस्ट toothpaste colgate paste"},
    {"id": 27, "name_hi": "पेट्रोल पम्प", "en_name": "Petrol Pump", "file": "petrol_pump.png", "keywords": "पेट्रोल पम्प petrol pump tel diesel"},
    {"id": 28, "name_hi": "ट्रक", "en_name": "Truck", "file": "truck.png", "keywords": "ट्रक truck larry dumper gadi"},
    {"id": 29, "name_hi": "करनी", "en_name": "Mason Trowel", "file": "mason_trowel.png", "keywords": "करनी trowel karni mistri plaster"},
    {"id": 30, "name_hi": "टायर", "en_name": "Tyre", "file": "tyre.png", "keywords": "टायर tyre tire pahiya rubber"},
    {"id": 31, "name_hi": "बिस्कुट", "en_name": "Biscuit", "file": "biscuit.png", "keywords": "बिस्कुट biscuit cookie biskut"},
    {"id": 32, "name_hi": "छड़ी", "en_name": "Walking Stick", "file": "walking_stick.png", "keywords": "छड़ी लाठी walking stick cane lathi soti"},
    {"id": 33, "name_hi": "प्रेशर कुकर", "en_name": "Pressure Cooker", "file": "pressure_cooker.png", "keywords": "प्रेशर कुकर प्रैशर कुकर cooker pressure cooker"},
    {"id": 34, "name_hi": "दीवार खूंटी", "en_name": "Wall Peg", "file": "wall_peg.png", "keywords": "दीवार खूंटी खूंटी peg hook diwar khunti"},
    {"id": 35, "name_hi": "रेजर", "en_name": "Razor", "file": "razor.png", "keywords": "रेजर उस्तरा ब्लेड razor blade ustara shaving"},
    {"id": 36, "name_hi": "अखरोट", "en_name": "Walnut", "file": "walnut.png", "keywords": "अखरोट walnut akhrot dryfruit"},
    {"id": 37, "name_hi": "खिड़की", "en_name": "Window", "file": "window.png", "keywords": "खिड़की खड़की window khidki jharokha"},
    {"id": 38, "name_hi": "तरबूज", "en_name": "Watermelon", "file": "watermelon.png", "keywords": "तरबूज मतीरा watermelon tarbooj matira"},
    {"id": 39, "name_hi": "रोड रोलर", "en_name": "Road Roller", "file": "road_roller.png", "keywords": "रोड रोलर road roller roller sarak"},
    {"id": 40, "name_hi": "पानी का टैंक", "en_name": "Water Tank", "file": "water_tank.png", "keywords": "पानी का टैंक सिंटेक्स water tank tanki pani"},
]

WARD_PANCH_SYMBOLS: List[Dict[str, Any]] = [
    {"id": 1, "name_hi": "अलमारी", "en_name": "Almirah", "file": "almirah.png", "keywords": "अलमारी almirah wardrobe cupboard almari"},
    {"id": 2, "name_hi": "चप्पलें", "en_name": "Slippers", "file": "slippers.png", "keywords": "चप्पलें चप्पल slippers chappal hawai"},
    {"id": 3, "name_hi": "सेब", "en_name": "Apple", "file": "apple.png", "keywords": "सेब apple seb fruit phal"},
    {"id": 4, "name_hi": "कोट", "en_name": "Coat", "file": "coat.png", "keywords": "कोट coat blazer suit kapda"},
    {"id": 5, "name_hi": "गुब्बारा", "en_name": "Balloon", "file": "balloon.png", "keywords": "गुब्बारा balloon gubbara hawa"},
    {"id": 6, "name_hi": "चारपाई", "en_name": "Cot", "file": "cot.png", "keywords": "चारपाई खाट cot khat charpai palang"},
    {"id": 7, "name_hi": "चूड़ियां", "en_name": "Bangles", "file": "bangles.png", "keywords": "चूड़ियां चूड़ी bangles churi kangan"},
    {"id": 8, "name_hi": "मिक्सी", "en_name": "Mixer Grinder", "file": "mixer.png", "keywords": "मिक्सी mixer grinder mixi masala"},
    {"id": 9, "name_hi": "मोतियों का हार", "en_name": "Pearl Necklace", "file": "pearl_necklace.png", "keywords": "मोतियों का हार हार necklace moti haar jewar"},
    {"id": 10, "name_hi": "पेन्सिल डिब्बा", "en_name": "Pencil Box", "file": "pencil_box.png", "keywords": "पेन्सिल डिब्बा pencil box compass box dibba"},
    {"id": 11, "name_hi": "बैंच", "en_name": "Bench", "file": "bench.png", "keywords": "बैंच बेंच bench baithak"},
    {"id": 12, "name_hi": "तकिया", "en_name": "Pillow", "file": "pillow.png", "keywords": "तकिया pillow takiya godari"},
    {"id": 13, "name_hi": "नाव", "en_name": "Boat with Sail", "file": "boat_sail.png", "keywords": "नाव नौका आदमी व पाल युक्त नौका boat sail boat nava nauka"},
    {"id": 14, "name_hi": "बिजली का खंभा", "en_name": "Electric Pole", "file": "electric_pole.png", "keywords": "बिजली का खंभा खंभा electric pole bijli khamba tower light"},
    {"id": 15, "name_hi": "बक्सा", "en_name": "Box / Trunk", "file": "box_trunk.png", "keywords": "बक्सा पेटी trunk box baksa peti trank"},
    {"id": 16, "name_hi": "बांसुरी", "en_name": "Flute", "file": "flute.png", "keywords": "बांसुरी flute bansuri murli sangeet"},
    {"id": 17, "name_hi": "ईंटें", "en_name": "Bricks", "file": "bricks.png", "keywords": "ईंटें ईंट bricks inte eent bhavan makan"},
    {"id": 18, "name_hi": "फ्रॉक", "en_name": "Frock", "file": "frock.png", "keywords": "फ्रॉक frock dress frock kapda"},
    {"id": 19, "name_hi": "बाल्टी", "en_name": "Bucket", "file": "bucket.png", "keywords": "बाल्टी bucket balti pani dhol"},
    {"id": 20, "name_hi": "रेफ्रिजरेटर", "en_name": "Refrigerator", "file": "refrigerator.png", "keywords": "रेफ्रिजरेटर फ्रिज refrigerator fridge barf sardi"},
    {"id": 21, "name_hi": "लिफाफा", "en_name": "Envelope", "file": "envelope.png", "keywords": "लिफाफा envelope lifafa chithi patra"},
    {"id": 22, "name_hi": "टेलीविजन", "en_name": "Television", "file": "television.png", "keywords": "टेलीविजन टीवी television tv dordarshan"},
    {"id": 23, "name_hi": "फव्वारा", "en_name": "Fountain", "file": "fountain.png", "keywords": "फव्वारा fountain fawara pani garden"},
    {"id": 24, "name_hi": "टेलीफोन", "en_name": "Telephone", "file": "telephone.png", "keywords": "टेलीफोन फोन telephone phone landline"},
    {"id": 25, "name_hi": "फूलगोभी", "en_name": "Cauliflower", "file": "cauliflower.png", "keywords": "फूलगोभी गोभी cauliflower gobhi sabzi"},
    {"id": 26, "name_hi": "गैस का चूल्हा", "en_name": "Gas Stove", "file": "gas_stove.png", "keywords": "गैस का चूल्हा चूल्हा gas stove chulha rasoi cylinder"},
    {"id": 27, "name_hi": "जंजीर", "en_name": "Chain", "file": "chain.png", "keywords": "जंजीर chain janjir sankar loha"},
    {"id": 28, "name_hi": "टॉफियां", "en_name": "Toffees", "file": "toffees.png", "keywords": "टॉफियां टॉफी toffees toffee mithaai candy chocolate"},
    {"id": 29, "name_hi": "चक्की", "en_name": "Flour Mill", "file": "flour_mill.png", "keywords": "चक्की flour mill chakki atta ghanti"},
    {"id": 30, "name_hi": "साइकिल पम्प", "en_name": "Bicycle Pump", "file": "bicycle_pump.png", "keywords": "साइकिल पम्प साइकिल पंप bicycle pump cycle hawa"},
    {"id": 31, "name_hi": "अंगूर", "en_name": "Grapes", "file": "grapes.png", "keywords": "अंगूर grapes angoor dakh phal"},
    {"id": 32, "name_hi": "आरी", "en_name": "Saw", "file": "saw.png", "keywords": "आरी saw aari lakdi hathiyar tool"},
    {"id": 33, "name_hi": "हरी मिर्च", "en_name": "Green Chilli", "file": "green_chilli.png", "keywords": "हरी मिर्च मिर्च green chilli mirch hari sabzi"},
    {"id": 34, "name_hi": "स्कूल का बस्ता", "en_name": "School Bag", "file": "school_bag.png", "keywords": "स्कूल का बस्ता बस्ता school bag basta kitab padhai"},
    {"id": 35, "name_hi": "हाथ गाड़ी", "en_name": "Handcart", "file": "handcart.png", "keywords": "हाथ गाड़ी ठेला handcart thela gaddi"},
    {"id": 36, "name_hi": "कैंची", "en_name": "Scissors", "file": "scissors.png", "keywords": "कैंची scissors kainchi darji cutter"},
    {"id": 37, "name_hi": "हारमोनियम", "en_name": "Harmonium", "file": "harmonium.png", "keywords": "हारमोनियम harmonium baja sangeet sur"},
    {"id": 38, "name_hi": "सिलाई मशीन", "en_name": "Sewing Machine", "file": "sewing_machine.png", "keywords": "सिलाई मशीन सिलाई की मशीन sewing machine silai darji"},
    {"id": 39, "name_hi": "आइसक्रीम", "en_name": "Ice Cream", "file": "ice_cream.png", "keywords": "आइसक्रीम ice cream kulfi icecream meetha thanda"},
    {"id": 40, "name_hi": "जूता", "en_name": "Shoe", "file": "shoe.png", "keywords": "जूता shoe joota boot boot chappal"},
]

POPULAR_FREE_SYMBOLS: List[Dict[str, Any]] = [
    {"name_hi": "ट्रैक्टर", "en_name": "Tractor", "file": "tractor.png", "keywords": "tractor tractor.png ट्रैक्टर traktor kisan"},
    {"name_hi": "हैंडपंप", "en_name": "Handpump", "file": "handpump.png", "keywords": "handpump handpump.png हैंडपंप हैंडपम्प nalka"},
    {"name_hi": "सिलेंडर", "en_name": "Gas Cylinder", "file": "gas_cylinder.png", "keywords": "gas_cylinder gas cylinder गैस सिलेंडर सिलेंडर rasoi"},
    {"name_hi": "पंखा", "en_name": "Ceiling Fan", "file": "ceiling_fan.png", "keywords": "ceiling_fan fan छत का पंखा पंखा pankha"},
    {"name_hi": "कप प्लेट", "en_name": "Cup Saucer", "file": "cup_saucer.png", "keywords": "cup_saucer cup plate कप प्लेट कप-प्लेट chai"},
    {"name_hi": "ताला-चाबी", "en_name": "Lock & Key", "file": "lock_key.png", "keywords": "lock_key lock key ताला-चाबी ताला चाबी chabi"},
    {"name_hi": "उगता सूरज", "en_name": "Sun", "file": "sun.png", "keywords": "sun सूरज उगता सूरज suraj surya"},
    {"name_hi": "बल्ला", "en_name": "Bat", "file": "bat.png", "keywords": "bat बल्ला bat cricket balla"},
    {"name_hi": "पतंग", "en_name": "Kite", "file": "kite.png", "keywords": "kite पतंग patang hawa"},
    {"name_hi": "टॉर्च", "en_name": "Torch", "file": "torch.png", "keywords": "torch टॉर्च torch batti roshni"},
    {"name_hi": "घंटी", "en_name": "Bell", "file": "bell.png", "keywords": "bell घंटी ghanti mandir"},
    {"name_hi": "तराजू", "en_name": "Scale", "file": "scale.png", "keywords": "scale तराजू taraju tula nyay"},
    {"name_hi": "छाता", "en_name": "Umbrella", "file": "umbrella.png", "keywords": "umbrella छाता chhati chata barish"},
    {"name_hi": "दीपक / दिया", "en_name": "Diya", "file": "diya.png", "keywords": "diya दीपक दिया deepak diwali"},
    {"name_hi": "गिलास", "en_name": "Glass", "file": "glass.png", "keywords": "glass गिलास kanch glass pani"},
    {"name_hi": "मोमबत्ती", "en_name": "Candle", "file": "candle.png", "keywords": "candle मोमबत्ती mombatti batti"},
    {"name_hi": "सीटी", "en_name": "Whistle", "file": "whistle.png", "keywords": "whistle सीटी seeti referee"},
]

POLITICAL_PARTY_SYMBOLS: List[Dict[str, Any]] = [
    {
        "name_hi": "कांग्रेस (INC)",
        "party_name_hi": "भारतीय राष्ट्रीय कांग्रेस (INC)",
        "name_en": "Congress",
        "file": "congress_hand.png",
        "preset_key": "congress_hand",
        "keywords": "congress_hand congress inc हाथ कांग्रेस पंज़ा"
    },
    {
        "name_hi": "भाजपा (BJP)",
        "party_name_hi": "भारतीय जनता पार्टी (BJP)",
        "name_en": "BJP",
        "file": "bjp_lotus.png",
        "preset_key": "bjp_lotus",
        "keywords": "bjp_lotus bjp lotus कमल भाजपा कमल का फूल"
    },
    {
        "name_hi": "रालोपा (RLP)",
        "party_name_hi": "राष्ट्रीय लोकतांत्रिक पार्टी (RLP)",
        "name_en": "RLP",
        "file": "rlp_bottle.png",
        "preset_key": "rlp_bottle",
        "keywords": "rlp_bottle rlp bottle बोतल रालोपा आरएलपी"
    },
    {
        "name_hi": "बीएपी (BAP)",
        "party_name_hi": "भारत आदिवासी पार्टी (BAP)",
        "name_en": "BAP",
        "file": "bap_hockey.png",
        "preset_key": "bap_hockey",
        "keywords": "bap_hockey bap hockey हॉकी बीएपी हॉकी स्टिक और गेंद"
    },
    {
        "name_hi": "आप (AAP)",
        "party_name_hi": "आम आदमी पार्टी (AAP)",
        "name_en": "AAP",
        "file": "aap_broom.png",
        "preset_key": "aap_broom",
        "keywords": "aap_broom aap broom झाड़ू आप झाडू"
    },
    {
        "name_hi": "बसपा (BSP)",
        "party_name_hi": "बहुजन समाज पार्टी (BSP)",
        "name_en": "BSP",
        "file": "bsp_elephant.png",
        "preset_key": "bsp_elephant",
        "keywords": "bsp_elephant bsp elephant हाथी बसपा"
    },
    {
        "name_hi": "सपा (SP)",
        "party_name_hi": "समाजवादी पार्टी (SP)",
        "name_en": "SP",
        "file": "sp_cycle.png",
        "preset_key": "sp_cycle",
        "keywords": "sp_cycle sp cycle साइकिल सपा साईकिल"
    },
    {
        "name_hi": "माकपा (CPI-M)",
        "party_name_hi": "मार्क्सवादी कम्युनिस्ट पार्टी (CPI-M)",
        "name_en": "CPI-M",
        "file": "cpim_hammer_sickle.png",
        "preset_key": "cpim_hammer_sickle",
        "keywords": "cpim_hammer_sickle cpim hammer sickle हँसिया हथौड़ा माकपा हंसिया-हथौड़ा"
    },
]


def build_symbol_map() -> Dict[str, str]:
    """Generates an exhaustive lookup dictionary mapping names, keywords, and aliases to symbol filenames (without ext)."""
    smap: Dict[str, str] = {}

    def _strip_ext(f: str) -> str:
        return os.path.splitext(f)[0]

    # 1. Recognized Political Parties
    party_aliases = {
        "congress_hand.png": ["हाथ", "कांग्रेस", "पंज़ा", "hand", "inc", "congress"],
        "bjp_lotus.png": ["कमल", "कमल का फूल", "भाजपा", "lotus", "bjp"],
        "rlp_bottle.png": ["बोतल", "रालोपा", "आरएलपी", "RLP", "rlp", "bottle"],
        "bap_hockey.png": ["हॉकी", "हॉकी और गेंद", "हॉकी स्टिक और गेंद", "बीएपी", "bap", "hockey"],
        "aap_broom.png": ["झाड़ू", "झाडू", "आप", "aap", "broom"],
        "bsp_elephant.png": ["हाथी", "बसपा", "bsp", "elephant"],
        "sp_cycle.png": ["साइकिल", "साईकिल", "सपा", "sp", "cycle", "bicycle"],
        "cpim_hammer_sickle.png": ["माकपा", "हंसिया-हथौड़ा", "हंसिया हथौड़ा", "हँसिया हथौड़ा", "cpim", "hammer_sickle"],
    }
    for file, aliases in party_aliases.items():
        base = _strip_ext(file)
        smap[base] = base
        smap[file] = base
        for alias in aliases:
            smap[alias] = base
            smap[alias.lower()] = base

    # 2. Sarpanch Symbols
    for item in SARPANCH_SYMBOLS:
        file = item["file"]
        base = _strip_ext(file)
        name_hi = item["name_hi"]
        smap[base] = base
        smap[file] = base
        smap[name_hi] = base
        smap[name_hi.strip()] = base
        smap[item["en_name"]] = base
        smap[item["en_name"].lower()] = base
        for kw in item["keywords"].split():
            if len(kw) >= 3 and kw not in smap:
                smap[kw] = base
                smap[kw.lower()] = base

    # 3. Ward Panch Symbols
    for item in WARD_PANCH_SYMBOLS:
        file = item["file"]
        base = _strip_ext(file)
        name_hi = item["name_hi"]
        smap[base] = base
        smap[file] = base
        smap[name_hi] = base
        smap[name_hi.strip()] = base
        smap[item["en_name"]] = base
        smap[item["en_name"].lower()] = base
        for kw in item["keywords"].split():
            if len(kw) >= 3 and kw not in smap:
                smap[kw] = base
                smap[kw.lower()] = base

    # 4. Popular Free Symbols
    for item in POPULAR_FREE_SYMBOLS:
        file = item["file"]
        base = _strip_ext(file)
        name_hi = item["name_hi"]
        smap[base] = base
        smap[file] = base
        smap[name_hi] = base
        smap[name_hi.strip()] = base
        smap[item["en_name"]] = base
        smap[item["en_name"].lower()] = base
        for kw in item["keywords"].split():
            if len(kw) >= 3 and kw not in smap:
                smap[kw] = base
                smap[kw.lower()] = base

    # 5. Legacy aliases
    legacy_map = {
        "ट्रैक्टर": "tractor",
        "tractor": "tractor",
        "हैंडपंप": "handpump",
        "हैंडपम्प": "handpump",
        "handpump": "handpump",
        "ताला-चाबी": "lock_key",
        "ताला चाबी": "lock_key",
        "ताला": "lock_key",
        "lock": "lock_key",
        "lock_key": "lock_key",
        "बल्ला": "bat",
        "bat": "bat",
        "सिलेंडर": "gas_cylinder",
        "गैस सिलेंडर": "gas_cylinder",
        "gas_cylinder": "gas_cylinder",
        "पतंग": "kite",
        "kite": "kite",
        "सूरज": "sun",
        "उगता सूरज": "sun",
        "sun": "sun",
        "छत का पंखा": "ceiling_fan",
        "पंखा": "ceiling_fan",
        "fan": "ceiling_fan",
        "कप प्लेट": "cup_saucer",
        "कप-प्लेट": "cup_saucer",
        "टॉर्च": "torch",
        "torch": "torch",
        "घंटी": "bell",
        "bell": "bell",
        "तराजू": "scale",
        "scale": "scale",
        "छाता": "umbrella",
        "umbrella": "umbrella",
        "दीपक": "diya",
        "दिया": "diya",
        "diya": "diya",
        "गिलास": "glass",
        "glass": "glass",
        "मोमबत्ती": "candle",
        "candle": "candle",
        "सीटी": "whistle",
        "whistle": "whistle",
    }
    for k, v in legacy_map.items():
        if k not in smap:
            smap[k] = v

    return smap


# Pre-built canonical symbol lookup dictionary
COMPREHENSIVE_SYMBOL_MAP = build_symbol_map()
