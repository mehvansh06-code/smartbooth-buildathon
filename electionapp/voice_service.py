"""
electionapp/voice_service.py
Carrier gateway client for CloudShope Voice Platform (OBD & Voice Broadcast).

Handles:
- Sound list retrieval via CloudShope REST API (/voice/list)
- Audio upload to CloudShope (/voice/voice-upload)
- Mobile normalization & deduplication
- Chunked outbound blast dispatch (/voice/voiceCampaign)
- Two-phase Quota reservation & settlement
"""

import os
import re
import json
import logging
from typing import List, Dict, Any, Tuple, Optional, Union

from electionapp.services.cloudshope_voice import (
    CloudShopeClient,
    default_client,
    CLOUDSHOPE_DEFAULT_CLI,
    CLOUDSHOPE_BASE_URL,
    CLOUDSHOPE_USERNAME,
)

logger = logging.getLogger("smartbooth.voice_service")


def extract_clean_mobiles(raw_phone: Any) -> Optional[str]:
    """Normalizes Indian mobiles to standard 10-digit strings starting with 6-9.
    Strips +91, 0, whitespace, hyphens, and non-numeric characters.
    Returns None if not a valid 10-digit Indian mobile.
    """
    if not raw_phone:
        return None
    s = str(raw_phone).strip()
    digits = re.sub(r'\D', '', s)
    if digits.startswith('91') and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith('0') and len(digits) == 11:
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in '6789':
        return digits
    return None


def deduplicate_voter_mobiles(voters_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Groups voters by clean mobile number, returning 1 primary voter per mobile.
    Avoids duplicate calling within the same family or shared phone number.
    Preserves voter records with valid cleaned mobile attached.
    """
    seen_mobiles = set()
    deduped = []
    for v in voters_list:
        raw = v.get('phone') or v.get('contact_phone') or v.get('mobile') or v.get('mobile_no')
        clean = extract_clean_mobiles(raw)
        if not clean:
            continue
        if clean not in seen_mobiles:
            seen_mobiles.add(clean)
            v_copy = dict(v)
            v_copy['clean_mobile'] = clean
            deduped.append(v_copy)
    return deduped


def get_sound_list(client: Optional[CloudShopeClient] = None, ukey: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetches approved voice clips from CloudShope.
    Returns: [{'sound_name': ..., 'carrier_sound_name': ..., 'duration_seconds': ..., 'credit': ...}, ...]
    """
    cli = client or default_client
    try:
        sounds = cli.get_sound_list()
        logger.info("Retrieved %d sound clips from CloudShope", len(sounds))
        return sounds
    except Exception as e:
        logger.error("Failed to fetch sound list from CloudShope: %s", e)
        return []


def upload_sound(
    file_bytes_or_path: Union[bytes, bytearray, str, os.PathLike],
    sound_name: str,
    client: Optional[CloudShopeClient] = None,
    ukey: Optional[str] = None
) -> Tuple[bool, str]:
    """Uploads an 8kHz mono WAV recording to CloudShope.

    :param file_bytes_or_path: Path string to .wav file, or raw WAV bytes
    :param sound_name: Sound identifier or label
    :param client: Optional CloudShopeClient instance
    :returns: (success, file_id_or_message)
    """
    cli = client or default_client
    clean_sound_name = str(sound_name).strip()
    success, message, file_id = cli.upload_sound(file_bytes_or_path, clean_sound_name)
    if success and file_id is not None:
        return True, str(file_id)
    return success, message


def get_balance(client: Optional[CloudShopeClient] = None) -> int:
    """Returns available CloudShope live VOICE_CALL credits."""
    cli = client or default_client
    return cli.get_wallet_balance()


def get_caller_ids(client: Optional[CloudShopeClient] = None) -> List[Dict[str, Any]]:
    """Returns verified outbound Caller IDs from CloudShope."""
    cli = client or default_client
    return cli.get_caller_ids()


def dispatch_voice_blast(
    tenant_slug: str,
    numbers_list: List[str],
    sound_name: str,
    campaign_name: str = "Voice Broadcast",
    cli: Optional[str] = None,
    ukey: Optional[str] = None,
    credits_per_call: int = 1,
    client: Optional[CloudShopeClient] = None,
    callback_url: Optional[str] = None
) -> Tuple[bool, str, Dict[str, Any]]:
    """Splits recipients into chunks of up to 500 numbers and dispatches
    outbound voice broadcast via CloudShope (/voice/voiceCampaign).

    Credits reserved & spent scale with 30s pulse duration (credits_per_call).
    Returns: (success, message_or_campaign_id, meta)
    """
    from electionapp import voice_quota

    caller = client or default_client
    caller_id = cli or caller.default_cli or CLOUDSHOPE_DEFAULT_CLI
    pulse_credits = max(1, int(credits_per_call))

    # Filter and validate mobile numbers
    clean_numbers = []
    for n in numbers_list:
        clean = extract_clean_mobiles(n)
        if clean and clean not in clean_numbers:
            clean_numbers.append(clean)

    total_recipients = len(clean_numbers)
    if total_recipients == 0:
        return False, "No valid mobile numbers provided", {'dispatched': 0, 'total': 0}

    total_credits_needed = total_recipients * pulse_credits

    # Reserve voice quota credits with fail-closed guarantee
    try:
        reservation = voice_quota.reserve(tenant_slug, total_credits_needed)
    except voice_quota.QuotaExhausted as e:
        return False, str(e), {'error': 'quota_exhausted', 'total': total_recipients, 'credits_needed': total_credits_needed}
    except voice_quota.QuotaUnavailable as e:
        return False, f"Voice quota service unavailable: {e}", {'error': 'quota_unavailable'}
    except Exception as e:
        return False, f"Quota reservation failed: {e}", {'error': 'reservation_failed'}

    # Resolve voice_file_id (CloudShope requires integer clip id)
    voice_file_id: Optional[int] = None
    if str(sound_name).isdigit():
        voice_file_id = int(sound_name)
    else:
        # Check if matching sound is in CloudShope list
        try:
            available_sounds = caller.get_sound_list()
            for s in available_sounds:
                if str(s.get('carrier_sound_name')) == str(sound_name) or s.get('sound_name') == sound_name:
                    voice_file_id = int(s['id'])
                    break
        except Exception as err:
            logger.warning("Could not look up sound list for %s: %s", sound_name, err)

    if voice_file_id is None:
        # Fallback to clip 4 if existing or sound_name has any digits
        digits = re.findall(r'\d+', str(sound_name))
        voice_file_id = int(digits[0]) if digits else 4

    chunk_size = 500
    chunks = [clean_numbers[i:i + chunk_size] for i in range(0, total_recipients, chunk_size)]

    dispatched_count = 0
    gateway_campaign_ids = []
    errors = []

    try:
        for idx, chunk in enumerate(chunks):
            sub_name = f"{campaign_name}_{idx+1}" if len(chunks) > 1 else campaign_name

            ok, msg, camp_id = caller.dispatch_campaign(
                campaign_name=sub_name,
                voice_file_id=voice_file_id,
                numbers=chunk,
                caller_id=caller_id,
                callback_url=callback_url
            )

            if ok:
                chunk_sent = len(chunk)
                reservation.spend(chunk_sent * pulse_credits)
                dispatched_count += chunk_sent
                gateway_campaign_ids.append(camp_id or f"cs_camp_{idx+1}")
                logger.info("Dispatched CloudShope chunk %d/%d (%d recipients)", idx + 1, len(chunks), chunk_sent)
            else:
                err_msg = f"Chunk {idx+1} failed: {msg}"
                logger.error(err_msg)
                errors.append(err_msg)

    finally:
        # Reconcile DB: bank spent, return unspent
        reservation.close()

    success = dispatched_count > 0
    summary_msg = f"Dispatched {dispatched_count}/{total_recipients} calls via CloudShope."
    if errors:
        summary_msg += f" Encountered {len(errors)} error(s)."

    return success, summary_msg, {
        "dispatched": dispatched_count,
        "total": total_recipients,
        "credits_per_call": pulse_credits,
        "credits_spent": dispatched_count * pulse_credits,
        "gateway_campaign_ids": gateway_campaign_ids,
        "errors": errors
    }
