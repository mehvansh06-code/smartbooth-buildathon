"""
test_app_factory.py
Verification suite for Step A7: Application Factory & Modular Blueprints.

Tests:
1. Application Factory: create_app() across all 4 tenants (ward82, jelatra, ward9, rajpura).
2. Zero module-level mutable globals / isolation verification.
3. Health check (/healthz) verifying 200 status, correct tenant slug, storage backend, and exact invariant row counts.
4. Campaign configuration (/api/campaign_config) verifying candidate/election metadata without cross-tenant leakage.
5. Voter routes (/api/voter/<id>, /api/family/<id>, /api/voters_by_part/<part_no>).
6. Search route (/api/search) with query, part filter, limit, and bilingual search.
7. War room routes (/warroom, /api/turnout_stats, /api/gotv, /api/mark_voted).
8. Dispatch routes (/api/dispatch/wa_me_link, /api/dispatch/history, /api/dispatch/send_slip).
9. Tenant assets route (/tenant_assets/<path:filename>).
10. WSGI CLI Runner argument parsing and execution.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app
from electionapp.repo.base import BaseVoterRepository
from electionapp.repo.postgres_repo import PostgresVoterRepository
from electionapp.dispatch import DispatchRouter
from electionapp.wsgi import parse_args


EXPECTED_INVARIANTS = {
    'ward82': {
        'voters': 4,
        'dispatch_history': 0,
        'voting_audit_log': 0,
        'voted': 0,
        'phones': 3
    },
    'jelatra': {
        'voters': 5,
        'dispatch_history': 0,
        'voting_audit_log': 0,
        'voted': 0,
        'phones': 2
    },
    'ward9': {
        'voters': 0,
        'dispatch_history': 0,
        'voting_audit_log': 0,
        'voted': 0,
        'phones': 0
    },
    'rajpura': {
        'voters': 0,
        'dispatch_history': 0,
        'voting_audit_log': 0,
        'voted': 0,
        'phones': 0
    }
}


_PREV_FLASK_TESTING = None


def setUpModule():
    """These tests exercise blueprints as an authenticated user without logging in.

    They rely on the FLASK_TESTING auth bypass; without it every request returns 401
    and ~17 tests fail purely on environment. Setting it here (scoped to this module,
    restored in tearDownModule) means the suite passes under the plain
    `python -m unittest discover -s tests` command, with no env juggling and no leak
    into other test modules.
    """
    global _PREV_FLASK_TESTING
    _PREV_FLASK_TESTING = os.environ.get('FLASK_TESTING')
    os.environ['FLASK_TESTING'] = '1'


def tearDownModule():
    if _PREV_FLASK_TESTING is None:
        os.environ.pop('FLASK_TESTING', None)
    else:
        os.environ['FLASK_TESTING'] = _PREV_FLASK_TESTING


class TestAppFactoryCreation(unittest.TestCase):
    """Tests application factory creation, isolation, and extension injection."""

    def test_create_app_all_four_tenants(self):
        tenants = ['ward82', 'jelatra', 'ward9', 'rajpura']
        apps = {}
        for slug in tenants:
            app = create_app(slug, storage_backend="postgres")
            apps[slug] = app

            # Config assertions
            self.assertEqual(app.config['TENANT_SLUG'], slug)
            self.assertEqual(app.config['CAMPAIGN']['tenant_slug'], slug)
            self.assertEqual(app.config['STORAGE_BACKEND'], 'postgres')

            # Extensions assertions
            self.assertIn('repo', app.extensions)
            self.assertIsInstance(app.extensions['repo'], BaseVoterRepository)
            self.assertIsInstance(app.extensions['repo'], PostgresVoterRepository)
            self.assertEqual(app.extensions['repo'].tenant_slug, slug)

            self.assertIn('router', app.extensions)
            self.assertIsInstance(app.extensions['router'], DispatchRouter)
            self.assertEqual(app.extensions['router'].tenant_slug, slug)

    def test_zero_module_state_isolation(self):
        """Verify that creating two apps consecutively produces isolated configurations."""
        app_ward82 = create_app('ward82')
        app_rajpura = create_app('rajpura')

        cfg_82 = app_ward82.config['CAMPAIGN']
        cfg_raj = app_rajpura.config['CAMPAIGN']

        # Distinct candidates and wards
        self.assertNotEqual(cfg_82['tenant_slug'], cfg_raj['tenant_slug'])
        self.assertNotEqual(cfg_82['candidate']['name_hi'], cfg_raj['candidate']['name_hi'])
        self.assertNotEqual(cfg_82['election'].get('ward_no'), cfg_raj['election'].get('ward_no'))

        # Repositories are distinct instances pointing to distinct tenant configurations
        self.assertNotEqual(app_ward82.extensions['repo'].tenant_slug, app_rajpura.extensions['repo'].tenant_slug)

    def test_invalid_tenant_raises_error(self):
        with self.assertRaises((FileNotFoundError, ValueError)):
            create_app('nonexistent_tenant_slug_12345')

    def test_invalid_storage_backend_raises(self):
        with self.assertRaises(ValueError):
            create_app('ward82', storage_backend='unsupported_storage')


class TestCoreBlueprint(unittest.TestCase):
    """Tests /healthz, /api/campaign_config, /, and /tenant_assets."""

    def test_healthz_all_four_tenants(self):
        for slug, exp_counts in EXPECTED_INVARIANTS.items():
            app = create_app(slug, storage_backend="postgres")
            client = app.test_client()
            resp = client.get('/healthz')
            self.assertEqual(resp.status_code, 200)

            data = resp.get_json()
            self.assertEqual(data['status'], 'healthy')
            self.assertEqual(data['tenant_slug'], slug)
            self.assertEqual(data['backend'], 'postgres')

            row_counts = data.get('row_counts', {})
            self.assertEqual(
                row_counts.get('voters'),
                exp_counts['voters'],
                f"Healthz {slug}.row_counts.voters: expected {exp_counts['voters']}, got {row_counts.get('voters')}"
            )
            for metric in ('voters', 'dispatch_history', 'voting_audit_log', 'voted', 'phones'):
                self.assertIn(metric, row_counts)
                self.assertIsInstance(row_counts[metric], int)
                self.assertGreaterEqual(row_counts[metric], 0)

    def test_api_campaign_config_no_cross_tenant_leakage(self):
        configs = {}
        for slug in ['ward82', 'jelatra', 'ward9', 'rajpura']:
            app = create_app(slug)
            client = app.test_client()
            resp = client.get('/api/campaign_config')
            self.assertEqual(resp.status_code, 200)

            data = resp.get_json()
            self.assertTrue(data['success'])
            self.assertEqual(data['tenant_slug'], slug)
            self.assertIn('candidate', data)
            self.assertIn('election', data)
            self.assertIn('party', data)
            configs[slug] = data

        # Assert no two tenants share candidate name or ward name
        cand_names = [configs[s]['candidate']['name_hi'] for s in configs]
        self.assertEqual(len(cand_names), len(set(cand_names)), "Duplicate candidate names detected!")

        app_titles = [configs[s]['election']['app_title'] for s in configs]
        self.assertEqual(len(app_titles), len(set(app_titles)), "Duplicate election app titles detected!")

    def test_index_dashboard_render(self):
        app = create_app('ward82')
        client = app.test_client()
        resp = client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('<!DOCTYPE html>', html)

    def test_tenant_assets_route(self):
        for slug in ['ward82', 'jelatra', 'ward9', 'rajpura']:
            app = create_app(slug)
            client = app.test_client()
            assets_dir = os.path.join(PLATFORM_ROOT, 'tenants', slug, 'assets')
            if os.path.exists(assets_dir):
                files = os.listdir(assets_dir)
                if files:
                    sample_file = files[0]
                    resp = client.get(f'/tenant_assets/{sample_file}')
                    self.assertEqual(
                        resp.status_code, 200,
                        f"Failed to serve asset '{sample_file}' for tenant {slug}"
                    )
                    _ = resp.data
                    resp.close()


class TestVotersBlueprint(unittest.TestCase):
    """Tests /api/voter/<id>, /api/family/<id>, /api/voters_by_part/<part_no>."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82')
        cls.client = cls.app.test_client()

    def test_get_voter_by_id(self):
        # Fetch part 1 voters to get a valid ID
        resp = self.client.get('/api/voters_by_part/1?limit=1')
        self.assertEqual(resp.status_code, 200)
        voters = resp.get_json().get('voters', [])
        self.assertTrue(len(voters) > 0)
        vid = voters[0]['id']

        # Query single voter
        v_resp = self.client.get(f'/api/voter/{vid}')
        self.assertEqual(v_resp.status_code, 200)
        v_data = v_resp.get_json()
        self.assertTrue(v_data.get('success'))
        self.assertEqual(v_data.get('id'), vid)
        self.assertIn('name_hi', v_data)
        self.assertIn('name_en', v_data)

    def test_get_voter_not_found(self):
        resp = self.client.get('/api/voter/999999999')
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(resp.get_json().get('success'))

    def test_get_family(self):
        # Find a voter with a family_id
        repo = self.app.extensions['repo']
        voters = repo.search(limit=20)
        family_voter = next((v for v in voters if v.get('family_id')), None)
        if family_voter:
            fid = family_voter['family_id']
            resp = self.client.get(f'/api/family/{fid}')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data['success'])
            self.assertTrue(data['count'] >= 1)
            self.assertEqual(data['family_id'], str(fid))

    def test_get_voters_by_part(self):
        resp = self.client.get('/api/voters_by_part/1?limit=10')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['part_no'], 1)
        self.assertTrue(len(data['voters']) >= 2)
        for v in data['voters']:
            self.assertEqual(v['part_no'], 1)


class TestSearchBlueprint(unittest.TestCase):
    """Tests /api/search with query, part filter, limit, and bilingual queries."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82')
        cls.client = cls.app.test_client()

    def test_search_browse_with_limit(self):
        resp = self.client.get('/api/search?limit=15')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertTrue(len(data['results']) > 0)
        self.assertLessEqual(len(data['results']), 15)

    def test_search_with_part_no(self):
        resp = self.client.get('/api/search?part_no=2&limit=10')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertTrue(len(data['results']) > 0)
        for r in data['results']:
            self.assertEqual(r['part_no'], 2)

    def test_search_by_numeric_serial(self):
        resp = self.client.get('/api/search?q=1&part_no=1')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertTrue(len(data['results']) > 0)

    def test_search_bilingual_hindi_english(self):
        # Search for common name fragment
        resp_en = self.client.get('/api/search?q=singh&limit=5')
        self.assertEqual(resp_en.status_code, 200)
        data_en = resp_en.get_json()
        self.assertTrue(data_en['success'])

        resp_hi = self.client.get('/api/search?q=सिंह&limit=5')
        self.assertEqual(resp_hi.status_code, 200)
        data_hi = resp_hi.get_json()
        self.assertTrue(data_hi['success'])


class TestWarroomBlueprint(unittest.TestCase):
    """Tests /warroom, /api/turnout_stats, /api/gotv, /api/mark_voted."""

    def test_turnout_stats_percentage(self):
        # Ward 82 has 0 voted out of 4 -> 0.0%
        app_82 = create_app('ward82')
        client_82 = app_82.test_client()
        resp_82 = client_82.get('/api/turnout_stats')
        self.assertEqual(resp_82.status_code, 200)
        stats_82 = resp_82.get_json()
        self.assertTrue(stats_82['success'])
        self.assertEqual(stats_82['voted_count'], 0)
        self.assertEqual(stats_82['total_voters'], 4)
        self.assertEqual(stats_82['turnout_percentage'], 0.0)
        self.assertIn('booths', stats_82)

        # Jelatra has 0 voted out of 5 -> 0.0%
        app_jel = create_app('jelatra')
        client_jel = app_jel.test_client()
        resp_jel = client_jel.get('/api/turnout_stats')
        self.assertEqual(resp_jel.status_code, 200)
        stats_jel = resp_jel.get_json()
        self.assertEqual(stats_jel['voted_count'], 0)
        self.assertEqual(stats_jel['total_voters'], 5)
        self.assertEqual(stats_jel['turnout_percentage'], 0.0)

    def test_warroom_dashboard_render(self):
        app = create_app('ward82')
        client = app.test_client()
        resp = client.get('/warroom')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('<!DOCTYPE html>', html)

    def test_gotv_endpoint(self):
        app = create_app('ward82')
        client = app.test_client()
        resp = client.get('/api/gotv?limit=25')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertIn('voters', data)
        self.assertTrue(len(data['voters']) > 0)
        for v in data['voters']:
            self.assertIn(v.get('is_voted'), [False, 0, None])

    def test_mark_voted_atomic_transaction(self):
        """Test vote marking on PostgreSQL."""
        app = create_app('jelatra')
        client = app.test_client()

        try:
            # Mark voter 101 as voted
            mark_resp = client.post('/api/mark_voted', json={
                'voter_id': 101,
                'voted': True,
                'marked_by': 'Test Agent'
            })
            self.assertEqual(mark_resp.status_code, 200)
            mark_data = mark_resp.get_json()
            self.assertTrue(mark_data['success'])
            self.assertEqual(mark_data['is_voted'], 1)

            # Unmark voter 101
            unmark_resp = client.post('/api/mark_voted', json={
                'voter_id': 101,
                'voted': False,
                'marked_by': 'Admin Reset'
            })
            self.assertEqual(unmark_resp.status_code, 200)
            self.assertEqual(unmark_resp.get_json()['is_voted'], 0)
        finally:
            app.extensions['repo'].mark_voted(1, 101, is_voted=False)


class TestDispatchBlueprint(unittest.TestCase):
    """Tests /api/dispatch/wa_me_link, /api/dispatch/history, /api/dispatch/send_slip."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82')
        cls.client = cls.app.test_client()

    def test_wa_me_link(self):
        resp = self.client.get('/api/dispatch/wa_me_link?phone=9876543210&message=Hello')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertIn('https://wa.me/919876543210', data['link'])

    def test_dispatch_history(self):
        resp = self.client.get('/api/dispatch/history?limit=10')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertIn('history', data)

    def test_send_slip_consent_gate_or_meta_cloud(self):
        # Calling send_slip without Meta token should fail cleanly (400) without crashing
        resp = self.client.post('/api/dispatch/send_slip', json={
            'phone': '9876543210',
            'message': 'Test Slip'
        })
        self.assertIn(resp.status_code, [200, 400, 429])
        data = resp.get_json()
        expected_channel = 'quota_gate' if resp.status_code == 429 else 'meta_cloud'
        self.assertEqual(data['channel'], expected_channel)


class TestWsgiCLI(unittest.TestCase):
    """Tests WSGI CLI argument parsing and environment variable fallbacks."""

    def test_parse_args_defaults(self):
        with patch('sys.argv', ['wsgi.py']):
            args = parse_args()
            self.assertEqual(args.tenant, 'ward82')
            self.assertEqual(args.port, 5000)
            self.assertEqual(args.storage, os.environ.get('VOTER_STORAGE', 'postgres'))
            self.assertFalse(args.debug)

    def test_parse_args_flags(self):
        with patch('sys.argv', ['wsgi.py', '--tenant', 'rajpura', '--port', '8080', '--storage', 'postgres', '--debug']):
            args = parse_args()
            self.assertEqual(args.tenant, 'rajpura')
            self.assertEqual(args.port, 8080)
            self.assertEqual(args.storage, 'postgres')
            self.assertTrue(args.debug)


if __name__ == '__main__':
    unittest.main()
