"""
test_reports_blueprint.py
Unit tests for SmartBooth Voter Reports, Print & Export Center.
"""

import unittest
from electionapp import create_app


class ReportsBlueprintTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('ward82', 'postgres')
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def test_reports_page_status_200(self):
        """Verify that GET /reports loads with status 200 and renders reports center."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        response = self.client.get('/reports?tenant=ward82')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'reports.js', response.data)
        self.assertIn(b'btnPresetParchi', response.data)

    def test_reports_preview_endpoint(self):
        """Verify that GET /api/reports/preview returns valid JSON with match counts."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        response = self.client.get('/api/reports/preview?tenant=ward82&preset=parchi')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertIn('total_matching', data)
        self.assertIn('voters', data)
        self.assertIn('page_estimate', data)

    def test_reports_print_html_parchi_density_12(self):
        """Verify that GET /api/reports/print_html generates 12-Up voter parchi HTML with cutting lines."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        response = self.client.get('/api/reports/print_html?tenant=ward82&type=parchi&density=12')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'grid-density-12', response.data)
        self.assertIn('✂ कटिंग लाइन'.encode('utf-8'), response.data)
        self.assertIn('parchi-card'.encode('utf-8'), response.data)

    def test_reports_print_html_mobile_sheet(self):
        """Verify that GET /api/reports/print_html generates mobile surveyor sheet with write-in boxes."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        response = self.client.get('/api/reports/print_html?tenant=ward82&type=mobile_sheet')
        self.assertEqual(response.status_code, 200)
        self.assertIn('मोबाइल नंबर प्रविष्टि सर्वे पत्रक'.encode('utf-8'), response.data)

    def test_reports_excel_export(self):
        """Verify that GET /api/reports/export/excel returns valid openpyxl spreadsheet stream."""
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'super_admin'

        response = self.client.get('/api/reports/export/excel?tenant=ward82')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertTrue(len(response.data) > 0)


if __name__ == '__main__':
    unittest.main()
