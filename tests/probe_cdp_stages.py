"""
probe_cdp_stages.py
Instruments the REAL CDP sequence used by card_generator._render_bjp_voter_card_internal
so we can see where per-slip latency actually goes, rather than guessing.

Read-only: writes temp HTML + captures to the system temp dir, never to
generated_cards/, and never uploads anything.
"""
import os
import sys
import json
import time
import base64
import asyncio
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import websockets
from PIL import Image, ImageChops
from electionapp.tenant_branding import get_tenant_branding
from electionapp.card_templates import build_standard_single_html
import electionapp.card_generator as cg

PORT = int(os.environ.get('PROBE_PORT', '9222'))
DSF = cg.CARD_SCALE_FACTOR
WIN_W, WIN_H = 610, 950

READY_JS = """(async () => {
  const imgs = [...document.images];
  await Promise.all(imgs.map(i => i.complete
    ? (i.decode ? i.decode().catch(() => {}) : null)
    : new Promise(r => { i.onload = i.onerror = r; })));
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return document.readyState;
})()"""


VOTER = {
    'id': 101, 'sl_no': 15, 'part_no': 82,
    'name_en': 'RAMESH CHOUDHARY', 'name_hi': 'रमेश चौधरी',
    'rel_name_en': 'SURESH CHOUDHARY', 'rel_name_hi': 'सुरेश चौधरी',
    'rel_type': 'पिता', 'age': 35, 'gender': 'M',
    'house_no': '45', 'epic_no': 'ABC1234567',
}


async def one_render(html_path, t):
    url = "file:///" + html_path.replace(os.sep, '/').lstrip('/')

    t0 = time.perf_counter()
    req = urllib.request.Request(
        "http://127.0.0.1:%d/json/new?%s" % (PORT, url), method="PUT")
    with urllib.request.urlopen(req, timeout=5) as resp:
        target = json.loads(resp.read().decode())
    t['new_tab'] = (time.perf_counter() - t0) * 1000
    tid, ws_url = target.get('id'), target.get('webSocketDebuggerUrl')

    t0 = time.perf_counter()
    async with websockets.connect(ws_url, close_timeout=4, max_size=None) as ws:
        t['ws_connect'] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        await ws.send(json.dumps({"id": 1, "method": "Emulation.setDeviceMetricsOverride",
                                  "params": {"width": WIN_W, "height": WIN_H,
                                             "deviceScaleFactor": DSF, "mobile": False}}))
        await ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        t['send_setup'] = (time.perf_counter() - t0) * 1000

        mode = os.environ.get('PROBE_MODE', 'legacy')
        if mode == 'clean':
            # One correctly id-matched await: images decoded + layout/paint
            # committed. No polling, no fixed sleeps.
            t0 = time.perf_counter()
            await ws.send(json.dumps({"id": 21, "method": "Runtime.evaluate",
                "params": {"expression": READY_JS, "awaitPromise": True,
                           "returnByValue": True}}))
            for _ in range(40):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if m.get('id') == 21:
                    break
            t['readiness_poll'] = (time.perf_counter() - t0) * 1000
            t['poll_iters'] = 1
        else:
            # Replicate the PRODUCTION readiness poll verbatim (the id-mismatch one).
            t0 = time.perf_counter()
            iters = 0
            for _ in range(15):
                iters += 1
                await ws.send(json.dumps({
                    "id": 20, "method": "Runtime.evaluate",
                    "params": {"expression":
                               "document.readyState === 'complete' && document.fonts.status === 'loaded'"}}))
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.1))
                    if msg.get('id') == 20 and msg.get('result', {}).get('result', {}).get('value') is True:
                        await asyncio.sleep(0.06)
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.03)
            t['readiness_poll'] = (time.perf_counter() - t0) * 1000
            t['poll_iters'] = iters

        # What the height actually is, measured the Stage-3b way.
        t0 = time.perf_counter()
        await ws.send(json.dumps({
            "id": 30, "method": "Runtime.evaluate",
            "params": {"expression": """(() => {
                 if (document.querySelector('.data-box')) return 1440;
                 const c = document.querySelector('.card') || document.body.firstElementChild;
                 return Math.ceil((c || document.body).getBoundingClientRect().bottom);
               })()""", "returnByValue": True}}))
        for _ in range(30):
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            if m.get('id') == 30:
                t['measured_css_h'] = m['result']['result'].get('value')
                break
        t['measure_height'] = (time.perf_counter() - t0) * 1000

        async def shoot(fmt, quality=None, clip=None, label=''):
            params = {"format": fmt, "captureBeyondViewport": True}
            if quality is not None:
                params["quality"] = quality
            if clip is not None:
                params["clip"] = clip
                params["captureBeyondViewport"] = False
            mid = 40 + len(label)
            s = time.perf_counter()
            await ws.send(json.dumps({"id": mid, "method": "Page.captureScreenshot",
                                      "params": params}))
            data = None
            for _ in range(40):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if m.get('id') == mid and 'result' in m:
                    data = base64.b64decode(m['result']['data'])
                    break
            return data, (time.perf_counter() - s) * 1000

        png, ms = await shoot('png', label='a')
        t['shot_png_full'] = ms
        t['png_bytes'] = len(png)

        jpg, ms = await shoot('jpeg', quality=95, label='bb')
        t['shot_jpeg_full'] = ms
        t['jpeg_bytes'] = len(jpg)

        h = t.get('measured_css_h') or WIN_H
        clip = {"x": 0, "y": 0, "width": WIN_W, "height": h, "scale": 1}
        jpgc, ms = await shoot('jpeg', quality=95, clip=clip, label='ccc')
        t['shot_jpeg_clip'] = ms
        t['jpeg_clip_bytes'] = len(jpgc)
        with Image.open(__import__('io').BytesIO(jpgc)) as im:
            t['clip_out_size'] = im.size

    t0 = time.perf_counter()
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (PORT, tid), timeout=2)
    except Exception:
        pass
    t['close_tab'] = (time.perf_counter() - t0) * 1000

    # The production PIL crop + re-encode, measured on the PNG.
    import io as _io
    t0 = time.perf_counter()
    im = Image.open(_io.BytesIO(png))
    bg = Image.new(im.mode, im.size, (255, 255, 255))
    bbox = ImageChops.difference(im, bg).getbbox()
    t['pil_getbbox'] = (time.perf_counter() - t0) * 1000
    t['png_full_size'] = im.size

    t0 = time.perf_counter()
    out = os.path.join(tempfile.gettempdir(), 'probe_crop.png')
    im.crop((0, 0, im.width, bbox[3] + 1)).save(out)
    t['pil_crop_save'] = (time.perf_counter() - t0) * 1000
    t['cropped_h'] = bbox[3] + 1

    # The upload_media PNG->JPEG re-encode.
    t0 = time.perf_counter()
    with Image.open(out) as im2:
        buf = _io.BytesIO()
        im2.convert('RGB').save(buf, format='JPEG', quality=95, optimize=True)
    t['upload_reencode'] = (time.perf_counter() - t0) * 1000
    return t


def main():
    branding = get_tenant_branding('ward82')
    html = build_standard_single_html(VOTER, {'name': 'Test Booth'}, branding, tenant='ward82')
    hp = os.path.join(tempfile.gettempdir(), 'probe_slip.html')
    with open(hp, 'w', encoding='utf-8') as f:
        f.write(html)
    print("asset_mode=%s  html_bytes=%s  port=%d"
          % (os.environ.get('SLIP_ASSET_MODE', 'file'), format(len(html.encode()), ','), PORT))

    runs = int(os.environ.get('PROBE_RUNS', '7'))
    acc = {}
    for i in range(runs):
        t = asyncio.run(one_render(hp, {}))
        if i == 0:
            print("(cold run discarded)")
            continue
        for k, v in t.items():
            acc.setdefault(k, []).append(v)

    print("\n%-20s %10s %10s" % ("stage", "p50", "max"))
    print("-" * 42)
    order = ['new_tab', 'ws_connect', 'send_setup', 'readiness_poll', 'poll_iters',
             'measure_height', 'shot_png_full', 'shot_jpeg_full', 'shot_jpeg_clip',
             'close_tab', 'pil_getbbox', 'pil_crop_save', 'upload_reencode']
    for k in order:
        v = acc.get(k)
        if not v:
            continue
        nums = sorted(v)
        print("%-20s %10.1f %10.1f" % (k, nums[len(nums) // 2], nums[-1]))
    print("-" * 42)
    for k in ['measured_css_h', 'cropped_h', 'png_full_size', 'clip_out_size',
              'png_bytes', 'jpeg_bytes', 'jpeg_clip_bytes']:
        if k in acc:
            print("%-20s %s" % (k, acc[k][0]))


if __name__ == '__main__':
    main()
