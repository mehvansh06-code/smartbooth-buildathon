"""
test_individual_phone_update.py
Unit tests verifying that voter phone updates are strictly isolated to the individual voter,
never propagating to or overwriting other family members or household members.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app


class TestIndividualPhoneUpdate(unittest.TestCase):
    def setUp(self):
        os.environ['FLASK_TESTING'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    @patch('electionapp.blueprints.history._get_tenant')
    def test_phone_update_strictly_updates_individual_voter_only(self, mock_get_tenant):
        """Verifies that updating a phone number updates ONLY the targeted voter, leaving household/family members untouched."""
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Target voter is in part 1, house 42
        mock_cur.fetchone.return_value = {
            'id': 101,
            'part_no': 1,
            'house_no': '42',
            'name_en': 'Ramesh',
            'name_hi': 'रमेश'
        }

        resp = self.client.post('/api/voter/update_phone', json={
            'voter_id': 101,
            'phone': '9829012345'
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['phone'], '9829012345')
        self.assertEqual(data['voter_id'], 101)
        self.assertEqual(data['updated_count'], 1)
        self.assertEqual(data['updated_ids'], [101])

        # Verify SQL statements executed on the cursor:
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        executed_params = [call[0][1] for call in mock_cur.execute.call_args_list]

        # 1. First query should be the single voter lookup
        self.assertIn("SELECT voter_id AS id", executed_sqls[0])

        # 2. Family lookup query (SELECT ... WHERE tenant_id = %s AND part_no = %s AND house_no = %s) MUST NOT BE EXECUTED
        for sql in executed_sqls:
            self.assertNotIn("part_no = %s AND house_no = %s", sql, "Family member query must NOT be executed")

        # 3. Update query must strictly target voter_id = %s OR legacy_id = %s with only this single voter
        update_voters_call = None
        for sql, params in zip(executed_sqls, executed_params):
            if "UPDATE voters SET contact_phone" in sql:
                update_voters_call = (sql, params)
                break

        self.assertIsNotNone(update_voters_call, "Must execute UPDATE voters query")
        sql, params = update_voters_call
        self.assertNotIn("ANY(%s)", sql, "Must not use array ANY(%s) updating multiple family members")
        self.assertEqual(params, ('9829012345', 3, 101, 101))

    @patch('electionapp.blueprints.history._get_tenant')
    def test_phone_clearing_strictly_affects_individual_only(self, mock_get_tenant):
        """Verifies clearing a voter's phone number only clears that voter's phone."""
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = {
            'id': 102,
            'part_no': 2,
            'house_no': '15',
            'name_en': 'Sita',
            'name_hi': 'सीता'
        }

        resp = self.client.post('/api/voter/update_phone', json={
            'voter_id': 102,
            'phone': ''
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['phone'], '')
        self.assertEqual(data['updated_count'], 1)
        self.assertEqual(data['updated_ids'], [102])

        # Verify update executed with empty string for voter 102 only
        update_voters_call = None
        for call in mock_cur.execute.call_args_list:
            sql, params = call[0][0], call[0][1]
            if "UPDATE voters SET contact_phone" in sql:
                update_voters_call = (sql, params)
                break

        self.assertIsNotNone(update_voters_call)
        self.assertEqual(update_voters_call[1], ('', 3, 102, 102))

    @patch('electionapp.blueprints.history._get_tenant')
    def test_invalid_phone_formats_rejected(self, mock_get_tenant):
        """Verifies invalid mobile numbers return 400 error."""
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        # Fewer than 10 digits
        resp = self.client.post('/api/voter/update_phone', json={
            'voter_id': 101,
            'phone': '98290'
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertFalse(data['success'])
        self.assertIn("10-अंकों", data['error'])

        # Starts with invalid digit (e.g. 2)
        resp2 = self.client.post('/api/voter/update_phone', json={
            'voter_id': 101,
            'phone': '2345678901'
        })
        self.assertEqual(resp2.status_code, 400)
        self.assertFalse(resp2.get_json()['success'])

    @patch('electionapp.blueprints.history._get_tenant')
    def test_missing_or_invalid_voter_id(self, mock_get_tenant):
        """Verifies validation on missing or invalid voter_id."""
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        resp = self.client.post('/api/voter/update_phone', json={
            'phone': '9829012345'
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('voter_id is required', resp.get_json()['error'])

        resp2 = self.client.post('/api/voter/update_phone', json={
            'voter_id': 'abc',
            'phone': '9829012345'
        })
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('Invalid voter_id', resp2.get_json()['error'])

    @patch('electionapp.blueprints.slip._get_tenant')
    def test_family_matching_works_with_distinct_phones(self, mock_get_tenant):
        """Verifies that clicking the family button (/api/family_smart) continues to match family
        members by part_no, house_no, family_id and relations, completely independent of phone numbers."""
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_repo.get_booths_dict.return_value = {1: {'part_no': 1, 'booth_name': 'Govt School'}}
        mock_repo.storage_backend = 'postgres'
        mock_get_tenant.return_value = ('ward82', mock_repo)

        # Primary voter: Ramesh (Phone: 9829011111)
        mock_repo.get_voters_by_ids.return_value = [{
            'id': 101,
            'voter_id': 101,
            'part_no': 1,
            'house_no': '42',
            'name_en': 'Ramesh Kumar',
            'name_hi': 'रमेश कुमार',
            'rel_name_en': 'Mohan Lal',
            'contact_phone': '9829011111',
            'family_id': 'FAM_42'
        }]

        # Family member Sita has a different phone (9829022222), Gopal has no phone
        mock_repo.get_family_voters.return_value = [
            {
                'id': 101,
                'voter_id': 101,
                'part_no': 1,
                'house_no': '42',
                'name_en': 'Ramesh Kumar',
                'contact_phone': '9829011111',
                'family_id': 'FAM_42'
            },
            {
                'id': 102,
                'voter_id': 102,
                'part_no': 1,
                'house_no': '42',
                'name_en': 'Sita Devi',
                'contact_phone': '9829022222',
                'family_id': 'FAM_42'
            },
            {
                'id': 103,
                'voter_id': 103,
                'part_no': 1,
                'house_no': '42',
                'name_en': 'Gopal Kumar',
                'contact_phone': None,
                'family_id': 'FAM_42'
            }
        ]

        resp = self.client.get('/api/family_smart?voter_id=101&tenant=ward82')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()

        # Verify all 3 members are matched into the family
        direct_members = data.get('direct', [])
        self.assertEqual(len(direct_members), 3)

        member_ids = [m['id'] for m in direct_members]
        self.assertIn(101, member_ids)
        self.assertIn(102, member_ids)
        self.assertIn(103, member_ids)

        # Verify each family member preserves their own individual phone number
        phone_map = {m['id']: m.get('contact_phone') for m in direct_members}
        self.assertEqual(phone_map[101], '9829011111')
        self.assertEqual(phone_map[102], '9829022222')
        self.assertIsNone(phone_map[103])


if __name__ == '__main__':
    unittest.main()
