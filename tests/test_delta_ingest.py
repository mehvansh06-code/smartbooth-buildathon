#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_delta_ingest
=================
Unit tests for the sarpanch roll delta-ingestion core (identity / detect / differ).

Uses a real extracted ward (SEC_ROLLS_2026/RECORDS) as the "old" roll and
synthesises a "new" edition from it, so the diff logic is exercised against
genuine Devanagari data rather than toy strings.
"""
import copy
import glob
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.normpath(os.path.join(_HERE, "..", "..", "SEC_ROLLS_2026", "pipeline"))
if _PIPELINE not in sys.path:
    sys.path.insert(0, _PIPELINE)

try:
    from delta_ingest import detect, differ          # noqa: E402
    from delta_ingest.identity import clean_epic, fuzzy_key  # noqa: E402
except ImportError:
    detect = None
    differ = None
    clean_epic = None
    fuzzy_key = None

_RECORDS = os.path.normpath(os.path.join(_HERE, "..", "..", "SEC_ROLLS_2026", "RECORDS"))


def _load_a_ward():
    hits = glob.glob(os.path.join(_RECORDS, "AJMER", "*", "*", "*.json"))
    if not hits:
        return None
    import json
    with open(hits[0], encoding="utf-8") as fh:
        return json.load(fh)


def setUpModule():
    if detect is None:
        raise unittest.SkipTest("delta_ingest pipeline not available in standalone checkout")


class TestIdentity(unittest.TestCase):
    def test_clean_epic(self):
        self.assertEqual(clean_epic("ALC2266682"), "ALC2266682")
        self.assertEqual(clean_epic("  alc 226-6682 "), "ALC2266682")
        self.assertEqual(clean_epic(""), "")
        self.assertEqual(clean_epic(None), "")
        self.assertEqual(clean_epic("ABC" + chr(0xFFFD) + "123"), "")  # undecoded glyph -> unusable
        self.assertEqual(clean_epic("xx"), "")                          # too short / no digit

    def test_fuzzy_key_rejects_corrupt_name(self):
        self.assertIsNone(fuzzy_key(chr(0xFFFD) + "राम", "मोहन", "पुरुष"))
        self.assertIsNone(fuzzy_key("", "मोहन", "पुरुष"))
        self.assertIsNotNone(fuzzy_key("राम", "मोहन", "पुरुष"))


class TestDetect(unittest.TestCase):
    def test_full_from_path(self):
        recs = [{"sl_no": i, "section": "main"} for i in range(1, 30)]
        mode, _ = detect.classify(recs, "PRI/Final/296/AJAYSAR-Ward No-001.pdf")
        self.assertEqual(mode, detect.FULL)

    def test_supplement_from_path(self):
        recs = [{"sl_no": 5, "section": "जोड़"}, {"sl_no": 9, "section": "विलोपन"}]
        mode, _ = detect.classify(recs, "PRI/Supplement/296/AJAYSAR-Ward No-001.pdf")
        self.assertEqual(mode, detect.SUPPLEMENT)

    def test_conflict_when_path_and_content_disagree(self):
        # path says final, but body is clearly a tiny delta
        recs = [{"sl_no": 900, "section": "विलोपन"}]
        mode, _ = detect.classify(recs, "PRI/Final/296/AJAYSAR-Ward No-001.pdf")
        self.assertEqual(mode, detect.CONFLICT)

    def test_content_only_full(self):
        recs = [{"sl_no": i, "section": "main"} for i in range(1, 50)]
        mode, _ = detect.classify(recs, None)
        self.assertEqual(mode, detect.FULL)

    def test_supplement_path_but_full_content_trusts_content(self):
        # Rajasthan SEC's "Supplement" link is actually the portal's "Final With Supp-2"
        # document -- a complete merged re-roll, not a short delta sheet. Real downloads
        # confirmed this (majority 'main' section, contiguous serials from 1), so this
        # must resolve to FULL rather than CONFLICT.
        recs = [{"sl_no": i, "section": "main"} for i in range(1, 400)]
        mode, reason = detect.classify(recs, "PRI_UPDATES/Supplement/166/GUDA VISHNOIYAN_Ward_001.pdf")
        self.assertEqual(mode, detect.FULL)
        self.assertIn("supplement", reason)


class TestDifferSynthetic(unittest.TestCase):
    """Deterministic synthetic old/new sets (no dependency on corpus presence)."""

    def setUp(self):
        self.old = [
            {"id": 1, "epic_no": "AAA1111111", "name_hi": "कुसुम बंसल", "rel_type": "पति",
             "rel_name_hi": "हरीश बंसल", "age": 52, "gender": "स्त्री", "house_no": "0",
             "part_no": 105, "sl_no": 1, "status": "Active", "source_file": "W1.pdf"},
            {"id": 2, "epic_no": "BBB2222222", "name_hi": "हरीश बंसल", "rel_type": "पिता",
             "rel_name_hi": "रामेश्वरलाल", "age": 54, "gender": "पुरुष", "house_no": "0",
             "part_no": 105, "sl_no": 2, "status": "Active", "source_file": "W1.pdf"},
            {"id": 3, "epic_no": "CCC3333333", "name_hi": "सीता देवी", "rel_type": "पति",
             "rel_name_hi": "मोहन लाल", "age": 40, "gender": "स्त्री", "house_no": "5",
             "part_no": 105, "sl_no": 3, "status": "Active", "source_file": "W1.pdf"},
            {"id": 4, "epic_no": "", "name_hi": "बसीर", "rel_type": "पिता",
             "rel_name_hi": "हकीम", "age": 36, "gender": "पुरुष", "house_no": "9",
             "part_no": 105, "sl_no": 4, "status": "Deleted", "del_reason": "विलोपन",
             "source_file": "W1.pdf"},
        ]

    def test_supplement_add_and_delete(self):
        new = [
            # explicit deletion of voter #2 (supplement विलोपन row)
            {"epic_no": "BBB2222222", "name_hi": "हरीश बंसल", "rel_type": "पिता",
             "rel_name_hi": "रामेश्वरलाल", "age": 54, "gender": "पुरुष", "part_no": 105,
             "sl_no": 2, "status": "Deleted", "del_reason": "मृत्यु", "section": "विलोपन",
             "source_file": "W1-supp.pdf"},
            # brand-new voter
            {"epic_no": "DDD4444444", "name_hi": "नया मतदाता", "rel_type": "पिता",
             "rel_name_hi": "पिताजी", "age": 25, "gender": "पुरुष", "part_no": 105,
             "sl_no": 150, "status": "Active", "section": "जोड़", "source_file": "W1-supp.pdf"},
        ]
        res = diff = differ.diff_ward(self.old, new, detect.SUPPLEMENT)
        s = res.summary()
        self.assertEqual(s["adds"], 1)
        self.assertEqual(s["deletes"], 1)
        # voters #1 and #3 absent from supplement must NOT be deleted
        self.assertEqual(res.deletes[0]["old"]["_id"], 2)
        self.assertEqual(res.adds[0]["epic_no"], "DDD4444444")

    def test_full_reroll_infers_deletion_by_absence(self):
        # new complete roll: keep #1 (age changed), #3 revived? no -> keep #3, drop #2 entirely
        new = [
            {"epic_no": "AAA1111111", "name_hi": "कुसुम बंसल", "rel_type": "पति",
             "rel_name_hi": "हरीश बंसल", "age": 53, "gender": "स्त्री", "part_no": 105,
             "sl_no": 1, "status": "Active", "section": "main", "source_file": "W1-new.pdf"},
            {"epic_no": "CCC3333333", "name_hi": "सीता देवी", "rel_type": "पति",
             "rel_name_hi": "मोहन लाल", "age": 41, "gender": "स्त्री", "part_no": 105,
             "sl_no": 2, "status": "Active", "section": "main", "source_file": "W1-new.pdf"},
        ]
        res = differ.diff_ward(self.old, new, detect.FULL)
        s = res.summary()
        # #2 absent -> implicit delete; #1 age changed -> modify; #3 age changed -> modify
        self.assertEqual(s["deletes"], 1)
        self.assertEqual(res.deletes[0]["old"]["_id"], 2)
        self.assertEqual(s["modifies"], 2)
        self.assertEqual(s["adds"], 0)

    def test_revive_previously_deleted(self):
        # #4 (बसीर) was Deleted; a full re-roll lists him Active again -> revive
        new = [
            {"epic_no": "AAA1111111", "name_hi": "कुसुम बंसल", "rel_type": "पति",
             "rel_name_hi": "हरीश बंसल", "age": 52, "gender": "स्त्री", "part_no": 105,
             "sl_no": 1, "status": "Active", "section": "main", "source_file": "W1-new.pdf"},
            {"epic_no": "BBB2222222", "name_hi": "हरीश बंसल", "rel_type": "पिता",
             "rel_name_hi": "रामेश्वरलाल", "age": 54, "gender": "पुरुष", "part_no": 105,
             "sl_no": 2, "status": "Active", "section": "main", "source_file": "W1-new.pdf"},
            {"epic_no": "CCC3333333", "name_hi": "सीता देवी", "rel_type": "पति",
             "rel_name_hi": "मोहन लाल", "age": 40, "gender": "स्त्री", "part_no": 105,
             "sl_no": 3, "status": "Active", "section": "main", "source_file": "W1-new.pdf"},
            # बसीر, no EPIC, now active -> must fuzzy-match the deleted #4 and REVIVE
            {"epic_no": "", "name_hi": "बसीर", "rel_type": "पिता", "rel_name_hi": "हकीम",
             "age": 37, "gender": "पुरुष", "part_no": 105, "sl_no": 4, "status": "Active",
             "section": "main", "source_file": "W1-new.pdf"},
        ]
        res = differ.diff_ward(self.old, new, detect.FULL)
        s = res.summary()
        self.assertEqual(s["revives"], 1, res.summary())
        self.assertEqual(res.revives[0]["old"]["_id"], 4)
        self.assertEqual(s["deletes"], 0)
        self.assertEqual(s["adds"], 0)

    def test_stale_deletion_for_old_serial_does_not_hijack_current_voter(self):
        # SEC represents a voter whose serial number changed as TWO line items in the
        # SAME supplement: "delete old serial" + "add new serial" for the same person.
        # A stale deletion note for a serial we never held (sl_no 99, age close enough
        # to fuzzy-match #3) must not steal the match that rightfully belongs to #3's
        # own still-current row, wrongly deleting her and creating a disconnected dupe.
        new = [
            # dangling deletion for an old position we don't hold for this voter
            {"epic_no": "", "name_hi": "सीता देवी", "rel_type": "पति",
             "rel_name_hi": "मोहन लाल", "age": 38, "gender": "स्त्री", "part_no": 105,
             "sl_no": 99, "status": "Deleted", "del_reason": "विलोपन", "section": "विलोपन",
             "source_file": "W1-supp.pdf"},
            # her real, current entry -- same identity, close age, new serial
            {"epic_no": "", "name_hi": "सीता देवी", "rel_type": "पति",
             "rel_name_hi": "मोहन लाल", "age": 40, "gender": "स्त्री", "part_no": 105,
             "sl_no": 200, "status": "Active", "section": "परिवर्धन",
             "source_file": "W1-supp.pdf"},
        ]
        res = differ.diff_ward(self.old, new, detect.SUPPLEMENT)
        s = res.summary()
        # #3 (सीता देवी) must stay matched/untouched -- not deleted, not re-added.
        self.assertEqual(s["deletes"], 0, res.summary())
        self.assertEqual(s["adds"], 0, res.summary())
        self.assertEqual(s["orphans"], 1, res.summary())


class TestAccuracyGuard(unittest.TestCase):
    """A freshly-extracted name carrying U+FFFD must never overwrite a clean one."""

    def setUp(self):
        self.old = [{"id": 1, "epic_no": "AAA1111111", "name_hi": "उर्मिला", "rel_type": "पति",
                     "rel_name_hi": "रमेश", "age": 40, "gender": "स्त्री", "part_no": 105,
                     "sl_no": 1, "status": "Active", "source_file": "W1.pdf"}]

    def test_fffd_name_does_not_overwrite_clean(self):
        new = [{"epic_no": "AAA1111111", "name_hi": "उ" + chr(0xFFFD) + "मला", "rel_type": "पति",
                "rel_name_hi": "रमेश", "age": 40, "gender": "स्त्री", "part_no": 105,
                "sl_no": 1, "status": "Active", "section": "main", "source_file": "W1-new.pdf"}]
        res = differ.diff_ward(self.old, new, detect.FULL)
        self.assertNotIn("name_hi", dict(
            (k, v) for m in res.modifies for k, v in m["changes"].items()))

    def test_clean_name_does_heal_fffd(self):
        old = [{"id": 1, "epic_no": "AAA1111111", "name_hi": "उ" + chr(0xFFFD) + "मला",
                "rel_type": "पति", "rel_name_hi": "रमेश", "age": 40, "gender": "स्त्री",
                "part_no": 105, "sl_no": 1, "status": "Active", "source_file": "W1.pdf"}]
        new = [{"epic_no": "AAA1111111", "name_hi": "उर्मिला", "rel_type": "पति",
                "rel_name_hi": "रमेश", "age": 40, "gender": "स्त्री", "part_no": 105,
                "sl_no": 1, "status": "Active", "section": "main", "source_file": "W1-new.pdf"}]
        res = differ.diff_ward(old, new, detect.FULL)
        self.assertEqual(res.summary()["modifies"], 1)
        self.assertEqual(res.modifies[0]["changes"]["name_hi"], "उर्मिला")

    def test_invalid_vowel_nukta_does_not_overwrite_clean(self):
        # A real font-glyph mix-up seen in a Jodhpur Supplement ward decoded "सलीम खां"
        # as "सलीम मा़" -- valid-looking characters, but "ा़" (vowel sign + nukta) is not
        # a real Devanagari sequence, so the ordinary FFFD-count guard misses it.
        old = [{"id": 1, "epic_no": "AAA1111111", "rel_type": "पति",
                "rel_name_hi": "सलीम खां", "name_hi": "शबनम", "age": 40, "gender": "स्त्री",
                "part_no": 105, "sl_no": 1, "status": "Active", "source_file": "W1.pdf"}]
        new = [{"epic_no": "AAA1111111", "rel_type": "पति",
                "rel_name_hi": "सलीम मा़", "name_hi": "शबनम", "age": 40, "gender": "स्त्री",
                "part_no": 105, "sl_no": 1, "status": "Active", "section": "main",
                "source_file": "W1-new.pdf"}]
        res = differ.diff_ward(old, new, detect.FULL)
        self.assertNotIn("rel_name_hi", dict(
            (k, v) for m in res.modifies for k, v in m["changes"].items()))


class TestDifferAgainstRealWard(unittest.TestCase):
    def test_real_ward_roundtrips_with_no_changes(self):
        recs = _load_a_ward()
        if not recs:
            self.skipTest("no extracted AJMER ward available")
        old = []
        for i, r in enumerate(recs):
            row = copy.deepcopy(r)
            row["id"] = i + 1
            row["part_no"] = r.get("booth_no")
            old.append(row)
        # diffing a roll against itself (full) => everything matches, nothing deleted
        res = differ.diff_ward(old, recs, detect.FULL)
        s = res.summary()
        self.assertEqual(s["adds"], 0, s)
        self.assertEqual(s["deletes"], 0, s)
        # already-deleted rows in the roll stay unchanged, not re-deleted
        self.assertLessEqual(s["modifies"], 2, s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
