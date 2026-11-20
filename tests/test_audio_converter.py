"""
tests/test_audio_converter.py
Comprehensive unit tests for Voice OBD Module Revamp (Component A & B):
1. electionapp.services.audio_converter:
   - Telecom WAV transcoding (8,000 Hz, 16-bit PCM, mono)
   - 30-second pulse billing boundaries (0s, 15s, 30.0s, 30.1s, 60.0s, 60.1s)
   - Bounded opaque carrier sound name generation (< 20 chars, zero tenant name leakage)
   - Path traversal safety enforcement on output directories
2. electionapp.voice_service:
   - Gateway HTML and 404 error response rejection in get_sound_list()
   - Telecom WAV forwarding and safe parameterization in upload_sound()
"""

import io
import os
import re
import struct
import tempfile
import unittest
import wave
import sys

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)
from unittest.mock import MagicMock, patch

from electionapp.services.audio_converter import (
    calculate_pulse_credits,
    convert_to_telecom_wav,
    generate_carrier_sound_name,
)
from electionapp import voice_service


def _create_synthetic_pcm_wav(
    duration_seconds: float = 1.0,
    sample_rate: int = 44100,
    channels: int = 2,
    sample_width: int = 2,
) -> bytes:
    """Helper to synthesize a valid in-memory PCM WAV audio buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        total_frames = int(sample_rate * duration_seconds)
        # Create silent PCM frames
        frame = struct.pack("<" + ("h" * channels), *([0] * channels))
        wf.writeframes(frame * total_frames)
    return buf.getvalue()


class TestPulseCostCalculation(unittest.TestCase):
    """Verifies strict 30-second pulse cost calculation across exact boundary values."""

    def test_pulse_boundary_values(self):
        # 0s -> 1 credit (minimum charge)
        self.assertEqual(calculate_pulse_credits(0.0), 1)

        # 15s -> 1 credit
        self.assertEqual(calculate_pulse_credits(15.0), 1)

        # 30.0s -> 1 credit (boundary of 1st pulse)
        self.assertEqual(calculate_pulse_credits(30.0), 1)

        # 30.1s -> 2 credits (crosses into 2nd pulse)
        self.assertEqual(calculate_pulse_credits(30.1), 2)

        # 60.0s -> 2 credits (boundary of 2nd pulse)
        self.assertEqual(calculate_pulse_credits(60.0), 2)

        # 60.1s -> 3 credits (crosses into 3rd pulse)
        self.assertEqual(calculate_pulse_credits(60.1), 3)

    def test_pulse_edge_cases(self):
        # Negative durations default safely to 1 credit
        self.assertEqual(calculate_pulse_credits(-1.0), 1)
        self.assertEqual(calculate_pulse_credits(-10.5), 1)

        # Fractional second immediately above 0
        self.assertEqual(calculate_pulse_credits(0.01), 1)

        # Multi-minute pulses
        self.assertEqual(calculate_pulse_credits(90.0), 3)
        self.assertEqual(calculate_pulse_credits(90.01), 4)
        self.assertEqual(calculate_pulse_credits(120.0), 4)
        self.assertEqual(calculate_pulse_credits(120.1), 5)


class TestCarrierSoundNameGeneration(unittest.TestCase):
    """Verifies generation of bounded, opaque carrier sound identifiers."""

    def test_sound_name_format_and_length(self):
        # Test standard tenant IDs
        for tid in (1, 13, 27, 105, 9999):
            sound_name = generate_carrier_sound_name(tid)
            # Under 20 alphanumeric/underscore characters
            self.assertLess(len(sound_name), 20)
            # Starts with t<tenant_id>_ followed by 10 hex characters
            pattern = rf"^t{tid}_[0-9a-f]{{10}}$"
            self.assertRegex(sound_name, pattern)

    def test_label_does_not_leak_into_sound_name(self):
        # Sensitive campaign/candidate labels must NEVER be sent to third-party carrier
        sound_name = generate_carrier_sound_name(27, label="Secret_Campaign_Slug_NenuDevi")
        self.assertNotIn("Secret", sound_name)
        self.assertNotIn("NenuDevi", sound_name)
        self.assertNotIn("Campaign", sound_name)
        self.assertTrue(sound_name.startswith("t27_"))
        self.assertLess(len(sound_name), 20)

    def test_uniqueness_zero_collisions(self):
        # Generate 1,000 sound names and ensure zero collisions
        names = {generate_carrier_sound_name(13) for _ in range(1000)}
        self.assertEqual(len(names), 1000)


class TestTelecomAudioConverter(unittest.TestCase):
    """Tests FFmpeg-based transcoding to 8,000 Hz 16-bit linear PCM mono WAV."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="smartbooth_audio_test_")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            for root, _, files in os.walk(self.test_dir, topdown=False):
                for f in files:
                    try:
                        os.remove(os.path.join(root, f))
                    except OSError:
                        pass
            try:
                os.rmdir(self.test_dir)
            except OSError:
                pass

    def test_convert_synthetic_stereo_wav_bytes(self):
        # Synthesize a 44.1 kHz stereo audio buffer (duration: 1.5s)
        raw_audio = _create_synthetic_pcm_wav(duration_seconds=1.5, sample_rate=44100, channels=2)

        wav_bytes, out_path, duration, credits_per_call = convert_to_telecom_wav(
            raw_audio, output_dir=self.test_dir, output_filename="prompt_1.wav"
        )

        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(len(wav_bytes), os.path.getsize(out_path))
        self.assertAlmostEqual(duration, 1.5, places=1)
        self.assertEqual(credits_per_call, 1)

        # Inspect resulting WAV header
        with wave.open(out_path, "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1, "Must be mono (1 channel)")
            self.assertEqual(wf.getframerate(), 8000, "Must be 8000 Hz")
            self.assertEqual(wf.getsampwidth(), 2, "Must be 16-bit linear PCM (2 bytes/sample)")

    def test_convert_from_input_file_path(self):
        # Write 2-second stereo wav to disk
        input_file = os.path.join(self.test_dir, "input_source.wav")
        with open(input_file, "wb") as f:
            f.write(_create_synthetic_pcm_wav(duration_seconds=2.0, sample_rate=22050, channels=2))

        wav_bytes, out_path, duration, credits_per_call = convert_to_telecom_wav(
            input_file, output_dir=self.test_dir
        )

        self.assertTrue(os.path.isfile(out_path))
        self.assertAlmostEqual(duration, 2.0, places=1)
        self.assertEqual(credits_per_call, 1)

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 8000)

    def test_convert_duration_and_pulse_calculation(self):
        # 31.0 seconds audio -> 2 credits
        raw_long = _create_synthetic_pcm_wav(duration_seconds=31.0, sample_rate=8000, channels=1)

        _, _, duration, credits_per_call = convert_to_telecom_wav(
            raw_long, output_dir=self.test_dir
        )

        self.assertAlmostEqual(duration, 31.0, places=1)
        self.assertEqual(credits_per_call, 2)

    def test_path_traversal_rejection(self):
        raw_audio = _create_synthetic_pcm_wav(duration_seconds=1.0)

        # Directory traversal using '..'
        with self.assertRaises(ValueError) as ctx:
            convert_to_telecom_wav(raw_audio, output_dir=self.test_dir, output_filename="../escaped.wav")
        self.assertIn("Path traversal detected", str(ctx.exception))

        # Windows backslash traversal
        with self.assertRaises(ValueError) as ctx:
            convert_to_telecom_wav(raw_audio, output_dir=self.test_dir, output_filename="..\\escaped.wav")
        self.assertIn("Path traversal detected", str(ctx.exception))

        # Absolute paths
        with self.assertRaises(ValueError) as ctx:
            convert_to_telecom_wav(raw_audio, output_dir=self.test_dir, output_filename="/etc/passwd")
        self.assertIn("Path traversal detected", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            convert_to_telecom_wav(raw_audio, output_dir=self.test_dir, output_filename="C:\\Windows\\hack.wav")
        self.assertIn("Path traversal detected", str(ctx.exception))

        # Subdirectory traversal
        with self.assertRaises(ValueError) as ctx:
            convert_to_telecom_wav(raw_audio, output_dir=self.test_dir, output_filename="subfolder/audio.wav")
        self.assertIn("Path traversal detected", str(ctx.exception))

    def test_empty_input_audio_rejected(self):
        with self.assertRaises(ValueError):
            convert_to_telecom_wav(b"", output_dir=self.test_dir)

    def test_missing_input_file_rejected(self):
        with self.assertRaises(FileNotFoundError):
            convert_to_telecom_wav(os.path.join(self.test_dir, "nonexistent.mp3"), output_dir=self.test_dir)


class TestGatewaySoundListRejection(unittest.TestCase):
    """Ensures get_sound_list() safely handles CloudShope HTTP errors and sound formatting."""

    def setUp(self):
        voice_service.default_client._token = "valid_token"
        voice_service.default_client._token_expires_at = 9999999999.0

    @patch("requests.Session.request")
    def test_rejects_http_404_status(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "404 Not Found"
        mock_request.return_value = mock_resp

        result = voice_service.get_sound_list()
        self.assertEqual(result, [])

    @patch("requests.Session.request")
    def test_rejects_http_500_status(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_request.return_value = mock_resp

        result = voice_service.get_sound_list()
        self.assertEqual(result, [])

    @patch("requests.Session.request")
    def test_accepts_valid_json_sound_list(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "1": {"id": 1, "name": "clip_one.wav", "duration": 12, "credit": 1},
                "2": {"id": 2, "name": "clip_two.wav", "duration": 25, "credit": 1}
            }
        }
        mock_request.return_value = mock_resp

        result = voice_service.get_sound_list()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["sound_name"], "clip_one.wav")
        self.assertEqual(result[1]["sound_name"], "clip_two.wav")


class TestUploadSoundTelecomMonoWav(unittest.TestCase):
    """Ensures upload_sound properly parameterizes and transmits 8kHz mono WAV bytes."""

    @patch("requests.Session.request")
    def test_upload_sound_multipart_parameters(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"file_id": 105}
        mock_request.return_value = mock_resp

        dummy_wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80\x3e\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        voice_service.default_client._token = "valid_token"
        voice_service.default_client._token_expires_at = 9999999999.0

        ok, msg = voice_service.upload_sound(
            file_bytes_or_path=dummy_wav_bytes,
            sound_name="t13_a1b2c3d4e5"
        )

        self.assertTrue(ok)
        self.assertEqual(msg, "105")
        mock_request.assert_called_once()

        # Verify files multipart payload
        call_kwargs = mock_request.call_args[1]
        files = call_kwargs["files"]
        self.assertIn("file", files)
        filename, content, mime = files["file"]
        self.assertEqual(filename, "t13_a1b2c3d4e5.wav")
        self.assertEqual(content, dummy_wav_bytes)
        self.assertEqual(mime, "audio/wav")

    def test_upload_sound_empty_content_rejected(self):
        ok, msg = voice_service.upload_sound(b"", "t13_empty")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

    def test_upload_sound_missing_path_rejected(self):
        ok, msg = voice_service.upload_sound("C:\\nonexistent_audio_path_xyz.wav", "t13_missing")
        self.assertFalse(ok)
        self.assertIn("not found", msg.lower())


if __name__ == "__main__":
    unittest.main()
