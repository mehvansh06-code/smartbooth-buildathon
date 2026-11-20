import urllib.parse
from typing import Optional, Dict, Any
from .base import BaseDispatchChannel, DispatchResult
from .meta_cloud import clean_phone_number


class WaMeChannel(BaseDispatchChannel):
    """
    Fallback deep link generator for manual volunteer sharing or WhatsApp Web share-sheet.
    Generates standard https://wa.me/<phone>?text=<encoded_text>.
    """
    channel_name: str = "wa_me"

    def __init__(self, default_prefix: str = "https://wa.me"):
        self.default_prefix = default_prefix

    def generate_link(self, phone: str, message: str) -> str:
        clean = clean_phone_number(phone) or ''.join(filter(str.isdigit, str(phone or '')))
        encoded_msg = urllib.parse.quote(message, safe='')
        if clean:
            return f"{self.default_prefix}/{clean}?text={encoded_msg}"
        return f"{self.default_prefix}/?text={encoded_msg}"

    def send_message(
        self,
        phone: str,
        message: str,
        voter_data: Optional[Dict[str, Any]] = None,
        credit: Optional[Any] = None   # deep link only, never billed
    ) -> DispatchResult:
        link = self.generate_link(phone, message)
        return DispatchResult(
            success=True,
            channel=self.channel_name,
            recipient_phone=phone,
            details={'deep_link': link, 'action': 'manual_share'}
        )

    def send_slip(
        self,
        phone: str,
        message: str,
        image_path: str,
        voter_data: Optional[Dict[str, Any]] = None,
        credit: Optional[Any] = None   # deep link only, never billed
    ) -> DispatchResult:
        link = self.generate_link(phone, message)
        return DispatchResult(
            success=True,
            channel=self.channel_name,
            recipient_phone=phone,
            details={'deep_link': link, 'image_path': image_path, 'action': 'manual_share'}
        )

    def is_healthy(self) -> bool:
        return True
