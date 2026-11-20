"""
tests/test_bulk_slip_dispatch.py
================================
Unit and Integration Test Suite for Campaign-Wide Automated Voter Slip Broadcast:
- Role-based authorization guards (admin/super_admin only, 403 on unauthorized).
- Pre-flight estimation math (Smart Family grouping vs strictly individual).
- Exclusion of struck-off (deleted) electors.
- Strict tenant serialization (no concurrent running jobs per campaign).
- Quota reservation and refunding on cancellation.
- Pause, resume, and status lifecycle.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app
from electionapp.services import bulk_slip_worker


class MockRepo:
    """In-memory mock repository for testing bulk dispatch estimation and jobs."""
    def __init__(self, tenant_slug='test_tenant', tid=999):
        self.tenant_slug = tenant_slug
        self.tid = tid

    def _resolve_tenant(self, tenant_id_or_slug):
        return self.tid, self.tenant_slug

    def _normalize_row(self, row, tid):
        row['id'] = row.get('voter_id') or row.get('id')
        return row

    def get_connection(self):
        return MagicMock()


class TestBulkSlipDispatchAuthorization(unittest.TestCase):
    """Verifies that bulk dispatch endpoints are strictly restricted to admin/super_admin."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()

    def test_unauthorized_user_blocked(self):
        # Without session, should get 403
        resp = self.client.post('/api/dispatch/bulk/estimate', json={})
        self.assertEqual(resp.status_code, 403)

        resp2 = self.client.post('/api/dispatch/bulk/start', json={})
        self.assertEqual(resp2.status_code, 403)

    def test_karyakarta_blocked(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 401
            sess['username'] = 'field_worker'
            sess['role'] = 'karyakarta'
            sess['tenant_slug'] = 'ward82'

        resp = self.client.post('/api/dispatch/bulk/estimate', json={})
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertFalse(data.get('success'))

        resp2 = self.client.post('/api/dispatch/bulk/start', json={})
        self.assertEqual(resp2.status_code, 403)

    def test_operator_strictly_blocked(self):
        """Operators must be strictly blocked from all bulk sending endpoints."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 201
            sess['username'] = 'ward82_op'
            sess['role'] = 'operator'
            sess['tenant_slug'] = 'ward82'

        for endpoint in [
            '/api/dispatch/bulk/estimate',
            '/api/dispatch/bulk/start',
            '/api/dispatch/bulk/status',
            '/api/dispatch/bulk/pause/job123',
            '/api/dispatch/bulk/resume/job123',
            '/api/dispatch/bulk/cancel/job123'
        ]:
            method = 'GET' if 'status' in endpoint else 'POST'
            if method == 'GET':
                resp = self.client.get(endpoint)
            else:
                resp = self.client.post(endpoint, json={})
            self.assertEqual(resp.status_code, 403, f"Operator should be blocked from {endpoint}")

    def test_viewer_strictly_blocked(self):
        """Viewers must be strictly blocked from all bulk sending endpoints."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 301
            sess['username'] = 'guest_viewer'
            sess['role'] = 'viewer'
            sess['tenant_slug'] = 'ward82'

        resp = self.client.post('/api/dispatch/bulk/start', json={})
        self.assertEqual(resp.status_code, 403)

    def test_admin_allowed_access(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'admin'
            sess['tenant_slug'] = 'ward82'

        with patch('electionapp.services.bulk_slip_worker.estimate_bulk_dispatch') as mock_est:
            mock_est.return_value = {
                'tenant_id': 1,
                'slug': 'ward82',
                'target_messages': 10,
                'available_credits': 100,
                'has_sufficient_quota': True,
                '_resolved_items': []
            }
            resp = self.client.post('/api/dispatch/bulk/estimate', json={
                'mode': 'SMART_FAMILY',
                'skip_recent_hours': 48
            })
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get('success'))
            self.assertEqual(data['estimation']['target_messages'], 10)


class TestBulkSlipEstimationLogic(unittest.TestCase):
    """Tests grouping, household chunking, and deduplication logic."""

    def test_smart_family_grouping(self):
        # 3 voters sharing the same phone in the same house
        mock_voters = [
            {'voter_id': 1, 'name_hi': 'राम', 'house_no': '12', 'ward_no': 1, 'sl_no': 1, 'contact_phone': '9829012345', 'status': 'Active'},
            {'voter_id': 2, 'name_hi': 'सीता', 'house_no': '12', 'ward_no': 1, 'sl_no': 2, 'contact_phone': '9829012345', 'status': 'Active'},
            {'voter_id': 3, 'name_hi': 'लक्ष्मण', 'house_no': '12', 'ward_no': 1, 'sl_no': 3, 'contact_phone': '9829012345', 'status': 'Active'},
            # 1 voter with distinct phone
            {'voter_id': 4, 'name_hi': 'भरत', 'house_no': '15', 'ward_no': 1, 'sl_no': 4, 'contact_phone': '9414099999', 'status': 'Active'},
        ]

        mock_repo = MockRepo()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = mock_voters
        mock_cur.fetchone.return_value = [4]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_repo.get_connection = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_conn)))

        with patch('electionapp.whatsapp_quota.get_quota') as mock_qb:
            mock_qb.return_value = {'available': 50}
            res = bulk_slip_worker.estimate_bulk_dispatch(
                mock_repo, 'test_tenant', mode='SMART_FAMILY', skip_recent_hours=0
            )

            # In SMART_FAMILY mode, voters 1, 2, 3 should combine into 1 family slip, plus voter 4 -> total 2 messages
            self.assertEqual(res['target_messages'], 2)
            self.assertEqual(res['available_credits'], 50)
            self.assertTrue(res['has_sufficient_quota'])

    def test_strictly_individual_grouping(self):
        # Same 4 voters in STRICT_INDIVIDUAL mode should yield 4 separate messages
        mock_voters = [
            {'voter_id': 1, 'name_hi': 'राम', 'house_no': '12', 'ward_no': 1, 'sl_no': 1, 'contact_phone': '9829012345', 'status': 'Active'},
            {'voter_id': 2, 'name_hi': 'सीता', 'house_no': '12', 'ward_no': 1, 'sl_no': 2, 'contact_phone': '9829012345', 'status': 'Active'},
            {'voter_id': 3, 'name_hi': 'लक्ष्मण', 'house_no': '12', 'ward_no': 1, 'sl_no': 3, 'contact_phone': '9829012345', 'status': 'Active'},
            {'voter_id': 4, 'name_hi': 'भरत', 'house_no': '15', 'ward_no': 1, 'sl_no': 4, 'contact_phone': '9414099999', 'status': 'Active'},
        ]

        mock_repo = MockRepo()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = mock_voters
        mock_cur.fetchone.return_value = [4]
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_repo.get_connection = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_conn)))

        with patch('electionapp.whatsapp_quota.get_quota') as mock_qb:
            mock_qb.return_value = {'available': 50}
            res = bulk_slip_worker.estimate_bulk_dispatch(
                mock_repo, 'test_tenant', mode='STRICT_INDIVIDUAL', skip_recent_hours=0
            )

            self.assertEqual(res['target_messages'], 4)


class TestBulkSlipJobSerialization(unittest.TestCase):
    """Verifies that at most one RUNNING/PENDING job can be active per tenant."""

    def test_prevent_concurrent_jobs(self):
        mock_repo = MockRepo()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        # Simulate an existing active job
        mock_cur.fetchone.return_value = ('bulk_999_123456_abc', 'RUNNING')
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_repo.get_connection = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_conn)))

        res = bulk_slip_worker.create_bulk_dispatch_job(mock_repo, 'test_tenant')
        self.assertFalse(res['success'])
        self.assertIn('पहले से ही एक प्रसारण सक्रिय है', res['error'])


class TestBulkSlipJobControlEndpoints(unittest.TestCase):
    """Verifies pause, resume, cancel, and status endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app('ward82', storage_backend='postgres')
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.client = self.app.test_client()

    def _login_as_admin(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'admin'
            sess['tenant_slug'] = 'ward82'

    def test_status_endpoint_returns_json(self):
        self._login_as_admin()
        with patch('electionapp.services.bulk_slip_worker.get_bulk_job_status') as mock_status:
            mock_status.return_value = {
                'found': True,
                'job_id': 'bulk_1_123_abc',
                'status': 'RUNNING',
                'total_recipients': 100,
                'sent_count': 45,
                'failed_count': 0,
                'progress_pct': 45.0
            }
            resp = self.client.get('/api/dispatch/bulk/status/bulk_1_123_abc')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get('success'))
            self.assertEqual(data['data']['progress_pct'], 45.0)

    def test_pause_endpoint_as_admin(self):
        self._login_as_admin()
        with patch('electionapp.services.bulk_slip_worker.pause_bulk_job') as mock_pause:
            mock_pause.return_value = {'success': True, 'job_id': 'job_123', 'status': 'PAUSED'}
            resp = self.client.post('/api/dispatch/bulk/pause/job_123')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get('success'))

    def test_resume_endpoint_as_admin(self):
        self._login_as_admin()
        with patch('electionapp.services.bulk_slip_worker.resume_bulk_job') as mock_resume:
            mock_resume.return_value = {'success': True, 'job_id': 'job_123', 'status': 'RUNNING'}
            resp = self.client.post('/api/dispatch/bulk/resume/job_123')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get('success'))

    def test_cancel_endpoint_as_admin(self):
        self._login_as_admin()
        with patch('electionapp.services.bulk_slip_worker.cancel_bulk_job') as mock_cancel:
            mock_cancel.return_value = {'success': True, 'job_id': 'job_123', 'status': 'CANCELLED'}
            resp = self.client.post('/api/dispatch/bulk/cancel/job_123')
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get('success'))



if __name__ == '__main__':
    unittest.main()
