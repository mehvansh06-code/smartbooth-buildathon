"""
tests/test_video_stitching.py
=============================
Unit and integration tests for SmartBooth Autonomous AI Video Pipeline Engine (Task 3).
Validates:
1. Rule-based & AI name normalization (Devanagari & Latin).
2. Audio generation & fallback mechanisms.
3. Antialiased intro frame and watermark badge generation with Pillow.
4. Fast FFmpeg video concatenation and overlay pipeline.
5. Flask API endpoint POST /api/agent/generate_video.
"""

import os
import sys
import unittest

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app
from electionapp.services import video_stitching


class TestVideoStitchingPipeline(unittest.TestCase):
    """Test suite for personalized video creation."""

    def test_salutation_normalization_english(self):
        cases = [
            ("SMT REKHA DEVI W/O RAMESH", "Rekha ji"),
            ("SHRI MOHAN LAL SHARMA S/O KISHAN", "Mohan ji"),
            ("Rekha Devi", "Rekha ji"),
            ("KUMARI POOJA D/O SURESH", "Pooja ji"),
            ("HARCHAND RAM", "Harchand ji"),
        ]
        for raw, expected in cases:
            res = video_stitching.normalize_voter_salutation(raw)
            self.assertEqual(res, expected, f"Failed for {raw}: got {res}")

    def test_salutation_normalization_hindi(self):
        cases = [
            ("श्रीमती रेखा देवी पत्नी रमेश", "रेखा जी"),
            ("सुमित दुनीवाल", "सुमित जी"),
        ]
        for raw, expected in cases:
            res = video_stitching.normalize_voter_salutation(raw)
            self.assertEqual(res, expected, f"Failed for {raw}: got {res}")

    def test_greeting_audio_generation_and_duration(self):
        audio_path, dur = video_stitching.generate_greeting_audio("Rekha ji", "नीलू धोनीवाल")
        self.assertTrue(os.path.exists(audio_path), f"Audio file not found: {audio_path}")
        self.assertGreater(dur, 2.0, "Audio duration must be greater than 2 seconds")

    def test_intro_card_and_watermark_generation(self):
        intro_png = os.path.join(video_stitching.STATIC_VIDEOS_DIR, "test_unit_intro.png")
        overlay_png = os.path.join(video_stitching.STATIC_VIDEOS_DIR, "test_unit_overlay.png")

        video_stitching.create_intro_card_image(
            salutation="Rekha ji",
            candidate_name="नीलू धोनीवाल",
            booth_info="Booth #14, Rajkiya Vidyalaya Sarana",
            symbol_path=video_stitching.DEFAULT_TRACTOR_SYMBOL,
            output_path=intro_png
        )
        self.assertTrue(os.path.exists(intro_png))
        self.assertGreater(os.path.getsize(intro_png), 5000)

        video_stitching.create_watermark_overlay_image(
            booth_info="Booth #14, Rajkiya Vidyalaya Sarana",
            symbol_path=video_stitching.DEFAULT_TRACTOR_SYMBOL,
            output_path=overlay_png
        )
        self.assertTrue(os.path.exists(overlay_png))
        self.assertGreater(os.path.getsize(overlay_png), 5000)

        # Cleanup temporary unit files
        for p in [intro_png, overlay_png]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_end_to_end_stitch_personalized_video(self):
        res = video_stitching.stitch_personalized_campaign_video(
            raw_voter_name="SMT REKHA DEVI W/O RAMESH",
            candidate_name="नीलू धोनीवाल",
            booth_info="Booth #14, Rajkiya Vidyalaya Sarana"
        )
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["salutation"], "Rekha ji")
        self.assertTrue(res["video_url"].startswith("/static/videos/"))
        self.assertTrue(os.path.exists(res["video_path"]))
        self.assertGreater(res["duration_seconds"], 10.0)
        self.assertLess(res["latency_ms"], 10000.0)

    def test_api_generate_video_endpoint(self):
        app = create_app("sanchore", storage_backend="postgres")
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.post("/api/agent/generate_video", json={
            "voter_name": "SMT REKHA DEVI W/O RAMESH",
            "candidate_name": "नीलू धोनीवाल",
            "booth_info": "Booth #14, Rajkiya Vidyalaya Sarana"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["salutation"], "Rekha ji")
        self.assertEqual(data["video_url"], "/static/videos/personalized_sample.mp4")
        self.assertIn("Authorized by Candidate", data["caption"])


if __name__ == "__main__":
    unittest.main()
