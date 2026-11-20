from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import datetime
from typing import Optional, Dict, Any


@dataclass
class DispatchResult:
    """
    Standard outcome object for any dispatch attempt.
    """
    success: bool
    channel: str
    recipient_phone: str
    message_id: Optional[str] = None
    port: Optional[int] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            'success': self.success,
            'channel': self.channel,
            'recipient_phone': self.recipient_phone,
            'message_id': self.message_id,
            'port': self.port,
            'error': self.error,
            'details': self.details,
            'timestamp': self.timestamp
        }


class BaseDispatchChannel(ABC):
    """
    Abstract Protocol/Base for dispatch channels (Meta Cloud, wa.me, Null).
    """
    channel_name: str = "base"

    @abstractmethod
    def send_message(
        self,
        phone: str,
        message: str,
        voter_data: Optional[Dict[str, Any]] = None,
        credit: Optional[Any] = None
    ) -> DispatchResult:
        """Sends a text message to recipient phone.

        `credit` is a WhatsApp quota reservation. Channels that actually bill (i.e.
        call a provider API) MUST require it and claim from it; link-only channels
        such as wa_me ignore it because they cost nothing.
        """
        pass

    @abstractmethod
    def send_slip(
        self,
        phone: str,
        message: str,
        image_path: str,
        voter_data: Optional[Dict[str, Any]] = None,
        credit: Optional[Any] = None
    ) -> DispatchResult:
        """Sends a voter slip image with caption text to recipient phone."""
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        """Checks if the channel gateway or API is accessible."""
        pass
