"""
card_generator.py
High-Resolution Digital Voter Slip Poster Generator.
Supports Multi-Daemon Chrome CDP, Headless CLI Fallback, and Multi-Tenant Branding.
"""

import os
import re
import time
import json
import base64
import uuid
import math
import hashlib
import tempfile
import asyncio
import random
from collections import OrderedDict
import threading
import subprocess
import urllib.request
from typing import Optional, List, Dict, Any

try:
    import websockets
except ImportError:
    websockets = None

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None
    ImageChops = None

from electionapp.config import PLATFORM_ROOT, load_tenant_config, is_panchayat_campaign
from electionapp.tenant_branding import get_tenant_branding, get_tenant_custom_background, _file_uri

try:
    from electionapp import cdp_session
except Exception:  # never let a persistent-session import break slip rendering
    cdp_session = None
from electionapp.card_templates import (
    format_bilingual,
    build_custom_bg_single_html,
    build_custom_bg_family_html,
    build_standard_single_html,
    build_standard_family_html
)

BASE_DIR = PLATFORM_ROOT

# Default tenant branding fallback for legacy global references
_DEFAULT_BRANDING = get_tenant_branding('ward82')

CARD_BALLOT_NO = _DEFAULT_BRANDING['ballot_no']
CARD_SYMBOL_HI = _DEFAULT_BRANDING['symbol_name_hi']
CARD_PARTY_LINE = _DEFAULT_BRANDING['party_line']
CARD_CANDIDATE = _DEFAULT_BRANDING['candidate_disp']
CARD_PLACE_HI = _DEFAULT_BRANDING['place_hi']
CARD_APPEAL = _DEFAULT_BRANDING['appeal_msg']
CARD_SLOGAN = _DEFAULT_BRANDING['slogan']

BOOTHS = _DEFAULT_BRANDING['booths']

def find_chrome_executable():
    chrome_paths = [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe'),
        '/opt/google/chrome/chrome',
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/snap/bin/chromium'
    ]
    for p in chrome_paths:
        if p and os.path.exists(p):
            return p
    return None

def get_custom_background():
    """
    Checks if a custom user-designed background poster exists.
    Checks Desktop and project directory for voter_card_background.png/.jpg or similar names.
    """
    desktop_dirs = [
        os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop'),
        os.path.join(os.path.expanduser('~'), 'Desktop'),
        BASE_DIR
    ]
    filenames = [
        'voter_card_background.png',
        'voter_card_background.jpg',
        'voter_slip_background.png',
        'voter_slip_background.jpg',
        'voter_slip_design.png',
        'voter_slip_design.jpg',
        'VOTER_SLIP_DESIGN_BACKGROUND.png',
        'VOTER_SLIP_DESIGN_BACKGROUND.jpg',
        'custom_background.png',
        'custom_background.jpg'
    ]
    for d in desktop_dirs:
        for fn in filenames:
            p = os.path.join(d, fn)
            if os.path.exists(p) and os.path.getsize(p) > 2000:
                return p
    return None

# ==========================================
# MULTI-DAEMON CHROME CDP POOL & DEDUPLICATION
# ==========================================
CHROME_CDP_PORTS = [9222, 9223, 9224]
_chrome_port_counter = 0
_chrome_port_lock = threading.Lock()
_PORT_SEMAPHORES = {p: threading.BoundedSemaphore(1) for p in CHROME_CDP_PORTS}
_RENDER_SEMAPHORE = threading.BoundedSemaphore(int(os.environ.get('MAX_CONCURRENT_SLIP_RENDERS', 2)))
_inflight_renders = {}
_inflight_lock = threading.Lock()
_CACHED_CARD_ASSETS = {}

# ---------------------------------------------------------------------------
# Background cleanup: purge generated_cards/ files older than 2 hours.
# Since slips are no longer served from disk cache, old PNGs accumulate
# quickly. This daemon thread runs every 30 minutes to keep disk usage bounded.
# ---------------------------------------------------------------------------
_CARDS_MAX_AGE_SECONDS = 7 * 24 * 3600   # 7 days
_CARDS_CLEANUP_INTERVAL = 30 * 60   # every 30 minutes

def _cleanup_old_generated_cards():
    """Daemon thread: deletes generated_cards/ PNGs older than _CARDS_MAX_AGE_SECONDS."""
    while True:
        try:
            time.sleep(_CARDS_CLEANUP_INTERVAL)
            cards_dir = os.path.join(BASE_DIR, 'generated_cards')
            if not os.path.isdir(cards_dir):
                continue
            cutoff = time.time() - _CARDS_MAX_AGE_SECONDS
            deleted = 0
            for fname in os.listdir(cards_dir):
                if not fname.endswith(('.png', '.jpg', '.jpeg', '.html')):
                    continue
                fpath = os.path.join(cards_dir, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        deleted += 1
                except Exception:
                    pass
            if deleted:
                print(f"[CARD_CLEANUP] Purged {deleted} old slip files from generated_cards/", flush=True)
        except Exception:
            pass

_cleanup_thread = threading.Thread(target=_cleanup_old_generated_cards, daemon=True, name="CardCleanup")
_cleanup_thread.start()

def acquire_chrome_port(timeout=3.5):
    """
    Acquires an idle Chrome daemon port across all Gunicorn threads AND processes.
    Enforces thread-level semaphore (in-process) AND kernel flock (cross-process).
    """
    start = time.time()
    base_idx = (os.getpid() + threading.get_ident()) % len(CHROME_CDP_PORTS)
    ports_order = [CHROME_CDP_PORTS[(base_idx + i) % len(CHROME_CDP_PORTS)] for i in range(len(CHROME_CDP_PORTS))]

    # Skip daemons known to be down. acquire previously always handed back the
    # same first port, so a single dead daemon made EVERY render pay a failed
    # connect on the fast path, then again on the legacy path, then fall to the
    # CLI subprocess -- measured ~4.9s/slip. With the skip it costs one probe
    # per SLIP_DEAD_PORT_TTL window and renders just use a healthy port.
    def _skip(p):
        try:
            return cdp_session is not None and cdp_session._recently_dead(p)
        except Exception:
            return False

    live_order = [p for p in ports_order if not _skip(p)] or ports_order

    while time.time() - start < timeout:
        for port in live_order:
            # 1. Acquire thread-level semaphore in this process
            sem = _PORT_SEMAPHORES.get(port)
            if not sem or not sem.acquire(blocking=False):
                continue

            # 2. Acquire OS-level flock across processes (on Linux)
            if hasattr(os, 'O_CREAT') and os.name != 'nt':
                fd = None
                try:
                    import fcntl
                    lock_file = f"/tmp/chrome_port_{port}.lock"
                    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return port, fd
                except (BlockingIOError, OSError):
                    if fd is not None:
                        try: os.close(fd)
                        except Exception: pass
                    try: sem.release()
                    except ValueError: pass
                    continue
            else:
                # Windows fallback (single process development)
                return port, None

        time.sleep(0.02)
    return None, None

def release_chrome_port(port, lock_handle):
    if lock_handle is not None:
        try:
            import fcntl
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            os.close(lock_handle)
        except Exception:
            pass
    if port in _PORT_SEMAPHORES:
        try:
            _PORT_SEMAPHORES[port].release()
        except ValueError:
            pass

def get_cached_bjp_assets(tenant_config=None):
    """Returns in-memory cached base64 logo and candidate photo to eliminate disk reads."""
    global _CACHED_CARD_ASSETS
    if not _CACHED_CARD_ASSETS:
        _P = (tenant_config or {}).get('party', {})
        _C = (tenant_config or {}).get('candidate', {})
        symbol_fn = (_P.get('symbol_filename') or '').strip()
        cand_fn = (_C.get('photo_filename') or '').strip()
        fallback_fn = (_C.get('fallback_photo_filename') or '').strip()

        lotus_src = ""
        if symbol_fn:
            lotus_file = os.path.join(BASE_DIR, symbol_fn)
            if os.path.exists(lotus_file):
                try:
                    with open(lotus_file, 'rb') as lf:
                        mime = 'image/jpeg' if lotus_file.lower().endswith(('.jpg', '.jpeg')) else 'image/png'
                        lotus_src = f"data:{mime};base64,{base64.b64encode(lf.read()).decode('ascii')}"
                except Exception:
                    pass

        candidate_src = ""
        if cand_fn or fallback_fn:
            cand_png = os.path.join(BASE_DIR, cand_fn) if cand_fn else ''
            cand_jpg = os.path.join(BASE_DIR, fallback_fn) if fallback_fn else ''
            candidate_file = cand_png if (cand_png and os.path.exists(cand_png)) else (cand_jpg if (cand_jpg and os.path.exists(cand_jpg)) else '')
            if candidate_file and os.path.exists(candidate_file):
                cand_mime = 'image/jpeg' if candidate_file.lower().endswith(('.jpg', '.jpeg')) else 'image/png'
                try:
                    with open(candidate_file, 'rb') as cf:
                        candidate_src = f"data:{cand_mime};base64,{base64.b64encode(cf.read()).decode('ascii')}"
                except Exception:
                    pass

        _CACHED_CARD_ASSETS = {
            'lotus_src': lotus_src,
            'candidate_src': candidate_src
        }
    return _CACHED_CARD_ASSETS

# ---------------------------------------------------------------------------
# Custom background source resolution.
#
# This used to re-read the poster and base64-encode it on EVERY render, adding
# several MB to the HTML each time and defeating Chrome's image cache. A
# file:// URL lets Blink cache the decoded bitmap; the data: form is kept as a
# fallback (and via SLIP_ASSET_MODE=data) for environments without file access.
# ---------------------------------------------------------------------------
_CUSTOM_BG_CACHE = {}
_CUSTOM_BG_LOCK = threading.Lock()


def _custom_bg_src(custom_bg):
    """Returns a cached image source for the tenant's custom background poster."""
    if not custom_bg or not os.path.exists(custom_bg):
        return ""
    try:
        key = (custom_bg, os.path.getmtime(custom_bg))
    except OSError:
        return ""

    with _CUSTOM_BG_LOCK:
        hit = _CUSTOM_BG_CACHE.get(key)
    if hit:
        return hit

    src = ""
    if os.environ.get('SLIP_ASSET_MODE', 'file').strip().lower() != 'data':
        src = _file_uri(custom_bg)
    if not src:
        try:
            mime = 'image/png' if custom_bg.lower().endswith('.png') else 'image/jpeg'
            with open(custom_bg, 'rb') as cbf:
                src = f"data:{mime};base64,{base64.b64encode(cbf.read()).decode('ascii')}"
        except Exception:
            return ""

    with _CUSTOM_BG_LOCK:
        if len(_CUSTOM_BG_CACHE) > 32:
            _CUSTOM_BG_CACHE.clear()
        _CUSTOM_BG_CACHE[key] = src
    return src


# ---------------------------------------------------------------------------
# Readiness + exact card height, in ONE awaited round trip.
#
# Replaces a 15-iteration poll that cost ~190ms/slip because it sent id 20 and
# then read a single frame -- which returned the acks for ids 1 and 2 first,
# mismatched, slept, and re-sent. Measured: 9 iterations, ~355ms -> 1, ~165ms.
#
# Height must be measured from the CARD, not the document: all templates set
# `html, body { overflow: hidden }`, which clamps documentElement.scrollHeight
# to the viewport, so it returns win_h (e.g. 950) whenever the card is shorter
# -- the common case -- resurrecting the white band the old PIL crop removed.
# custom_bg templates size `body` to the full 1080x1440 poster deliberately and
# position .data-box absolutely, so they are a fixed 1440.
# ---------------------------------------------------------------------------
_READY_AND_HEIGHT_JS = """(async () => {
  // Wait for parsing to finish FIRST. On the navigate fallback path the
  // document can still be loading when we measure, yielding a part-laid-out
  // card (observed in production: 217px instead of ~677px, caught by the
  // MIN_CARD_HEIGHT guard and retried). setDocumentContent replaces the
  // document synchronously so this is a no-op there; the race bounds it so a
  // document that never fires 'load' cannot hang the render.
  if (document.readyState !== 'complete') {
    await Promise.race([
      new Promise(r => window.addEventListener('load', r, {once: true})),
      new Promise(r => setTimeout(r, 2000)),
    ]);
  }
  const imgs = [...document.images];
  await Promise.all(imgs.map(i => i.complete
    ? (i.decode ? i.decode().catch(() => {}) : null)
    : new Promise(r => { i.onload = i.onerror = r; })));
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  if (document.querySelector('.data-box')) return 1440;
  const card = document.querySelector('.card') || document.body.firstElementChild;
  // Whole CSS px + 1. Chrome snaps captureScreenshot's clip rect to integer CSS
  // pixels, so at deviceScaleFactor 1.5 the only reachable output heights are
  // ceil(n*1.5) -- the legacy PIL autocrop height often falls between two of
  // them. The card's antialiased bottom border also paints a faint row past
  // ceil(bottom), which the old crop kept. The +1 guarantees we are never
  // SHORT (which would truncate the border) at the cost of at most ~2 device
  // px of white edge, which is invisible and matches what the crop varied by.
  return Math.ceil((card || document.body).getBoundingClientRect().bottom) + 1;
})()"""

# Matches the quality meta_whatsapp.upload_media already re-encoded at, so the
# bytes WhatsApp receives are unchanged -- we just stop encoding three times.
CARD_JPEG_QUALITY = int(os.environ.get('SLIP_JPEG_QUALITY', '88'))

# Hands the just-rendered bytes to upload_media without a disk round trip.
# A cache, never a contract: a miss simply falls back to reading the file.
_RECENT_BYTES = OrderedDict()
_RECENT_BYTES_LOCK = threading.Lock()
_RECENT_BYTES_MAX = 32


def _remember_bytes(path, data):
    with _RECENT_BYTES_LOCK:
        _RECENT_BYTES[path] = data
        while len(_RECENT_BYTES) > _RECENT_BYTES_MAX:
            _RECENT_BYTES.popitem(last=False)


def get_card_bytes(path):
    """Returns the in-memory bytes for a freshly rendered slip, or None."""
    with _RECENT_BYTES_LOCK:
        return _RECENT_BYTES.get(path)


MIN_CARD_HEIGHT = 240
CARD_SCALE_FACTOR = 1.5

def _is_card_valid(img_path):
    if not (os.path.exists(img_path) and os.path.getsize(img_path) > 5000):
        return False
    try:
        from PIL import Image
        with Image.open(img_path) as _im:
            if _im.size[1] >= MIN_CARD_HEIGHT:
                return True
            else:
                print(f"[CARD_CORRUPT] Cached card {img_path} height {_im.size[1]} < {MIN_CARD_HEIGHT}px. Purging.", flush=True)
                try: os.remove(img_path)
                except Exception: pass
                return False
    except Exception:
        try: os.remove(img_path)
        except Exception: pass
        return False

def generate_bjp_voter_card(voters, slip_type='Single', page=1, page_size=8, tenant='ward82'):
    """
    Generates an official digital voter card image with in-flight deduplication
    and multi-worker Chrome daemon pool support.

    NO DISK CACHE: Every call renders a fresh slip from current campaign branding.
    This guarantees that any changes made via the admin portal (candidate name,
    ballot serial, symbol, etc.) are immediately reflected in the very next slip
    sent, without requiring any cache purge.
    """
    if isinstance(voters, dict):
        voters = [voters]
    elif not voters:
        return None

    # Resolve tenant dynamically
    t_from_v = voters[0].get('tenant_slug') or voters[0].get('tenant') if voters and isinstance(voters[0], dict) else None
    tenant = str(tenant or t_from_v or 'ward82').strip().lower()
    branding = dict(get_tenant_branding(tenant))
    if tenant in ('smartbooth', 'neutral', 'default') or not branding.get('has_candidate'):
        first_v = (voters[0] if voters else {})
        gp = first_v.get('gram_panchayat') or ''
        dist = first_v.get('district') or 'राजस्थान'
        if gp:
            branding['place_hi'] = f"ग्राम पंचायत {gp} · {dist} (राजस्थान)"
            branding['title'] = f"डिजिटल मतदाता पर्ची — {gp}"

    # Sanitise slip_type to alphanumeric/underscore to prevent path traversal
    slip_type = re.sub(r'[^A-Za-z0-9_]', '', str(slip_type or 'Single')) or 'Single'
    page_size = max(1, min(int(page_size or 8), 50))

    cards_dir = os.path.join(BASE_DIR, 'generated_cards')
    os.makedirs(cards_dir, exist_ok=True)

    custom_bg = branding.get('custom_bg') or get_custom_background()
    bg_mtime = int(os.path.getmtime(custom_bg)) if custom_bg else 0
    bg_tag = f"_custom_{bg_mtime}" if custom_bg else "_ballot2_v2"

    total_pages = max(1, (len(voters) + page_size - 1) // page_size) if voters else 1
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * page_size
    end_idx = min(start_idx + page_size, len(voters))
    page_voters = voters[start_idx:end_idx] if len(voters) > 1 else voters

    page_suffix = f"_p{page}of{total_pages}" if total_pages > 1 else ""
    voter_ids = sorted([str(v.get('id', '')) for v in voters if v.get('id')])
    raw_key = f"{tenant}_{slip_type}_c{len(voters)}{page_suffix}_{'_'.join(voter_ids)}{bg_tag}" if voter_ids else f"{tenant}_{slip_type}_P{voters[0].get('part_no')}_S{voters[0].get('sl_no')}{page_suffix}{bg_tag}"
    key_hash = hashlib.md5(raw_key.encode('utf-8')).hexdigest()[:12]

    # Always generate a unique output filename per render — no disk cache.
    # A UUID suffix guarantees no stale file is ever reused.
    unique_tag = uuid.uuid4().hex[:8]
    safe_name = f"{tenant}_{slip_type}_c{len(voters)}{page_suffix}_{key_hash}_{unique_tag}{bg_tag}"
    out_img = os.path.join(cards_dir, f"bjp_slip_{safe_name}.jpg")

    # Inflight deduplication keyed on raw_key (not safe_name): if two concurrent
    # requests for the exact same voter arrive simultaneously (e.g. double-click),
    # the second waits briefly then proceeds to render its own fresh copy. This is
    # purely a Chrome CDP slot optimisation — NOT a caching mechanism.
    with _inflight_lock:
        if raw_key in _inflight_renders:
            wait_event = _inflight_renders[raw_key]
            is_leader = False
        else:
            wait_event = threading.Event()
            _inflight_renders[raw_key] = wait_event
            is_leader = True

    if not is_leader:
        # Wait briefly, then fall through to render our own fresh copy
        wait_event.wait(timeout=8.0)

    try:
        with _RENDER_SEMAPHORE:
            return _render_bjp_voter_card_internal(voters, slip_type, page, page_size, total_pages, cards_dir, custom_bg, page_voters, safe_name, out_img, tenant=tenant)
    finally:
        if is_leader:
            with _inflight_lock:
                evt = _inflight_renders.pop(raw_key, None)
                if evt:
                    evt.set()

def _render_bjp_voter_card_internal(voters, slip_type, page, page_size, total_pages, cards_dir, custom_bg, page_voters, safe_name, out_img, tenant='ward82'):
    branding = dict(get_tenant_branding(tenant))
    if tenant in ('smartbooth', 'neutral', 'default') or not branding.get('has_candidate'):
        first_v = (voters[0] if voters else {})
        gp = first_v.get('gram_panchayat') or ''
        dist = first_v.get('district') or 'राजस्थान'
        if gp:
            branding['place_hi'] = f"ग्राम पंचायत {gp} · {dist} (राजस्थान)"
            branding['title'] = f"डिजिटल मतदाता पर्ची — {gp}"
    slip_type = re.sub(r'[^A-Za-z0-9_]', '', str(slip_type or 'Single')) or 'Single'
    safe_name = re.sub(r'[^A-Za-z0-9_]', '', str(safe_name or 'slip'))

    is_multi = len(voters) > 1 or slip_type in ['Family', 'Combined']

    if custom_bg:
        win_w, win_h = 1080, 1440
        custom_bg_src = _custom_bg_src(custom_bg)

        if not is_multi:
            v = voters[0]
            booth = branding['get_booth'](v.get('part_no', 1))
            html = build_custom_bg_single_html(v, booth, branding, custom_bg_src, tenant=tenant)
        else:
            html = build_custom_bg_family_html(page_voters, voters, branding, custom_bg_src, page, total_pages, slip_type=slip_type)
    else:
        win_w = 710 if is_multi else 610
        has_symbol = branding['has_symbol']
        has_ballot = branding['has_ballot']
        win_h = max(1100, 650 + len(voters) * 55) if is_multi else (950 if (has_ballot or has_symbol) else 800)

        if not is_multi:
            v = voters[0]
            booth = branding['get_booth'](v.get('part_no', 1))
            html = build_standard_single_html(v, booth, branding, tenant=tenant)
        else:
            html = build_standard_family_html(page_voters, branding, page, total_pages, slip_type=slip_type, tenant=tenant, all_voters=voters)

    unique_suffix = uuid.uuid4().hex[:8]
    tmp_html = os.path.join(cards_dir, f"tmp_{safe_name}_{unique_suffix}.html")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html)

    try:
        # 0. Persistent shell tab per port (Stage 4). Measured 4.6x faster than
        #    creating a tab per render -- the fresh-tab page load was 257ms of a
        #    506ms slip; setDocumentContent on a warm tab makes it 29ms, with
        #    byte-identical output. Falls through to the paths below on ANY
        #    failure, so the existing ladder remains the safety net.
        if cdp_session is not None and cdp_session.available():
            chosen_port, port_lock = acquire_chrome_port(timeout=2.5)
            if chosen_port:
                try:
                    img_bytes, _css_h = cdp_session.render(
                        chosen_port, html, win_w, win_h, CARD_SCALE_FACTOR,
                        CARD_JPEG_QUALITY, MIN_CARD_HEIGHT, cards_dir)
                    with open(out_img, 'wb') as _f:
                        _f.write(img_bytes)
                    _remember_bytes(out_img, img_bytes)
                    print(f"[CDP_FAST] port={chosen_port} out={os.path.basename(out_img)}", flush=True)
                    return out_img
                except Exception as fast_err:
                    print(f"[CDP_FAST_FALLBACK] port={chosen_port} err={fast_err}", flush=True)
                finally:
                    release_chrome_port(chosen_port, port_lock)

        # 1. Multi-Worker Chrome Daemon Pool via CDP (~250-550ms parallel across 4 cores)
        url_file = "file:///" + tmp_html.replace(os.sep, '/').lstrip('/')

        # Bound before the first attempt: the retry block below calls _do_cdp(),
        # which previously raised NameError (swallowed, so the retry NEVER ran)
        # whenever the first attempt bailed before defining it.
        _do_cdp = None

        chosen_port, port_lock = acquire_chrome_port(timeout=2.5)
        if chosen_port:
            try:
                import socket
                daemon_listening = False
                try:
                    with socket.create_connection(('127.0.0.1', chosen_port), timeout=0.06):
                        daemon_listening = True
                except Exception:
                    daemon_listening = False

                if daemon_listening:
                    target_id = None
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{chosen_port}/json/new?{url_file}", method="PUT")
                        with urllib.request.urlopen(req, timeout=3.5) as resp:
                            target = json.loads(resp.read().decode())
                        target_id = target.get('id')
                        ws_url = target.get('webSocketDebuggerUrl')
                        if ws_url:
                            import websockets
                            async def _do_cdp():
                                # max_size=None: a large family/custom-bg capture base64-encodes
                                # past the websockets default 1 MiB frame cap, which previously
                                # raised PayloadTooBig and silently fell through to CLI Chrome.
                                async with websockets.connect(ws_url, close_timeout=4,
                                                              max_size=None) as ws:
                                    pending = {}

                                    async def call(mid, method, params, timeout):
                                        """Sends a CDP command and waits for ITS reply by id.

                                        The previous implementation sent id 20 and then read a
                                        single frame, which returned the acks for ids 1 and 2
                                        first. Those mismatched, so it slept and re-sent --
                                        burning ~9 iterations and ~190ms per slip.
                                        """
                                        await ws.send(json.dumps({"id": mid, "method": method,
                                                                  "params": params}))
                                        deadline = time.time() + timeout
                                        while time.time() < deadline:
                                            raw = await asyncio.wait_for(
                                                ws.recv(), timeout=max(0.05, deadline - time.time()))
                                            msg = json.loads(raw)
                                            got = msg.get('id')
                                            if got is None:
                                                continue            # unsolicited event
                                            if got == mid:
                                                if 'error' in msg:
                                                    raise RuntimeError(msg['error'])
                                                return msg.get('result', {})
                                            pending[got] = msg       # someone else's reply
                                        raise asyncio.TimeoutError(method)

                                    await call(1, "Emulation.setDeviceMetricsOverride",
                                               {"width": win_w, "height": win_h,
                                                "deviceScaleFactor": CARD_SCALE_FACTOR,
                                                "mobile": False}, 3.0)

                                    # One awaited round trip replaces the old 15-iteration poll.
                                    # Templates have no @font-face, so document.fonts was always
                                    # instantly true; what actually matters is that the images
                                    # are DECODED (complete only means bytes arrived) and that
                                    # style/layout/paint has been committed for the frame.
                                    res = await call(20, "Runtime.evaluate",
                                                     {"expression": _READY_AND_HEIGHT_JS,
                                                      "awaitPromise": True,
                                                      "returnByValue": True}, 8.0)
                                    css_h = (res.get('result') or {}).get('value')
                                    if not isinstance(css_h, (int, float)) or css_h <= 0:
                                        print("[CDP_INCOMPLETE] Height measurement failed.", flush=True)
                                        return False
                                    if css_h < MIN_CARD_HEIGHT:
                                        print(f"[CDP_INCOMPLETE] Partial card ({css_h:.0f}px < "
                                              f"{MIN_CARD_HEIGHT}px CSS). Rejecting.", flush=True)
                                        return False

                                    clip_h = int(css_h)
                                    shot = await call(3, "Page.captureScreenshot",
                                                      {"format": "jpeg", "quality": CARD_JPEG_QUALITY,
                                                       "captureBeyondViewport": False,
                                                       "clip": {"x": 0, "y": 0, "width": win_w,
                                                                "height": clip_h, "scale": 1}}, 15.0)
                                    data = shot.get('data')
                                    if not data:
                                        return False
                                    img_bytes = base64.b64decode(data)
                                    if len(img_bytes) < 5000:
                                        print(f"[CDP_INCOMPLETE] Capture too small "
                                              f"({len(img_bytes)}B). Rejecting.", flush=True)
                                        return False
                                    # No PIL crop, no re-encode: Chrome rasterised exactly the
                                    # card, already in the JPEG that WhatsApp will receive.
                                    with open(out_img, 'wb') as _f:
                                        _f.write(img_bytes)
                                    _remember_bytes(out_img, img_bytes)
                                    return True
                                return False

                            success = False
                            try:
                                success = asyncio.run(_do_cdp())
                            finally:
                                if target_id:
                                    try:
                                        urllib.request.urlopen(f"http://127.0.0.1:{chosen_port}/json/close/{target_id}", timeout=0.8)
                                    except Exception:
                                        pass

                            # _do_cdp validated height + byte count in memory before
                            # writing, so re-opening with PIL here was a redundant read.
                            if success and os.path.exists(out_img):
                                print(f"[CDP_SUCCESS] port={chosen_port} out={os.path.basename(out_img)}", flush=True)
                                return out_img
                    except Exception as cdp_err:
                        import traceback
                        print(f"[CDP_ERR] port={chosen_port} err={repr(cdp_err)}\n{traceback.format_exc()}", flush=True)
                        if target_id:
                            try:
                                urllib.request.urlopen(f"http://127.0.0.1:{chosen_port}/json/close/{target_id}", timeout=0.8)
                            except Exception:
                                pass
            finally:
                release_chrome_port(chosen_port, port_lock)

        print(f"[CDP_FALLBACK] all daemon ports busy for {safe_name}, attempting final daemon wait", flush=True)
        time.sleep(0.6)
        chosen_port, port_lock = acquire_chrome_port(timeout=3.0)
        if chosen_port:
            try:
                # Retry daemon one last time before touching heavy OS processes
                import socket
                if True:
                    req = urllib.request.Request(f"http://127.0.0.1:{chosen_port}/json/new?{url_file}", method="PUT")
                    with urllib.request.urlopen(req, timeout=3.5) as resp:
                        target = json.loads(resp.read().decode())
                    target_id = target.get('id')
                    ws_url = target.get('webSocketDebuggerUrl')
                    if ws_url and _do_cdp is not None:
                        import websockets
                        success = asyncio.run(_do_cdp())
                        if target_id:
                            try: urllib.request.urlopen(f"http://127.0.0.1:{chosen_port}/json/close/{target_id}", timeout=0.8)
                            except Exception: pass
                        if success and os.path.exists(out_img):
                            print(f"[CDP_RETRY_SUCCESS] port={chosen_port} out={os.path.basename(out_img)}", flush=True)
                            return out_img
            except Exception as retry_e:
                print(f"[CDP_RETRY_ERR] port={chosen_port} err={retry_e}", flush=True)
            finally:
                release_chrome_port(chosen_port, port_lock)

        # 2. Guarded single-concurrency CLI Chrome fallback (Never fork-bomb the OS)
        cli_lock_fd = None
        if hasattr(os, 'O_CREAT') and os.name != 'nt':
            try:
                import fcntl
                cli_lock_fd = os.open("/tmp/cli_chrome_single.lock", os.O_CREAT | os.O_RDWR, 0o666)
                # Non-blocking lock: if another CLI Chrome is running, wait up to 3s
                lock_start = time.time()
                acquired = False
                while time.time() - lock_start < 3.0:
                    try:
                        fcntl.flock(cli_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except (BlockingIOError, OSError):
                        time.sleep(0.2)
                if not acquired:
                    print(f"[CLI_REJECT] Another CLI Chrome render is active. Refusing fork-bomb for {safe_name}.", flush=True)
                    try: os.close(cli_lock_fd)
                    except: pass
                    return None
            except Exception:
                pass

        chrome_exe = find_chrome_executable()
        if not chrome_exe:
            if cli_lock_fd is not None:
                try: os.close(cli_lock_fd)
                except: pass
            return None

        # Unique user profile per render to prevent process lock collisions on Windows / Linux
        snap_common = os.path.expanduser('~/snap/chromium/common')
        if os.path.exists(snap_common):
            slip_temp_profile = os.path.join(snap_common, f'prof_{random.randint(1000, 9999)}')
        else:
            slip_temp_profile = os.path.join(tempfile.gettempdir(), f'voter_chrome_prof_{random.randint(1000, 9999)}')
        os.makedirs(slip_temp_profile, exist_ok=True)

        cmd = [
            chrome_exe,
            '--headless=new',
            '--disable-gpu',
            '--no-sandbox',
            '--disable-dev-shm-usage',
            '--hide-scrollbars',
            '--no-first-run',
            '--no-default-browser-check',
            '--allow-file-access-from-files',
            '--virtual-time-budget=2000',
            f'--user-data-dir={slip_temp_profile}',
            '--disable-extensions',
            '--disable-sync',
            '--disable-background-networking',
            f'--force-device-scale-factor={CARD_SCALE_FACTOR}',
            f'--window-size={win_w},{win_h}',
            f'--screenshot={out_img}',
            url_file
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            import shutil
            try: shutil.rmtree(slip_temp_profile, ignore_errors=True)
            except: pass
            if os.path.exists(out_img) and os.path.getsize(out_img) > 5000:
                try:
                    from PIL import Image, ImageChops
                    im = Image.open(out_img)
                    bg = Image.new(im.mode, im.size, (255, 255, 255))
                    diff = ImageChops.difference(im, bg)
                    bbox = diff.getbbox()
                    if bbox:
                        cropped_h = bbox[3] + 1
                        min_expected_h = int(MIN_CARD_HEIGHT * CARD_SCALE_FACTOR)
                        if cropped_h >= min_expected_h:
                            im = im.crop((0, 0, im.width, cropped_h))
                            # CLI Chrome writes PNG whatever the extension says, and
                            # out_img is now .jpg -- JPEG cannot hold an alpha channel.
                            if out_img.lower().endswith(('.jpg', '.jpeg')):
                                im = im.convert('RGB')
                                im.save(out_img, format='JPEG',
                                        quality=CARD_JPEG_QUALITY, optimize=True)
                            else:
                                im.save(out_img)
                            return out_img
                        else:
                            print(f"[CLI_REJECT] Cropped height {cropped_h} < {min_expected_h}px (header only). Purging.", flush=True)
                            try: os.remove(out_img)
                            except Exception: pass
                            return None
                    else:
                        print(f"[CLI_REJECT] Empty bbox from CLI Chrome. Purging.", flush=True)
                        try: os.remove(out_img)
                        except Exception: pass
                        return None
                except Exception as cli_proc_err:
                    print(f"[CLI_ERR] Image processing error: {cli_proc_err}", flush=True)
                    if _is_card_valid(out_img):
                        return out_img
        finally:
            if cli_lock_fd is not None:
                try:
                    import fcntl
                    fcntl.flock(cli_lock_fd, fcntl.LOCK_UN)
                    os.close(cli_lock_fd)
                except: pass
    except Exception as e:
        print(f"Error generating card in _render_bjp_voter_card_internal: {e}", flush=True)
        return None
    finally:
        try:
            if os.path.exists(tmp_html):
                os.remove(tmp_html)
        except Exception:
            pass

def generate_all_bjp_voter_card_pages(voters, slip_type='Single', page_size=8, tenant='ward82'):
    """
    Returns a list of image file paths for all pages of the slip.
    If len(voters) <= 8, returns 1 card image path.
    If len(voters) == 12, returns [page1_card_path, page2_card_path].
    """
    if not voters:
        return []
    total_pages = max(1, (len(voters) + page_size - 1) // page_size)
    pages = []
    for p in range(1, total_pages + 1):
        card = generate_bjp_voter_card(voters, slip_type=slip_type, page=p, page_size=page_size, tenant=tenant)
        if card and os.path.exists(card):
            pages.append(card)
    return pages

# Canonical aliases
generate_voter_card = generate_bjp_voter_card
generate_all_voter_card_pages = generate_all_bjp_voter_card_pages

# Wire slip generator function into bulk campaign service
try:
    from bulk_campaign_service import campaign_manager
    campaign_manager.set_slip_generator(generate_all_bjp_voter_card_pages)
except Exception:
    pass

