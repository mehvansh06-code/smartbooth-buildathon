"""
electionapp/cashfree_client.py
Cashfree Payment Gateway Integration Client (v2023-08-01).
Handles order creation, payment session generation, order status polling,
and UPI-first checkout for PhotoParchi access codes.
Supports both Sandbox (Test) and Production environments.
"""

import os
import json
import logging
import requests
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("smartbooth.cashfree")

# Environment & Credentials
CASHFREE_APP_ID = os.environ.get("CASHFREE_APP_ID", "")
CASHFREE_SECRET_KEY = os.environ.get("CASHFREE_SECRET_KEY", "").strip()
if not CASHFREE_SECRET_KEY:
    # Check fallback secure config path if present
    _cfg_path = os.path.expanduser("~/.cashfree/secret_key")
    if os.path.exists(_cfg_path):
        with open(_cfg_path, "r", encoding="utf-8") as _f:
            CASHFREE_SECRET_KEY = _f.read().strip()
CASHFREE_ENV = os.environ.get("CASHFREE_ENV", "PROD").strip().upper()
CASHFREE_API_VERSION = os.environ.get("CASHFREE_API_VERSION", "2023-08-01").strip()

SANDBOX_URL = "https://sandbox.cashfree.com/pg"
PRODUCTION_URL = "https://api.cashfree.com/pg"


def get_base_url() -> str:
    """Returns Sandbox or Production URL based on CASHFREE_ENV."""
    env = os.environ.get("CASHFREE_ENV", CASHFREE_ENV).strip().upper()
    return PRODUCTION_URL if env in ("PROD", "PRODUCTION") else SANDBOX_URL


def get_environment_mode() -> str:
    """Returns 'production' or 'sandbox' for frontend Cashfree SDK initialization."""
    env = os.environ.get("CASHFREE_ENV", CASHFREE_ENV).strip().upper()
    return "production" if env in ("PROD", "PRODUCTION") else "sandbox"


def get_headers() -> Dict[str, str]:
    """Builds standard Cashfree authentication and version headers."""
    app_id = os.environ.get("CASHFREE_APP_ID", CASHFREE_APP_ID).strip()
    secret_key = os.environ.get("CASHFREE_SECRET_KEY", CASHFREE_SECRET_KEY).strip()
    api_version = os.environ.get("CASHFREE_API_VERSION", CASHFREE_API_VERSION).strip()
    return {
        "x-client-id": app_id,
        "x-client-secret": secret_key,
        "x-api-version": api_version,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def create_payment_order(
    order_id: str,
    amount: float,
    customer_phone: str,
    customer_name: str,
    return_url: str,
    customer_id: Optional[str] = None,
    order_note: Optional[str] = None,
    payment_methods: str = "upi"
) -> Tuple[bool, Dict[str, Any]]:
    """
    Creates an order on Cashfree PG and returns (success, response_dict).
    On success, response_dict contains 'payment_session_id', 'cf_order_id', 'order_status'.
    """
    clean_amount = round(float(amount), 2)
    if clean_amount <= 0:
        return False, {"error": "Order amount must be greater than 0"}

    # Sanitize phone (must be valid 10-digit Indian mobile number for Cashfree)
    clean_phone = "".join(filter(str.isdigit, str(customer_phone)))
    if len(clean_phone) > 10 and clean_phone.startswith("91"):
        clean_phone = clean_phone[2:]
    if len(clean_phone) != 10:
        clean_phone = "9829012345"  # Safe default placeholder for payment session

    clean_name = (customer_name or "Candidate").strip()[:64]
    cust_id = (customer_id or f"cust_{order_id}")[:50]

    payload = {
        "order_id": order_id,
        "order_amount": clean_amount,
        "order_currency": "INR",
        "customer_details": {
            "customer_id": cust_id,
            "customer_name": clean_name,
            "customer_phone": clean_phone
        },
        "order_meta": {
            "return_url": return_url,
            "payment_methods": payment_methods
        },
        "order_note": order_note or f"PhotoParchi Order {order_id}"
    }

    url = f"{get_base_url()}/orders"
    headers = get_headers()

    try:
        logger.info("Creating Cashfree order: %s, amount: ₹%s (env: %s)", order_id, clean_amount, get_base_url())
        resp = requests.post(url, json=payload, headers=headers, timeout=12)
        data = resp.json()

        if resp.status_code in (200, 201):
            return True, data
        else:
            err_msg = data.get("message") or data.get("error") or str(data)
            err_code = data.get("code") or "request_failed"
            logger.error("Cashfree order creation failed [%d]: %s (code: %s)", resp.status_code, err_msg, err_code)
            is_pg_disabled = "transactions are not enabled" in str(err_msg).lower()
            return False, {
                "error": err_msg,
                "code": err_code,
                "is_pg_disabled": is_pg_disabled,
                "details": data,
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.error("Exception in Cashfree create_payment_order: %s", e)
        return False, {"error": str(e), "is_pg_disabled": False}


def get_order_status(order_id: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Fetches the authoritative status of an order from Cashfree server.
    Returns (success, response_dict).
    Key field in response: 'order_status' ('ACTIVE', 'PAID', 'EXPIRED', 'TERMINATED').
    """
    if not order_id:
        return False, {"error": "order_id is required"}

    url = f"{get_base_url()}/orders/{order_id}"
    headers = get_headers()

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if resp.status_code == 200:
            return True, data
        else:
            err_msg = data.get("message") or str(data)
            logger.error("Cashfree get_order_status failed [%d]: %s", resp.status_code, err_msg)
            return False, {"error": err_msg, "status_code": resp.status_code}
    except Exception as e:
        logger.error("Exception in Cashfree get_order_status: %s", e)
        return False, {"error": str(e)}


def get_payments_for_order(order_id: str) -> Tuple[bool, list]:
    """
    Retrieves all payment attempts for an order.
    Returns list of payment entities with payment_method, upi details, payment_status ('SUCCESS', 'FAILED').
    """
    if not order_id:
        return False, []

    url = f"{get_base_url()}/orders/{order_id}/payments"
    headers = get_headers()

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if resp.status_code == 200 and isinstance(data, list):
            return True, data
        return False, []
    except Exception as e:
        logger.error("Exception in Cashfree get_payments_for_order: %s", e)
        return False, []
