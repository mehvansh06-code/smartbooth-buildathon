from typing import Optional, Dict, Any, List
from .base import BaseDispatchChannel, DispatchResult


class NullChannel(BaseDispatchChannel):
    """
    In-memory mock/null channel for testing and offline simulations.
    Makes zero network requests and tracks all outbound dispatches in memory.
    """
    channel_name: str = "null"

    def __init__(self):
        self.sent_messages: List[Dict[str, Any]] = []

    def send_message(
        self,
        phone: str,
        message: str,
        voter_data: Optional[Dict[str, Any]] = None,
        credit: Optional[Any] = None   # in-memory test channel, never billed
    ) -> DispatchResult:
        msg_id = f"null_msg_{len(self.sent_messages) + 1}"
        record = {
            'phone': phone,
            'message': message,
            'voter_data': voter_data,
            'type': 'text',
            'id': msg_id
        }
        self.sent_messages.append(record)
        return DispatchResult(
            success=True,
            channel=self.channel_name,
            recipient_phone=phone,
            message_id=msg_id,
            details=record
        )

    def send_slip(
        self,
        phone: str,
        message: str,
        image_path: str,
        voter_data: Optional[Dict[str, Any]] = None,
        credit: Optional[Any] = None   # in-memory test channel, never billed
    ) -> DispatchResult:
        msg_id = f"null_slip_{len(self.sent_messages) + 1}"
        record = {
            'phone': phone,
            'message': message,
            'image_path': image_path,
            'voter_data': voter_data,
            'type': 'slip',
            'id': msg_id
        }
        self.sent_messages.append(record)
        return DispatchResult(
            success=True,
            channel=self.channel_name,
            recipient_phone=phone,
            message_id=msg_id,
            details=record
        )

    def is_healthy(self) -> bool:
        return True

    def clear(self) -> None:
        self.sent_messages.clear()
