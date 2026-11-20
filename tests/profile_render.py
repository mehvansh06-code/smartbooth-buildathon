"""
profile_render.py
Attributes the non-CDP ("Python overhead") portion of generate_bjp_voter_card.

Instruments by monkeypatching the real call sites -- no production edits -- so
the numbers reflect the shipped code path exactly.

Usage:
    python tests/profile_render.py --runs 12
    python tests/profile_render.py --runs 12 --cprofile
"""
import os
import sys
import time
import socket
import argparse
import asyncio
import statistics
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import electionapp.card_generator as cg
import electionapp.tenant_branding as tb

TIMES = {}


def rec(key, dt_ms):
    TIMES.setdefault(key, []).append(dt_ms)


def timed(key, fn):
    def wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            rec(key, (time.perf_counter() - t0) * 1000)
    return wrapper


def install_probes():
    # --- branding / template build ---
    tb_get = tb.get_tenant_branding
    cg.get_tenant_branding = timed('branding_lookup', tb_get)

    for name in ('build_standard_single_html', 'build_standard_family_html',
                 'build_custom_bg_single_html', 'build_custom_bg_family_html'):
        if hasattr(cg, name):
            setattr(cg, name, timed('html_build', getattr(cg, name)))

    # --- port acquisition + redundant liveness probe ---
    cg.acquire_chrome_port = timed('acquire_chrome_port', cg.acquire_chrome_port)
    cg.release_chrome_port = timed('release_chrome_port', cg.release_chrome_port)

    orig_conn = socket.create_connection

    def conn(addr, *a, **kw):
        t0 = time.perf_counter()
        try:
            return orig_conn(addr, *a, **kw)
        finally:
            rec('socket_liveness_probe', (time.perf_counter() - t0) * 1000)
    socket.create_connection = conn

    # --- the two unpooled loopback HTTP calls ---
    orig_open = urllib.request.urlopen

    def uo(req, *a, **kw):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        key = 'http_json_new' if '/json/new' in url else (
            'http_json_close' if '/json/close' in url else 'http_other')
        t0 = time.perf_counter()
        try:
            return orig_open(req, *a, **kw)
        finally:
            rec(key, (time.perf_counter() - t0) * 1000)
    urllib.request.urlopen = uo

    # --- event loop setup/teardown + the CDP conversation itself ---
    orig_run = asyncio.run

    def arun(coro, **kw):
        t0 = time.perf_counter()
        try:
            return orig_run(coro, **kw)
        finally:
            rec('asyncio_run_total', (time.perf_counter() - t0) * 1000)
    asyncio.run = arun

    # --- post-capture disk re-read ---
    if hasattr(cg, '_is_card_valid'):
        cg._is_card_valid = timed('is_card_valid_disk_reread', cg._is_card_valid)

    # --- temp html write ---
    import builtins
    orig_builtin_open = builtins.open

    def bopen(file, mode='r', *a, **kw):
        is_tmp = isinstance(file, str) and 'tmp_' in os.path.basename(file) \
            and file.endswith('.html')
        is_out = isinstance(file, str) and os.path.basename(file).startswith('bjp_slip_')
        if not (is_tmp or is_out):
            return orig_builtin_open(file, mode, *a, **kw)
        t0 = time.perf_counter()
        try:
            return orig_builtin_open(file, mode, *a, **kw)
        finally:
            rec('tmp_html_write' if is_tmp else 'out_image_write',
                (time.perf_counter() - t0) * 1000)
    builtins.open = bopen


VOTER = {
    'id': 101, 'sl_no': 15, 'part_no': 82,
    'name_en': 'RAMESH CHOUDHARY', 'name_hi': 'रमेश चौधरी',
    'rel_name_en': 'SURESH CHOUDHARY', 'rel_name_hi': 'सुरेश चौधरी',
    'rel_type': 'पिता', 'age': 35, 'gender': 'M',
    'house_no': '45', 'epic_no': 'ABC1234567',
}


def pct(v, q):
    v = sorted(v)
    return v[max(0, min(len(v) - 1, int(round(q / 100.0 * (len(v) - 1)))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=12)
    ap.add_argument('--cprofile', action='store_true')
    args = ap.parse_args()

    up = [p for p in cg.CHROME_CDP_PORTS if _up(p)]
    print("daemons up: %s" % (up or 'NONE -- start them first, or this profiles the CLI fallback'))

    install_probes()

    totals = []
    for i in range(args.runs + 1):
        t0 = time.perf_counter()
        p = cg.generate_bjp_voter_card([VOTER], slip_type='Single', tenant='ward82')
        dt = (time.perf_counter() - t0) * 1000
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
        if i == 0:
            TIMES.clear()           # discard warm-up
            continue
        totals.append(dt)

    print("\n%-28s %5s %9s %9s %9s" % ("block", "n", "p50", "mean", "total/run"))
    print("-" * 66)
    rows = []
    for k, v in TIMES.items():
        per_run = sum(v) / float(len(totals))
        rows.append((per_run, k, len(v), pct(v, 50), statistics.fmean(v)))
    for per_run, k, n, p50, mean in sorted(rows, reverse=True):
        print("%-28s %5d %9.1f %9.1f %9.1f" % (k, n, p50, mean, per_run))
    print("-" * 66)
    print("%-28s %5d %9.1f %9.1f" % ("RENDER TOTAL", len(totals),
                                     pct(totals, 50), statistics.fmean(totals)))

    cdp = sum(TIMES.get('asyncio_run_total', [])) / float(len(totals))
    known = sum(sum(v) for k, v in TIMES.items()
                if k != 'asyncio_run_total') / float(len(totals))
    print("\nasyncio_run (CDP conversation incl. loop setup) : %7.1f ms/run" % cdp)
    print("instrumented non-CDP blocks                     : %7.1f ms/run" % known)
    print("unattributed (pure Python: hashing, uuid, regex,"
          " path ops, semaphores)      : %7.1f ms/run"
          % (statistics.fmean(totals) - cdp - known))

    if args.cprofile:
        import cProfile
        import pstats
        pr = cProfile.Profile()
        pr.enable()
        for _ in range(5):
            p = cg.generate_bjp_voter_card([VOTER], slip_type='Single', tenant='ward82')
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        pr.disable()
        print("\n--- cProfile, top 25 by cumulative ---")
        pstats.Stats(pr).sort_stats('cumulative').print_stats(25)


def _up(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.2):
            return True
    except Exception:
        return False


if __name__ == '__main__':
    main()
