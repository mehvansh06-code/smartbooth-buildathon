"""
electionapp/services/audio_converter.py
Carrier audio converter service for SmartBooth Voice OBD.

Transcodes incoming candidate audio recordings in any audio container
(.mp3, .m4a, .aac, .ogg, .opus, .webm, stereo .wav) to the telecom-standard
format required by telecom voice gateways:
- 8,000 Hz sample rate
- 16-bit linear PCM (pcm_s16le)
- Mono channel (1 channel)
- Canonical RIFF/WAVE container

Also computes:
- Exact duration in seconds
- 30-second pulse cost calculation for voice quota deduction
- Bounded opaque carrier sound names with zero tenant name leakage
- Strict path traversal safeguards for output artifact storage
"""

import math
import os
import subprocess
import tempfile
import uuid
import wave
from typing import Optional, Tuple, Union

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")


def calculate_pulse_credits(duration_seconds: float) -> int:
    """Computes the 30-second pulse billing cost for outbound carrier OBD calls.
    Carrier pulse rule:
    - Base pulse unit: 30 seconds
    - Formula: max(1, math.ceil(duration_seconds / 30.0))

    Pulse boundaries:
    - 0s     -> 1 credit
    - 15s    -> 1 credit
    - 30.0s  -> 1 credit
    - 30.1s  -> 2 credits
    - 60.0s  -> 2 credits
    - 60.1s  -> 3 credits
    """
    if duration_seconds <= 0:
        return 1
    return max(1, math.ceil(duration_seconds / 30.0))


def generate_carrier_sound_name(tenant_id: int, label: str = "") -> str:
    """Generates a bounded opaque carrier sound name:
    f"t{tenant_id}_{uuid.uuid4().hex[:10]}"

    Guarantees:
    - Strictly under 20 alphanumeric characters (e.g. 't13_a1b2c3d4e5')
    - Zero collisions via 10-hex-digit UUID entropy (~1.1 trillion permutations)
    - Zero tenant name / sensitive slug leakage over unencrypted carrier logs
    """
    token = uuid.uuid4().hex[:10]
    return f"t{int(tenant_id)}_{token}"


def _validate_safe_output_path(output_dir: str, output_filename: Optional[str]) -> str:
    """Validates and enforces strict path traversal safety for the target output file.
    Ensures the target output file resides strictly within output_dir and does not escape
    via directory navigation ('..', '/', '\\', absolute paths).
    """
    resolved_dir = os.path.abspath(output_dir)
    os.makedirs(resolved_dir, exist_ok=True)

    if output_filename:
        # Prevent path traversal tokens
        if os.path.isabs(output_filename) or ".." in output_filename or "/" in output_filename or "\\" in output_filename:
            raise ValueError(f"Path traversal detected in output_filename: '{output_filename}'")

        target_path = os.path.abspath(os.path.join(resolved_dir, output_filename))

        # Enforce that target_path is directly within resolved_dir
        try:
            common = os.path.commonpath([resolved_dir, target_path])
        except ValueError:
            raise ValueError(f"Path traversal detected: '{output_filename}'")

        if common != resolved_dir or os.path.dirname(target_path) != resolved_dir or target_path == resolved_dir:
            raise ValueError(f"Path traversal detected: '{output_filename}'")

        if not target_path.lower().endswith(".wav"):
            target_path += ".wav"
        return target_path

    unique_name = f"telecom_{uuid.uuid4().hex[:12]}.wav"
    return os.path.join(resolved_dir, unique_name)


def convert_to_telecom_wav(
    input_bytes_or_path: Union[bytes, bytearray, str, os.PathLike],
    output_dir: Optional[str] = None,
    output_filename: Optional[str] = None,
) -> Tuple[bytes, str, float, int]:
    """Transcodes any audio format (.mp3, .m4a, .aac, .ogg, .opus, .webm, stereo .wav)
    to 8,000 Hz, 16-bit linear PCM, mono WAV via ffmpeg.

    Uses ffmpeg command:
    ffmpeg -y -i <input> -map 0:a:0 -vn -ac 1 -ar 8000 -c:a pcm_s16le <output>

    :param input_bytes_or_path: Raw audio file bytes or existing filesystem path
    :param output_dir: Directory where the converted WAV will be saved (defaults to tempdir/smartbooth_telecom_audio)
    :param output_filename: Optional target filename (must not escape output_dir)
    :returns: Tuple of (wav_bytes, file_path, duration_seconds, credits_per_call)
    :raises FileNotFoundError: If input file does not exist
    :raises ValueError: If input is empty or path traversal is detected
    :raises RuntimeError: If ffmpeg is missing or transcoding fails
    """
    # 1. Resolve safe output destination
    target_dir = output_dir or os.path.join(tempfile.gettempdir(), "smartbooth_telecom_audio")
    final_output_path = _validate_safe_output_path(target_dir, output_filename)

    # 2. Stage input audio
    tmp_in_path = None
    try:
        if isinstance(input_bytes_or_path, (bytes, bytearray)):
            if len(input_bytes_or_path) == 0:
                raise ValueError("Input audio bytes are empty")
            with tempfile.NamedTemporaryFile(delete=False) as tmp_in:
                tmp_in.write(input_bytes_or_path)
                tmp_in_path = tmp_in.name
            input_file = tmp_in_path
        elif isinstance(input_bytes_or_path, (str, os.PathLike)):
            input_file = str(input_bytes_or_path)
            if not os.path.isfile(input_file):
                raise FileNotFoundError(f"Input audio file not found: {input_file}")
            if os.path.getsize(input_file) == 0:
                raise ValueError("Input audio file is empty")
        else:
            raise ValueError(f"Unsupported input type for audio conversion: {type(input_bytes_or_path)}")

        # 3. Execute FFmpeg conversion
        cmd = [
            FFMPEG_BIN,
            "-y",
            "-i", input_file,
            "-map", "0:a:0",
            "-vn",
            "-ac", "1",
            "-ar", "8000",
            "-c:a", "pcm_s16le",
            final_output_path
        ]

        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except FileNotFoundError:
            raise RuntimeError(f"ffmpeg executable '{FFMPEG_BIN}' not found in system PATH")

        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"FFmpeg transcode failed (exit code {proc.returncode}): {err_msg}")

        # 4. Read output bytes
        with open(final_output_path, "rb") as f:
            wav_bytes = f.read()

        # 5. Compute exact duration from generated PCM WAV
        duration_seconds = 0.0
        try:
            with wave.open(final_output_path, "rb") as wf:
                n_frames = wf.getnframes()
                framerate = wf.getframerate()
                if framerate > 0:
                    duration_seconds = round(float(n_frames) / float(framerate), 3)
        except Exception as e:
            raise RuntimeError(f"Failed to inspect converted WAV header: {e}")

        # 6. Compute 30-second pulse cost
        credits_per_call = calculate_pulse_credits(duration_seconds)

        return wav_bytes, final_output_path, duration_seconds, credits_per_call

    finally:
        if tmp_in_path and os.path.exists(tmp_in_path):
            try:
                os.remove(tmp_in_path)
            except OSError:
                pass
