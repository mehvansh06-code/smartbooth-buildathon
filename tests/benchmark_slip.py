"""
benchmark_slip.py
Per-stage latency harness + pixel-parity gate for voter-slip rendering.

Unlike tests/benchmark_card.py, this NEVER uploads to the Meta Graph API
unless --upload is passed explicitly, so it is safe to run many times.

Usage:
    python tests/benchmark_slip.py --runs 30 --layout single,family
    python tests/benchmark_slip.py --parity
    python tests/benchmark_slip.py --start-daemon --runs 20
"""
import os
import sys
import json
import time
import socket
import argparse
import platform
import statistics
import subprocess
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from electionapp.tenant_branding import get_tenant_branding
from electionapp.card_templates import build_standard_single_html, build_standard_family_html
import electionapp.card_generator as cg

CDP_PORTS = cg.CHROME_CDP_PORTS

# Real Devanagari, including conjuncts and matras that break naive shapers.
FIXTURES = {
    'single': [{
        'id': 101, 'voter_id': 101, 'sl_no': 15, 'part_no': 82,
        'name_en': 'RAMESH CHOUDHARY', 'name_hi': 'रमेश चौधरी',
        'rel_name_en': 'SURESH CHOUDHARY', 'rel_name_hi': 'सुरेश चौधरी',
        'rel_type': 'पिता', 'age': 35, 'gender': 'M',
        'house_no': '45', 'epic_no': 'ABC1234567',
    }],
    'shaping': [{
        'id': 102, 'voter_id': 102, 'sl_no': 16, 'part_no': 82,
        'name_en': 'SHRIKRISHNA DWARIKAPRASAD', 'name_hi': 'श्रीकृष्ण द्वारिकाप्रसाद ऋद्धि',
        'rel_name_en': 'TRILOKCHAND SRIVASTAVA', 'rel_name_hi': 'त्रिलोकचन्द्र श्रीवास्तव',
        'rel_type': 'पिता', 'age': 61, 'gender': 'M',
        'house_no': '112/अ', 'epic_no': 'RLJ0465054',
    }],
}
FIXTURES['family'] = [
    dict(FIXTURES['single'][0], id=200 + i, voter_id=200 + i, sl_no=100 + i,
         name_hi=n, name_en=e)
    for i, (n, e) in enumerate([
        ('आनंद', 'Aanand'), ('विजय प्रकाश सांखला', 'Vijay Prakash Sankhla'),
        ('अविनाश', 'Avinash'), ('श्रीकृष्ण द्वारिकाप्रसाद', 'Shrikrishna Dwarikaprasad'),
        ('राधा देवी', 'Radha Devi'), ('मोहनलाल', 'Mohanlal'),
        ('सीता', 'Sita'), ('घनश्याम', 'Ghanshyam'),
    ])
]


def daemon_up(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
            return True
    except Exception:
        return False


def start_daemon(port):
    """Spawns a headless Chrome daemon mirroring scripts/start_chrome.sh flags."""
    exe = cg.find_chrome_executable()
    if not exe:
        raise SystemExit("No Chrome/Chromium executable found.")
    profile = os.path.join(tempfile.gettempdir(), 'bench_chrome_prof_%d' % port)
    os.makedirs(profile, exist_ok=True)
    proc = subprocess.Popen([
        exe, '--headless=new', '--remote-debugging-port=%d' % port,
        '--remote-debugging-address=127.0.0.1', '--no-sandbox', '--disable-gpu',
        '--disable-dev-shm-usage', '--hide-scrollbars', '--no-first-run',
        '--no-default-browser-check',
        '--allow-file-access-from-files',   # REQUIRED: slip assets are file:// URLs
        '--disable-extensions', '--disable-sync', '--disable-background-networking',
        '--user-data-dir=%s' % profile,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if daemon_up(port):
            print("  started Chrome daemon on %d (pid %d)" % (port, proc.pid))
            return proc
        time.sleep(0.1)
    proc.kill()
    raise SystemExit("Chrome daemon on %d did not come up." % port)


def build_html(layout, branding):
    """Times HTML construction only. Returns (html, ms)."""
    voters = FIXTURES[layout]
    t0 = time.perf_counter()
    if len(voters) == 1:
        html = build_standard_single_html(
            voters[0], {'name': 'Test Booth'}, branding, tenant='ward82')
    else:
        html = build_standard_family_html(
            voters, branding, 1, 1, slip_type='Family',
            tenant='ward82', all_voters=voters)
    return html, (time.perf_counter() - t0) * 1000


def render_once(layout, tenant='ward82'):
    """One full generate_bjp_voter_card call. Returns (path, ms)."""
    voters = FIXTURES[layout]
    slip_type = 'Single' if len(voters) == 1 else 'Family'
    t0 = time.perf_counter()
    path = cg.generate_bjp_voter_card(voters, slip_type=slip_type, tenant=tenant)
    return path, (time.perf_counter() - t0) * 1000


def pct(vals, q):
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round((q / 100.0) * (len(vals) - 1)))))
    return vals[k]


def summarise(name, vals):
    if not vals:
        return {'stage': name, 'n': 0}
    return {
        'stage': name, 'n': len(vals),
        'min': round(min(vals), 1), 'p50': round(pct(vals, 50), 1),
        'p95': round(pct(vals, 95), 1), 'max': round(max(vals), 1),
        'mean': round(statistics.fmean(vals), 1),
    }


def run_bench(args):
    branding = get_tenant_branding('ward82')
    up = [p for p in CDP_PORTS if daemon_up(p)]
    report = {
        'platform': platform.platform(),
        'asset_mode': os.environ.get('SLIP_ASSET_MODE', 'file'),
        'daemons_up': up,
        'layouts': {},
    }
    print("asset mode : %s" % report['asset_mode'])
    print("daemons up : %s" % (up or 'NONE (renders will take the slow CLI fallback)'))
    if not up:
        print("  hint: re-run with --start-daemon, or on the server:")
        print("        bash scripts/start_chrome.sh 9222")

    for layout in [x.strip() for x in args.layout.split(',')]:
        if layout not in FIXTURES:
            print("  !! unknown layout '%s', skipping" % layout)
            continue
        html, _ = build_html(layout, branding)
        html_bytes = len(html.encode('utf-8'))

        build_ms, total_ms, sizes = [], [], []
        cold = None
        for i in range(args.runs + 1):      # run 0 is warm-up, reported separately
            _, bms = build_html(layout, branding)
            path, tms = render_once(layout)
            if path and os.path.exists(path):
                sizes.append(os.path.getsize(path))
                if not args.keep:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            if i == 0:
                cold = round(tms, 1)
                continue
            build_ms.append(bms)
            total_ms.append(tms)

        report['layouts'][layout] = {
            'html_bytes': html_bytes,
            'cold_ms': cold,
            'out_bytes_p50': int(pct(sizes, 50)) if sizes else 0,
            'stages': [summarise('html_build', build_ms),
                       summarise('render_total', total_ms)],
        }
        print("\n--- %s ---" % layout)
        print("  html_bytes   : %s" % format(html_bytes, ','))
        print("  cold run     : %s ms" % cold)
        print("  out_bytes p50: %s" % format(int(pct(sizes, 50)) if sizes else 0, ','))
        for st in report['layouts'][layout]['stages']:
            if st.get('n'):
                print("  %-14s n=%-4d p50=%8s p95=%8s mean=%8s max=%8s  (ms)"
                      % (st['stage'], st['n'], st['p50'], st['p95'], st['mean'], st['max']))

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print("\nwrote %s" % args.json)
    return report


def run_parity(args):
    """Renders each layout under data: and file:// asset modes and diffs them.

    These must be pixel-identical: same image bytes, same decoder, only the URL
    scheme differs. Any delta means the file:// URL failed to resolve -- which
    Chrome does SILENTLY (blank image box, no exception), so we also assert the
    output is not blank.
    """
    from PIL import Image, ImageChops
    ok = True
    out_dir = os.path.join(tempfile.gettempdir(), 'slip_parity')
    os.makedirs(out_dir, exist_ok=True)

    for layout in [x.strip() for x in args.layout.split(',')]:
        if layout not in FIXTURES:
            continue
        paths = {}
        for mode in ('data', 'file'):
            os.environ['SLIP_ASSET_MODE'] = mode
            cg._CUSTOM_BG_CACHE.clear()
            p, _ = render_once(layout)
            if not p or not os.path.exists(p):
                print("  %s/%s: RENDER FAILED" % (layout, mode))
                ok = False
                break
            dst = os.path.join(out_dir, "%s_%s.png" % (layout, mode))
            with open(p, 'rb') as a, open(dst, 'wb') as b:
                b.write(a.read())
            paths[mode] = dst
        os.environ.pop('SLIP_ASSET_MODE', None)
        if len(paths) != 2:
            continue

        with Image.open(paths['data']) as a, Image.open(paths['file']) as b:
            if a.size != b.size:
                print("  %s: SIZE MISMATCH %s vs %s  FAIL" % (layout, a.size, b.size))
                ok = False
                continue
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB'))
            bbox = diff.getbbox()
            maxd = max(hi for _, hi in diff.getextrema())
            blank = Image.new('RGB', b.size, (255, 255, 255))
            content = ImageChops.difference(b.convert('RGB'), blank).getbbox()
            print("  %s: size=%s max_delta=%d identical=%s content=%s"
                  % (layout, a.size, maxd,
                     'YES' if bbox is None else 'NO',
                     'present' if content else 'BLANK!'))
            if bbox is not None or not content:
                ok = False
            print("      %s\n      %s" % (paths['data'], paths['file']))

    print("\nPARITY: %s" % ('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=20)
    ap.add_argument('--layout', default='single,family')
    ap.add_argument('--parity', action='store_true')
    ap.add_argument('--start-daemon', action='store_true')
    ap.add_argument('--port', type=int, default=9222)
    ap.add_argument('--json', default='')
    ap.add_argument('--keep', action='store_true', help='keep rendered slips on disk')
    ap.add_argument('--upload', action='store_true',
                    help='also time the real Meta media upload (sends network traffic)')
    args = ap.parse_args()

    if args.upload and not os.environ.get('META_WA_ACCESS_TOKEN'):
        raise SystemExit("--upload requires META_WA_ACCESS_TOKEN to be set.")

    procs = []
    if args.start_daemon:
        # Start ALL pool ports: acquire_chrome_port round-robins across them,
        # and a dead port makes the render skip CDP for the CLI fallback.
        for port in CDP_PORTS:
            if not daemon_up(port):
                procs.append(start_daemon(port))
    try:
        if args.parity:
            return run_parity(args)
        run_bench(args)
        return 0
    finally:
        for pr in procs:
            pr.kill()
        if procs:
            print("  stopped %d benchmark Chrome daemon(s)" % len(procs))


if __name__ == '__main__':
    sys.exit(main() or 0)
