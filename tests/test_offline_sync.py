import unittest
from unittest.mock import MagicMock, patch
from flask import Flask
from electionapp.blueprints.voters import voters_bp


class TestOfflineSync(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test_secret'
        self.app.config['TESTING'] = True
        self.app.register_blueprint(voters_bp)
        self.client = self.app.test_client()

    @patch('electionapp.blueprints.voters.resolve_tenant')
    def test_offline_sync_empty(self, mock_resolve):
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (27, 'chhoti_virol')
        mock_resolve.return_value = ('chhoti_virol', mock_repo)

        res = self.client.post('/api/voter/offline_sync', json={'mutations': []})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['synced_ids'], [])
        self.assertEqual(data['count'], 0)

    @patch('electionapp.blueprints.voters.resolve_tenant')
    def test_offline_sync_profile_mutations(self, mock_resolve):
        mock_repo = MagicMock()
        mock_repo._resolve_tenant.return_value = (27, 'chhoti_virol')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_repo.get_connection.return_value = mock_conn
        mock_resolve.return_value = ('chhoti_virol', mock_repo)

        mutations = [
            {
                'id': 'mut_1001_55',
                'voter_id': 55,
                'payload': {
                    'contact_phone': '9829012345',
                    'caste_tag': 'Bishnoi',
                    'voter_category': 100,
                    'is_outside': True,
                    'notes': 'Pakka voter contacted'
                }
            },
            {
                'id': 'mut_1002_56',
                'voter_id': '56',
                'payload': {
                    'contact_phone': '+91 9414054321',
                    'is_voted': True
                }
            }
        ]

        res = self.client.post('/api/voter/offline_sync', json={'mutations': mutations})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['count'], 2)
        self.assertIn('mut_1001_55', data['synced_ids'])
        self.assertIn('mut_1002_56', data['synced_ids'])
        self.assertEqual(mock_cur.execute.call_count, 2)


if __name__ == '__main__':
    unittest.main()
