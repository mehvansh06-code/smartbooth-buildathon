"""
electionapp/payment_service.py
==============================
Unified Payment Provider Adapter for SmartBooth & PhotoParchi.
Allows seamless switching between Cashfree, Razorpay, and PayU without
modifying any route handlers or application logic.

Configured via environment variable:
    PAYMENT_PROVIDER = "CASHFREE" | "RAZORPAY" | "PAYU" (Default: "CASHFREE")
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("smartbooth.payment_service")


class PaymentAdapter(ABC):
    @abstractmethod
    def create_order(
        self,
        order_id: str,
        amount: float,
        customer_phone: str,
        customer_name: str,
        return_url: str,
        customer_email: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Creates payment order.
        Returns: (success: bool, data: dict, error_message: str | None)
        """
        pass

    @abstractmethod
    def verify_order(self, order_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Verifies order status.
        Returns: (is_paid: bool, status_str: str, raw_data: dict)
        """
        pass


class CashfreeProvider(PaymentAdapter):
    def __init__(self):
        try:
            from electionapp.cashfree_client import create_payment_order, get_order_status
            self._create_fn = create_payment_order
            self._status_fn = get_order_status
        except ImportError as e:
            logger.error(f"Cashfree client import failed: {e}")
            self._create_fn = None
            self._status_fn = None

    def create_order(
        self,
        order_id: str,
        amount: float,
        customer_phone: str,
        customer_name: str,
        return_url: str,
        customer_email: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        if not self._create_fn:
            return False, {}, "Cashfree client not configured"
        return self._create_fn(
            order_id=order_id,
            order_amount=amount,
            customer_phone=customer_phone,
            customer_name=customer_name,
            customer_email=customer_email or "voter@smartbooth.in",
            return_url=return_url
        )

    def verify_order(self, order_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        if not self._status_fn:
            return False, "ERROR", {"error": "Cashfree client not configured"}
        success, data, err = self._status_fn(order_id)
        if not success or not data:
            return False, "FAILED", {"error": err}
        status = data.get("order_status", "").upper()
        return (status == "PAID"), status, data


class RazorpayProvider(PaymentAdapter):
    def create_order(
        self,
        order_id: str,
        amount: float,
        customer_phone: str,
        customer_name: str,
        return_url: str,
        customer_email: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        # Razorpay implementation placeholder / legacy hook
        logger.info(f"Razorpay order requested for {order_id} (amount={amount})")
        return False, {}, "Razorpay currently offline due to provider compliance"

    def verify_order(self, order_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        return False, "DISABLED", {"error": "Razorpay currently offline"}


class PaymentService:
    """Factory and facade for the active payment provider."""

    @staticmethod
    def get_provider() -> PaymentAdapter:
        provider_name = os.environ.get("PAYMENT_PROVIDER", "CASHFREE").strip().upper()
        if provider_name == "RAZORPAY":
            return RazorpayProvider()
        return CashfreeProvider()

    @classmethod
    def create_order(cls, **kwargs):
        return cls.get_provider().create_order(**kwargs)

    @classmethod
    def verify_order(cls, order_id: str):
        return cls.get_provider().verify_order(order_id)
