"""
electionapp/services/sarvam_voice_agent.py
Official Sarvam Voice Agents (Samvaad) Platform Integration Client.

Supports:
1. Real-time interaction log tracking, turn transcripts, and recording retrieval.
2. Instant 1-Click Outbound Calls (when telephony connection is attached).
3. Batch Outbound Campaign management and cohort streaming.
"""

import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import requests

logger = logging.getLogger("smartbooth.sarvam_voice")

SARVAM_VOICE_BASE_URL = os.environ.get("SARVAM_VOICE_BASE_URL", "https://apps.sarvam.ai/api")
SARVAM_VOICE_API_KEY = os.environ.get("SARVAM_VOICE_API_KEY", "sk_samvaad_7zu88kyw_qg8SD5r9eqNrw1PVVpXBl0cS")
SARVAM_ORG_ID = os.environ.get("SARVAM_ORG_ID", "019f7b99-64c6-7354-af44-30bcace61ed7")
SARVAM_WORKSPACE_ID = os.environ.get("SARVAM_WORKSPACE_ID", "019f7b99-64d0-7d3f-868f-0b3f62a588a0")
SARVAM_AGENT_APP_ID = os.environ.get("SARVAM_AGENT_APP_ID", "Conversatio-ce6ec544-ee50")


def _get_headers() -> Dict[str, str]:
    return {
        "X-API-Key": SARVAM_VOICE_API_KEY,
        "Content-Type": "application/json"
    }


def get_interactions(
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    limit: int = 20,
    app_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Fetches recent voice agent interactions (calls/sessions).
    Dates should be ISO 8601 strings (e.g. 2026-11-01T00:00:00Z).
    """
    target_app = app_id or SARVAM_AGENT_APP_ID
    if not start_datetime:
        start_datetime = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
    if not end_datetime:
        end_datetime = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT23:59:59Z")

    url = f"{SARVAM_VOICE_BASE_URL}/analytics/v1/{SARVAM_ORG_ID}/{SARVAM_WORKSPACE_ID}/{target_app}/interactions"
    params = {
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "limit": limit
    }

    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("items", [])
        logger.warning("Sarvam analytics interactions returned status %d: %s", resp.status_code, resp.text)
        return []
    except Exception as e:
        logger.error("Error fetching Sarvam interactions: %s", e)
        return []


def get_transcript(interaction_id: str, app_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves the complete turn-by-turn transcript for a given interaction ID."""
    target_app = app_id or SARVAM_AGENT_APP_ID
    url = f"{SARVAM_VOICE_BASE_URL}/analytics/v1/{SARVAM_ORG_ID}/{SARVAM_WORKSPACE_ID}/{target_app}/transcripts/{interaction_id}"
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.json().get("messages", [])
        logger.warning("Sarvam transcript returned %d: %s", resp.status_code, resp.text)
        return []
    except Exception as e:
        logger.error("Error fetching Sarvam transcript: %s", e)
        return []


def get_recording_url(interaction_id: str, app_id: Optional[str] = None) -> Optional[str]:
    """Retrieves the audio recording URL for a given interaction ID."""
    target_app = app_id or SARVAM_AGENT_APP_ID
    url = f"{SARVAM_VOICE_BASE_URL}/analytics/v1/{SARVAM_ORG_ID}/{SARVAM_WORKSPACE_ID}/{target_app}/recordings/{interaction_id}"
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.json().get("recording_url")
        return None
    except Exception as e:
        logger.error("Error fetching Sarvam recording URL: %s", e)
        return None


def trigger_instant_outbound_call(
    user_phone_number: str,
    connection_id: str,
    agent_phone_number: str,
    app_version: int = 1,
    agent_variables: Optional[Dict[str, Any]] = None,
    webhook_url: Optional[str] = None,
    app_id: Optional[str] = None
) -> Dict[str, Any]:
    """Places an instant outbound AI call to the user_phone_number using the configured telephony connection."""
    target_app = app_id or SARVAM_AGENT_APP_ID
    url = f"{SARVAM_VOICE_BASE_URL}/outbounds/v1/orgs/{SARVAM_ORG_ID}/workspaces/{SARVAM_WORKSPACE_ID}/outbounds"

    payload: Dict[str, Any] = {
        "app_config": {
            "app_id": target_app,
            "app_version": app_version,
            "connection_config": {
                "connection_id": connection_id,
                "agent_phone_number": agent_phone_number
            }
        },
        "user_config": {
            "user_phone_number": user_phone_number
        }
    }

    if agent_variables:
        payload["app_config"]["agent_variables"] = agent_variables

    if webhook_url:
        payload["webhook_config"] = {"url": webhook_url}

    try:
        resp = requests.post(url, headers=_get_headers(), json=payload, timeout=12)
        if resp.status_code == 200:
            return {"ok": True, "attempt_id": resp.json().get("attempt_id")}
        return {"ok": False, "status_code": resp.status_code, "error": resp.text}
    except Exception as e:
        logger.error("Instant outbound call trigger error: %s", e)
        return {"ok": False, "error": str(e)}
