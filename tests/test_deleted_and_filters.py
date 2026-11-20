import unittest
from electionapp.config import parse_location_filter, parse_location_filter_extended
from electionapp.search.two_stage_search import build_stage1_query, build_count_query


class TestDeletedVotersAndMultiWardFilters(unittest.TestCase):
    def test_parse_location_filter_extended_multi_ward(self):
        """Test multi-ward parsing 'wards:5,6,7'."""
        w, p, ps, gp, wards = parse_location_filter_extended("wards:5,6,7", None)
        self.assertEqual(wards, [5, 6, 7])
        self.assertIsNone(w)
        self.assertIsNone(p)
        self.assertIsNone(ps)

    def test_parse_location_filter_extended_multi_part(self):
        """Test multi-part parsing 'parts:10,11,12'."""
        w, p, ps, gp, wards = parse_location_filter_extended(None, "parts:10,11,12")
        self.assertEqual(ps, [10, 11, 12])
        self.assertIsNone(w)
        self.assertIsNone(p)
        self.assertIsNone(wards)

    def test_parse_location_filter_backward_compat(self):
        """Ensure legacy parse_location_filter still unpacks 3-tuple cleanly."""
        w, p, ps = parse_location_filter("ward:5", None)
        self.assertEqual(w, 5)
        self.assertIsNone(p)
        self.assertIsNone(ps)

    def test_two_stage_search_include_deleted_by_default(self):
        """Search query must include deleted electors by default so they can be found."""
        sql, params = build_stage1_query(tenant_id=27, query="Ramesh")
        self.assertNotIn("LOWER(COALESCE(status, '')) <> 'deleted'", sql)

    def test_two_stage_search_exclude_deleted_when_requested(self):
        """Search query can exclude deleted electors when include_deleted=False."""
        sql, params = build_stage1_query(tenant_id=27, query="Ramesh", include_deleted=False)
        self.assertIn("LOWER(COALESCE(status, '')) <> 'deleted'", sql)

    def test_two_stage_search_ward_nos_query(self):
        """Search query applies ward_no = ANY(%(ward_nos)s) for multi-ward filter."""
        sql, params = build_stage1_query(tenant_id=27, ward_nos=[5, 6, 7])
        self.assertIn("ward_no = ANY(%(ward_nos)s)", sql)
        self.assertEqual(params["ward_nos"], [5, 6, 7])

    def test_count_query_include_deleted_by_default(self):
        """Count query must not exclude deleted voters by default."""
        sql, params = build_count_query(tenant_id=27)
        self.assertNotIn("LOWER(COALESCE(status, '')) <> 'deleted'", sql)


if __name__ == '__main__':
    unittest.main()
