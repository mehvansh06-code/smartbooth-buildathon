"""
electionapp/services/cloudshope_voice.py
Official CloudShope Voice Broadcast & Outbound Dialer (OBD) Client.

Features:
- Long-Lived API Token support (Profile -> API Token) with automatic fallback to JWT login
- Automated JWT Authentication (/api/v1/auth/login) with thread-safe token caching & auto-renewal
- Live Wallet Balance (/api/v1/wallet/balance)
- Verified Outbound Caller IDs / CLIs (/api/v1/voice/getCallerIds)
- Audio WAV Upload (/api/v1/voice/voice-upload) returning voice_file_id (no operator approval needed)
- Sound Clips List (/api/v1/voice/list)
- Voice Campaign Dispatch (/api/v1/voice/voiceCampaign)
- Campaign Status & Delivery Reporting (/api/v1/voice/voiceDetails, /api/v1/voice/voiceEntry)
"""

import os
import io
import time
import json
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple, Union

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("smartbooth.cloudshope_voice")

# Configuration via environment variables with production defaults
CLOUDSHOPE_BASE_URL = os.environ.get("CLOUDSHOPE_BASE_URL", "https://cloudshope.com/api/v1").rstrip("/")
CLOUDSHOPE_USERNAME = os.environ.get("CLOUDSHOPE_USERNAME", "digitalpracharwala")
CLOUDSHOPE_PASSWORD = os.environ.get("CLOUDSHOPE_PASSWORD", "digitalpracharwala")
CLOUDSHOPE_DEFAULT_CLI = os.environ.get("CLOUDSHOPE_DEFAULT_CLI", "6746780235")
DEFAULT_TIMEOUT = int(os.environ.get("CLOUDSHOPE_TIMEOUT", 30))

# 365-day production API token generated on user profile (expires Nov 3, 2027)
CLOUDSHOPE_API_TOKEN = os.environ.get(
    "CLOUDSHOPE_API_TOKEN",
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjo2NzM4LCJzb3VyY2UiOiJhcGkiLCJ1c2VyX3R5cGUiOiJVU0VSIiwibWFpbl91c2VyIjo2NzM4LCJ1cGxpbmUiOjY3MzgsInBhcmVudCI6MSwicm9sZV9pZCI6MiwidHlwZSI6ImFjY2VzcyIsImp0aSI6IjNjY2VkNzRiLTU1YWItNDRjZS05NjYzLTA1MWVlZWVmYTc1MCIsInN1YiI6IjY3MzgiLCJpc3MiOiJodHRwczovL2F1dGguY2xvdWRzaG9wZS5jb20iLCJhdWQiOiJodHRwczovL2FwaS5jbG91ZHNob3BlLmNvbSIsImlhdCI6MTc5MzczMDAyNywiZXhwIjoxODI1MjY2MDI3fQ.ZNz7lgoqGTqX-0G_Dgl42NrLG8KDMp8oj1hy98YHLy2SD6qkpHbgJOBvDkvmwi8_W60tISZP92tL9sNjLPK8M0bJ72myNP25AId6pJth2ZP7ynWu7rtZPvEvAUJm8PkpHjVxN-qKaQ5kMLhFxtdOLyYfWVAd9bxrIAWIW9eH-q04jemd9JxZRmZUKx_FDP3wXUM4edVxxVxsLzGpzsHDq2YMBV1i0Vf9YtQgL1qq_Rwc5f80v2YSGMkq5P-IwHYMMGc0nTfq45KwgfF1ASQkodZ4KzxmIe_G0sr19sNtYvRZZngl8o9xvpnrJIbhXZFGBe2rlxXHWoZ7DrH3N1lJiQ"
)


class CloudShopeClient:
    """Thread-safe REST client for CloudShope Voice Platform."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_token: Optional[str] = None,
        default_cli: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT
    ):
        self.base_url = (base_url or CLOUDSHOPE_BASE_URL).rstrip("/")
        self.username = username or CLOUDSHOPE_USERNAME
        self.password = password or CLOUDSHOPE_PASSWORD
        self.api_token = api_token if api_token is not None else CLOUDSHOPE_API_TOKEN
        self.default_cli = default_cli or CLOUDSHOPE_DEFAULT_CLI
        self.timeout = timeout

        self._token: Optional[str] = self.api_token or None
        # Long-lived token default (1 year)
        self._token_expires_at: float = time.time() + (365 * 86400) if self.api_token else 0.0
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "SmartBooth-VoiceEngine/2.0 (+https://smartbooth.vedmap.com)"
        })

    def get_token(self, force_refresh: bool = False) -> str:
        """Retrieves a valid token. Uses static API token if available, or dynamically logs in."""
        with self._lock:
            now = time.time()
            if not force_refresh and self._token and now < (self._token_expires_at - 300):
                return self._token

            # If force_refresh is requested and api_token was static, try login refresh
            login_url = f"{self.base_url}/auth/login"
            payload = {
                "username": self.username,
                "password": self.password
            }

            try:
                resp = self._session.post(
                    login_url,
                    json=payload,
                    timeout=self.timeout
                )
                if resp.status_code != 200:
                    # If dynamic login fails but static token exists, fallback to static token
                    if self.api_token:
                        self._token = self.api_token
                        return self._token
                    raise RuntimeError(f"CloudShope login failed ({resp.status_code}): {resp.text}")

                data = resp.json()
                token = data.get("data", {}).get("accessToken")
                if not token:
                    raise RuntimeError(f"CloudShope response missing accessToken: {resp.text}")

                self._token = token
                self._token_expires_at = now + 82800
                logger.info("CloudShope JWT access token acquired successfully")
                return self._token
            except Exception as e:
                if self.api_token:
                    logger.warning("Dynamic login failed (%s); using static API token", e)
                    self._token = self.api_token
                    return self._token
                logger.error("Failed to authenticate with CloudShope: %s", e)
                raise

    def _request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        retry_on_401: bool = True,
        **kwargs
    ) -> requests.Response:
        """Executes an authenticated HTTP request, handling 401 re-authentication automatically."""
        token = self.get_token()
        req_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*"
        }
        if headers:
            req_headers.update(headers)

        url = f"{self.base_url}/{path.lstrip('/')}"
        kwargs.setdefault("timeout", self.timeout)

        resp = self._session.request(method, url, headers=req_headers, **kwargs)

        if resp.status_code == 401 and retry_on_401:
            logger.warning("CloudShope returned 401 Unauthorized, refreshing token and retrying...")
            fresh_token = self.get_token(force_refresh=True)
            req_headers["Authorization"] = f"Bearer {fresh_token}"
            resp = self._session.request(method, url, headers=req_headers, **kwargs)

        return resp

    def get_wallet_balance(self) -> int:
        """Fetches available VOICE_CALL credit balance from CloudShope wallet."""
        try:
            resp = self._request("GET", "/wallet/balance")
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    if item.get("credit_type") == "VOICE_CALL":
                        return int(item.get("balance", 0))
            logger.warning("Unexpected response fetching wallet balance: %s", resp.text)
            return 0
        except Exception as e:
            logger.error("Error fetching CloudShope wallet balance: %s", e)
            return 0

    def get_caller_ids(self) -> List[Dict[str, Any]]:
        """Returns verified outbound Caller IDs (CLIs) configured on CloudShope."""
        try:
            resp = self._request("POST", "/voice/getCallerIds", json={})
            if resp.status_code == 200:
                raw_list = resp.json().get("data", [])
                result = []
                for item in raw_list:
                    num = str(item.get("number") or "")
                    result.append({
                        "number": num,
                        "displayNumber": num.split(" (")[0].strip(),
                        "expiry": item.get("expiry"),
                        "truecaller": str(item.get("truecaller")) in ("1", "true", "True"),
                        "isDefault": str(item.get("default")) in ("1", "true", "True")
                    })
                return result
            logger.warning("Unexpected response fetching caller IDs: %s", resp.text)
            return []
        except Exception as e:
            logger.error("Error fetching CloudShope caller IDs: %s", e)
            return []

    def get_sound_list(self) -> List[Dict[str, Any]]:
        """Fetches approved voice audio clips from CloudShope."""
        try:
            resp = self._request("GET", "/voice/list?call_type=obd_voice_call&page=1&pageSize=1000")
            if resp.status_code != 200:
                logger.warning("CloudShope voice/list returned status %d: %s", resp.status_code, resp.text)
                return []

            json_data = resp.json()
            raw_data = json_data.get("data", {})
            items = []
            if isinstance(raw_data, dict):
                items = list(raw_data.values())
            elif isinstance(raw_data, list):
                items = raw_data

            results = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                file_id = item.get("id")
                name = item.get("name") or f"audio_{file_id}"
                duration = int(item.get("duration") or 0)
                credit = int(item.get("credit") or 1)

                raw_html = item.get("audio_voice") or ""
                url = None
                if "src=" in raw_html:
                    parts = raw_html.split("src=")
                    if len(parts) > 1:
                        url = parts[1].split()[0].replace('"', '').replace("'", '').replace('>', '')

                results.append({
                    "id": file_id,
                    "sound_name": name,
                    "carrier_sound_name": str(file_id),
                    "duration_seconds": duration,
                    "credit": credit,
                    "url": url
                })
            return results
        except Exception as e:
            logger.error("Failed to fetch sound list from CloudShope: %s", e)
            return []

    def upload_sound(
        self,
        file_bytes_or_path: Union[bytes, bytearray, str, os.PathLike],
        sound_name: str
    ) -> Tuple[bool, str, Optional[int]]:
        """Uploads an 8kHz mono WAV audio prompt to CloudShope.
        Audio is immediately available and does NOT require manual operator whitelisting.

        :param file_bytes_or_path: Raw bytes or file path
        :param sound_name: Preferred sound label / filename
        :returns: (success, message, file_id)
        """
        try:
            if isinstance(file_bytes_or_path, (str, os.PathLike)):
                file_path = str(file_bytes_or_path)
                if not os.path.isfile(file_path):
                    return False, f"File not found: {file_path}", None
                with open(file_path, "rb") as f:
                    file_content = f.read()
            elif isinstance(file_bytes_or_path, (bytes, bytearray)):
                file_content = bytes(file_bytes_or_path)
            else:
                return False, f"Invalid audio input type: {type(file_bytes_or_path)}", None

            if not file_content:
                return False, "Audio content is empty", None

            filename = sound_name if sound_name.lower().endswith(".wav") else f"{sound_name}.wav"
            files = {
                "file": (filename, file_content, "audio/wav")
            }

            resp = self._request("POST", "/voice/voice-upload", files=files)
            logger.info("CloudShope voice-upload status=%s response=%s", resp.status_code, resp.text)

            if resp.status_code in (200, 201):
                data = resp.json()
                file_id = data.get("file_id") or data.get("data", {}).get("file_id") or data.get("id")
                if file_id is not None:
                    return True, "Audio uploaded successfully", int(file_id)
                return True, resp.text, None

            err_msg = f"CloudShope upload error ({resp.status_code}): {resp.text}"
            return False, err_msg, None
        except Exception as e:
            logger.error("Exception uploading audio to CloudShope: %s", e)
            return False, str(e), None

    def dispatch_campaign(
        self,
        campaign_name: str,
        voice_file_id: Union[int, str],
        numbers: List[str],
        caller_id: Optional[str] = None,
        call_type: str = "obd_voice_call",
        max_retry: int = 2,
        retry_after: int = 10,
        callback_url: Optional[str] = None,
        scheduled_at: Optional[str] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """Launches an Outbound Voice Broadcast (OBD) campaign on CloudShope.
        Passes voice_file_id strictly as a string matching CloudShope's validation schema.

        :param campaign_name: Descriptive campaign label (e.g., 'Ward 4 Sarpanch Speech')
        :param voice_file_id: ID of uploaded voice clip (string e.g. "4")
        :param numbers: List of 10-digit Indian mobile numbers
        :param caller_id: Outbound CLI (defaults to configured Truecaller CLI)
        :param call_type: Type of voice campaign (default: 'obd_voice_call')
        :param max_retry: Retries for unanswered calls (1-3)
        :param retry_after: Minutes between retries
        :param callback_url: Webhook URL for real-time delivery notifications
        :param scheduled_at: Optional ISO schedule string, omit for immediate blast
        :returns: (success, message, campaign_id)
        """
        if not numbers:
            return False, "No recipients provided", None

        cli = caller_id or self.default_cli
        # Note: CloudShope API schema strictly expects voice_file_id as a string ("4")
        # and accommodates both sendTo and sentTo keys.
        payload = {
            "campaign_name": campaign_name[:190],
            "voice_file_id": str(voice_file_id),
            "call_type": call_type,
            "sendTo": "Numbers",
            "sentTo": "Numbers",
            "number": ",".join(numbers),
            "caller_id": cli,
            "max_retry": int(max_retry),
            "retry_after": int(retry_after)
        }

        if callback_url:
            payload["callback_url"] = callback_url

        if scheduled_at:
            payload["schedulled_at"] = scheduled_at

        try:
            resp = self._request("POST", "/voice/voiceCampaign", json=payload)
            logger.info("CloudShope voiceCampaign status=%s response=%s", resp.status_code, resp.text[:300])

            if resp.status_code in (200, 201):
                data = resp.json()
                camp_id = data.get("campaignId") or data.get("data", {}).get("campaignId") or data.get("id")
                return True, "Campaign submitted successfully", str(camp_id or "")

            err_msg = f"CloudShope campaign dispatch failed ({resp.status_code}): {resp.text}"
            return False, err_msg, None
        except Exception as e:
            logger.error("Exception dispatching CloudShope campaign: %s", e)
            return False, str(e), None

    def get_campaign_status(self, campaign_id: Union[int, str]) -> Dict[str, Any]:
        """Queries campaign analytics and overall status via /api/v1/voice/voiceDetails."""
        try:
            resp = self._request(
                "POST",
                "/voice/voiceDetails",
                json={"obdEntryId": int(campaign_id)}
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.error("Error fetching campaign %s status: %s", campaign_id, e)
            return {"error": str(e)}

    def get_call_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        status: str = "",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Queries detailed call entries via /api/v1/voice/voiceEntry."""
        try:
            payload: Dict[str, Any] = {
                "page": page,
                "pageSize": page_size,
                "search": search,
                "status": status,
                "callType": "obd_voice_call"
            }
            if date_from:
                payload["dateFrom"] = date_from
            if date_to:
                payload["dateTo"] = date_to

            resp = self._request("POST", "/voice/voiceEntry", json=payload)
            if resp.status_code == 200:
                return resp.json()
            return {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.error("Error fetching call logs: %s", e)
            return {"error": str(e)}


# Global default singleton client
default_client = CloudShopeClient()
