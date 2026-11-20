import unittest
from unittest.mock import MagicMock, patch
from electionapp.repo.postgres_repo import PostgresVoterRepository

class TestMultiGpGuarantee(unittest.TestCase):
    """Guarantees that multi-ward campaigns (Zila Parishad, Panchayat Samiti) never collapse to single ward."""

    def test_zila_parishad_always_multi_gp_even_if_gp_null(self):
        repo = PostgresVoterRepository('gwala')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # 1st call for gram_panchayat column, 2nd call for panchayat_samiti column, 3rd call for tenant info
        mock_cur.fetchone.side_effect = [
            (1,),                     # gram_panchayat column exists
            (1,),                     # panchayat_samiti column exists
            ('ZILA_PARISHAD', 'gwala') # tenant info
        ]
        # Rows where gram_panchayat was NULL / General
        mock_cur.fetchall.return_value = [
            ('General', 1, 1200, [1, 2], ['98 - राजकीय उच्च माध्यमिक विद्यालय बाना का बास कमरा नं 1', '99 - राजकीय उच्च माध्यमिक विद्यालय बाना का बास कमरा नं 2']),
            ('General', 2, 1100, [3, 4], ['11 - राजकीय उच्च माध्यमिक विद्यालय खेतासर कमरा नं 1', '12 - राजकीय उच्च माध्यमिक विद्यालय खेतासर कमरा नं 2']),
        ]

        with patch.object(repo, 'get_connection', return_value=mock_conn), \
             patch.object(repo, '_resolve_tenant', return_value=(171, 'gwala')):
            res = repo.get_panchayat_hierarchy('gwala')

        # GUARANTEE: is_multi_gp must be TRUE
        self.assertTrue(res['is_multi_gp'])
        # GUARANTEE: Must have separated into distinct GPs based on booth names (e.g. बाना का बास, खेतासर)
        self.assertGreaterEqual(len(res['panchayats']), 2)
        gp_names = [p['gp_name_hi'] for p in res['panchayats']]
        self.assertTrue(any('बाना का बास' in n for n in gp_names))
        self.assertTrue(any('खेतासर' in n for n in gp_names))

    def test_panchayat_samiti_always_multi_gp(self):
        repo = PostgresVoterRepository('baytoo_ps_w1')
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.side_effect = [
            (1,),                     # gram_panchayat column exists
            (1,),                     # panchayat_samiti column exists
            ('PANCHAYAT_SAMITI', 'baytoo_ps_w1')
        ]
        mock_cur.fetchall.return_value = [
            ('Lilala', 1, 300, [9], ['9 - राजकीय उच्च माध्यमिक विद्यालय लीलाला']),
            ('Mahingoni', 2, 400, [11], ['11 - राजकीय उच्च प्राथमिक विद्यालय माहिगोणी']),
        ]

        with patch.object(repo, 'get_connection', return_value=mock_conn), \
             patch.object(repo, '_resolve_tenant', return_value=(104, 'baytoo_ps_w1')):
            res = repo.get_panchayat_hierarchy('baytoo_ps_w1')

        self.assertTrue(res['is_multi_gp'])
        self.assertEqual(len(res['panchayats']), 2)

if __name__ == '__main__':
    unittest.main()
