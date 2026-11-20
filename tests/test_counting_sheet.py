import unittest
from unittest.mock import MagicMock
from electionapp.services.counting_sheet_service import (
    get_wards_counting_data,
    render_counting_sheet_html,
    generate_counting_sheet_excel
)

class TestCountingSheetService(unittest.TestCase):
    def setUp(self):
        self.mock_cfg = {
            "candidate": {
                "name_hi": "चेतन सकलेचा",
                "name_en": "Chetan Saklecha",
                "symbol": "स्लेट"
            },
            "party": {
                "symbol_name_hi": "स्लेट",
                "ballot_serial": "2",
                "slogan": "विकास, निष्ठा और जनसेवा ही संकल्प"
            },
            "election": {
                "ward_name_hi": "ग्राम पंचायत मंडावरिया",
                "ward_name_en": "Gram Panchayat Mandawariya",
                "district": "AJMER"
            },
            "panchayat_samiti": "ARAIN PANCHAYAT SAMITI",
            "booths": {
                "1": {"name": "रा.उ.मा.वि. मंडावरिया कमरा 3", "area": "केरियां का रास्ता"},
                "2": {"name": "रा.उ.मा.वि. मंडावरिया कमरा 3", "area": "झाड़ोल का रास्ता"}
            }
        }
        
    def test_get_wards_counting_data_fallback(self):
        # Test fallback when DB cursor fails or is sqlite-like
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"ward_no": 1, "part_no": 1, "total": 206, "active": 198, "voted": 0},
            {"ward_no": 2, "part_no": 2, "total": 336, "active": 327, "voted": 0}
        ]
        mock_conn.cursor.return_value = mock_cur
        
        data = get_wards_counting_data(mock_conn, 30, self.mock_cfg)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["ward_no"], 1)
        self.assertEqual(data[0]["total_electors"], 198)
        self.assertEqual(data[0]["school"], "रा.उ.मा.वि. मंडावरिया")
        self.assertEqual(data[0]["room_no"], "कमरा 3")

    def test_render_counting_sheet_html(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"ward_no": 1, "part_no": 1, "total": 206, "active": 198, "voted": 0}
        ]
        mock_conn.cursor.return_value = mock_cur
        
        html = render_counting_sheet_html(mock_conn, 30, "mandawariya_sarpanch", self.mock_cfg)
        self.assertIn("चेतन सकलेचा", html)
        self.assertIn("स्लेट", html)
        self.assertIn("मतगणना सारणी", html)
        self.assertIn("50-मतपत्र बंडल टैली", html)

    def test_generate_counting_sheet_excel(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {"ward_no": 1, "part_no": 1, "total": 206, "active": 198, "voted": 0}
        ]
        mock_conn.cursor.return_value = mock_cur
        
        excel_stream = generate_counting_sheet_excel(mock_conn, 30, "mandawariya_sarpanch", self.mock_cfg)
        self.assertIsNotNone(excel_stream)
        excel_bytes = excel_stream.read()
        self.assertTrue(len(excel_bytes) > 1000)
        # Verify ZIP header for .xlsx
        self.assertTrue(excel_bytes.startswith(b'PK'))

if __name__ == '__main__':
    unittest.main()
