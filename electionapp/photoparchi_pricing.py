"""
electionapp/photoparchi_pricing.py
Centralized pricing rules for PhotoParchi (parchi.smartbooth.in).
Allows 1-line rate updates and environment variable overrides without changing business logic.
"""

import os
from typing import Dict

# Default fixed pricing matrix per campaign type (INR)
DEFAULT_CAMPAIGN_RATES: Dict[str, float] = {
    "WARD_PANCH": float(os.environ.get("RATE_WARD_PANCH", 99.0)),
    "PANCHAYAT": float(os.environ.get("RATE_SARPANCH", 499.0)),
    "PANCHAYAT_SAMITI": float(os.environ.get("RATE_SAMITI", 999.0)),
    "ZILA_PARISHAD": float(os.environ.get("RATE_ZILA_PARISHAD", 1999.0)),
}


def get_campaign_rate(campaign_type: str) -> float:
    """Returns the fixed price in INR for a given campaign type."""
    ctype = (campaign_type or "PANCHAYAT").strip().upper()
    return DEFAULT_CAMPAIGN_RATES.get(ctype, 499.0)


def get_all_campaign_rates() -> Dict[str, float]:
    """Returns all current pricing tiers."""
    return dict(DEFAULT_CAMPAIGN_RATES)
