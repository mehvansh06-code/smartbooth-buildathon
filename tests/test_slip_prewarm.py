"""
test_slip_prewarm.py
Unit tests for background slip pre-generation and inter-process prewarm cache.
"""

import os
import time
import json
import unittest
import tempfile
from unittest.mock import patch, MagicMock

from electionapp.slip_prewarm import (
    build_cache_key,
    get_prewarmed_slip,
    trigger_background_prewarm,
    _get_db,
    init_prewarm_db,
    PREWARM_DB_PATH
)


class TestSlipPrewarm(unittest.TestCase):
    def setUp(self):
        init_prewarm_db()

    def test_deterministic_cache_key(self):
        k1 = build_cache_key('ward82', 'Single', [101, 102])
        k2 = build_cache_key('ward82', 'Single', [102, 101])
        self.assertEqual(k1, k2, "Cache key must be identical regardless of ID order")

        k3 = build_cache_key('ward82', 'Family', [101, 102])
        self.assertNotEqual(k1, k3, "Different slip types must produce different keys")

    def test_cache_miss_returns_none(self):
        paths, media = get_prewarmed_slip('ward82', 'Single', [999999], wait_timeout=0.1)
        self.assertIsNone(paths)
        self.assertIsNone(media)

    def test_cache_hit_with_valid_files(self):
        # Create a temp file simulating a rendered card
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b'X' * 10000)
            tmp_card_path = f.name

        try:
            cache_key = build_cache_key('ward82', 'Single', [555])
            now = time.time()
            media_map = {tmp_card_path: {'primary': 'media_123456'}}

            with _get_db() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO prewarm_cache
                    (cache_key, tenant, slip_type, voter_ids, status, image_paths, media_ids, created_at, expires_at)
                    VALUES (?, ?, ?, ?, 'READY', ?, ?, ?, ?)
                    """,
                    (cache_key, 'ward82', 'Single', '[555]', json.dumps([tmp_card_path]), json.dumps(media_map), now, now + 300)
                )

            paths, media = get_prewarmed_slip('ward82', 'Single', [555], wait_timeout=0.1)
            self.assertIsNotNone(paths)
            self.assertEqual(paths, [tmp_card_path])
            self.assertIsNotNone(media)
            self.assertEqual(media[tmp_card_path]['primary'], 'media_123456')

        finally:
            if os.path.exists(tmp_card_path):
                os.remove(tmp_card_path)

    def test_expired_cache_entry_returns_none(self):
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b'X' * 10000)
            tmp_card_path = f.name

        try:
            cache_key = build_cache_key('ward82', 'Single', [777])
            now = time.time()

            with _get_db() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO prewarm_cache
                    (cache_key, tenant, slip_type, voter_ids, status, image_paths, media_ids, created_at, expires_at)
                    VALUES (?, ?, ?, ?, 'READY', ?, ?, ?, ?)
                    """,
                    (cache_key, 'ward82', 'Single', '[777]', json.dumps([tmp_card_path]), '{}', now - 400, now - 100)
                )

            paths, media = get_prewarmed_slip('ward82', 'Single', [777], wait_timeout=0.1)
            self.assertIsNone(paths)
            self.assertIsNone(media)

        finally:
            if os.path.exists(tmp_card_path):
                os.remove(tmp_card_path)

    def test_in_flight_timeout_falls_back_gracefully(self):
        cache_key = build_cache_key('ward82', 'Single', [888])
        now = time.time()

        with _get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO prewarm_cache
                (cache_key, tenant, slip_type, voter_ids, status, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'IN_FLIGHT', ?, ?)
                """,
                (cache_key, 'ward82', 'Single', '[888]', now, now + 60)
            )

        # Calling with a 0.15s timeout should gracefully return None without error
        t0 = time.time()
        paths, media = get_prewarmed_slip('ward82', 'Single', [888], wait_timeout=0.15)
        elapsed = time.time() - t0

        self.assertIsNone(paths)
        self.assertIsNone(media)
        self.assertGreaterEqual(elapsed, 0.10)

    def test_fallback_lookup_by_voter_ids(self):
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as f:
            f.write(b'X' * 10000)
            tmp_card_path = f.name

        try:
            # Stored as 'Single', requested as 'Combined'
            cache_key = build_cache_key('ward82', 'Single', [12345])
            now = time.time()
            media_map = {tmp_card_path: {'primary': 'media_999'}}

            with _get_db() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO prewarm_cache
                    (cache_key, tenant, slip_type, voter_ids, status, image_paths, media_ids, created_at, expires_at)
                    VALUES (?, ?, ?, ?, 'READY', ?, ?, ?, ?)
                    """,
                    (cache_key, 'ward82', 'Single', '[12345]', json.dumps([tmp_card_path]), json.dumps(media_map), now, now + 300)
                )

            # Even though requested slip_type is Combined, it falls back to matching voter_ids
            paths, media = get_prewarmed_slip('ward82', 'Combined', [12345], wait_timeout=0.1)
            self.assertIsNotNone(paths)
            self.assertEqual(paths, [tmp_card_path])
            self.assertIsNotNone(media)
            self.assertEqual(media[tmp_card_path]['primary'], 'media_999')

        finally:
            if os.path.exists(tmp_card_path):
                os.remove(tmp_card_path)


if __name__ == '__main__':
    unittest.main()
