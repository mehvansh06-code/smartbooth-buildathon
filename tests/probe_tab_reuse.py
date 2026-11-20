"""
probe_tab_reuse.py
The decisive Stage 4 experiment: how much of the per-render cost is the fresh
tab + file:// navigation, versus work that survives tab reuse?

Compares, on the SAME daemon:
  A) current production shape -- new tab per render, navigate to file://
  B) one reused tab + Page.setDocumentContent per render

Measures nothing else differently: same readiness JS, same clip, same JPEG.
"""
import os
import sys
import json
import time
import base64
import asyncio
import tempfile
import statistics
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets
from electionapp.tenant_branding import get_tenant_branding
from electionapp.card_templates import build_standard_single_html
import electionapp.card_generator as cg

PORT = int(os.environ.get('PROBE_PORT', '9222'))
DSF = cg.CARD_SCALE_FACTOR
WIN_W, WIN_H = 610, 950
READY_JS = cg._READY_AND_HEIGHT_JS

VOTER = {
    'id': 101, 'sl_no': 15, 'part_no': 82,
    'name_en': 'RAMESH CHOUDHARY', 'name_hi': 'रमेश चौधरी',
    'rel_name_en': 'SURESH CHOUDHARY', 'rel_name_hi': 'सुरेश चौधरी',
    'rel_type': 'पिता', 'age': 35, 'gender': 'M',
    'house_no': '45', 'epic_no': 'ABC1234567',
}


class Conn:
    """Minimal id-matched CDP client, mirroring the shipped call() helper."""

    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method, params, timeout=15.0):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(self.ws.recv(),
                                         timeout=max(0.05, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError(msg['error'])
                return msg.get('result', {})
        raise asyncio.TimeoutError(method)


async def capture(c, t, label):
    t0 = time.perf_counter()
    await c.call("Emulation.setDeviceMetricsOverride",
                 {"width": WIN_W, "height": WIN_H,
                  "deviceScaleFactor": DSF, "mobile": False}, 3.0)
    t.setdefault(label + '_metrics', []).append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    res = await c.call("Runtime.evaluate",
                       {"expression": READY_JS, "awaitPromise": True,
                        "returnByValue": True}, 10.0)
    h = res['result']['value']
    t.setdefault(label + '_ready_measure', []).append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    shot = await c.call("Page.captureScreenshot",
                        {"format": "jpeg", "quality": 95,
                         "captureBeyondViewport": False,
                         "clip": {"x": 0, "y": 0, "width": WIN_W,
                                  "height": int(h), "scale": 1}}, 15.0)
    img = base64.b64decode(shot['data'])
    t.setdefault(label + '_screenshot', []).append((time.perf_counter() - t0) * 1000)
    return h, len(img)


async def mode_new_tab(html_path, runs, t):
    """A) Current production shape: new tab + file:// navigation per render."""
    url = "file:///" + html_path.replace(os.sep, '/').lstrip('/')
    for i in range(runs + 1):
        w0 = time.perf_counter()
        t0 = time.perf_counter()
        req = urllib.request.Request(
            "http://127.0.0.1:%d/json/new?%s" % (PORT, url), method="PUT")
        with urllib.request.urlopen(req, timeout=5) as r:
            target = json.loads(r.read().decode())
        newtab = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        async with websockets.connect(target['webSocketDebuggerUrl'],
                                      close_timeout=4, max_size=None) as ws:
            wsc = (time.perf_counter() - t0) * 1000
            c = Conn(ws)
            h, n = await capture(c, t, 'A')
        t0 = time.perf_counter()
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s"
                                   % (PORT, target['id']), timeout=2)
        except Exception:
            pass
        closed = (time.perf_counter() - t0) * 1000
        if i == 0:
            for k in list(t):
                if k.startswith('A_'):
                    t[k].clear()
            continue
        t.setdefault('A_new_tab', []).append(newtab)
        t.setdefault('A_ws_connect', []).append(wsc)
        t.setdefault('A_close_tab', []).append(closed)
        t.setdefault('A_WALL', []).append((time.perf_counter() - w0) * 1000)
    return h, n


async def mode_reuse(html, html_path, runs, t):
    """B) One tab, one websocket, Page.setDocumentContent per render."""
    url = "file:///" + html_path.replace(os.sep, '/').lstrip('/')
    t0 = time.perf_counter()
    req = urllib.request.Request(
        "http://127.0.0.1:%d/json/new?%s" % (PORT, url), method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        target = json.loads(r.read().decode())
    async with websockets.connect(target['webSocketDebuggerUrl'],
                                  close_timeout=4, max_size=None) as ws:
        c = Conn(ws)
        tree = await c.call("Page.getFrameTree", {}, 5.0)
        frame_id = tree['frameTree']['frame']['id']
        t['B_one_time_setup'] = [(time.perf_counter() - t0) * 1000]

        for i in range(runs + 1):
            w0 = time.perf_counter()
            t0 = time.perf_counter()
            await c.call("Page.setDocumentContent",
                         {"frameId": frame_id, "html": html}, 10.0)
            setdoc = (time.perf_counter() - t0) * 1000
            h, n = await capture(c, t, 'B')
            if i == 0:
                for k in list(t):
                    if k.startswith('B_') and k != 'B_one_time_setup':
                        t[k].clear()
                continue
            t.setdefault('B_set_content', []).append(setdoc)
            t.setdefault('B_WALL', []).append((time.perf_counter() - w0) * 1000)
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s"
                               % (PORT, target['id']), timeout=2)
    except Exception:
        pass
    return h, n


def p50(v):
    v = sorted(v)
    return v[len(v) // 2]


def main():
    runs = int(os.environ.get('PROBE_RUNS', '12'))
    branding = get_tenant_branding('ward82')
    booth = branding['get_booth'](VOTER.get('part_no', 1))
    html = build_standard_single_html(VOTER, booth, branding, tenant='ward82')
    hp = os.path.join(tempfile.gettempdir(), 'reuse_probe.html')
    with open(hp, 'w', encoding='utf-8') as f:
        f.write(html)

    t = {}
    hA, nA = asyncio.run(mode_new_tab(hp, runs, t))
    hB, nB = asyncio.run(mode_reuse(html, hp, runs, t))

    print("\n%-26s %9s %9s" % ("stage", "p50 ms", "mean ms"))
    print("-" * 46)
    order = ['A_new_tab', 'A_ws_connect', 'A_metrics', 'A_ready_measure',
             'A_screenshot', 'A_close_tab', 'A_WALL',
             'B_one_time_setup', 'B_set_content', 'B_metrics',
             'B_ready_measure', 'B_screenshot', 'B_WALL']
    for k in order:
        v = t.get(k)
        if not v:
            continue
        if k.endswith('WALL'):
            print("-" * 46)
        print("%-26s %9.1f %9.1f" % (k, p50(v), statistics.fmean(v)))
    print("-" * 46)
    a, b = p50(t['A_WALL']), p50(t['B_WALL'])
    print("A) new tab per render : %7.1f ms" % a)
    print("B) reused tab         : %7.1f ms   (%.0f ms saved, %.1fx)"
          % (b, a - b, a / b if b else 0))
    print("\nheight A=%s B=%s   bytes A=%s B=%s  %s"
          % (hA, hB, nA, nB,
             'IDENTICAL OUTPUT' if (hA == hB and nA == nB) else '*** OUTPUT DIFFERS ***'))


if __name__ == '__main__':
    main()
