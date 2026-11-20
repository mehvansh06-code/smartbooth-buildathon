# -*- coding: utf-8 -*-
"""Mine a Hindi-word -> English-spelling lexicon from a database that has been corrected.

Names are the part of a roll where being merely plausible is not good enough, and generic
transliteration rules only get so far on local surnames. A campaign that has already been
through a cycle has something better: spellings argued over by people who know the names.
Whatever survived that is worth more than any rule.

Two things are stripped on the way in:
  * machine-translation wreckage, which is how पिस्ता became Pistachio and सूर्यप्रकाश
    became Sunlight in the Ward 82 data;
  * anything whose English form reads as an English dictionary word rather than a name.
"""
import re
import json
import sqlite3
import collections

from .gate import looks_english

MIN_COUNT = 2          # a word must be seen at least twice
MIN_SHARE = 0.50       # plurality wins: Ashok and Ashoka are both defensible, and taking
                       # the commoner one is what keeps spelling consistent

# Translations that reached production in Ward 82. Never let these back in.
KNOWN_BAD = {
    'pistachio', 'sunlight', 'pacific', 'avoidance', 'gourd', 'canal', 'pulled', 'cast',
    'chain', 'range', 'straight', 'success', 'direction', 'aura', 'flows', 'madam',
    'chimney', 'sugar', 'cover', 'per', 'menu', 'satan', 'fool', 'goody', 'bevy', 'self',
    'poker', 'talk', 'jane', 'style', 'ascending', 'aspirational', 'saffron', 'sir',
}


def _tokens(text):
    return [t for t in re.split(r'\s+', (text or '').strip()) if t]


def harvest(db_path, pairs=(('name_hi', 'name_en'), ('rel_name_hi', 'rel_name_en')),
            table='voters'):
    """Vote on word -> spelling from name pairs whose word counts line up."""
    conn = sqlite3.connect('file:%s?mode=ro' % db_path.replace('\\', '/'), uri=True)
    columns = sorted({c for pair in pairs for c in pair})
    rows = conn.execute('SELECT %s FROM %s' % (', '.join(columns), table)).fetchall()
    conn.close()

    index = {c: i for i, c in enumerate(columns)}
    votes = collections.defaultdict(collections.Counter)
    for row in rows:
        for hi_col, en_col in pairs:
            hi, en = row[index[hi_col]], row[index[en_col]]
            t_hi, t_en = _tokens(hi), _tokens(en)
            if t_hi and len(t_hi) == len(t_en):
                for a, b in zip(t_hi, t_en):
                    if re.fullmatch(r'[A-Za-z.]+', b):
                        votes[a][b.strip('.').title()] += 1
    return votes


def add_overrides(votes, corrections_json):
    """Hand-written per-voter fixes count as heavy evidence -- someone checked them."""
    with open(corrections_json, encoding='utf-8') as f:
        data = json.load(f)
    for _vid, fix in data.get('exact_overrides', {}).items():
        for hi_key, en_key in (('name_hi', 'name_en'), ('rel_name_hi', 'rel_name_en')):
            hi, en = fix.get(hi_key), fix.get(en_key)
            if hi and en:
                t_hi, t_en = _tokens(hi), _tokens(en)
                if len(t_hi) == len(t_en):
                    for a, b in zip(t_hi, t_en):
                        votes[a][b.title()] += 50
    return votes


def build(votes, min_count=MIN_COUNT, min_share=MIN_SHARE):
    """Settle each word on one spelling, dropping anything that reads as English."""
    lexicon, rejected = {}, []
    for word, counter in votes.items():
        total = sum(counter.values())
        best, n = counter.most_common(1)[0]
        if total < min_count or n / float(total) < min_share:
            continue
        if best.lower() in KNOWN_BAD or looks_english(best):
            rejected.append((word, best))
            continue
        lexicon[word] = {'en': best, 'count': total, 'share': round(n / float(total), 3)}
    return lexicon, rejected


def save(lexicon, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(lexicon, f, ensure_ascii=False, indent=0)


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)
