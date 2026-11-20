"""
electionapp/services/video_stitching.py
=======================================
SmartBooth Autonomous AI Video Pipeline Engine (Task 3 - Buildathon Blueprint).
Automates personalized candidate campaign videos for Indian Electoral Campaigns:
1. Input: master_video.mp4 (15s candidate pitch), raw_voter_name (e.g. "SMT REKHA DEVI W/O RAMESH"),
   candidate_name, symbol_image.png, booth_info.
2. Step 1 (Name Normalization): Uses Gemini 2.5 Flash on Vertex AI (or robust fallback rules)
   to convert raw_voter_name into a natural spoken Hindi salutation (e.g., "Rekha ji").
3. Step 2 (Greeting Clip): Generates personalized greeting audio via ElevenLabs API
   (or clean pre-rendered audio fallback).
4. Step 3 (Stitch & Watermark): Uses FFmpeg to concatenate personalized intro card with candidate master video,
   overlays Tractor symbol + Booth #14 badge, and burns the authorized AI campaign caption.
5. Returns high-performance, browser-ready MP4 (/static/videos/personalized_sample.mp4) in < 2 seconds.
"""

import os
import re
import sys
import time
import json
import logging
import subprocess
from typing import Dict, Any, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("smartbooth.video_stitching")

# Base Paths
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATIC_VIDEOS_DIR = os.path.join(BASE_DIR, "static", "videos")
STATIC_SYMBOLS_DIR = os.path.join(BASE_DIR, "static", "symbols")
DEFAULT_MASTER_VIDEO = os.path.join(STATIC_VIDEOS_DIR, "master_video.mp4")
DEFAULT_TRACTOR_SYMBOL = os.path.join(STATIC_SYMBOLS_DIR, "tractor.png")
DEFAULT_FALLBACK_AUDIO = os.path.join(STATIC_VIDEOS_DIR, "sample_greeting.mp3")

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")


def _get_font(font_size: int, bold: bool = False):
    """Loads best available font supporting Devanagari and Latin on Windows and Linux."""
    candidates = [
        # Windows
        "C:\\Windows\\Fonts\\Nirmala.ttc",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\calibrib.ttf" if bold else "C:\\Windows\\Fonts\\calibri.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, font_size)
            except Exception:
                continue
    return ImageFont.load_default()


# ==============================================================================
# STEP 1: Name Normalization (Gemini 2.5 Flash + Fallback Rules)
# ==============================================================================
def normalize_voter_salutation(raw_voter_name: str) -> str:
    """
    Converts raw voter roll name (e.g. "SMT REKHA DEVI W/O RAMESH", "SHRI MOHAN LAL SHARMA")
    into a natural, respectful spoken Hindi salutation (e.g. "Rekha ji" or "रेखा जी").
    Tries Gemini 2.5 Flash on Vertex AI first; falls back seamlessly to rule-based parser.
    """
    if not raw_voter_name or not str(raw_voter_name).strip():
        return "Matdata ji"

    clean_raw = str(raw_voter_name).strip()

    # Attempt Gemini 2.5 Flash via buildathon_agent client
    try:
        from electionapp.services.buildathon_agent import get_genai_client
        client = get_genai_client()
        prompt = (
            f"You are an Indian electoral name normalizer. Convert this raw voter roll name into a natural, "
            f"respectful spoken Hindi greeting name with 'ji' (e.g. 'SMT REKHA DEVI W/O RAMESH' -> 'Rekha ji', "
            f"'SHRI MOHAN LAL' -> 'Mohan ji', 'श्रीमती रेखा देवी पत्नी रमेश' -> 'रेखा जी'): '{clean_raw}'. "
            f"Return ONLY the salutation string without quotes or preamble."
        )
        ai_resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        res_text = ai_resp.text.strip().strip('"\'')
        if res_text and len(res_text) <= 30:
            return res_text
    except Exception as e:
        logger.debug("Gemini name normalization skipped, using rule-based fallback: %s", e)

    # Robust Fallback Heuristic Parser
    return _rule_based_salutation(clean_raw)


def _rule_based_salutation(raw_name: str) -> str:
    name = raw_name.strip()
    is_hindi = bool(re.search(r'[\u0900-\u097F]', name))

    # Strip relationships (W/O, S/O, D/O, पत्नी, पुत्र, etc.)
    name = re.sub(r'(?i)\s+(?:w/o|s/o|d/o|c/o|wife\s+of|son\s+of|daughter\s+of)\b.*$', '', name)
    name = re.sub(r'\s+(?:पत्नी|सुपुत्र|पुत्र|पुत्री|आत्मज|आत्मजा|पति)\b.*$', '', name)

    # Strip common prefixes (SMT, SHRI, DR, MRS, etc.)
    name = re.sub(r'(?i)^(?:smt\.?|shri\.?|sh\.?|mr\.?|mrs\.?|ms\.?|kumari|dr\.?)\s+', '', name)
    name = re.sub(r'^(?:श्रीमती|श्री|सुश्री|डॉ\.?)\s+', '', name)

    tokens = name.split()
    if not tokens:
        return "Matdata ji" if not is_hindi else "मतदाता जी"

    first_token = tokens[0]

    if not is_hindi:
        clean_first = first_token.capitalize()
        return f"{clean_first} ji"
    else:
        return f"{first_token} जी"


# ==============================================================================
# STEP 2: Greeting Audio Generation (ElevenLabs + High-Quality Fallback)
# ==============================================================================
def get_audio_duration(audio_path: str) -> float:
    """Returns duration in seconds using ffprobe."""
    try:
        cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        dur = float(out)
        if dur > 0:
            return dur
    except Exception as e:
        logger.warning("ffprobe duration check failed for %s: %s", audio_path, e)
    return 5.1


def generate_greeting_audio(salutation: str, candidate_name: str = "नीलू धोनीवाल", output_path: Optional[str] = None) -> Tuple[str, float]:
    """
    Generates personalized candidate greeting audio (e.g. 'नमस्ते रेखा जी! मैं नीलू धोनीवाल...').
    Uses ElevenLabs API if ELEVENLABS_API_KEY is present; otherwise returns clean audio fallback.
    """
    if not output_path:
        os.makedirs(STATIC_VIDEOS_DIR, exist_ok=True)
        output_path = os.path.join(STATIC_VIDEOS_DIR, "greeting_temp.mp3")

    greeting_text = (
        f"नमस्ते {salutation}! मैं {candidate_name}। "
        f"सराना ग्राम पंचायत के विकास के लिए 14 नंबर बूथ पर ट्रैक्टर चुनाव चिन्ह पर अपना आशीर्वाद जरूर दें।"
    )

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "NS7Vw48CjzUduWcoi5eJ")

    if api_key and voice_id:
        try:
            import requests
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }
            payload = {
                "text": greeting_text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=12)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
                dur = get_audio_duration(output_path)
                logger.info("ElevenLabs generated audio for '%s': %.2fs", salutation, dur)
                return output_path, dur
            else:
                logger.warning("ElevenLabs TTS returned %d: %s", resp.status_code, resp.text[:150])
        except Exception as e:
            logger.warning("ElevenLabs request failed: %s", e)

    # High-quality fallback audio
    if os.path.exists(DEFAULT_FALLBACK_AUDIO):
        logger.info("Using pre-rendered fallback audio: %s", DEFAULT_FALLBACK_AUDIO)
        return DEFAULT_FALLBACK_AUDIO, get_audio_duration(DEFAULT_FALLBACK_AUDIO)

    # Synthesize fallback audio tone with FFmpeg if fallback file is missing
    try:
        cmd = [
            FFMPEG_BIN, "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=4.5",
            "-c:a", "aac", "-b:a", "128k",
            output_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
        return output_path, 4.5
    except Exception as e:
        logger.error("FFmpeg sine synthesis failed: %s", e)
        return output_path, 4.5


# ==============================================================================
# STEP 3: Visual Card & Watermark Generation (PIL Antialiased Graphics)
# ==============================================================================
def create_intro_card_image(
    salutation: str,
    candidate_name: str,
    booth_info: str,
    symbol_path: str,
    output_path: str,
    width: int = 1280,
    height: int = 720
) -> str:
    """Generates an antialiased 1280x720 personalized campaign greeting frame."""
    img = Image.new("RGB", (width, height), (15, 23, 42))  # Slate 900 base
    draw = ImageDraw.Draw(img)

    # Top emerald banner bar
    draw.rectangle([0, 0, width, 12], fill=(22, 163, 74))

    # Gradient-style subtle container card
    card_margin_x, card_margin_y = 60, 48
    draw.rounded_rectangle(
        [card_margin_x, card_margin_y, width - card_margin_x, height - card_margin_y],
        radius=20,
        fill=(10, 37, 27),
        outline=(34, 197, 94),
        width=2
    )

    font_badge = _get_font(20, bold=True)
    font_large = _get_font(46, bold=True)
    font_sub = _get_font(26, bold=True)
    font_detail = _get_font(22, bold=False)

    # Top Pill Header
    header_text = "स्मार्टबूथ AI • व्यक्तिगत चुनाव संदेश 2026"
    draw.text((card_margin_x + 36, card_margin_y + 30), header_text, fill=(134, 239, 172), font=font_badge)

    # Personalized Salutation Heading
    greeting_line = f"नमस्ते {salutation}!"
    draw.text((card_margin_x + 36, card_margin_y + 90), greeting_line, fill=(254, 240, 138), font=font_large)

    # Candidate Subtitle
    cand_line = f"मैं {candidate_name} • प्रत्याशी, ग्राम पंचायत सराना (अजमेर)"
    draw.text((card_margin_x + 36, card_margin_y + 165), cand_line, fill=(255, 255, 255), font=font_sub)

    # Candidate Slogan / Message
    msg_line = "आपके परिवार का अमूल्य मत, समर्थन और आशीर्वाद हमारे लिए सर्वांगीण विकास की नींव है।"
    draw.text((card_margin_x + 36, card_margin_y + 220), msg_line, fill=(203, 213, 225), font=font_detail)

    # Booth Info Card
    booth_box_top = card_margin_y + 285
    draw.rounded_rectangle(
        [card_margin_x + 36, booth_box_top, card_margin_x + 640, booth_box_top + 70],
        radius=12,
        fill=(15, 23, 42),
        outline=(234, 179, 8),
        width=1
    )
    draw.text((card_margin_x + 56, booth_box_top + 12), "आपका मतदान केंद्र (Polling Station):", fill=(203, 213, 225), font=_get_font(16))
    draw.text((card_margin_x + 56, booth_box_top + 34), booth_info, fill=(254, 240, 138), font=_get_font(20, bold=True))

    # Right Symbol Showcase Card
    sym_box_w = 420
    sym_box_h = 240
    sym_box_x = width - card_margin_x - sym_box_w - 36
    sym_box_y = card_margin_y + 115
    draw.rounded_rectangle(
        [sym_box_x, sym_box_y, sym_box_x + sym_box_w, sym_box_y + sym_box_h],
        radius=16,
        fill=(255, 255, 255),
        outline=(22, 101, 52),
        width=3
    )

    if os.path.exists(symbol_path):
        try:
            sym_img = Image.open(symbol_path).convert("RGBA")
            sym_img.thumbnail((190, 190), Image.Resampling.LANCZOS)
            img.paste(sym_img, (sym_box_x + 20, sym_box_y + 25), sym_img)
        except Exception as e:
            logger.warning("Failed to paste symbol image: %s", e)

    draw.text((sym_box_x + 220, sym_box_y + 60), "चुनाव चिन्ह:", fill=(22, 101, 52), font=_get_font(22, bold=True))
    draw.text((sym_box_x + 220, sym_box_y + 105), "ट्रैक्टर (TRACTOR)", fill=(15, 23, 42), font=_get_font(26, bold=True))

    # Bottom Audio Indicator Bar
    bar_y = height - card_margin_y - 75
    draw.text((card_margin_x + 36, bar_y + 15), "▶ AI वॉइस संदेश प्रसारित हो रहा है...", fill=(34, 197, 94), font=_get_font(22, bold=True))

    img.save(output_path, "PNG")
    return output_path


def create_watermark_overlay_image(
    booth_info: str,
    symbol_path: str,
    output_path: str,
    width: int = 1280,
    height: int = 720
) -> str:
    """Generates transparent 1280x720 overlay containing Tractor Badge, Booth info, and AI Caption."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_bold = _get_font(18, bold=True)
    font_small = _get_font(15, bold=False)

    # 1. Top-Right Tractor Symbol Badge
    badge_w, badge_h = 175, 84
    badge_x, badge_y = width - badge_w - 24, 20
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=12,
        fill=(255, 255, 255, 240),
        outline=(22, 101, 52, 255),
        width=2
    )

    if os.path.exists(symbol_path):
        try:
            sym = Image.open(symbol_path).convert("RGBA")
            sym.thumbnail((68, 68), Image.Resampling.LANCZOS)
            overlay.paste(sym, (badge_x + 10, badge_y + 8), sym)
        except Exception as e:
            logger.warning("Overlay symbol paste error: %s", e)

    draw.text((badge_x + 85, badge_y + 16), "चिन्ह:", fill=(22, 101, 52, 255), font=font_small)
    draw.text((badge_x + 85, badge_y + 38), "TRACTOR", fill=(15, 23, 42, 255), font=font_bold)

    # 2. Top-Left Booth Badge
    bb_w, bb_h = 370, 42
    draw.rounded_rectangle(
        [24, 20, 24 + bb_w, 20 + bb_h],
        radius=10,
        fill=(15, 23, 42, 220),
        outline=(234, 179, 8, 255),
        width=1
    )
    draw.text((38, 30), booth_info, fill=(254, 240, 138, 255), font=font_small)

    # 3. Bottom Banner Caption
    banner_h = 44
    draw.rectangle([0, height - banner_h, width, height], fill=(15, 23, 42, 225))
    caption_text = "AI-Personalized Campaign Message • Authorized by Candidate"
    draw.text((width // 2 - 250, height - banner_h + 12), caption_text, fill=(255, 255, 255, 240), font=font_small)

    overlay.save(output_path, "PNG")
    return output_path


# ==============================================================================
# STEP 4: High-Speed FFmpeg Stitching Engine
# ==============================================================================
def stitch_personalized_campaign_video(
    raw_voter_name: str,
    master_video_path: Optional[str] = None,
    candidate_name: str = "नीलू धोनीवाल",
    symbol_image_path: Optional[str] = None,
    booth_info: str = "Booth #14, Rajkiya Vidyalaya Sarana",
    output_video_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main orchestrator: Normalizes name, creates greeting audio, renders card & overlay,
    and stitches master video using FFmpeg into a seamless personalized video.
    """
    start_time = time.time()
    os.makedirs(STATIC_VIDEOS_DIR, exist_ok=True)

    master_video = master_video_path or DEFAULT_MASTER_VIDEO
    symbol_img = symbol_image_path or DEFAULT_TRACTOR_SYMBOL
    output_video = output_video_path or os.path.join(STATIC_VIDEOS_DIR, "personalized_sample.mp4")

    # Step 1: Normalize Voter Salutation
    salutation = normalize_voter_salutation(raw_voter_name)
    logger.info("Normalized '%s' -> '%s'", raw_voter_name, salutation)

    # Step 2: Generate Greeting Audio
    temp_audio = os.path.join(STATIC_VIDEOS_DIR, "temp_greeting_audio.mp3")
    audio_path, intro_duration = generate_greeting_audio(salutation, candidate_name, temp_audio)

    # Step 3: Generate Intro Frame & Watermark Overlay
    intro_frame_path = os.path.join(STATIC_VIDEOS_DIR, "temp_intro_frame.png")
    overlay_path = os.path.join(STATIC_VIDEOS_DIR, "temp_overlay.png")

    create_intro_card_image(
        salutation=salutation,
        candidate_name=candidate_name,
        booth_info=booth_info,
        symbol_path=symbol_img,
        output_path=intro_frame_path
    )
    create_watermark_overlay_image(
        booth_info=booth_info,
        symbol_path=symbol_img,
        output_path=overlay_path
    )

    # Step 4: Single-Pass FFmpeg Stitch & Overlay
    # Filter graph:
    # 1. Scale intro frame and master video to 1280x720 25fps yuv420p
    # 2. Resample greeting audio and master audio to 44100Hz mono
    # 3. Trim intro frame to greeting audio duration
    # 4. Concatenate intro segment + master segment
    # 5. Overlay watermark PNG badge on concatenated video
    # 6. Encode with ultrafast x264 and aac
    filter_complex = (
        f"[0:v]scale=1280:720,setsar=1,format=yuv420p,trim=duration={intro_duration:.3f},setpts=PTS-STARTPTS[v0t]; "
        f"[2:v]scale=1280:720,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v2]; "
        f"[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono,atrim=duration={intro_duration:.3f},asetpts=PTS-STARTPTS[a1t]; "
        f"[2:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=mono,asetpts=PTS-STARTPTS[a2]; "
        f"[v0t][a1t][v2][a2]concat=n=2:v=1:a=1[vcat][acat]; "
        f"[vcat][3:v]overlay=0:0[vfinal]"
    )

    cmd = [
        FFMPEG_BIN, "-y",
        "-loop", "1", "-framerate", "25", "-i", intro_frame_path,
        "-i", audio_path,
        "-i", master_video,
        "-i", overlay_path,
        "-filter_complex", filter_complex,
        "-map", "[vfinal]", "-map", "[acat]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        output_video
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=25)
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg stitching execution failed: %s", e.stderr.decode(errors="ignore"))
        raise RuntimeError(f"FFmpeg stitching failed: {e.stderr.decode(errors='ignore')[:200]}")

    latency_ms = (time.time() - start_time) * 1000
    final_duration = get_audio_duration(output_video)
    video_public_url = f"/static/videos/{os.path.basename(output_video)}"

    result = {
        "status": "SUCCESS",
        "raw_voter_name": raw_voter_name,
        "salutation": salutation,
        "candidate_name": candidate_name,
        "symbol": "Tractor",
        "booth_info": booth_info,
        "caption": "AI-Personalized Campaign Message • Authorized by Candidate",
        "video_url": video_public_url,
        "video_path": output_video,
        "duration_seconds": round(final_duration, 2),
        "latency_ms": round(latency_ms, 2)
    }

    # Log into autonomous AI traces panel
    try:
        from electionapp.services import buildathon_agent
        buildathon_agent.log_trace(
            event_type="TOOL_CALL",
            tool_name="stitch_personalized_campaign_video",
            input_payload={"voter_name": raw_voter_name, "candidate": candidate_name, "booth": booth_info},
            output_payload=result,
            latency_ms=latency_ms,
            metadata={"video_url": video_public_url, "duration": f"{final_duration:.1f}s"}
        )
    except Exception as e:
        logger.debug("Trace logging skipped: %s", e)

    return result
