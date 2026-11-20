"""
test_karyakarta_magic_link.py
Comprehensive automated test suite for Worker-Specific Magic Link (Zero-Password Field Auth)
"""

import unittest
from electionapp import create_app


class TestKaryakartaMagicLink(unittest.TestCase):
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
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'

    def test_magic_link_full_lifecycle(self):
        self._login_as_admin()

        # 1. Add a new Karyakarta and verify access_token is auto-generated
        add_res = self.client.post('/api/karyakartas/add', json={
            'name': 'Ramesh Kumar Village Worker',
            'phone': '9829011223',
            'booth_no': 5,
            'area': 'Ward 82 Village North'
        })
        self.assertEqual(add_res.status_code, 200)
        data = add_res.get_json()
        self.assertTrue(data.get('success'))
        k_id = data.get('id')
        token = data.get('access_token')
        magic_link = data.get('magic_link')

        self.assertIsNotNone(k_id)
        self.assertIsNotNone(token)
        self.assertTrue(len(token) >= 24)
        self.assertEqual(magic_link, f"/k/{token}")

        # 2. Assign a voter to this karyakarta
        assign_res = self.client.post('/api/karyakartas/assign', json={
            'voter_ids': [3513],
            'karyakarta_id': k_id
        })
        self.assertEqual(assign_res.status_code, 200)

        # 3. Simulate village worker opening the Magic Link on a fresh phone (clear session/cookies)
        worker_client = self.app.test_client()
        k_res = worker_client.get(f"/k/{token}", follow_redirects=False)

        # Must redirect (302) to /my-voters?tenant=ward82
        self.assertEqual(k_res.status_code, 302)
        self.assertIn('/my-voters', k_res.headers.get('Location', ''))
        self.assertIn('tenant=ward82', k_res.headers.get('Location', ''))

        # Verify cookie is set with long max_age
        cookie_header = k_res.headers.get('Set-Cookie', '')
        self.assertIn('voter_tenant=ward82', cookie_header)

        # 4. Verify worker's session variables are strictly scoped
        with worker_client.session_transaction() as sess:
            self.assertEqual(sess.get('role'), 'karyakarta')
            self.assertEqual(sess.get('role_tier'), 4)
            self.assertEqual(sess.get('karyakarta_id'), k_id)
            self.assertEqual(sess.get('full_name'), 'Ramesh Kumar Village Worker')
            self.assertEqual(sess.get('assigned_booth'), 5)
            self.assertEqual(sess.get('tenant_slug'), 'ward82')
            self.assertEqual(sess.get('karyakarta_token'), token)
            self.assertEqual(sess.get('allowed_modules'), ['my_voters'])

        # 5. Idempotent GET: Verify that tapping the link again (e.g. WhatsApp crawler or reopened tab) works
        k_res_2 = worker_client.get(f"/k/{token}", follow_redirects=False)
        self.assertEqual(k_res_2.status_code, 302)

        # 6. Fetch /api/karyakarta/my_voters as the worker
        my_voters_res = worker_client.get('/api/karyakarta/my_voters')
        self.assertEqual(my_voters_res.status_code, 200)
        mv_data = my_voters_res.get_json()
        self.assertTrue(mv_data.get('success'))
        self.assertTrue(any(v.get('id') == 3513 for v in mv_data.get('voters', [])))

        # 7. Rotate token via Admin API and verify instant revocation of old link
        rotate_res = self.client.post(f"/api/karyakartas/{k_id}/rotate_token")
        self.assertEqual(rotate_res.status_code, 200)
        r_data = rotate_res.get_json()
        new_token = r_data.get('access_token')
        self.assertNotEqual(token, new_token)

        # Old link MUST now return 404
        old_res = worker_client.get(f"/k/{token}")
        self.assertEqual(old_res.status_code, 404)

        # New link MUST work
        new_worker_client = self.app.test_client()
        new_res = new_worker_client.get(f"/k/{new_token}", follow_redirects=False)
        self.assertEqual(new_res.status_code, 302)

        # 8. Security Checks:
        # A. Karyakarta cannot access /karyakartas admin page (redirected to /my-voters)
        admin_page_res = new_worker_client.get('/karyakartas', follow_redirects=False)
        self.assertEqual(admin_page_res.status_code, 302)
        self.assertIn('/my-voters', admin_page_res.headers.get('Location', ''))

        # B. Karyakarta cannot access /api/karyakartas admin API
        admin_api_res = new_worker_client.get('/api/karyakartas')
        self.assertEqual(admin_api_res.status_code, 403)

        # C. IDOR Protection: Passing karyakarta_id=9999 still only returns current worker's voters
        idor_res = new_worker_client.get('/api/karyakarta/my_voters?karyakarta_id=9999')
        self.assertEqual(idor_res.status_code, 200)
        self.assertTrue(any(v.get('id') == 3513 for v in idor_res.get_json().get('voters', [])))

        # D. Karyakarta cannot access /voting-day or /voters (redirects to /my-voters)
        vd_page_res = new_worker_client.get('/voting-day', follow_redirects=False)
        self.assertEqual(vd_page_res.status_code, 302)
        self.assertIn('/my-voters', vd_page_res.headers.get('Location', ''))

        voters_page_res = new_worker_client.get('/voters', follow_redirects=False)
        self.assertEqual(voters_page_res.status_code, 302)
        self.assertIn('/my-voters', voters_page_res.headers.get('Location', ''))

        # E. Karyakarta cannot mark votes (/api/vote/toggle -> 403 Forbidden)
        vote_res = new_worker_client.post('/api/vote/toggle', json={'voter_id': 3513, 'is_voted': 1})
        self.assertEqual(vote_res.status_code, 403)

        # F. Karyakarta cannot send WhatsApp (/api/dispatch/send_slip -> 403 Forbidden)
        wa_res = new_worker_client.post('/api/dispatch/send_slip', json={'voter_id': 3513, 'phone': '9829012345'})
        self.assertEqual(wa_res.status_code, 403)

        # G. Karyakarta CAN edit his assigned voter (phone, sentiment, caste_tag)
        edit_res = new_worker_client.post('/api/voter/profile/update', json={
            'voter_id': 3513,
            'contact_phone': '9829012345',
            'voter_category': 100,
            'caste_tag': 'विश्नोई',
            'notes': 'Verified at house'
        })
        self.assertEqual(edit_res.status_code, 200)
        self.assertTrue(edit_res.get_json().get('success'))

        # H. Karyakarta CANNOT edit unassigned voters (/api/voter/profile/update -> 403)
        unassigned_edit_res = new_worker_client.post('/api/voter/profile/update', json={
            'voter_id': 999999,
            'contact_phone': '9829012345'
        })
        self.assertEqual(unassigned_edit_res.status_code, 403)

        # 9. Deactivate worker and verify instant revocation of active session
        toggle_res = self.client.post(f"/api/karyakartas/{k_id}/toggle_status")
        self.assertEqual(toggle_res.status_code, 200)
        t_data = toggle_res.get_json()
        self.assertFalse(t_data.get('is_active'))

        # Deactivated link returns 404
        deact_res = self.app.test_client().get(f"/k/{new_token}")
        self.assertEqual(deact_res.status_code, 404)

        # Active worker session is immediately rejected on next request
        evicted_res = new_worker_client.get('/api/karyakarta/my_voters')
        self.assertEqual(evicted_res.status_code, 401)

        # Cleanup
        self.client.post(f"/api/karyakartas/delete/{k_id}")

    def test_invalid_tokens_return_404(self):
        # Random non-existent token
        res = self.client.get('/k/completely_bogus_token_12345678')
        self.assertEqual(res.status_code, 404)

        # Short malformed token
        short_res = self.client.get('/k/abc')
        self.assertEqual(short_res.status_code, 404)

    def test_my_voters_page_html_rendering_and_components(self):
        self._login_as_admin()

        # Create worker
        add_res = self.client.post('/api/karyakartas/add', json={
            'name': 'Devendra Singh Panna Pramukh',
            'phone': '9829099887',
            'booth_no': 3,
            'area': 'Ward 82 South'
        })
        k_id = add_res.get_json()['id']
        token = add_res.get_json()['access_token']

        # Worker opens link
        worker_client = self.app.test_client()
        login_res = worker_client.get(f'/k/{token}', follow_redirects=False)
        self.assertEqual(login_res.status_code, 302)

        # Worker requests /my-voters HTML
        page_res = worker_client.get('/my-voters?tenant=ward82')
        self.assertEqual(page_res.status_code, 200)
        self.assertIn(b'karyaSearchInput', page_res.data)
        self.assertIn(b'karyaOfflineBanner', page_res.data)
        self.assertIn(b'karyaPwaBanner', page_res.data)
        self.assertIn(b'Devendra Singh Panna Pramukh', page_res.data)
        self.assertIn(b'Booth #3', page_res.data)
        self.assertIn(b'escapeHtml', page_res.data)
        self.assertIn(b'app_state.js', page_res.data)

        # Cleanup
        self.client.post(f'/api/karyakartas/delete/{k_id}')

    def test_karyakarta_send_whatsapp_dispatches_template(self):
        """Verify /api/karyakartas/<k_id>/send_whatsapp dispatches magic link with approved template."""
        self._login_as_admin()
        add_res = self.client.post('/api/karyakartas/add', json={
            'name': 'Suresh Karyakarta',
            'phone': '9829012345',
            'booth_no': 2,
            'area': 'Booth 2'
        })
        k_id = add_res.get_json()['id']
        token = add_res.get_json()['access_token']

        from unittest.mock import MagicMock, patch
        mock_res = MagicMock()
        mock_res.__enter__.return_value = mock_res
        with patch('electionapp.whatsapp_quota.reserve', return_value=mock_res):
            with patch('meta_whatsapp.send_karyakarta_magic_link_template', return_value=(True, {'messages': [{'id': 'test_msg_1'}]}, None)) as mock_send:
                send_res = self.client.post(f'/api/karyakartas/{k_id}/send_whatsapp')
                self.assertEqual(send_res.status_code, 200)
                data = send_res.get_json()
                self.assertTrue(data.get('success'))
                mock_send.assert_called_once()
                call_kwargs = mock_send.call_args[1]
                self.assertIn(token, call_kwargs.get('magic_url', ''))
                self.assertEqual(call_kwargs.get('worker_name'), 'Suresh Karyakarta')

        # Cleanup
        self.client.post(f'/api/karyakartas/delete/{k_id}')

    def test_meta_whatsapp_send_karyakarta_signature_and_no_nameerror(self):
        """Verify meta_whatsapp.send_karyakarta_magic_link_template does not crash with NameError."""
        import meta_whatsapp
        from unittest.mock import MagicMock, patch

        mock_credit = MagicMock()
        mock_provider = MagicMock()
        mock_provider.id = "mock_prov"
        mock_provider.api_version = "v21.0"
        mock_provider.phone_number_id = "12345"
        mock_provider.template_name = "info_update_new_0101"
        mock_provider.template_lang = "en"
        mock_provider.get_auth_headers.return_value = {"Authorization": "Bearer test"}

        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"messages": [{"id": "wamid.test"}]}

        with patch.object(meta_whatsapp, '_assert_billable'):
            with patch.object(meta_whatsapp.provider_pool, 'get_available_providers', return_value=[mock_provider]):
                with patch.object(meta_whatsapp._session, 'post', return_value=mock_res):
                    ok, res, err = meta_whatsapp.send_karyakarta_magic_link_template(
                        phone='9829012345',
                        worker_name='Test Worker',
                        assignment_summary='Booth 1',
                        magic_url='https://smartbooth.vedmap.com/k/abc',
                        credit=mock_credit
                    )
                    self.assertTrue(ok)
                    self.assertIsNone(err)

    def test_karyakarta_send_whatsapp_exception_returns_json_500(self):
        """Verify unexpected dispatch exception returns JSON 500 instead of crashing to HTML."""
        self._login_as_admin()
        add_res = self.client.post('/api/karyakartas/add', json={
            'name': 'Crash Test Worker',
            'phone': '9829012345',
            'booth_no': 2,
            'area': 'Booth 2'
        })
        k_id = add_res.get_json()['id']

        from unittest.mock import MagicMock, patch
        mock_res = MagicMock()
        mock_res.__enter__.return_value = mock_res
        with patch('electionapp.whatsapp_quota.reserve', return_value=mock_res):
            with patch('meta_whatsapp.send_karyakarta_magic_link_template', side_effect=RuntimeError("Meta network failure")):
                res = self.client.post(f'/api/karyakartas/{k_id}/send_whatsapp')
                self.assertEqual(res.status_code, 500)
                data = res.get_json()
                self.assertIsNotNone(data)
                self.assertFalse(data.get('success'))
                self.assertIn("Meta network failure", data.get('error', ''))

        self.client.post(f'/api/karyakartas/delete/{k_id}')


if __name__ == '__main__':
    unittest.main()
