"""
test_voter_sentiment_edit_card.py
Comprehensive unit tests verifying the Voter Sentiment marking feature in the
Edit Card component, including backend API persistence, batch updates,
card presentation styles, and template markup parity.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from electionapp import create_app


class TestVoterSentimentEditCard(unittest.TestCase):
    def setUp(self):
        os.environ['FLASK_TESTING'] = '1'
        self.app = create_app('ward82', storage_backend='postgres')
        self.client = self.app.test_client()

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_single_voter_sentiment_update_100_pakka(self, mock_get_tenant):
        """Verifies saving 100% Pakka sentiment for a single voter."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_id': 501,
            'voter_category': 100,
            'contact_phone': '9829012345',
            'caste_tag': 'जाट'
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['updated']['voter_category'], 100)
        self.assertEqual(data['voter_id'], 501)

        # Verify SQL execution
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        executed_params = [call[0][1] for call in mock_cur.execute.call_args_list]

        update_call = None
        for sql, params in zip(executed_sqls, executed_params):
            if "UPDATE voters SET" in sql:
                update_call = (sql, params)
                break

        self.assertIsNotNone(update_call, "Must execute UPDATE voters query")
        sql, params = update_call
        self.assertIn("voter_category = %s", sql)
        self.assertIn("category_updated_at = NOW()", sql)
        self.assertIn(100, params)

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_single_voter_sentiment_update_swing_and_opposition(self, mock_get_tenant):
        """Verifies string and numeric representations for 50-50 and opposition."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Test swing: '50_50' string converts to 50
        resp1 = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_id': 502,
            'voter_category': '50_50'
        })
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.get_json()['updated']['voter_category'], 50)

        # Test opposition: 'opposition' string converts to -1
        resp2 = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_id': 503,
            'voter_category': 'opposition'
        })
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.get_json()['updated']['voter_category'], -1)

    @patch('electionapp.blueprints.campaign._get_tenant')
    def test_batch_voters_sentiment_update(self, mock_get_tenant):
        """Verifies bulk sentiment update across multiple voter IDs."""
        mock_repo = MagicMock()
        mock_repo.storage_backend = 'postgres'
        mock_repo._resolve_tenant.return_value = (3, 'ward82')
        mock_get_tenant.return_value = ('ward82', mock_repo)

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_repo.get_connection.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        target_ids = [601, 602, 603]
        resp = self.client.post('/api/voter/profile/update?tenant=ward82', json={
            'voter_ids': target_ids,
            'voter_category': 100
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['count'], 3)
        self.assertEqual(data['voter_ids'], target_ids)
        self.assertEqual(data['updated']['voter_category'], 100)

        # Verify SQL uses ANY(%s) for batch
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        executed_params = [call[0][1] for call in mock_cur.execute.call_args_list]

        update_call = None
        for sql, params in zip(executed_sqls, executed_params):
            if "UPDATE voters SET" in sql:
                update_call = (sql, params)
                break

        self.assertIsNotNone(update_call)
        sql, params = update_call
        self.assertIn("ANY(%s)", sql)

    def test_template_markup_has_sentiment_buttons(self):
        """Verifies that templates contain the 4 sentiment buttons and setVpmSentiment handler."""
        templates_dir = os.path.join(PLATFORM_ROOT, 'templates')

        for tmpl_file in [os.path.join('modals', '_voter_profile_modal.html')]:
            path = os.path.join(templates_dir, tmpl_file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            self.assertIn('setVpmSentiment(100)', content, f"{tmpl_file} missing Pakka button")
            self.assertIn('setVpmSentiment(50)', content, f"{tmpl_file} missing 50-50 button")
            self.assertIn('setVpmSentiment(-1)', content, f"{tmpl_file} missing Opposition button")
            self.assertIn('setVpmSentiment(0)', content, f"{tmpl_file} missing Neutral button")
            self.assertIn('data-cat="100"', content, f"{tmpl_file} missing data-cat=100")
            self.assertIn('vpm-sentiment-btn', content, f"{tmpl_file} missing vpm-sentiment-btn class")

    def test_voter_cards_script_has_left_accent_and_badges(self):
        """Verifies that voter_cards.js constructs sentiment left-border accent classes and badges."""
        js_path = os.path.join(PLATFORM_ROOT, 'static', 'js', 'voter_cards.js')
        with open(js_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check left border accent classes
        self.assertIn('border-l-emerald-500', content, "Missing emerald accent for 100% Pakka")
        self.assertIn('border-l-amber-500', content, "Missing amber accent for 50-50")
        self.assertIn('border-l-rose-500', content, "Missing rose accent for opposition")
        self.assertIn('${sentimentBorderClass}', content, "Missing sentimentBorderClass insertion in card")

        # Check sentiment badges
        self.assertIn('🟢 100% पक्का', content)
        self.assertIn('🟡 50-50', content)
        self.assertIn('🔴 विरोधी', content)

        # Check table view integration
        self.assertIn('Political Sentiment Badge for Table', content)

        # Check CSV export
        self.assertIn('राजनैतिक रुझान (Sentiment)', content)


if __name__ == '__main__':
    unittest.main()
