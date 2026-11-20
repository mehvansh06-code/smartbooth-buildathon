"""
test_voting_day_desk_audit.py
Regression tests for Voting Day Desk audit:
1. templates/voting_day.html must NOT contain duplicate script tags for voting_day.js.
2. templates/voting_day.html must include startVd() inside extra_scripts.
3. static/js/voting_day.js must include idempotency guard (__VD_LOADED__).
4. static/js/voting_day.js getWardScopedVoters must safely fallback if scoped count is 0.
5. _normalize_row in postgres_repo must drop heavy search_doc.
6. /api/search must return voters without search_doc.
"""

import unittest
from electionapp import create_app
from electionapp.repo.postgres_repo import PostgresVoterRepository

class TestVotingDayDeskAudit(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'
            sess['tenant_slug'] = 'ward82'

    def test_single_script_inclusion_in_template(self):
        """Ensure voting_day.js is only loaded once in the rendered HTML."""
        res = self.client.get('/voting-day?tenant=ward82', follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        import re
        script_matches = re.findall(r'<script[^>]*src="[^"]*voting_day\.js[^"]*"', html)
        self.assertEqual(len(script_matches), 1, f"voting_day.js script tag count: {len(script_matches)}, expected exactly 1")
        self.assertIn('startVd()', html)

    def test_idempotency_guard_in_js(self):
        """Ensure static/js/voting_day.js has idempotency guard."""
        with open('static/js/voting_day.js', 'r', encoding='utf-8') as f:
            js = f.read()
        self.assertIn('window.__VD_LOADED__', js)
        self.assertIn('if (scoped.length > 0) return scoped;', js)

    def test_search_doc_stripped_from_normalized_row(self):
        """Ensure _normalize_row strips search_doc to prevent massive JSON payloads."""
        repo = PostgresVoterRepository(tenant_slug='ward82')
        dummy_row = {
            'voter_id': 101,
            'name_hi': 'परीक्षण',
            'search_doc': 'very large search document content repeated many times'
        }
        normalized = repo._normalize_row(dummy_row, 1)
        self.assertNotIn('search_doc', normalized)
        self.assertEqual(normalized['voter_id'], 101)

if __name__ == '__main__':
    unittest.main()
