import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from electionapp.blueprints.slip import sanitize_slip_emojis
from electionapp.dispatch.wa_me import WaMeChannel

class TestCampaignProvisioningAndWhatsApp(unittest.TestCase):

    def test_sanitize_slip_emojis_removes_variation_selectors(self):
        # Slip text containing emoji with variation selector \ufe0f and composite emoji
        raw_slip = "🗳️ *मतदाता सूचना पर्ची* 👨‍👦\nमतदान अवश्य करें! 🇮🇳"
        sanitized = sanitize_slip_emojis(raw_slip)
        # Variation selector-16 (\ufe0f) should be stripped
        self.assertNotIn('\ufe0f', sanitized)
        # Composite emoji 👨‍👦 replaced by 👨
        self.assertNotIn('\U0001f468\u200d\U0001f466', sanitized)
        self.assertIn('👨', sanitized)
        self.assertIn('🗳', sanitized)

    def test_wa_me_link_encodes_special_characters(self):
        channel = WaMeChannel()
        phone = "9829012345"
        msg = "नाम: रमेश कुमार/सुरेश\nभाग सं: 14 #वोट"
        link = channel.generate_link(phone, msg)
        self.assertIn("https://wa.me/919829012345?text=", link)
        # Ensure '/' is encoded to '%2F' and '#' to '%23'
        self.assertIn("%2F", link)
        self.assertIn("%23", link)

    def test_multi_gp_ward_sql_clause_generation(self):
        # Verify the logic for constructing gp_wards filter
        rajasthan_dist = "DHOLPUR"
        rajasthan_gps = ["Chitora", "Bareh"]
        gp_wards = {"chitora": [1, 2, 3]}

        norm_gp_wards = {}
        for k, v in gp_wards.items():
            norm_gp_wards[str(k).strip().lower()] = v

        gp_clauses = []
        gp_sql_params = [rajasthan_dist.upper()]
        for matched_g in rajasthan_gps:
            specific_w = norm_gp_wards.get(str(matched_g).strip().lower())
            if specific_w:
                in_placeholders = ', '.join(['%s'] * len(specific_w))
                gp_clauses.append(f"(gram_panchayat = %s AND ward_no IN ({in_placeholders}))")
                gp_sql_params.append(matched_g)
                gp_sql_params.extend(specific_w)
            else:
                gp_clauses.append("(gram_panchayat = %s)")
                gp_sql_params.append(matched_g)

        where_gp_filter = f"district = %s AND ({' OR '.join(gp_clauses)})"
        self.assertIn("(gram_panchayat = %s AND ward_no IN (%s, %s, %s))", where_gp_filter)
        self.assertIn("(gram_panchayat = %s)", where_gp_filter)
        self.assertEqual(gp_sql_params, ['DHOLPUR', 'Chitora', 1, 2, 3, 'Bareh'])

if __name__ == '__main__':
    unittest.main()
