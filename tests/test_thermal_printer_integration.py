import unittest
import json
from electionapp import create_app

class TestThermalPrinterIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()

    def test_01_sarana_campaign_renders_thermal_printer_buttons(self):
        """Verifies that the sarana campaign renders the HOP-HL58 thermal print button."""
        res = self.client.get('/voters?tenant=sarana')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        
        # Verify sarana has the thermal printer elements
        self.assertIn('thermalPrintCurrentSlip()', html, "Sarana page must contain thermalPrintCurrentSlip() onclick handler")
        self.assertIn('ThermalPrinter.connect()', html, "Sarana page must contain ThermalPrinter.connect() gear button")
        self.assertIn('thermal-printer-status-badge', html, "Sarana page must contain thermal printer status badge")
        self.assertIn('पर्ची प्रिंट करें', html, "Sarana page must contain 'पर्ची प्रिंट करें' button text")

    def test_01b_mandawariya_campaign_renders_thermal_printer_buttons(self):
        """Verifies that the mandawariya campaign renders the HOP-HL58 thermal print button."""
        res = self.client.get('/voters?tenant=mandawariya_sarpanch')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        
        # Verify mandawariya has the thermal printer elements
        self.assertIn('thermalPrintCurrentSlip()', html, "Mandawariya page must contain thermalPrintCurrentSlip() onclick handler")
        self.assertIn('ThermalPrinter.connect()', html, "Mandawariya page must contain ThermalPrinter.connect() gear button")
        self.assertIn('thermal-printer-status-badge', html, "Mandawariya page must contain thermal printer status badge")
        self.assertIn('पर्ची प्रिंट करें', html, "Mandawariya page must contain 'पर्ची प्रिंट करें' button text")

    def test_02_other_campaigns_do_not_render_thermal_printer(self):
        """Verifies that other campaigns (e.g. jelatra) do NOT render thermal print button."""
        res = self.client.get('/voters?tenant=jelatra')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        
        # Verify jelatra does NOT have the thermal printer elements
        self.assertNotIn('thermalPrintCurrentSlip()', html, "Non-sarana campaign must not contain thermal print button")
        self.assertIn('printSlipCard()', html, "Non-sarana campaign must retain standard printSlipCard()")
        self.assertIn('प्रिंट कार्ड', html, "Non-sarana campaign must retain 'प्रिंट कार्ड'")

    def test_03_base_html_includes_thermal_printer_script(self):
        """Verifies thermal_printer.js is loaded in the base HTML."""
        res = self.client.get('/?tenant=sarana')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')
        self.assertIn('/static/js/thermal_printer.js', html, "thermal_printer.js must be imported in base HTML")

    def test_04_sarana_voter_data_availability_for_slip(self):
        """Verifies sarana voter data is queryable and has all fields needed for official slip."""
        res = self.client.get('/api/slip_text?id=7&tenant=sarana')
        # If voter id 7 exists or general search returns voters
        search_res = self.client.get('/api/search?q=7&tenant=sarana')
        self.assertEqual(search_res.status_code, 200)
        search_data = search_res.get_json()
        self.assertTrue(search_data.get('success', False))
        voters = search_data.get('results', [])
        self.assertGreater(len(voters), 0, "Sarana campaign must return search results for sl_no 7")
        
        voter = voters[0]
        # Check required official slip fields
        for field in ['sl_no', 'name_hi', 'rel_name_hi', 'age', 'house_no', 'epic_no']:
            self.assertIn(field, voter, f"Voter object must contain '{field}' for official slip")

if __name__ == '__main__':
    unittest.main()
