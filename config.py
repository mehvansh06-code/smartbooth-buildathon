import os
import json
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'campaign_config.json')

DEFAULT_CONFIG = {
    'candidate': {
        'name_hi': '',
        'name_en': '',
        'photo_filename': '',
        'fallback_photo_filename': ''
    },
    'election': {
        'election_type': 'SARPANCH',
        'ward_no': '1-9',
        'ward_name_hi': 'ग्राम पंचायत जेलातरा',
        'ward_name_en': 'Gram Panchayat Jelatra',
        'constituency': '144-Sanchore, Jalore',
        'city': 'Jelatra',
        'app_title': 'जेलातरा चुनाव साथी',
        'portal_subtitle': 'ग्राम पंचायत चुनाव 2026 — जेलातरा (सांचौर, जालोर)'
    },
    'party': {
        'name_hi': '',
        'name_en': '',
        'symbol_name_hi': '',
        'symbol_name_en': '',
        'symbol_filename': '',
        'theme_color': '#2563eb'
    },
    'campaign_message': 'ग्राम पंचायत जेलातरा चुनाव 2026 — आपकी डिजिटल मतदाता सूचना पर्ची संलग्न है। मतदान हमारा अधिकार एवं कर्तव्य है।',
    'database': {
        'db_filename': 'voters_jelatra.db'
    },
    'booths': {}
}

def load_campaign_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cfg = dict(DEFAULT_CONFIG)
                for k, v in data.items():
                    if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                        cfg[k] = {**cfg[k], **v}
                    else:
                        cfg[k] = v
                return cfg
        except Exception as e:
            print(f'[CONFIG WARN] Failed to read {CONFIG_PATH}: {e}')
    return DEFAULT_CONFIG

CAMPAIGN = load_campaign_config()
ENABLE_VOICE_MODULE = os.environ.get('ENABLE_VOICE_MODULE', 'false').lower() == 'true'

def get_db_path():
    db_name = os.environ.get('VOTER_DB_NAME') or CAMPAIGN.get('database', {}).get('db_filename', 'voters_jelatra.db')
    if os.path.isabs(db_name):
        return db_name
    return os.path.join(BASE_DIR, db_name)

DB_PATH = get_db_path()
DEFAULT_CAMPAIGN_MSG = CAMPAIGN.get('campaign_message', DEFAULT_CONFIG['campaign_message'])

def get_booths_dict(db_path=None):
    # 1. First check explicit booths in config
    cfg_booths = CAMPAIGN.get('booths') or {}
    if cfg_booths:
        formatted = {}
        for k, v in cfg_booths.items():
            try:
                p_num = int(k)
                formatted[p_num] = {
                    'booth_no': v.get('booth_no', p_num),
                    'name': v.get('name', f'भाग {p_num}'),
                    'name_en': v.get('name_en', f'Part {p_num}'),
                    'area': v.get('area', ''),
                    'maps_url': v.get('maps_url', '')
                }
            except Exception:
                continue
        if formatted:
            return formatted

    # 2. Fallback: Auto-discover distinct parts from SQLite database
    target_db = db_path or get_db_path()
    discovered = {}
    if os.path.exists(target_db):
        try:
            conn = sqlite3.connect(target_db, timeout=5.0)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT DISTINCT part_no, 
                       coalesce(booth_name, '') as b_name
                FROM voters 
                WHERE part_no IS NOT NULL AND part_no > 0
                ORDER BY part_no
            """)
            for r in c.fetchall():
                p_num = int(r['part_no'])
                b_name = r['b_name'].strip()
                discovered[p_num] = {
                    'booth_no': p_num,
                    'name': b_name or f'भाग {p_num}',
                    'name_en': f'Part {p_num}',
                    'area': '',
                    'maps_url': ''
                }
            conn.close()
        except Exception as e:
            print(f'[CONFIG WARN] Could not auto-discover booths from DB: {e}')

    return discovered

BOOTHS = get_booths_dict(DB_PATH)
