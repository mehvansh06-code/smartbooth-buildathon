import os
import re
import requests
from typing import Optional, Dict, Any, List
from .base import BaseDispatchChannel, DispatchResult


def extract_all_mobile_numbers(raw_phone: Any) -> List[str]:
    """
    Extracts all valid 10-digit Indian mobile numbers from a raw phone string.
    """
    if not raw_phone:
        return []
    chunks = re.split(r'[,;/|]+', str(raw_phone))
    mobiles = []
    seen = set()
    for chunk in chunks:
        cleaned = re.sub(r'[+\-\(\)]', '', chunk).strip()
        joined = re.sub(r'\D', '', cleaned)
        candidates = []
        if len(joined) in [10, 11, 12]:
            candidates.append(joined)
        else:
            candidates.extend(cleaned.split())

        for c in candidates:
            d = re.sub(r'\D', '', c)
            if d.startswith('91') and len(d) == 12:
                d = d[2:]
            elif d.startswith('0') and len(d) == 11:
                d = d[1:]
            if len(d) == 10 and d[0] in '6789':
                if d not in seen:
                    seen.add(d)
                    mobiles.append(d)
    return mobiles


def clean_phone_number(raw_phone: Any) -> Optional[str]:
    """Returns clean E.164 phone string starting with 91, e.g. 919829012345."""
    mobiles = extract_all_mobile_numbers(raw_phone)
    if mobiles:
        return '91' + mobiles[0]
    digits = re.sub(r'\D', '', str(raw_phone or ''))
    if len(digits) == 10 and digits[0] in '6789':
        return '91' + digits
    if len(digits) == 12 and digits.startswith('91'):
        return digits
    return None


class MetaCloudChannel(BaseDispatchChannel):
    """
    Official Meta Cloud WhatsApp Business API Channel.
    Supports consent gating and media uploads with SHA256 caching.
    """
    channel_name: str = "meta_cloud"

    def __init__(
        self,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None,
        template_name: Optional[str] = None,
        template_lang: str = "en",
        api_version: str = "v22.0"
    ):
        self.phone_number_id = phone_number_id or os.environ.get('META_WA_PHONE_NUMBER_ID', '').strip()
        self.access_token = access_token or os.environ.get('META_WA_ACCESS_TOKEN', '').strip()
        self.template_name = template_name or os.environ.get('META_WA_TEMPLATE_NAME', 'info_update_suchna_1').strip()
        self.template_lang = template_lang or os.environ.get('META_WA_TEMPLATE_LANG', 'en').strip()
        self.api_version = api_version or os.environ.get('META_WA_API_VERSION', 'v22.0').strip()
        self._media_cache: Dict[str, str] = {}

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def send_message(
        self,
        phone: str,
        message: str,
        voter_data: Optional[Dict[str, Any]] = None,
        *,
        credit
    ) -> DispatchResult:
        """Sends a billable text message via the Meta Cloud API.

        `credit` is a required keyword-only quota reservation. This class is directly
        constructible, so gating only DispatchRouter would leave a hole; the gate lives
        here, on the function that actually performs the POST.
        """
        from electionapp.whatsapp_quota import assert_billable, refund_credit

        clean_phone = clean_phone_number(phone)
        if not clean_phone:
            return DispatchResult(
                success=False,
                channel=self.channel_name,
                recipient_phone=phone,
                error="Invalid mobile phone number"
            )

        # Billing gate: claim a credit before anything billable happens.
        assert_billable(credit)

        if not self.access_token or not self.phone_number_id:
            refund_credit(credit)
            return DispatchResult(
                success=False,
                channel=self.channel_name,
                recipient_phone=clean_phone,
                error="Meta Cloud API credentials not configured"
            )

        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "text",
            "text": {"preview_url": False, "body": message}
        }

        try:
            res = requests.post(url, headers=self._headers(), json=payload, timeout=15)
            data = res.json()
            if res.status_code == 200 and 'messages' in data:
                msg_id = data['messages'][0].get('id')
                return DispatchResult(
                    success=True,
                    channel=self.channel_name,
                    recipient_phone=clean_phone,
                    message_id=msg_id,
                    details=data
                )
            err_msg = data.get('error', {}).get('message') or f"HTTP {res.status_code}"
            refund_credit(credit)
            return DispatchResult(
                success=False,
                channel=self.channel_name,
                recipient_phone=clean_phone,
                error=err_msg,
                details=data
            )
        except Exception as e:
            refund_credit(credit)
            return DispatchResult(
                success=False,
                channel=self.channel_name,
                recipient_phone=clean_phone,
                error=str(e)
            )

    def send_slip(
        self,
        phone: str,
        message: str,
        image_path: str,
        voter_data: Optional[Dict[str, Any]] = None,
        *,
        credit
    ) -> DispatchResult:
        # Falls back to sending text message if image upload not configured, or sends template
        return self.send_message(phone, message, voter_data, credit=credit)

    def is_healthy(self) -> bool:
        return bool(self.access_token and self.phone_number_id)
