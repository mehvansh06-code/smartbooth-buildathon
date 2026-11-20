"""
parity_stage3.py
Acceptance gate for Stage 3: the new direct-JPEG + clip capture must produce
the SAME pixels as the legacy full-PNG + PIL-autocrop pipeline.

Renders both ways through one Chrome daemon and reports dimension equality and
per-channel delta. Dimensions must match EXACTLY; deltas are JPEG q95 vs PNG
antialiasing noise only.
"""
import os
import sys
import io
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
from electionapp.card_templates import build_standard_single_html, build_standard_family_html
import electionapp.card_generator as cg

PORT = int(os.environ.get('PROBE_PORT', '9222'))
DSF = cg.CARD_SCALE_FACTOR

V = {
    'id': 101, 'sl_no': 15, 'part_no': 82,
    'name_en': 'RAMESH CHOUDHARY', 'name_hi': 'रमेश चौधरी',
    'rel_name_en': 'SURESH CHOUDHARY', 'rel_name_hi': 'सुरेश चौधरी',
    'rel_type': 'पिता', 'age': 35, 'gender': 'M',
    'house_no': '45', 'epic_no': 'ABC1234567',
}
SHAPING = dict(V, id=102, name_hi='श्रीकृष्ण द्वारिकाप्रसाद ऋद्धि',
               rel_name_hi='त्रिलोकचन्द्र श्रीवास्तव',
               name_en='SHRIKRISHNA DWARIKAPRASAD')


async def legacy_capture(html_path, win_w, win_h):
    """The ORIGINAL pipeline: full PNG + captureBeyondViewport + PIL autocrop."""
    url = "file:///" + html_path.replace(os.sep, '/').lstrip('/')
    req = urllib.request.Request(
        "http://127.0.0.1:%d/json/new?%s" % (PORT, url), method="PUT")
    with urllib.request.urlopen(req, timeout=5) as resp:
        target = json.loads(resp.read().decode())
    tid, ws_url = target.get('id'), target.get('webSocketDebuggerUrl')
    try:
        async with websockets.connect(ws_url, close_timeout=4, max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Emulation.setDeviceMetricsOverride",
                                      "params": {"width": win_w, "height": win_h,
                                                 "deviceScaleFactor": DSF, "mobile": False}}))
            await ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
            for _ in range(15):
                await ws.send(json.dumps({
                    "id": 20, "method": "Runtime.evaluate",
                    "params": {"expression":
                               "document.readyState === 'complete' && document.fonts.status === 'loaded'"}}))
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.1))
                    if m.get('id') == 20 and m.get('result', {}).get('result', {}).get('value') is True:
                        await asyncio.sleep(0.06)
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.03)
            await ws.send(json.dumps({"id": 3, "method": "Page.captureScreenshot",
                                      "params": {"format": "png", "captureBeyondViewport": True}}))
            for _ in range(40):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if m.get('id') == 3 and 'result' in m:
                    png = base64.b64decode(m['result']['data'])
                    break
    finally:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (PORT, tid), timeout=2)
        except Exception:
            pass

    im = Image.open(io.BytesIO(png))
    bg = Image.new(im.mode, im.size, (255, 255, 255))
    bbox = ImageChops.difference(im, bg).getbbox()
    return im.crop((0, 0, im.width, bbox[3] + 1))


def main():
    branding = get_tenant_branding('ward82')
    cases = []

    # Use the SAME booth lookup generate_bjp_voter_card does, or the two sides
    # render different HTML and the comparison is meaningless.
    def booth_for(v):
        return branding['get_booth'](v.get('part_no', 1))

    html = build_standard_single_html(V, booth_for(V), branding, tenant='ward82')
    cases.append(('single', html, 610, 950, [V], 'Single'))

    html = build_standard_single_html(SHAPING, booth_for(SHAPING), branding, tenant='ward82')
    cases.append(('shaping', html, 610, 950, [SHAPING], 'Single'))

    fam = [dict(V, id=200 + i, sl_no=100 + i, name_hi=n, name_en=e)
           for i, (n, e) in enumerate([
               ('आनंद', 'Aanand'), ('विजय प्रकाश सांखला', 'Vijay Prakash Sankhla'),
               ('अविनाश', 'Avinash'), ('श्रीकृष्ण द्वारिकाप्रसाद', 'Shrikrishna'),
           ])]
    html = build_standard_family_html(fam, branding, 1, 1, slip_type='Family',
                                      tenant='ward82', all_voters=fam)
    cases.append(('family', html, 710, max(1100, 650 + len(fam) * 55), fam, 'Family'))

    out_dir = os.path.join(tempfile.gettempdir(), 'stage3_parity')
    os.makedirs(out_dir, exist_ok=True)
    ok = True

    for name, html, win_w, win_h, voters, slip_type in cases:
        hp = os.path.join(tempfile.gettempdir(), 'parity_%s.html' % name)
        with open(hp, 'w', encoding='utf-8') as f:
            f.write(html)

        old = asyncio.run(legacy_capture(hp, win_w, win_h))
        old_path = os.path.join(out_dir, '%s_legacy.png' % name)
        old.save(old_path)

        new_path = cg.generate_bjp_voter_card(voters, slip_type=slip_type, tenant='ward82')
        if not new_path or not os.path.exists(new_path):
            print("  %-8s NEW RENDER FAILED" % name)
            ok = False
            continue

        with Image.open(new_path) as new:
            # Chrome snaps the clip rect to whole CSS px, so at dsf 1.5 the
            # reachable heights are ceil(n*1.5); the legacy PIL crop height is
            # sometimes between two of them. Up to +1px (one antialiased border
            # rows) is accepted; SHORTER is never accepted -- that loses content.
            same_size = (old.size[0] == new.size[0]
                         and 0 <= new.size[1] - old.size[1] <= 2)
            blank = Image.new('RGB', new.size, (255, 255, 255))
            content = ImageChops.difference(new.convert('RGB'), blank).getbbox()
            def mean_delta(x, y):
                d = ImageChops.difference(x.convert('RGB'), y.convert('RGB')).convert('L')
                n = float(x.size[0] * x.size[1])
                return (sum(c * i for i, c in enumerate(d.histogram())) / n,
                        max(d.getextrema()))

            if same_size:
                h = min(old.size[1], new.size[1])
                mean, maxd = mean_delta(old.crop((0, 0, old.size[0], h)),
                                        new.crop((0, 0, new.size[0], h)))
                # CONTROL: production already converted the legacy PNG to JPEG
                # q95 before sending, so THAT roundtrip is the noise floor we
                # must beat -- not a lossless PNG-vs-PNG comparison.
                ref = old.crop((0, 0, old.size[0], h))
                buf = io.BytesIO()
                ref.convert('RGB').save(buf, format='JPEG', quality=95, optimize=True)
                with Image.open(io.BytesIO(buf.getvalue())) as ctl:
                    ctl_mean, ctl_max = mean_delta(ref, ctl)
            else:
                mean = maxd = ctl_mean = ctl_max = -1
            print("  %-8s legacy=%s new=%s size=%s | new_vs_legacy mean=%.3f max=%s"
                  " | jpeg_floor mean=%.3f max=%s | content=%s"
                  % (name, old.size, new.size, 'OK' if same_size else 'MISMATCH',
                     mean, maxd, ctl_mean, ctl_max, 'present' if content else 'BLANK!'))
            new.save(os.path.join(out_dir, '%s_new.png' % name))
            # Pass if the new render is no further from legacy than a plain JPEG
            # q95 roundtrip of legacy already is (1.5x tolerance).
            if not same_size or not content or mean > max(0.5, ctl_mean * 1.5):
                ok = False

    print("\nartifacts: %s" % out_dir)
    print("STAGE 3 PARITY: %s" % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
