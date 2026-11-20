import unittest
from typing import Dict, Any, List

from electionapp.config import (
    get_cached_community_tags,
    clear_community_tags_cache,
    DEFAULT_COMMUNITY_CHIPS,
    COMMUNITY_SYNONYMS,
    COMMUNITY_LABELS
)


class MockCursor:
    def __init__(self, repo):
        self.repo = repo
        self._results = []

    def execute(self, query: str, params=None):
        q = query.strip()
        if "caste_tag, count(*)" in q:
            counts: Dict[str, int] = {}
            for v in self.repo.voters:
                tag = v.get('caste_tag')
                if tag and str(tag).strip():
                    t_clean = str(tag).strip()
                    counts[t_clean] = counts.get(t_clean, 0) + 1
            self._results = sorted([(t, c) for t, c in counts.items()], key=lambda x: -x[1])[:15]
        elif "name_en, name_hi" in q:
            self._results = [
                (v.get('name_en'), v.get('name_hi'), v.get('rel_name_en'), v.get('rel_name_hi'))
                for v in self.repo.voters
            ][:1000]
        else:
            self._results = []

    def fetchall(self):
        return self._results

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConn:
    def __init__(self, repo):
        self.repo = repo

    def cursor(self):
        return MockCursor(self.repo)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class MockPostgresRepo:
    """Mock repository implementing PostgresVoterRepository protocol without external dependencies."""
    def __init__(self):
        self.tenant_slug = "test_tenant"
        self.voters: List[Dict[str, Any]] = []

    def _resolve_tenant(self, tenant_slug=None):
        return (1, tenant_slug or "test_tenant")

    def get_connection(self):
        return MockConn(self)

    def add_voter(self, **kwargs):
        self.voters.append(kwargs)


class TestCampaignCommunityBadges(unittest.TestCase):
    def setUp(self):
        clear_community_tags_cache()
        self.repo = MockPostgresRepo()

    def tearDown(self):
        clear_community_tags_cache()

    def test_explicit_config_community_tags(self):
        cfg = {
            'community_tags': [
                {'tag': 'बिश्नोई', 'label': 'बिश्नोई (Bishnoi)'},
                {'tag': 'देवासी', 'label': 'देवासी (Dewasi)'}
            ]
        }
        tags = get_cached_community_tags(self.repo, "test_tenant", cfg)
        self.assertEqual(len(tags), 2)
        self.assertEqual(tags[0]['tag'], 'बिश्नोई')
        self.assertEqual(tags[1]['tag'], 'देवासी')

    def test_tagged_voters_canonicalization_and_dedup(self):
        # Seed tags with varied casing and synonyms: 'jat', 'जाट', 'Jaat'
        self.repo.add_voter(name_en='Ram Jat', name_hi='राम जाट', caste_tag='जाट')
        self.repo.add_voter(name_en='Shyam Jat', name_hi='श्याम जाट', caste_tag='jat')
        self.repo.add_voter(name_en='Geeta Jaat', name_hi='गीता जाट', caste_tag='Jaat')
        self.repo.add_voter(name_en='Pravin Prajapat', name_hi='प्रवीण प्रजापत', caste_tag='प्रजापत')
        self.repo.add_voter(name_en='Babu Bishnoi', name_hi='बाबू विश्नोई', caste_tag='बिश्नोई')

        tags = get_cached_community_tags(self.repo, "test_tenant", {})
        tag_names = [t['tag'] for t in tags]

        # Ensure 'जाट' is canonicalized and deduped into single entry
        self.assertEqual(tag_names.count('जाट'), 1)
        self.assertIn('प्रजापत', tag_names)
        self.assertIn('बिश्नोई', tag_names)

    def test_cold_start_surname_inference(self):
        # Seed voters with NO caste_tag, but names having distinct surnames
        self.repo.add_voter(name_en='Harchand Patel', name_hi='हरचंद पटेल', caste_tag=None)
        self.repo.add_voter(name_en='Dinesh Patel', name_hi='दिनेश पटेल', caste_tag=None)
        self.repo.add_voter(name_en='Mohan Bishnoi', name_hi='मोहन बिश्नोई', caste_tag=None)
        self.repo.add_voter(name_en='Suresh Bishnoi', name_hi='सुरेश बिश्नोई', caste_tag=None)
        self.repo.add_voter(name_en='Pabu Dewasi', name_hi='पाबू देवासी', caste_tag=None)
        self.repo.add_voter(name_en='Rana Rajput', name_hi='राणा राजपूत', caste_tag=None)

        tags = get_cached_community_tags(self.repo, "test_cold_tenant", {})
        tag_names = [t['tag'] for t in tags]

        self.assertIn('पटेल', tag_names)
        self.assertIn('बिश्नोई', tag_names)

    def test_cache_ttl_and_invalidation(self):
        self.repo.add_voter(caste_tag='बिश्नोई')

        tags1 = get_cached_community_tags(self.repo, "cache_tenant", {})
        self.assertIn('बिश्नोई', [t['tag'] for t in tags1])

        # Add new tag
        self.repo.add_voter(caste_tag='देवासी')

        # Without clearing cache, tags1 is returned
        tags2 = get_cached_community_tags(self.repo, "cache_tenant", {})
        self.assertEqual(len(tags1), len(tags2))

        # Clear cache
        clear_community_tags_cache("cache_tenant")
        tags3 = get_cached_community_tags(self.repo, "cache_tenant", {})
        self.assertIn('देवासी', [t['tag'] for t in tags3])

    def test_strictly_campaign_specific_no_generic_default_list(self):
        # Empty tenant should return empty list, NOT the generic 10 statewide default chips
        tags = get_cached_community_tags(self.repo, "empty_tenant", {})
        self.assertEqual(tags, [], "Expected empty list for tenant with no campaign tags or matching names")

    def test_no_artificial_padding_when_few_campaign_castes_exist(self):
        # Tenant with only 2 genuine tags should return exactly those 2 tags without padding
        self.repo.add_voter(name_en='A', name_hi='अ', caste_tag='देवासी')
        self.repo.add_voter(name_en='B', name_hi='ब', caste_tag='राजपूत')

        tags = get_cached_community_tags(self.repo, "small_gp_tenant", {})
        tag_names = [t['tag'] for t in tags]
        self.assertEqual(len(tags), 2, f"Expected exactly 2 campaign tags, got {len(tags)}: {tag_names}")
        self.assertIn('देवासी', tag_names)
        self.assertIn('राजपूत', tag_names)


if __name__ == '__main__':
    unittest.main()
