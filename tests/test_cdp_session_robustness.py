"""
test_cdp_session_robustness.py
Failure-mode tests for the persistent CDP session layer (Stage 4).

These are the risks that matter more than the speed win:
  * zombie tabs accumulating across worker recycles  (1 tab per port, forever)
  * a killed/restarted Chrome daemon wedging every worker
  * tab recycling at the render cap
  * concurrent renders from multiple threads

Requires at least one Chrome daemon on 9222/9223/9224; skips otherwise.
Run directly:  python tests/test_cdp_session_robustness.py
"""
import os
import sys
import json
import time
import socket
import unittest
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import electionapp.card_generator as cg
from electionapp import cdp_session
from electionapp.tenant_branding import get_tenant_branding

VOTER = {
    'id': 101, 'sl_no': 15, 'part_no': 82,
    'name_en': 'RAMESH CHOUDHARY', 'name_hi': 'रमेश चौधरी',
    'rel_name_en': 'SURESH CHOUDHARY', 'rel_name_hi': 'सुरेश चौधरी',
    'rel_type': 'पिता', 'age': 35, 'gender': 'M',
    'house_no': '45', 'epic_no': 'ABC1234567',
}


def up(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.3):
            return True
    except Exception:
        return False


def live_ports():
    return [p for p in cg.CHROME_CDP_PORTS if up(p)]


def page_targets(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port,
                                    timeout=2) as r:
            return [t for t in json.loads(r.read().decode())
                    if t.get('type') == 'page']
    except Exception:
        return []


def render(voters=None, slip_type='Single'):
    p = cg.generate_bjp_voter_card(voters or [VOTER], slip_type=slip_type,
                                   tenant='ward82')
    if p and os.path.exists(p):
        size = os.path.getsize(p)
        try:
            os.remove(p)
        except OSError:
            pass
        return size
    return 0


@unittest.skipIf(not live_ports(), "no Chrome CDP daemon on 9222/9223/9224")
class TestCdpSessionRobustness(unittest.TestCase):

    def setUp(self):
        get_tenant_branding('ward82')          # warm the branding cache
        cdp_session.shutdown()

    def tearDown(self):
        cdp_session.shutdown()

    def test_01_renders_produce_valid_slips(self):
        for _ in range(5):
            self.assertGreater(render(), 5000)

    def test_02_tab_count_stays_bounded(self):
        """One shell tab per port, no matter how many renders."""
        before = {p: len(page_targets(p)) for p in live_ports()}
        for _ in range(12):
            self.assertGreater(render(), 5000)
        for p in live_ports():
            added = len(page_targets(p)) - before[p]
            self.assertLessEqual(
                added, 1, "port %d leaked %d tabs (expected at most 1 shell tab)"
                % (p, added))

    def test_03_session_reuse_is_sticky(self):
        """Repeat renders reuse the same session rather than reopening."""
        for _ in range(6):
            render()
        st = cdp_session.stats()
        self.assertTrue(st, "no persistent session was established")
        self.assertTrue(any(v['renders'] >= 2 for v in st.values()),
                        "sessions are not being reused: %r" % st)

    def test_04_adopts_existing_tab_after_shutdown(self):
        """A 'recycled worker' must adopt the live tab, not mint a new one.

        This is the zombie-tab regression: keying tabs on pid would leak one
        tab per Gunicorn worker restart.
        """
        render()
        counts = {p: len(page_targets(p)) for p in live_ports()}
        cdp_session.shutdown()                  # simulate worker going away
        time.sleep(0.3)
        for _ in range(3):
            self.assertGreater(render(), 5000)
        for p in live_ports():
            self.assertLessEqual(
                len(page_targets(p)), counts[p] + 1,
                "port %d leaked a tab across a simulated worker recycle" % p)

    def test_05_recycles_at_render_cap(self):
        """Hitting TAB_MAX_RENDERS must rotate the tab, not grow it forever."""
        orig = cdp_session.TAB_MAX_RENDERS
        cdp_session.TAB_MAX_RENDERS = 3
        try:
            for _ in range(8):
                self.assertGreater(render(), 5000)
            for v in cdp_session.stats().values():
                self.assertLessEqual(v['renders'], 3,
                                     "tab exceeded the recycle cap: %r" % v)
            for p in live_ports():
                self.assertLessEqual(len(page_targets(p)), 2,
                                     "recycling leaked tabs on port %d" % p)
        finally:
            cdp_session.TAB_MAX_RENDERS = orig

    def test_06_survives_dead_connection(self):
        """A poisoned/closed websocket must self-heal on the next render."""
        # A render may legitimately succeed via the fallback ladder without
        # establishing a session -- e.g. it drew a port whose daemon is down.
        # Retry until a session actually exists before poisoning it, otherwise
        # this asserts something the renderer never promised.
        st = {}
        for _ in range(len(cg.CHROME_CDP_PORTS) * 3):
            self.assertGreater(render(), 5000)
            st = cdp_session.stats()
            if st:
                break
        if not st:
            self.skipTest("no port established a persistent session "
                          "(all daemons unreachable?)")
        with cdp_session._SESSIONS_LOCK:
            for sess in cdp_session._SESSIONS.values():
                sess.conn.alive = False         # simulate a dropped connection
        self.assertGreater(render(), 5000,
                           "renderer did not recover from a dead connection")

    def test_07_concurrent_renders(self):
        """4 threads, mirroring Gunicorn worker threads sharing the pool."""
        results, errors = [], []

        def worker():
            try:
                for _ in range(3):
                    results.append(render())
            except Exception as exc:
                errors.append(exc)

        ts = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in ts]
        [t.join(timeout=120) for t in ts]
        self.assertEqual(errors, [], "concurrent renders raised: %r" % errors)
        self.assertEqual(len(results), 12)
        self.assertTrue(all(r > 5000 for r in results),
                        "some concurrent renders produced truncated slips: %r" % results)

    def test_08_family_and_single_widths_do_not_bleed(self):
        """Alternating 610px and 710px layouts on a shared tab.

        Emulation overrides are target-scoped, so a cached width would render a
        family card into a single-slip viewport.
        """
        fam = [dict(VOTER, id=200 + i, sl_no=100 + i) for i in range(4)]
        from PIL import Image
        for _ in range(3):
            p1 = cg.generate_bjp_voter_card([VOTER], slip_type='Single', tenant='ward82')
            p2 = cg.generate_bjp_voter_card(fam, slip_type='Family', tenant='ward82')
            with Image.open(p1) as a, Image.open(p2) as b:
                self.assertEqual(a.size[0], 915, "single slip width bled")
                self.assertEqual(b.size[0], 1065, "family slip width bled")
            for p in (p1, p2):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_09_disabled_by_env(self):
        """SLIP_CDP_PERSISTENT=0 must fall back cleanly, still producing slips."""
        os.environ['SLIP_CDP_PERSISTENT'] = '0'
        try:
            self.assertFalse(cdp_session.available())
            self.assertGreater(render(), 5000,
                               "fallback path broke when persistent CDP was disabled")
        finally:
            os.environ.pop('SLIP_CDP_PERSISTENT', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
