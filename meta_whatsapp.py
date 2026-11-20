import os
from config import load_campaign_config
import re
import json
import hashlib
import time
import requests
import threading
import uuid
from typing import Optional, List, Dict, Any

try:
    from dotenv import load_dotenv
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        load_dotenv(env_file, override=False)
    else:
        load_dotenv()
except ImportError:
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

# Banned phone numbers and sender IDs that must NEVER be used by SmartBooth
BANNED_PHONE_NUMBER_IDS = set()
BANNED_SENDER_NUMBERS = set()

# Default fallback credentials (production WhatsApp Cloud & GreenTick/AOC APIs loaded from environment)
DEFAULT_PHONE_NUMBER_ID = os.environ.get('META_WA_PHONE_NUMBER_ID', '')
DEFAULT_WABA_ID = os.environ.get('META_WA_WABA_ID', '')
DEFAULT_ACCESS_TOKEN = os.environ.get('META_WA_ACCESS_TOKEN', '')
DEFAULT_TEMPLATE_NAME = os.environ.get('META_WA_TEMPLATE_NAME', '')
DEFAULT_TEMPLATE_LANG = os.environ.get('META_WA_TEMPLATE_LANG', 'en')
DEFAULT_API_VERSION = os.environ.get('META_WA_API_VERSION', 'v22.0')
DEFAULT_SENDER_DISPLAY = os.environ.get('META_WA_SENDER_DISPLAY', '')
DEFAULT_AOC_API_KEY = os.environ.get('AOC_API_KEY', '')
DEFAULT_AOC_SENDER = os.environ.get('AOC_SENDER_NUMBER', '')
DEFAULT_AOC_TEMPLATE_NAME = os.environ.get('AOC_TEMPLATE_NAME', 'info_update_short')
PRIMARY_WA_GATEWAY = os.environ.get('PRIMARY_WA_GATEWAY', 'auto').lower().strip()

# Persistent HTTP Connection Pool for Meta Graph API
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=40,
    max_retries=Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
)
_session.mount('https://', _adapter)
_session.mount('http://', _adapter)

# In-memory media cache: key -> media_id (supports both file_hash and (provider_id, file_hash))
_MEDIA_CACHE = {}


# ===========================================================================
# MULTI-PROVIDER ARCHITECTURE & CIRCUIT BREAKER
# ===========================================================================

META_BAN_RESTRICTION_CODES = {
    131031,  # Account has been locked
    131042,  # Business Account is not in a valid status
    131045,  # Phone number is deregistered / unverified / banned
    131057,  # Account has been restricted from sending messages
    368,     # Temporarily blocked for policy violations (Spam/Abuse)
}

_JPEG_MAGIC = bytes.fromhex('ffd8ff')   # JPEG SOI marker


class WhatsAppProvider:
    """
    Encapsulates a single Meta WhatsApp Cloud API account (WABA / Phone Number ID).
    Maintains independent circuit breaker state, failure tracking, and daily quota.
    """
    def __init__(
        self,
        provider_id: str,
        phone_number_id: str,
        access_token: str,
        waba_id: str = "",
        template_name: str = DEFAULT_TEMPLATE_NAME,
        template_lang: str = DEFAULT_TEMPLATE_LANG,
        api_version: str = DEFAULT_API_VERSION,
        sender_display: str = "",
        priority: int = 1,
        daily_quota: int = 10000,
        enabled: bool = True
    ):
        self.id = str(provider_id).strip()
        self.phone_number_id = str(phone_number_id).strip()
        self.access_token = str(access_token).strip()
        self.waba_id = str(waba_id or "").strip()
        self.template_name = str(template_name or DEFAULT_TEMPLATE_NAME).strip()
        self.template_lang = str(template_lang or DEFAULT_TEMPLATE_LANG).strip()
        self.api_version = str(api_version or DEFAULT_API_VERSION).strip()
        self.sender_display = str(sender_display or "").strip()
        self.priority = int(priority)
        self.daily_quota = int(daily_quota)
        self.enabled = bool(enabled)

        # Circuit breaker & rate-limit state
        self.is_banned = False
        self.cooldown_until = 0.0
        self.failure_count = 0
        self.last_error = None
        self.sent_today = 0
        self.last_used_date = time.strftime('%Y-%m-%d')

    def _refresh_daily_quota(self):
        today = time.strftime('%Y-%m-%d')
        if self.last_used_date != today:
            self.sent_today = 0
            self.last_used_date = today

    def is_available(self) -> bool:
        """Returns True if provider is configured, enabled, within daily quota, and not in cooldown."""
        if not self.enabled or self.is_banned:
            return False
        if not self.phone_number_id or not self.access_token:
            return False
        self._refresh_daily_quota()
        if self.daily_quota > 0 and self.sent_today >= self.daily_quota:
            return False
        if time.time() < self.cooldown_until:
            return False
        return True

    def record_success(self):
        """Resets failure counter and advances daily usage on successful message dispatch."""
        self.failure_count = 0
        self.cooldown_until = 0.0
        self.last_error = None
        self._refresh_daily_quota()
        self.sent_today += 1

    def record_failure(self, error_msg: str, cooldown_seconds: int = 600):
        """
        Trips circuit breaker on fatal provider errors (rate limit, token expired, 5xx outages).
        Exponentially backs off on repeated failures up to 30 minutes.
        """
        self.failure_count += 1
        self.last_error = str(error_msg)
        multiplier = 2 ** max(0, min(self.failure_count - 1, 3))
        actual_cooldown = min(1800, cooldown_seconds * multiplier)
        self.cooldown_until = time.time() + actual_cooldown
        print(f"[CIRCUIT_BREAKER] Provider '{self.id}' tripped. In cooldown for {actual_cooldown}s. Reason: {error_msg}", flush=True)

    def record_ban(self, error_msg: str):
        """
        Immediately marks provider as BANNED/RESTRICTED and deactivates it for 24 hours.
        Prevents wasting further API requests or polling on a compromised account.
        """
        self.is_banned = True
        self.failure_count += 5
        self.last_error = f"[BANNED/RESTRICTED] {error_msg}"
        self.cooldown_until = time.time() + 86400  # 24 hours
        print(f"[CRITICAL_ALERT] Provider '{self.id}' ({self.phone_number_id}) is BANNED/RESTRICTED by Meta! Error: {error_msg}", flush=True)

    def get_auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}"
        }

    def to_dict(self) -> Dict[str, Any]:
        self._refresh_daily_quota()
        now = time.time()
        in_cooldown = now < self.cooldown_until
        status = 'BANNED' if self.is_banned else ('COOLDOWN' if in_cooldown else ('AVAILABLE' if self.is_available() else 'DISABLED'))
        return {
            'id': self.id,
            'phone_number_id': self.phone_number_id,
            'display_number': self.sender_display or f"ID: {self.phone_number_id}",
            'priority': self.priority,
            'enabled': self.enabled and not self.is_banned,
            'is_banned': self.is_banned,
            'daily_quota': self.daily_quota,
            'sent_today': self.sent_today,
            'status': status,
            'cooldown_remaining_sec': max(0, int(self.cooldown_until - now)) if in_cooldown else 0,
            'failure_count': self.failure_count,
            'last_error': self.last_error
        }



class WhatsAppProviderPool:
    """
    Thread-safe registry of all WhatsApp Cloud API accounts.
    Supports dynamic addition, JSON file loading, and priority-based cascade failover.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.providers: List[WhatsAppProvider] = []
        self.load_providers()

    def load_providers(self):
        with self.lock:
            self.providers.clear()

            # 1. Primary Provider from standard environment variables (100% backward compatible)
            p_phone = (os.environ.get('META_WA_PHONE_NUMBER_ID') or DEFAULT_PHONE_NUMBER_ID).strip()
            p_token = (os.environ.get('META_WA_ACCESS_TOKEN') or DEFAULT_ACCESS_TOKEN).strip()
            p_waba = (os.environ.get('META_WA_WABA_ID') or DEFAULT_WABA_ID).strip()
            p_tpl = (os.environ.get('META_WA_TEMPLATE_NAME') or DEFAULT_TEMPLATE_NAME).strip()
            p_lang = (os.environ.get('META_WA_TEMPLATE_LANG') or DEFAULT_TEMPLATE_LANG).strip()
            p_ver = (os.environ.get('META_WA_API_VERSION') or DEFAULT_API_VERSION).strip()
            p_disp = (os.environ.get('META_WA_SENDER_DISPLAY') or DEFAULT_SENDER_DISPLAY).strip()

            if p_phone in BANNED_PHONE_NUMBER_IDS:
                print(f"[PROVIDER_POOL] Bypassing banned provider '{p_phone}' ({p_disp})", flush=True)
            elif p_phone and p_token:
                self.providers.append(WhatsAppProvider(
                    provider_id="primary",
                    phone_number_id=p_phone,
                    access_token=p_token,
                    waba_id=p_waba,
                    template_name=p_tpl,
                    template_lang=p_lang,
                    api_version=p_ver,
                    sender_display=p_disp,
                    priority=1,
                    daily_quota=int(os.environ.get('META_WA_DAILY_QUOTA', 10000)),
                    enabled=True
                ))

            # 2. Check for whatsapp_providers.json config file in project root or config directory
            cfg_paths = [
                os.path.join(os.path.dirname(__file__), 'whatsapp_providers.json'),
                os.path.join(os.path.dirname(__file__), 'config', 'whatsapp_providers.json')
            ]
            for p in cfg_paths:
                if os.path.exists(p):
                    try:
                        with open(p, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                for item in data:
                                    if not item.get('phone_number_id') or not item.get('access_token'):
                                        continue
                                    if item.get('phone_number_id') in BANNED_PHONE_NUMBER_IDS:
                                        continue
                                    pid = item.get('id', f"line_{len(self.providers)+1}")
                                    # Avoid duplicate IDs
                                    if any(x.id == pid for x in self.providers):
                                        continue
                                    self.providers.append(WhatsAppProvider(
                                        provider_id=pid,
                                        phone_number_id=item['phone_number_id'],
                                        access_token=item['access_token'],
                                        waba_id=item.get('waba_id', ''),
                                        template_name=item.get('template_name', p_tpl),
                                        template_lang=item.get('template_lang', p_lang),
                                        api_version=item.get('api_version', p_ver),
                                        sender_display=item.get('sender_display', ''),
                                        priority=item.get('priority', len(self.providers) + 1),
                                        daily_quota=item.get('daily_quota', 10000),
                                        enabled=item.get('enabled', True)
                                    ))
                        break
                    except Exception as json_err:
                        print(f"[PROVIDER_POOL] Warning: failed to parse {p}: {json_err}", flush=True)

            # 3. Check for indexed environment variables (META_WA_BACKUP_1_..., META_WA_BACKUP_2_...)
            for idx in range(1, 11):
                b_phone = os.environ.get(f'META_WA_BACKUP_{idx}_PHONE_NUMBER_ID') or os.environ.get(f'META_WA_BACKUP_{idx}_PHONE_ID')
                b_token = os.environ.get(f'META_WA_BACKUP_{idx}_ACCESS_TOKEN') or os.environ.get(f'META_WA_BACKUP_{idx}_TOKEN')
                if b_phone and b_token:
                    pid = f"backup_{idx}"
                    if not any(x.id == pid for x in self.providers):
                        self.providers.append(WhatsAppProvider(
                            provider_id=pid,
                            phone_number_id=b_phone.strip(),
                            access_token=b_token.strip(),
                            waba_id=os.environ.get(f'META_WA_BACKUP_{idx}_WABA_ID', '').strip(),
                            template_name=os.environ.get(f'META_WA_BACKUP_{idx}_TEMPLATE_NAME', p_tpl).strip(),
                            template_lang=os.environ.get(f'META_WA_BACKUP_{idx}_TEMPLATE_LANG', p_lang).strip(),
                            api_version=os.environ.get(f'META_WA_BACKUP_{idx}_API_VERSION', p_ver).strip(),
                            sender_display=os.environ.get(f'META_WA_BACKUP_{idx}_SENDER_DISPLAY', '').strip(),
                            priority=int(os.environ.get(f'META_WA_BACKUP_{idx}_PRIORITY', idx + 1)),
                            daily_quota=int(os.environ.get(f'META_WA_BACKUP_{idx}_DAILY_QUOTA', 10000)),
                            enabled=os.environ.get(f'META_WA_BACKUP_{idx}_ENABLED', 'true').lower() in ('1', 'true', 'yes')
                        ))

            # 4. Sort providers by priority (ascending: 1 first, then 2, then 3...)
            self.providers.sort(key=lambda p: (p.priority, p.id))

    def get_primary_provider(self) -> Optional[WhatsAppProvider]:
        with self.lock:
            return self.providers[0] if self.providers else None

    def get_provider(self, provider_id: str) -> Optional[WhatsAppProvider]:
        with self.lock:
            for p in self.providers:
                if p.id == provider_id:
                    return p
            return None

    def get_available_providers(self) -> List[WhatsAppProvider]:
        """
        Returns all configured providers ready to send, sorted by priority.
        If all providers are in cooldown (disaster recovery), returns the one
        whose cooldown expires earliest so service is never completely choked.
        """
        with self.lock:
            healthy = [p for p in self.providers if p.is_available()]
            if healthy:
                return healthy
            # Emergency fallback: pick least recently failed enabled provider
            enabled = [p for p in self.providers if p.enabled and p.phone_number_id and p.access_token]
            if enabled:
                enabled.sort(key=lambda p: p.cooldown_until)
                return [enabled[0]]
            return []

    def get_all_providers(self) -> List[WhatsAppProvider]:
        with self.lock:
            return list(self.providers)

    def add_or_update_provider(self, provider: WhatsAppProvider):
        with self.lock:
            existing = [p for p in self.providers if p.id == provider.id]
            if existing:
                self.providers.remove(existing[0])
            self.providers.append(provider)
            self.providers.sort(key=lambda p: (p.priority, p.id))

    def get_status_summary(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [p.to_dict() for p in self.providers]


# Global singleton provider pool
provider_pool = WhatsAppProviderPool()


# ---------------------------------------------------------------------------
# PERSISTENT TCP/TLS SOCKET KEEPER (HEARTBEAT)
# Keeps the HTTPS socket to graph.facebook.com warm and open in RAM.
# Eliminates the ~120ms TCP+TLS handshake latency when an operator sends after an idle gap.
# ---------------------------------------------------------------------------
_KEEPER_INTERVAL = int(os.environ.get('META_KEEP_ALIVE_SECONDS', '35'))
_keeper_thread = None
_keeper_lock = threading.Lock()


def _keep_alive_loop():
    while True:
        try:
            time.sleep(_KEEPER_INTERVAL)
            if os.environ.get('DISABLE_META_KEEPER') == '1' or not provider_pool.get_primary_provider():
                break
            prov = provider_pool.get_primary_provider()
            if prov and prov.access_token and prov.phone_number_id:
                url = f"https://graph.facebook.com/{prov.api_version}/{prov.phone_number_id}?fields=id"
                headers = {"Authorization": f"Bearer {prov.access_token}"}
                _session.get(url, headers=headers, timeout=5)
        except Exception:
            pass


def ensure_connection_keeper():
    """Starts the background keep-alive daemon thread if not already running."""
    global _keeper_thread
    if os.environ.get('DISABLE_META_KEEPER') == '1' or not provider_pool.get_primary_provider():
        return
    if _keeper_thread is None or not _keeper_thread.is_alive():
        with _keeper_lock:
            if _keeper_thread is None or not _keeper_thread.is_alive():
                _keeper_thread = threading.Thread(
                    target=_keep_alive_loop,
                    name="meta_socket_keeper",
                    daemon=True
                )
                _keeper_thread.start()


try:
    ensure_connection_keeper()
except Exception:
    pass



# ===========================================================================
# BACKWARD COMPATIBILITY ACCESSORS
# ===========================================================================

def get_access_token():
    p = provider_pool.get_primary_provider()
    return p.access_token if p else (os.environ.get('META_WA_ACCESS_TOKEN') or DEFAULT_ACCESS_TOKEN).strip()

def get_phone_number_id():
    p = provider_pool.get_primary_provider()
    return p.phone_number_id if p else (os.environ.get('META_WA_PHONE_NUMBER_ID') or DEFAULT_PHONE_NUMBER_ID).strip()

def get_waba_id():
    p = provider_pool.get_primary_provider()
    return p.waba_id if p else (os.environ.get('META_WA_WABA_ID') or DEFAULT_WABA_ID).strip()

def get_template_name():
    p = provider_pool.get_primary_provider()
    return p.template_name if p else (os.environ.get('META_WA_TEMPLATE_NAME') or DEFAULT_TEMPLATE_NAME).strip()

def get_template_lang():
    p = provider_pool.get_primary_provider()
    return p.template_lang if p else (os.environ.get('META_WA_TEMPLATE_LANG') or DEFAULT_TEMPLATE_LANG).strip()

def get_api_version():
    p = provider_pool.get_primary_provider()
    return p.api_version if p else (os.environ.get('META_WA_API_VERSION') or DEFAULT_API_VERSION).strip()

def get_sender_display():
    p = provider_pool.get_primary_provider()
    return p.sender_display if p else (os.environ.get('META_WA_SENDER_DISPLAY') or DEFAULT_SENDER_DISPLAY).strip()

def get_aoc_api_key():
    return (os.environ.get('AOC_API_KEY') or DEFAULT_AOC_API_KEY).strip()

def get_aoc_sender_number():
    configured = (os.environ.get('AOC_SENDER_NUMBER') or DEFAULT_AOC_SENDER or '').strip()
    digits = re.sub(r'\D', '', configured)
    if digits in BANNED_SENDER_NUMBERS:
        digits = ''
    if digits:
        return '91' + digits if len(digits) == 10 else digits
    key = get_aoc_api_key()
    if key == 'jClTj0bl42yKbjFMNZG2Pel7VW5ULM':
        return '917300179679'
    return '917357598929'

def get_primary_gateway():
    return (os.environ.get('PRIMARY_WA_GATEWAY') or PRIMARY_WA_GATEWAY or 'auto').lower().strip()

META_WA_PHONE_NUMBER_ID = get_phone_number_id()
META_WA_WABA_ID = get_waba_id()
META_WA_ACCESS_TOKEN = get_access_token()
META_WA_TEMPLATE_NAME = get_template_name()
META_WA_TEMPLATE_LANG = get_template_lang()
META_WA_API_VERSION = get_api_version()
META_WA_SENDER_DISPLAY = get_sender_display()
AOC_API_KEY = get_aoc_api_key()
AOC_WA_API_URL = 'https://api.aoc-portal.com/v1/whatsapp'


# ===========================================================================
# MULTI-LINE AOC / GREENTICK PROVIDER POOL WITH AUTOMATED FAILOVER
# ===========================================================================

class AOCLine:
    def __init__(self, line_id: str, from_number: str, template_name: str, display_name: str = "", priority: int = 1):
        self.id = line_id
        self.from_number = str(from_number).strip().lstrip('+')
        self.template_name = str(template_name).strip()
        self.display_name = str(display_name).strip()
        self.priority = int(priority)
        self.cooldown_until = 0.0
        self.total_sent = 0
        self.total_failed = 0
        self.last_error = ""

    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def record_success(self):
        self.total_sent += 1
        self.cooldown_until = 0.0

    def record_failure(self, err_msg: str, cooldown_seconds: int = 180):
        self.total_failed += 1
        self.last_error = err_msg
        self.cooldown_until = time.time() + cooldown_seconds
        print(f"[AOC_LINE_COOLDOWN] Line '{self.from_number}' ({self.display_name}) cooling down for {cooldown_seconds}s. Reason: {err_msg}", flush=True)


class AOCLinePool:
    def __init__(self):
        self.lock = threading.Lock()
        self.lines: List[AOCLine] = []
        self._init_defaults()

    def _init_defaults(self):
        configured_primary = os.environ.get('AOC_SENDER_NUMBER', '').strip().lstrip('+')
        configured_tpl = os.environ.get('AOC_TEMPLATE_NAME', '').strip()

        known_templates = {
            "916376409037": "info__update",
            "917340693636": "info_update_new_spech",
            "917727983636": "info_update_new_01",
            "919351147539": "info_update_short",
            "917357598929": "info_update_vid3303",
            "918905123636": "info_update_parchi_new",
        }

        default_configs = [
            ("line_p2", "919351147539", "info_update_short", "Digital Parchar 2", 0),
            ("line_bjp", "917340693636", "info_update_new_spech", "भारतीय जनता पार्टी", 1),
            ("line_primary", "916376409037", "info__update", "Digital Parchi Primary", 2),
        ]

        self.lines.clear()
        if configured_primary and configured_primary not in BANNED_SENDER_NUMBERS:
            resolved_tpl = configured_tpl or known_templates.get(configured_primary, "info_update_new_01")
            self.lines.append(AOCLine("custom_primary", configured_primary, resolved_tpl, "Custom Primary", priority=0))

        for lid, num, tpl, name, prio in default_configs:
            if num not in BANNED_SENDER_NUMBERS and not any(l.from_number == num for l in self.lines):
                self.lines.append(AOCLine(lid, num, tpl, name, priority=prio))

    def get_available_lines(self) -> List[AOCLine]:
        with self.lock:
            avail = [l for l in self.lines if l.is_available()]
            if not avail:
                for l in self.lines:
                    l.cooldown_until = 0.0
                avail = list(self.lines)
            return sorted(avail, key=lambda x: x.priority)


aoc_line_pool = AOCLinePool()

def get_auth_headers():
    return {
        "Authorization": f"Bearer {get_access_token()}"
    }


# ---------------------------------------------------------------------------
# BILLING GATE
# ---------------------------------------------------------------------------

def _assert_billable(credit):
    """Claims one credit immediately before a billable API call."""
    from electionapp.whatsapp_quota import assert_billable
    assert_billable(credit)

def _refund(credit):
    """Returns the credit after a confirmed provider failure (Meta does not bill those)."""
    from electionapp.whatsapp_quota import refund_credit
    refund_credit(credit)


# ---------------------------------------------------------------------------
# PHONE & SANITIZATION UTILITIES
# ---------------------------------------------------------------------------

def extract_all_mobile_numbers(raw_phone):
    """
    Extracts all valid 10-digit Indian mobile numbers from a raw phone string,
    handling comma/slash-separated multi-numbers, embedded spaces, and discarding landlines.
    Returns a list of 10-digit mobile number strings without 91 prefix.
    """
    if not raw_phone:
        return []
    chunks = re.split(r'[,;/|]+', str(raw_phone))
    mobiles = []
    seen = set()
    for chunk in chunks:
        cleaned_chunk = re.sub(r'[+\-\(\)]', '', chunk).strip()
        joined_digits = re.sub(r'\D', '', cleaned_chunk)
        candidates = []
        if len(joined_digits) in [10, 11, 12]:
            candidates.append(joined_digits)
        else:
            candidates.extend(cleaned_chunk.split())

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

def clean_phone_number(raw_phone):
    """Sanitizes raw phone into clean E.164 without '+' (e.g. '919414127202')."""
    mobiles = extract_all_mobile_numbers(raw_phone)
    if mobiles:
        return '91' + mobiles[0]
    digits = re.sub(r'\D', '', str(raw_phone or ''))
    if digits.startswith('0') and len(digits) == 11:
        digits = digits[1:]
    if digits.startswith('91') and len(digits) == 12:
        return digits
    if len(digits) == 10:
        return '91' + digits
    return digits


# ---------------------------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------------------------

def check_health(provider_id: Optional[str] = None):
    """
    Queries health status for the active WhatsApp channel (AOC GreenTick or Meta Graph API).
    """
    if get_primary_gateway() == 'aoc' or not provider_pool.get_available_providers():
        aoc_key = get_aoc_api_key()
        if aoc_key:
            sender = get_aoc_sender_number()
            disp = f"+{sender}" if not sender.startswith('+') else sender
            return {
                'connected': True,
                'status': 'online',
                'provider_id': 'aoc_greentick',
                'phone_id': sender,
                'display_number': disp,
                'verified_name': 'Digital Prachar Wala (GreenTick AOC)',
                'quality_rating': 'GREEN',
                'messaging_limit': 'UNLIMITED',
                'verification_status': 'VERIFIED',
                'provider': 'aoc_greentick'
            }

    provider = provider_pool.get_provider(provider_id) if provider_id else provider_pool.get_primary_provider()
    token = provider.access_token if provider else get_access_token()
    phone_id = provider.phone_number_id if provider else get_phone_number_id()

    if not token or not phone_id:
        return {
            'connected': False,
            'status': 'not_configured',
            'error': 'Meta WhatsApp credentials missing'
        }

    api_ver = provider.api_version if provider else get_api_version()
    url = f"https://graph.facebook.com/{api_ver}/{phone_id}?fields=display_phone_number,verified_name,quality_rating,messaging_limit_tier,code_verification_status,health_status"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = _session.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            health = data.get('health_status', {})
            can_send = health.get('can_send_message', 'AVAILABLE')
            
            # Check for account-level ban
            if can_send == 'BLOCKED':
                ban_err = "Account is BANNED/BLOCKED by Meta"
                for entity in health.get('entities', []):
                    for e in entity.get('errors', []):
                        ban_err = f"{e.get('error_description')} (Code {e.get('error_code')})"
                        break
                    if ban_err != "Account is BANNED/BLOCKED by Meta":
                        break
                if provider:
                    provider.record_ban(ban_err)
                return {
                    'connected': False,
                    'status': 'banned',
                    'provider_id': provider.id if provider else 'primary',
                    'phone_id': phone_id,
                    'display_number': data.get('display_phone_number', provider.sender_display if provider else get_sender_display()),
                    'verified_name': data.get('verified_name', ''),
                    'quality_rating': data.get('quality_rating', 'RED'),
                    'messaging_limit': 'BLOCKED',
                    'error': ban_err,
                    'provider': 'meta_cloud_api'
                }

            return {
                'connected': True,
                'status': 'online',
                'provider_id': provider.id if provider else 'primary',
                'phone_id': phone_id,
                'display_number': data.get('display_phone_number', provider.sender_display if provider else get_sender_display()),
                'verified_name': data.get('verified_name', ''),
                'quality_rating': data.get('quality_rating', 'GREEN'),
                'messaging_limit': data.get('messaging_limit_tier', 'TIER_10K'),
                'verification_status': data.get('code_verification_status', 'VERIFIED'),
                'provider': 'meta_cloud_api'
            }
        else:
            err = res.json().get('error', {}).get('message', f"HTTP {res.status_code}")
            return {
                'connected': False,
                'status': 'error',
                'provider_id': provider.id if provider else 'primary',
                'error': err,
                'provider': 'meta_cloud_api'
            }
    except Exception as e:
        return {
            'connected': False,
            'status': 'offline',
            'provider_id': provider.id if provider else 'primary',
            'error': str(e),
            'provider': 'meta_cloud_api'
        }

def check_all_providers_health() -> List[Dict[str, Any]]:
    """Returns Meta Graph API health status for every configured provider in the pool."""
    results = []
    for prov in provider_pool.get_all_providers():
        h = check_health(prov.id)
        results.append({
            'provider': prov.to_dict(),
            'meta_health': h
        })
    return results


# ---------------------------------------------------------------------------
# MEDIA UPLOADER (WITH PROVIDER ISOLATION)
# ---------------------------------------------------------------------------

def upload_media(image_path: str, provider: Optional[WhatsAppProvider] = None,
                 image_bytes: Optional[bytes] = None):
    """
    Uploads a card image to Meta Graph API media endpoint for a specific provider.
    Converts PNG to optimized JPEG with clean filename 'voter_slip.jpg'
    so Android MediaStore and WhatsApp in-app viewer never fail with "Your file could not be accessed".
    Caches media_id by (provider_id, file_hash) so identical cards are never uploaded twice.
    """
    if image_bytes is not None:
        orig_bytes = image_bytes
    else:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")
        with open(image_path, 'rb') as f:
            orig_bytes = f.read()

    file_hash = hashlib.sha256(orig_bytes).hexdigest()
    prov_key = provider.id if provider else "primary"
    scoped_cache_key = f"{prov_key}:{file_hash}"

    # Return cached media ID if available for this specific provider
    if scoped_cache_key in _MEDIA_CACHE:
        return _MEDIA_CACHE[scoped_cache_key]
    if file_hash in _MEDIA_CACHE and prov_key == "primary":
        return _MEDIA_CACHE[file_hash]

    # Already JPEG (the renderer now captures JPEG directly from Chrome): upload
    # as-is. Re-decoding and re-encoding here was a second lossy generation and
    # ~25ms per call -- and it ran once PER PROVIDER in the failover loop, since
    # _MEDIA_CACHE is keyed by provider.
    if orig_bytes[:3] == _JPEG_MAGIC:
        upload_bytes = orig_bytes
        upload_filename = 'voter_slip.jpg'
        mime_type = 'image/jpeg'
    else:
        # Convert to high-quality baseline JPEG for 100% WhatsApp mobile app viewer compatibility
        try:
            from PIL import Image
            import io as _io
            with Image.open(_io.BytesIO(orig_bytes)) as im:
                rgb_im = im.convert('RGB')
                buf = _io.BytesIO()
                rgb_im.save(buf, format='JPEG', quality=95, optimize=True)
                upload_bytes = buf.getvalue()
                upload_filename = 'voter_slip.jpg'
                mime_type = 'image/jpeg'
        except Exception:
            upload_bytes = orig_bytes
            upload_filename = 'voter_slip.png'
            mime_type = 'image/png'

    target_prov = provider or provider_pool.get_primary_provider()
    phone_id = target_prov.phone_number_id if target_prov else get_phone_number_id()
    api_ver = target_prov.api_version if target_prov else get_api_version()
    token = target_prov.access_token if target_prov else get_access_token()

    url = f"https://graph.facebook.com/{api_ver}/{phone_id}/media"
    files = {
        'file': (upload_filename, upload_bytes, mime_type)
    }
    data = {
        'messaging_product': 'whatsapp',
        'type': mime_type
    }
    headers = {"Authorization": f"Bearer {token}"}

    res = _session.post(url, headers=headers, files=files, data=data, timeout=25)
    if res.status_code == 200:
        media_id = res.json().get('id')
        if media_id:
            _MEDIA_CACHE[scoped_cache_key] = media_id
            if prov_key == "primary":
                _MEDIA_CACHE[file_hash] = media_id
            print(f"[META_MEDIA] Uploaded {upload_filename} ({len(upload_bytes)} bytes) for {os.path.basename(image_path or 'memory')} on [{prov_key}] -> media_id={media_id}", flush=True)
            return media_id
        raise Exception("Meta Media API returned 200 but missing id")
    else:
        err_data = res.json().get('error', {})
        err_msg = err_data.get('message', res.text)
        print(f"[META_MEDIA_FAIL] Upload failed on [{prov_key}] for {os.path.basename(image_path)}: {err_msg}", flush=True)
        raise Exception(f"Media upload failed: {err_msg}")


def _notify_aoc_dashboard(phone, template_name, template_lang, media_id=None, phone_number_id=None):
    """
    Fire-and-forget: notify AOC portal (GreenTick dashboard) that a WhatsApp
    message was sent, so their sent-count updates correctly.
    """
    aoc_key = get_aoc_api_key()
    if not aoc_key:
        return
    try:
        payload = {
            "to": phone,
            "template_name": template_name,
            "language_code": template_lang,
            "phone_number_id": phone_number_id or get_phone_number_id(),
            "messaging_product": "whatsapp"
        }
        if media_id:
            payload["header_media_id"] = media_id
        headers = {
            "apikey": aoc_key,
            "Content-Type": "application/json"
        }
        _session.post(AOC_WA_API_URL, json=payload, headers=headers, timeout=8)
    except Exception:
        pass


def _campaign_candidate():
    """Candidate label for the WhatsApp template, from campaign_config.json."""
    try:
        cfg = load_campaign_config()
        cand = cfg.get('candidate', {}).get('name_hi', '')
        party = cfg.get('party', {}).get('name_hi', '')
        if cand and party:
            return f"{cand} ({party})"
        return cand or party or "चुनाव प्रबंधन समिति"
    except Exception:
        return "चुनाव प्रबंधन समिति"


def sanitize_template_param(val, max_length=1000, default="-"):
    """
    Sanitizes string to strictly comply with Meta WhatsApp Cloud API template parameter rules:
    - No newlines, carriage returns, or tabs.
    - No multiple consecutive spaces.
    - Trim leading/trailing whitespace.
    - Never empty.
    """
    if val is None:
        return default
    s = str(val)
    s = re.sub(r'[\r\n\t]+', ' ', s)
    s = re.sub(r'\s{2,}', ' ', s).strip()
    if not s:
        return default
    return s[:max_length]


# ===========================================================================
# DISPATCH WITH AUTOMATIC MULTI-PROVIDER CASCADE FAILOVER
# ===========================================================================


def _format_aoc_params(tpl_name: str, p1: str, p2: str = "", p3: str = "", p4: str = "", p5: str = "") -> List[str]:
    """
    Dynamically maps parameters to template schema:
    - 3-parameter templates (e.g. info__update: Hello {{1}}, {{2}} Regards, {{3}} Thank):
        {{1}}: Recipient / voter name and ward details (p1)
        {{2}}: Body content: Polling booth, slip notice & campaign message (p2 + p3 + p4)
        {{3}}: Signature / candidate name (p5)
    - 5-parameter templates (e.g. info_update_new_spech, info_update_new_01):
        [p1, p2, p3, p4, p5]
    """
    tpl = (tpl_name or "").strip()
    if tpl == "info__update" or "info__update" in tpl:
        combined_middle = "\n".join(str(p).strip() for p in [p2, p3, p4] if p and str(p).strip())
        sig = str(p5).strip() if p5 and str(p5).strip() else "— चुनाव प्रबंधन समिति"
        return [str(p1 or "").strip(), combined_middle, sig]
    return [str(p1 or ""), str(p2 or ""), str(p3 or ""), str(p4 or ""), str(p5 or "")]


def _dispatch_aoc_slip(
    clean_phone: str,
    param_1: str,
    param_2: str,
    param_3: str,
    param_4: str,
    param_5: str,
    image_path: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    template_name: Optional[str] = None
):
    aoc_key = get_aoc_api_key()
    if not aoc_key:
        return False, {}, "AOC API key not configured"

    clean_phone = str(clean_phone).strip().lstrip('+')
    if len(clean_phone) == 10 and not clean_phone.startswith('91'):
        clean_phone = f"91{clean_phone}"

    card_filename = os.path.basename(image_path) if image_path else None
    cards_dir = os.path.join(os.path.dirname(__file__), 'generated_cards')
    os.makedirs(cards_dir, exist_ok=True)

    if not card_filename and image_bytes:
        card_filename = f"slip_{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
        with open(os.path.join(cards_dir, card_filename), 'wb') as f:
            f.write(image_bytes)
    elif image_path and os.path.exists(image_path):
        target_path = os.path.join(cards_dir, os.path.basename(image_path))
        if os.path.abspath(image_path) != os.path.abspath(target_path) and not os.path.exists(target_path):
            import shutil
            try:
                shutil.copy2(image_path, target_path)
            except Exception:
                pass

    base_url = os.environ.get('APP_BASE_URL', 'https://smartbooth.vedmap.com').rstrip('/')
    card_public_url = f"{base_url}/static/cards/{card_filename}" if card_filename else None
    if not card_public_url:
        card_public_url = f"{base_url}/static/icons/icon-512.png"

    caption_lines = [
        "नमस्कार,",
        "",
        param_1,
        param_2,
        param_3,
        param_4,
        param_5
    ]
    caption_text = "\n".join(str(l) for l in caption_lines if l is not None)

    lines = aoc_line_pool.get_available_lines()
    last_err = "No AOC lines available"
    last_res = {}

    for line in lines:
        sender_num = line.from_number
        tpl_name = line.template_name or template_name or DEFAULT_AOC_TEMPLATE_NAME or 'info_update_new_01'

        if card_public_url and tpl_name:
            gt_payload = {
                "from": sender_num,
                "to": clean_phone,
                "type": "template",
                "campaignName": "voter-slip-campaign",
                "templateName": tpl_name,
                "components": {
                    "header": {
                        "type": "image",
                        "image": {
                            "link": card_public_url
                        }
                    },
                    "body": {
                        "params": _format_aoc_params(tpl_name, param_1, param_2, param_3, param_4, param_5)
                    }
                }
            }
        elif card_public_url:
            gt_payload = {
                "from": sender_num,
                "to": clean_phone,
                "type": "image",
                "image": {
                    "link": card_public_url,
                    "caption": caption_text
                }
            }
        else:
            gt_payload = {
                "from": sender_num,
                "to": clean_phone,
                "type": "text",
                "text": {"body": caption_text}
            }

        try:
            gt_headers = {"apikey": aoc_key, "Content-Type": "application/json; charset=utf-8"}
            gt_bytes = json.dumps(gt_payload, ensure_ascii=False).encode('utf-8')
            res = _session.post(AOC_WA_API_URL, data=gt_bytes, headers=gt_headers, timeout=20)
            res_json = res.json() if res.status_code == 200 else {}
            if res.status_code == 200 and not res_json.get('error'):
                msg_id = res_json.get('id') or (res_json.get('data', [{}])[0].get('messageId') if res_json.get('data') else None)
                line.record_success()
                print(f"[AOC_WA_SUCCESS] to {clean_phone} via {sender_num} ({tpl_name}) -> msg_id={msg_id}", flush=True)
                return True, {
                    'success': True,
                    'message_id': msg_id,
                    'phone': clean_phone,
                    'status': 'sent',
                    'provider': 'aoc_greentick', 'sender': sender_num
                }, None
            else:
                err_data = res_json.get('message') or res.text
                line.record_failure(f"HTTP {res.status_code}: {err_data}")
                last_err = f"Line {sender_num} ({res.status_code}): {err_data}"
                last_res = res_json
                print(f"[AOC_WA_FAIL] Line {sender_num} failed: {err_data}. Failing over to next line...", flush=True)
                continue
        except Exception as aoc_ex:
            line.record_failure(str(aoc_ex))
            last_err = f"Line {sender_num} Exception: {aoc_ex}"
            last_res = {}
            print(f"[AOC_WA_ERR] Line {sender_num} exception: {aoc_ex}. Failing over to next line...", flush=True)
            continue

    return False, last_res, last_err


def _dispatch_aoc_credentials(clean_phone, p1, p2, p3, p4, p5, active_header_url=None):
    aoc_key = get_aoc_api_key()
    if not aoc_key:
        return False, {}, "AOC API key not configured"

    clean_phone = str(clean_phone).strip().lstrip('+')
    if len(clean_phone) == 10 and not clean_phone.startswith('91'):
        clean_phone = f"91{clean_phone}"

    header_img = active_header_url or "https://smartbooth.vedmap.com/static/icons/icon-512.png"
    lines = aoc_line_pool.get_available_lines()
    last_err = "No AOC lines available"
    last_res = {}

    for line in lines:
        sender_num = line.from_number
        tpl_name = line.template_name or DEFAULT_AOC_TEMPLATE_NAME or "info__update"
        gt_payload = {
            "from": sender_num,
            "to": clean_phone,
            "type": "template",
            "campaignName": "credentials-dispatch",
            "templateName": tpl_name,
            "components": {
                "header": {
                    "type": "image",
                    "image": {"link": header_img}
                },
                "body": {
                    "params": _format_aoc_params(tpl_name, p1, p2, p3, p4, p5)
                }
            }
        }
        try:
            gt_headers = {"apikey": aoc_key, "Content-Type": "application/json; charset=utf-8"}
            gt_bytes = json.dumps(gt_payload, ensure_ascii=False).encode('utf-8')
            res = _session.post(AOC_WA_API_URL, data=gt_bytes, headers=gt_headers, timeout=15)
            res_json = res.json() if res.status_code == 200 else {}
            if res.status_code == 200 and not res_json.get('error'):
                msg_id = res_json.get('id') or (res_json.get('data', [{}])[0].get('messageId') if res_json.get('data') else None)
                line.record_success()
                print(f"[AOC_CREDENTIALS_SUCCESS] to {clean_phone} via {sender_num} ({tpl_name}) -> msg_id={msg_id}", flush=True)
                return True, {
                    'success': True,
                    'message_id': msg_id,
                    'phone': clean_phone,
                    'status': 'sent',
                    'provider': 'aoc_greentick', 'sender': sender_num
                }, None
            else:
                err_data = res_json.get('message') or res.text
                line.record_failure(f"HTTP {res.status_code}: {err_data}")
                last_err = f"Line {sender_num}: {err_data}"
                last_res = res_json
                print(f"[AOC_CREDENTIALS_FAIL] Line {sender_num} failed: {err_data}. Failing over...", flush=True)
                continue
        except Exception as e:
            line.record_failure(str(e))
            last_err = f"Line {sender_num} Exception: {e}"
            continue

    return False, last_res, last_err


def _dispatch_aoc_magic_link(clean_phone, p1, p2, p3, p4, p5, active_header_url=None):
    aoc_key = get_aoc_api_key()
    if not aoc_key:
        return False, {}, "AOC API key not configured"

    clean_phone = str(clean_phone).strip().lstrip('+')
    if len(clean_phone) == 10 and not clean_phone.startswith('91'):
        clean_phone = f"91{clean_phone}"

    header_img = active_header_url or "https://smartbooth.vedmap.com/static/icons/icon-512.png"
    lines = aoc_line_pool.get_available_lines()
    last_err = "No AOC lines available"
    last_res = {}

    for line in lines:
        sender_num = line.from_number
        tpl_name = line.template_name or DEFAULT_AOC_TEMPLATE_NAME or "info__update"
        gt_payload = {
            "from": sender_num,
            "to": clean_phone,
            "type": "template",
            "campaignName": "karyakarta-link-dispatch",
            "templateName": tpl_name,
            "components": {
                "header": {
                    "type": "image",
                    "image": {"link": header_img}
                },
                "body": {
                    "params": _format_aoc_params(tpl_name, p1, p2, p3, p4, p5)
                }
            }
        }
        try:
            gt_headers = {"apikey": aoc_key, "Content-Type": "application/json; charset=utf-8"}
            gt_bytes = json.dumps(gt_payload, ensure_ascii=False).encode('utf-8')
            res = _session.post(AOC_WA_API_URL, data=gt_bytes, headers=gt_headers, timeout=15)
            res_json = res.json() if res.status_code == 200 else {}
            if res.status_code == 200 and not res_json.get('error'):
                msg_id = res_json.get('id') or (res_json.get('data', [{}])[0].get('messageId') if res_json.get('data') else None)
                line.record_success()
                print(f"[AOC_MAGIC_LINK_SUCCESS] to {clean_phone} via {sender_num} ({tpl_name}) -> msg_id={msg_id}", flush=True)
                return True, {
                    'success': True,
                    'message_id': msg_id,
                    'phone': clean_phone,
                    'status': 'sent',
                    'provider': 'aoc_greentick', 'sender': sender_num
                }, None
            else:
                err_data = res_json.get('message') or res.text
                line.record_failure(f"HTTP {res.status_code}: {err_data}")
                last_err = f"Line {sender_num}: {err_data}"
                last_res = res_json
                print(f"[AOC_MAGIC_LINK_FAIL] Line {sender_num} failed: {err_data}. Failing over...", flush=True)
                continue
        except Exception as e:
            line.record_failure(str(e))
            last_err = f"Line {sender_num} Exception: {e}"
            continue

    return False, last_res, last_err

def send_voter_slip_card(
    phone,
    voter_name,
    part_no,
    sl_no,
    booth_name,
    image_path,
    candidate_name=None,
    campaign_message=None,
    ward_no=None,
    slogan=None,
    image_bytes=None,
    tenant_slug=None,
    campaign_cfg=None,
    *,
    credit,
    prewarmed_media_ids=None
):
    """
    Sends an official Meta WhatsApp template message with HD Voter Slip Card attached.
    Implements AUTOMATIC CASCADE FAILOVER across all configured WhatsApp providers:
    - Attempts Primary Provider.
    - If Rate-Limited (429/131048), Token-Expired, or Server Error, silently fails over to Backup Provider(s).
    - Preserves exact 1-credit quota deduction on success, refunds on total failure.
    - Skips failover on recipient errors (e.g. 131026: User Not on WhatsApp).
    """
    clean_phone = clean_phone_number(phone)
    if len(clean_phone) < 10:
        return False, {}, "Invalid phone number"

    # Billing gate: claim exactly one credit before attempting dispatches
    _assert_billable(credit)

    # Resolve active providers from pool (sorted by priority)
    providers = provider_pool.get_available_providers()
    if not providers and not get_aoc_api_key():
        print("[FAILOVER] No available WhatsApp providers in pool and AOC key missing!", flush=True)
        _refund(credit)
        return False, {}, "No WhatsApp API providers available (all in cooldown or disabled)"

    # Format template content details once
    v_name = (voter_name or 'मतदाता').strip()
    v_part = str(part_no or '1').strip()
    v_sl = str(sl_no or '-').strip()
    v_booth = (booth_name or 'राजकीय मतदान केंद्र').strip()
    v_candidate = (candidate_name or _campaign_candidate()).strip()

    c_msg = (campaign_message or '').strip()
    if not c_msg:
        try:
            c_msg = load_campaign_config().get('campaign_message', 'सादर प्रणाम! मतदान हमारा अधिकार एवं कर्तव्य है। अवश्य मतदान करें।')
        except Exception:
            c_msg = 'सादर प्रणाम! मतदान हमारा अधिकार एवं कर्तव्य है। अवश्य मतदान करें।'

    # Clean up duplicate trailing slip notice from campaign message if present
    c_msg = re.sub(r'[\s।,.]*आपकी डिजिटल मतदाता (?:सूचना )?पर्ची (?:नीचे )?संलग्न है[।.]*', '', c_msg).strip()
    if c_msg and not c_msg.endswith(('।', '.', '!')):
        c_msg += '।'

    cfg = None
    try:
        cfg = load_campaign_config()
        if slogan is None:
            slogan = cfg.get('party', {}).get('slogan') or cfg.get('slogan') or ''
    except Exception:
        slogan = slogan or ''

    elec_type = str(cfg.get('election', {}).get('election_type') or '').upper().strip() if cfg else ''
    constituency_ward = str(cfg.get('election', {}).get('ward_no') or '').strip() if cfg else ''

    v_ward = str(ward_no).strip() if ward_no and str(ward_no).strip() not in ('0', '', 'None') else None
    if elec_type == 'ZILA_PARISHAD' and constituency_ward:
        if v_ward:
            param_1 = sanitize_template_param(f"{v_name} (जि.प. वार्ड {constituency_ward} · पं. वार्ड {v_ward} · क्रम #{v_sl})", default="मतदाता")
        else:
            param_1 = sanitize_template_param(f"{v_name} (जिला परिषद वार्ड {constituency_ward} · क्रम #{v_sl})", default="मतदाता")
    elif elec_type == 'PANCHAYAT_SAMITI' and constituency_ward:
        if v_ward:
            param_1 = sanitize_template_param(f"{v_name} (पं.स. वार्ड {constituency_ward} · पं. वार्ड {v_ward} · क्रम #{v_sl})", default="मतदाता")
        else:
            param_1 = sanitize_template_param(f"{v_name} (पंचायत समिति वार्ड {constituency_ward} · क्रम #{v_sl})", default="मतदाता")
    elif v_ward:
        param_1 = sanitize_template_param(f"{v_name} (वार्ड {v_ward} · क्रम #{v_sl})", default="मतदाता")
    else:
        param_1 = sanitize_template_param(f"{v_name} (भाग {v_part} · क्रम #{v_sl})", default="मतदाता")

    v_booth_clean = re.sub(r'[,，\s]+$', '', v_booth)
    param_2 = sanitize_template_param(f"मतदान केंद्र: {v_booth_clean}", default="राजकीय मतदान केंद्र")

    slogan_clean = str(slogan or '').strip()
    slogan_clean = re.sub(r'[🗳️«»🚩🏛️✅❌]+', '', slogan_clean).strip()
    if slogan_clean and re.search(r'[\w\u0900-\u097F]', slogan_clean):
        param_3 = sanitize_template_param(f"{slogan_clean} — आपकी मतदाता पर्ची संलग्न है।", default="आपकी डिजिटल मतदाता सूचना पर्ची संलग्न है।")
    else:
        param_3 = sanitize_template_param("आपकी डिजिटल मतदाता सूचना पर्ची (Voter Slip) संलग्न है।", default="आपकी डिजिटल मतदाता सूचना पर्ची संलग्न है।")

    # Substitute voter name or other placeholders in campaign message
    if '{voter_name}' in c_msg:
        c_msg = c_msg.replace('{voter_name}', v_name)
    c_msg = re.sub(r'\{[^\}]*\}', '', c_msg).strip()
    param_4 = sanitize_template_param(c_msg, default="सादर प्रणाम! मतदान हमारा अधिकार एवं कर्तव्य है। अवश्य मतदान करें।")

    cand_sig = v_candidate.strip() if v_candidate else ""
    if not cand_sig or not re.search(r'[\w\u0900-\u097F]', cand_sig):
        cand_sig = "चुनाव प्रबंधन समिति"
    if cand_sig and not cand_sig.startswith("—") and not cand_sig.startswith("-"):
        cand_sig = f"— {cand_sig}"
    param_5 = sanitize_template_param(cand_sig, default="— चुनाव प्रबंधन समिति")

    last_error_msg = None
    last_res_data = {}

    use_aoc_first = (get_primary_gateway() == 'aoc') or (not providers)
    if use_aoc_first and get_aoc_api_key():
        ok, res, err = _dispatch_aoc_slip(
            clean_phone=clean_phone,
            param_1=param_1,
            param_2=param_2,
            param_3=param_3,
            param_4=param_4,
            param_5=param_5,
            image_path=image_path,
            image_bytes=image_bytes,
            template_name=DEFAULT_AOC_TEMPLATE_NAME,
            )
        if ok:
            return True, res, None
        last_error_msg = err
        last_res_data = res
        if not providers:
            _refund(credit)
            return False, last_res_data, last_error_msg

    for provider in providers:
        try:
            # 1. Upload card image for this specific provider (or retrieve from prewarmed cache)
            try:
                media_id = None
                if prewarmed_media_ids:
                    if isinstance(prewarmed_media_ids, dict):
                        media_id = prewarmed_media_ids.get(provider.id) or prewarmed_media_ids.get("primary")
                    elif isinstance(prewarmed_media_ids, str):
                        media_id = prewarmed_media_ids
                if not media_id:
                    media_id = upload_media(image_path, provider=provider, image_bytes=image_bytes)
            except Exception as up_err:
                err_str = f"Media upload failed on [{provider.id}]: {up_err}"
                print(f"[FAILOVER] {err_str}. Trying next backup provider...", flush=True)
                provider.record_failure(err_str, cooldown_seconds=300)
                last_error_msg = err_str
                continue

            # 2. Build template payload using this provider's approved template name and language
            components = [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "image",
                            "image": {
                                "id": media_id
                            }
                        }
                    ]
                },
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": param_1},
                        {"type": "text", "text": param_2},
                        {"type": "text", "text": param_3},
                        {"type": "text", "text": param_4},
                        {"type": "text", "text": param_5}
                    ]
                }
            ]

            meta_payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": clean_phone,
                "type": "template",
                "template": {
                    "name": provider.template_name,
                    "language": {
                        "code": provider.template_lang
                    },
                    "components": components
                }
            }

            url = f"https://graph.facebook.com/{provider.api_version}/{provider.phone_number_id}/messages"
            headers = {**provider.get_auth_headers(), "Content-Type": "application/json; charset=utf-8"}
            meta_data_bytes = json.dumps(meta_payload, ensure_ascii=False).encode('utf-8')

            res = _session.post(url, data=meta_data_bytes, headers=headers, timeout=20)
            res_data = res.json()

            if res.status_code == 200 and 'messages' in res_data:
                msg_id = res_data['messages'][0].get('id')
                provider.record_success()
                print(f"[META_WA_SUCCESS] via [{provider.id}] to {clean_phone} -> msg_id={msg_id}", flush=True)

                # Fire-and-forget background notification to GreenTick/AOC dashboard
                if get_aoc_api_key():
                    threading.Thread(
                        target=_notify_aoc_dashboard,
                        args=(clean_phone, provider.template_name, provider.template_lang, media_id, provider.phone_number_id),
                        daemon=True
                    ).start()

                # Message delivered: credit remains claimed
                return True, {
                    'success': True,
                    'message_id': msg_id,
                    'phone': clean_phone,
                    'media_id': media_id,
                    'status': 'sent',
                    'provider': f"meta_cloud_api ({provider.id})"
                }, None

            else:
                err = res_data.get('error', {})
                err_code = err.get('code')
                err_msg = err.get('message', res.text)
                err_details = err.get('error_data', {}).get('details')
                full_msg = f"{err_msg} ({err_details})" if err_details else err_msg

                # Distinct Recipient Error Check:
                # If voter phone is not on WhatsApp (131026) or invalid, do NOT failover to backup APIs!
                if err_code == 131026 or 'not a valid whatsapp user' in str(full_msg).lower():
                    print(f"[META_WA_RECIPIENT_ERR] {clean_phone} is not on WhatsApp: {full_msg}", flush=True)
                    _refund(credit)
                    return False, res_data, f"Meta API Error ({err_code}): {full_msg}"

                # Permanent / Severe Ban / Restriction Check:
                if err_code in META_BAN_RESTRICTION_CODES or any(w in str(full_msg).lower() for w in ['account has been restricted', 'account has been locked', 'temporarily blocked', 'deregistered']):
                    provider.record_ban(f"HTTP {res.status_code} [{err_code}]: {full_msg}")
                    last_error_msg = f"Provider '{provider.id}' BANNED/RESTRICTED: {full_msg}"
                    last_res_data = res_data
                    print(f"[PROVIDER_BAN_FAILOVER] {last_error_msg}. Deactivating and switching to next backup provider...", flush=True)
                    continue

                # Provider-level error: Rate limit, Token expired, WABA quota exceeded, 5xx outages
                provider.record_failure(f"HTTP {res.status_code} [{err_code}]: {full_msg}", cooldown_seconds=600)
                last_error_msg = f"Provider '{provider.id}' failed (HTTP {res.status_code} [{err_code}]): {full_msg}"
                last_res_data = res_data
                print(f"[PROVIDER_FAILOVER] {last_error_msg}. Switching to next backup provider in pool...", flush=True)
                continue

        except Exception as ex:
            err_str = f"Network exception on [{provider.id}]: {ex}"
            provider.record_failure(err_str, cooldown_seconds=300)
            last_error_msg = err_str
            print(f"[PROVIDER_FAILOVER] {err_str}. Switching to next backup provider in pool...", flush=True)
            continue

    # 3. Automatic Backup Failover: GreenTick / AOC Direct Media Gateway
    if get_aoc_api_key():
        ok, res, err = _dispatch_aoc_slip(
            clean_phone=clean_phone,
            param_1=param_1,
            param_2=param_2,
            param_3=param_3,
            param_4=param_4,
            param_5=param_5,
            image_path=image_path,
            image_bytes=image_bytes,
            template_name=DEFAULT_AOC_TEMPLATE_NAME,
            )
        if ok:
            return True, res, None
        last_error_msg = f"{last_error_msg} | AOC Fallback: {err}"
        last_res_data = res

    # All configured providers in the cascade pool failed
    print(f"[META_WA_ALL_FAILED] to {clean_phone}. Last error: {last_error_msg}", flush=True)
    _refund(credit)
    return False, last_res_data, f"All WhatsApp providers failed. Last error: {last_error_msg}"


def send_text_message(phone, text, *, credit):
    """
    Sends direct text message via GreenTick API with Meta Cloud API multi-provider fallback.
    """
    clean_phone = clean_phone_number(phone)

    # Billing gate: claim a credit before anything billable happens.
    _assert_billable(credit)

    # Primary text channel: GreenTick / AOC direct text if available
    aoc_key = get_aoc_api_key()
    if aoc_key:
        try:
            sender_num = get_aoc_sender_number()
            gt_payload = {
                "from": sender_num,
                "to": clean_phone,
                "type": "text",
                "text": {"body": text}
            }
            gt_headers = {"apikey": aoc_key, "Content-Type": "application/json; charset=utf-8"}
            gt_bytes = json.dumps(gt_payload, ensure_ascii=False).encode('utf-8')
            res = _session.post(AOC_WA_API_URL, data=gt_bytes, headers=gt_headers, timeout=15)
            if res.status_code == 200 and not res.json().get('error'):
                return True, res.json(), None
            else:
                err_data = res.json().get('message') or res.text
                print(f"[GREENTICK_TEXT_WARN] Status {res.status_code}: {err_data}, falling back to Meta pool...", flush=True)
        except Exception as e:
            print(f"[GREENTICK_TEXT_ERR] {e}, falling back to Meta pool...", flush=True)

    # Meta Provider Pool Fallback
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "text",
        "text": {"body": text}
    }
    data_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    providers = provider_pool.get_available_providers()
    last_err = "No providers available"
    last_res = {}

    for provider in providers:
        url = f"https://graph.facebook.com/{provider.api_version}/{provider.phone_number_id}/messages"
        headers = {**provider.get_auth_headers(), "Content-Type": "application/json; charset=utf-8"}
        try:
            res = _session.post(url, data=data_bytes, headers=headers, timeout=15)
            if res.status_code == 200 and 'messages' in res.json():
                provider.record_success()
                return True, res.json(), None
            else:
                err_msg = res.json().get('error', {}).get('message', res.text)
                provider.record_failure(f"HTTP {res.status_code}: {err_msg}")
                last_err = err_msg
                last_res = res.json()
                continue
        except Exception as ex:
            provider.record_failure(str(ex))
            last_err = str(ex)
            continue

    _refund(credit)
    return False, last_res, last_err


def send_credentials_template(phone, full_name, username, password, login_url, sender_label=None, *, credit, header_url=None):
    """
    Dispatches login credentials using the official approved Meta template `info_update_image_0101`.
    Guarantees outbound delivery to any recipient without 24-hour customer window restrictions.
    """
    clean_phone = clean_phone_number(phone)
    if len(clean_phone) < 10:
        return False, {}, "Invalid phone number"

    _assert_billable(credit)

    default_header_url = "https://smartbooth.vedmap.com/static/icons/icon-512.png"
    active_header_url = header_url or os.environ.get("META_CREDENTIALS_HEADER_URL") or default_header_url
    p1 = sanitize_template_param(full_name, default="साथी")
    p2 = sanitize_template_param("SmartBooth चुनाव प्रबंधन लॉगिन तैयार है।")
    p3 = sanitize_template_param(f"यूज़रनेम: {username} | पासवर्ड: {password}")
    p4 = sanitize_template_param(f"डायरेक्ट लिंक: {login_url}")
    p5 = sanitize_template_param(sender_label or "SmartBooth Admin")

    providers = provider_pool.get_available_providers()
    use_aoc_first = (get_primary_gateway() == 'aoc') or (not providers)
    if use_aoc_first and get_aoc_api_key():
        ok, res, err = _dispatch_aoc_credentials(
            clean_phone=clean_phone,
            p1=p1, p2=p2, p3=p3, p4=p4, p5=p5,
            active_header_url=active_header_url,
            )
        if ok:
            return True, res, None
        last_err = err
        last_res = res
        if not providers:
            _refund(credit)
            return False, last_res, last_err
    elif not providers:
        _refund(credit)
        return False, {}, "No WhatsApp API providers available" 

    last_err = "No providers available"
    last_res = {}

    for provider in providers:
        template_name = getattr(provider, 'template_name', None) or DEFAULT_TEMPLATE_NAME or "info_update_new_01"
        template_lang = getattr(provider, 'template_lang', None) or DEFAULT_TEMPLATE_LANG or "en"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": template_lang},
                "components": [
                    {
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"link": active_header_url}}]
                    },
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": p1},
                            {"type": "text", "text": p2},
                            {"type": "text", "text": p3},
                            {"type": "text", "text": p4},
                            {"type": "text", "text": p5}
                        ]
                    }
                ]
            }
        }
        data_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')

        url = f"https://graph.facebook.com/{provider.api_version}/{provider.phone_number_id}/messages"
        headers = {**provider.get_auth_headers(), "Content-Type": "application/json; charset=utf-8"}
        try:
            res = _session.post(url, data=data_bytes, headers=headers, timeout=15)
            if res.status_code == 200 and 'messages' in res.json():
                provider.record_success()
                print(f"[META_CREDENTIALS_SUCCESS] to {clean_phone} via [{provider.id}] ({template_name}) -> {res.json()}", flush=True)
                return True, res.json(), None
            else:
                err_data = res.json().get('error', {})
                err_msg = err_data.get('message', res.text)
                provider.record_failure(f"HTTP {res.status_code}: {err_msg}")
                last_err = err_msg
                last_res = res.json()
        except Exception as ex:
            err_str = f"Network exception on [{provider.id}] for credentials: {ex}"
            provider.record_failure(err_str)
    # Automatic Backup Failover: GreenTick / AOC Direct Gateway
    if get_aoc_api_key():
        ok, res, err = _dispatch_aoc_credentials(
            clean_phone=clean_phone,
            p1=p1, p2=p2, p3=p3, p4=p4, p5=p5,
            active_header_url=active_header_url,
            )
        if ok:
            return True, res, None
        last_err = f"{last_err} | AOC: {err}"
        last_res = res

    _refund(credit)
    return False, last_res, last_err


def send_karyakarta_magic_link_template(
    phone: str,
    worker_name: str,
    assignment_summary: str,
    magic_url: str,
    sender_label: Optional[str] = None,
    *,
    credit,
    header_url: Optional[str] = None
):
    """
    Dispatches Karyakarta 1-click magic login link using official Meta approved template `info_update_image_0101`.
    Guarantees outbound delivery to any recipient bypassing Meta's 24-hour customer window.
    """
    clean_phone = clean_phone_number(phone)
    if len(clean_phone) < 10:
        return False, {}, "Invalid phone number"

    _assert_billable(credit)

    default_header_url = "https://smartbooth.vedmap.com/static/icons/icon-512.png"
    active_header_url = header_url or os.environ.get("META_CREDENTIALS_HEADER_URL") or default_header_url
    p1 = sanitize_template_param(worker_name, default="कार्यकर्ता साथी")
    p2 = sanitize_template_param("SmartBooth कार्यकर्ता लॉगिन लिंक तैयार है।")
    p3 = sanitize_template_param(assignment_summary, default="आपकी मतदाता दायित्व सूची संलग्न लिंक में सक्रिय है।")
    p4 = sanitize_template_param(f"सीधा लिंक: {magic_url}")
    p5 = sanitize_template_param(sender_label or "— चुनाव प्रबंधन समिति")

    providers = provider_pool.get_available_providers()
    use_aoc_first = (get_primary_gateway() == 'aoc') or (not providers)
    if use_aoc_first and get_aoc_api_key():
        ok, res, err = _dispatch_aoc_magic_link(
            clean_phone=clean_phone,
            p1=p1, p2=p2, p3=p3, p4=p4, p5=p5,
            active_header_url=active_header_url,
            )
        if ok:
            return True, res, None
        last_err = err
        last_res = res
        if not providers:
            _refund(credit)
            return False, last_res, last_err
    elif not providers:
        _refund(credit)
        return False, {}, "No WhatsApp API providers available" 

    last_err = "No providers available"
    last_res = {}

    for provider in providers:
        template_name = getattr(provider, 'template_name', None) or DEFAULT_TEMPLATE_NAME or "info_update_new_01"
        template_lang = getattr(provider, 'template_lang', None) or DEFAULT_TEMPLATE_LANG or "en"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": template_lang},
                "components": [
                    {
                        "type": "header",
                        "parameters": [{"type": "image", "image": {"link": active_header_url}}]
                    },
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": p1},
                            {"type": "text", "text": p2},
                            {"type": "text", "text": p3},
                            {"type": "text", "text": p4},
                            {"type": "text", "text": p5}
                        ]
                    }
                ]
            }
        }
        data_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')

        url = f"https://graph.facebook.com/{provider.api_version}/{provider.phone_number_id}/messages"
        headers = {**provider.get_auth_headers(), "Content-Type": "application/json; charset=utf-8"}
        try:
            res = _session.post(url, data=data_bytes, headers=headers, timeout=15)
            if res.status_code == 200 and 'messages' in res.json():
                provider.record_success()
                print(f"[META_KARYAKARTA_LINK_SUCCESS] to {clean_phone} via [{provider.id}] ({template_name}) -> {res.json()}", flush=True)
                return True, res.json(), None
            else:
                err_data = res.json().get('error', {})
                err_msg = err_data.get('message', res.text)
                provider.record_failure(f"HTTP {res.status_code}: {err_msg}")
                last_err = err_msg
                last_res = res.json()
        except Exception as ex:
            err_str = f"Network exception on [{provider.id}] for karyakarta link: {ex}"
            provider.record_failure(err_str)
    # Automatic Backup Failover: GreenTick / AOC Direct Gateway
    if get_aoc_api_key():
        ok, res, err = _dispatch_aoc_magic_link(
            clean_phone=clean_phone,
            p1=p1, p2=p2, p3=p3, p4=p4, p5=p5,
            active_header_url=active_header_url,
            )
        if ok:
            return True, res, None
        last_err = f"{last_err} | AOC: {err}"
        last_res = res

    _refund(credit)
    return False, last_res, last_err
