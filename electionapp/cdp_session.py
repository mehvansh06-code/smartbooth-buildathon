"""
cdp_session.py
Persistent Chrome DevTools Protocol sessions for voter-slip rendering.

WHY THIS EXISTS
---------------
The original renderer created a fresh tab, a fresh websocket and a fresh
asyncio event loop for every single slip, then navigated to a file:// URL.
Measured on one daemon (tests/probe_tab_reuse.py), identical readiness JS,
clip and JPEG settings on both sides:

    new tab per render : 506.0 ms   (page load alone: 257 ms)
    reused tab         : 109.1 ms   (page load alone:  29 ms)   -> 4.6x

...with byte-identical output. Nearly all of a slip's cost was Chrome parsing
and laying out a brand-new document and re-decoding the branding images, which
`Page.setDocumentContent` on an already-warm tab avoids entirely.

DESIGN
------
* ONE shell tab per PORT, fleet-wide -- not per (pid, port).
  card_generator.acquire_chrome_port() holds an exclusive cross-process
  fcntl.flock on /tmp/chrome_port_{port}.lock for the whole render, so two
  workers never drive the same port concurrently. A per-PID scheme would mint a
  new tab every time Gunicorn recycled a worker and never close the old one,
  leaking zombie tabs. Tabs are therefore discovered via /json/list and adopted.
* ONE background thread running ONE asyncio loop for the whole process.
* ONE persistent websocket per port, with a reader task that resolves replies
  onto per-id futures (the old code read a single frame after each send, so the
  acks for earlier ids mismatched and it re-sent -- ~190 ms of pure waste).
* The shell tab stays on a real file:/// URL. This is load-bearing: slip assets
  are file:// URLs (see tenant_branding._file_uri), and a document on
  about:blank or a data: URL is an opaque origin that cannot load them. The
  images would silently render as blank boxes.

Every entry point raises on failure so card_generator can fall through to its
existing ephemeral-tab path and then the CLI path. The fallback ladder is the
safety net and must not be removed.
"""
import os
import json
import time
import base64
import asyncio
import inspect
import logging
import itertools
import threading
import urllib.request

try:
    import websockets
except ImportError:
    websockets = None

log = logging.getLogger(__name__)

# Recycle a shell tab after this many renders to bound renderer memory growth.
TAB_MAX_RENDERS = int(os.environ.get('SLIP_TAB_MAX_RENDERS', '400'))

# Per-step CDP budgets (seconds). Kept tight: a wedged step must surface fast so
# the caller can fall back, not stall a Gunicorn thread.
T_METRICS = 3.0
T_SETDOC = 10.0
T_EVAL = 10.0
T_SHOT = 15.0
T_FRAMETREE = 5.0


class CdpError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# One event loop for the process
# ---------------------------------------------------------------------------
class _CdpLoop:
    _inst = None
    _lock = threading.Lock()

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name='CdpLoop', daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @classmethod
    def get(cls):
        with cls._lock:
            inst = cls._inst
            if inst is None or not inst._thread.is_alive() or inst._loop.is_closed():
                inst = cls._inst = cls()
            return inst

    @property
    def loop(self):
        return self._loop

    def submit(self, coro, timeout):
        """Runs a coroutine on the shared loop and blocks for its result."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except Exception:
            fut.cancel()
            raise


# ---------------------------------------------------------------------------
# One websocket per port, with an id-matched reader
# ---------------------------------------------------------------------------
class _CdpConn:
    def __init__(self, ws, loop):
        self.ws = ws
        self._loop = loop
        self._ids = itertools.count(1)
        self._pending = {}
        self.alive = True
        self._reader = loop.create_task(self._pump())

    async def _pump(self):
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mid = msg.get('id')
                if mid is None:
                    continue            # unsolicited event; we subscribe to none
                fut = self._pending.pop(mid, None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except Exception as exc:
            self._fail_all(exc)
        finally:
            self.alive = False
            self._fail_all(CdpError('CDP connection closed'))

    def _fail_all(self, exc):
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc if isinstance(exc, BaseException)
                                  else CdpError(str(exc)))
        self._pending.clear()

    async def call(self, method, params, timeout):
        if not self.alive:
            raise CdpError('CDP connection is dead')
        mid = next(self._ids)
        fut = self._loop.create_future()
        self._pending[mid] = fut
        try:
            await self.ws.send(json.dumps({"id": mid, "method": method,
                                           "params": params}))
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            # A step that timed out leaves the connection in an unknown state.
            # Poison it so the next caller reconnects rather than queueing
            # behind a wedged renderer.
            self.alive = False
            raise CdpError('CDP timeout on %s' % method)
        finally:
            self._pending.pop(mid, None)
        if 'error' in msg:
            raise CdpError('%s: %s' % (method, msg['error']))
        return msg.get('result', {})

    async def close(self):
        self.alive = False
        try:
            self._reader.cancel()
        except Exception:
            pass
        try:
            await self.ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# One shell tab per port
# ---------------------------------------------------------------------------
_SHELL_HTML = "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body></body></html>"


class _Session:
    def __init__(self, port, conn, target_id, frame_id):
        self.port = port
        self.conn = conn
        self.target_id = target_id
        self.frame_id = frame_id
        self.renders = 0

    @property
    def alive(self):
        return self.conn.alive

    async def render(self, html, win_w, win_h, dsf, quality, min_css_h):
        """Swaps the document, measures the card, captures. Returns (bytes, css_h)."""
        # Sent unconditionally every render: the tab outlives any single render,
        # so a crash mid-render could otherwise leave stale Emulation overrides
        # (e.g. a 710px family width applied to a 610px single slip).
        await self.conn.call("Emulation.setDeviceMetricsOverride",
                             {"width": win_w, "height": win_h,
                              "deviceScaleFactor": dsf, "mobile": False},
                             T_METRICS)
        await self.conn.call("Page.setDocumentContent",
                             {"frameId": self.frame_id, "html": html}, T_SETDOC)

        from electionapp.card_generator import _READY_AND_HEIGHT_JS
        res = await self.conn.call("Runtime.evaluate",
                                   {"expression": _READY_AND_HEIGHT_JS,
                                    "awaitPromise": True,
                                    "returnByValue": True}, T_EVAL)
        css_h = (res.get('result') or {}).get('value')
        if not isinstance(css_h, (int, float)) or css_h <= 0:
            raise CdpError('height measurement failed')
        if css_h < min_css_h:
            raise CdpError('partial card: %.0f < %s CSS px' % (css_h, min_css_h))

        shot = await self.conn.call("Page.captureScreenshot",
                                    {"format": "jpeg", "quality": quality,
                                     "captureBeyondViewport": False,
                                     "clip": {"x": 0, "y": 0, "width": win_w,
                                              "height": int(css_h), "scale": 1}},
                                    T_SHOT)
        data = shot.get('data')
        if not data:
            raise CdpError('empty screenshot payload')
        img = base64.b64decode(data)
        if len(img) < 5000:
            raise CdpError('screenshot too small (%d bytes)' % len(img))
        self.renders += 1
        return img, css_h

    async def close(self):
        await self.conn.close()
        if self.target_id:
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/close/%s" % (self.port, self.target_id),
                    timeout=1.0)
            except Exception:
                pass


_SESSIONS = {}
_SESSIONS_LOCK = threading.Lock()


def _shell_path(port, base_dir):
    path = os.path.join(base_dir, 'shell_%d.html' % port)
    if not os.path.exists(path):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(_SHELL_HTML)
        except Exception:
            pass
    return path


def _shell_url(port, base_dir):
    p = _shell_path(port, base_dir).replace(os.sep, '/')
    return "file:///" + p.lstrip('/')


def _http_json(url, timeout=3.5, method=None):
    req = urllib.request.Request(url, method=method) if method else url
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _find_or_create_target(port, shell_url):
    """Adopts this port's existing shell tab if present, else creates it.

    Adoption is what keeps tab count bounded at 1/port across Gunicorn worker
    recycles -- a restarted worker reuses the live tab instead of leaking one.
    """
    try:
        for t in _http_json("http://127.0.0.1:%d/json/list" % port, timeout=2.0):
            if t.get('type') == 'page' and t.get('url') == shell_url:
                return t
    except Exception:
        pass
    return _http_json("http://127.0.0.1:%d/json/new?%s" % (port, shell_url),
                      timeout=3.5, method="PUT")


async def _open_session(port, shell_url):
    target = _find_or_create_target(port, shell_url)
    ws_url = target.get('webSocketDebuggerUrl')
    if not ws_url:
        raise CdpError('no webSocketDebuggerUrl for port %d' % port)

    kwargs = dict(max_size=None, open_timeout=4, close_timeout=2,
                  ping_interval=20, ping_timeout=20)
    # compression=None: on loopback, deflating ~250KB of base64 costs more CPU
    # on a 2-vCPU box than it saves in transfer.
    try:
        if 'compression' in inspect.signature(websockets.connect).parameters:
            kwargs['compression'] = None
    except (TypeError, ValueError):
        pass
    ws = await websockets.connect(ws_url, **kwargs)

    loop = asyncio.get_event_loop()
    conn = _CdpConn(ws, loop)
    tree = await conn.call("Page.getFrameTree", {}, T_FRAMETREE)
    frame_id = tree['frameTree']['frame']['id']
    return _Session(port, conn, target.get('id'), frame_id)


def _drop(port, session=None):
    with _SESSIONS_LOCK:
        cur = _SESSIONS.get(port)
        if session is None or cur is session:
            _SESSIONS.pop(port, None)
            cur = cur or session
    if cur is not None:
        try:
            _CdpLoop.get().submit(cur.close(), timeout=4)
        except Exception:
            pass


# A daemon that just died (restart, OOM, deploy) must not cost a failed TCP
# connect on EVERY render until it returns. Remember the failure briefly and
# skip the port, so the caller falls through to another port or the CLI path
# immediately instead of paying the connect timeout each time.
_DEAD_PORT_TTL = float(os.environ.get('SLIP_DEAD_PORT_TTL', '5.0'))
_DEAD_PORTS = {}


def _port_listening(port, timeout=0.06):
    import socket
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except Exception:
        return False


def _mark_dead(port):
    _DEAD_PORTS[port] = time.time()


def _recently_dead(port):
    ts = _DEAD_PORTS.get(port)
    if ts is None:
        return False
    if time.time() - ts > _DEAD_PORT_TTL:
        _DEAD_PORTS.pop(port, None)
        return False
    return True


def available():
    return websockets is not None and os.environ.get(
        'SLIP_CDP_PERSISTENT', '1').strip().lower() not in ('0', 'false', 'no')


def render(port, html, win_w, win_h, dsf, quality, min_css_h, base_dir):
    """Renders one slip on the port's persistent shell tab.

    MUST be called while holding that port's lock (card_generator.acquire_chrome_port).
    Raises CdpError on any failure so the caller can fall back; it never returns
    a partial or stale image.
    """
    if not available():
        raise CdpError('persistent CDP disabled or websockets missing')

    loop = _CdpLoop.get()
    shell_url = _shell_url(port, base_dir)

    for attempt in (0, 1):
        with _SESSIONS_LOCK:
            sess = _SESSIONS.get(port)
        if sess is not None and (not sess.alive or sess.renders >= TAB_MAX_RENDERS):
            if sess.renders >= TAB_MAX_RENDERS:
                log.info("[CDP_RECYCLE] port=%s after %d renders", port, sess.renders)
            _drop(port, sess)
            sess = None
        if sess is None:
            if _recently_dead(port):
                raise CdpError('port %d marked dead %.1fs ago' %
                               (port, time.time() - _DEAD_PORTS[port]))
            if not _port_listening(port):
                _mark_dead(port)
                raise CdpError('port %d not listening' % port)
            try:
                sess = loop.submit(_open_session(port, shell_url), timeout=12)
            except Exception as exc:
                _mark_dead(port)
                raise CdpError('session open failed on port %d: %r' % (port, exc))
            _DEAD_PORTS.pop(port, None)
            with _SESSIONS_LOCK:
                _SESSIONS[port] = sess

        budget = T_METRICS + T_SETDOC + T_EVAL + T_SHOT + 2.0
        try:
            return loop.submit(
                sess.render(html, win_w, win_h, dsf, quality, min_css_h),
                timeout=budget)
        except Exception as exc:
            # A dead/stale tab is retried ONCE on a fresh session; a genuine
            # content problem (partial card) must not be retried silently.
            _drop(port, sess)
            msg = str(exc)
            if attempt == 0 and 'partial card' not in msg and 'too small' not in msg:
                log.warning("[CDP_SESSION_RETRY] port=%s err=%r", port, exc)
                continue
            raise CdpError('render failed on port %d: %r' % (port, exc))


def shutdown():
    """Closes every session. For tests and clean process exit."""
    with _SESSIONS_LOCK:
        ports = list(_SESSIONS)
    for p in ports:
        _drop(p)


def stats():
    with _SESSIONS_LOCK:
        return {p: {'renders': s.renders, 'alive': s.alive}
                for p, s in _SESSIONS.items()}
