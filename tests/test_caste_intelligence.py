import unittest
from electionapp.blueprints.caste import (
    CASTE_PRESETS,
    SURNAME_SYNONYMS,
    _household_token_pattern,
    _is_declared_family_member,
)


def _mishra_patterns():
    return [('mishra', _household_token_pattern('mishra'))]


class TestCasteFamilyClusterGuard(unittest.TestCase):
    """
    Regression coverage for the household-leak bug: searching "mishra" was
    pulling in anyone who merely shared a family_id/house number (e.g.
    "Sharif"/"Nasima"), with no check that they were actually connected to a
    real Mishra. _is_declared_family_member must require an actual declared
    link in the record, not just a shared address.
    """

    def test_unrelated_housemate_is_excluded(self):
        # Sharif/Nasima: share a family_id with a confirmed Mishra, but
        # nothing in their own record ties them to that Mishra.
        confirmed = [{
            'name_en': 'Ramesh Mishra', 'name_hi': '', 'rel_name_en': 'Suresh Mishra', 'rel_name_hi': '',
        }]
        candidate = {
            'name_en': 'Sharif', 'name_hi': '', 'rel_name_en': 'Abdul Rahim', 'rel_name_hi': '',
        }
        verified, reason = _is_declared_family_member(candidate, confirmed, _mishra_patterns())
        self.assertFalse(verified)
        self.assertIsNone(reason)

    def test_surname_less_child_included_via_declared_father(self):
        # Parsram, s/o Mohanram - neither name carries "Mishra", but Mohanram
        # himself is a confirmed direct match, and Parsram's declared father
        # field names him.
        confirmed = [{
            'name_en': 'Mohanram Mishra', 'name_hi': '', 'rel_name_en': 'Gangaram', 'rel_name_hi': '',
        }]
        candidate = {
            'name_en': 'Parsram', 'name_hi': '', 'rel_name_en': 'Mohanram', 'rel_name_hi': '',
        }
        verified, reason = _is_declared_family_member(candidate, confirmed, _mishra_patterns())
        self.assertTrue(verified)
        self.assertIn('Parent/Spouse', reason)

    def test_surname_less_parent_included_via_confirmed_childs_declared_father(self):
        # Reverse direction: Mohanram (the father, no surname printed) is
        # pulled in because his confirmed son Rajesh Mishra's declared father
        # field names him.
        confirmed = [{
            'name_en': 'Rajesh Mishra', 'name_hi': '', 'rel_name_en': 'Mohanram', 'rel_name_hi': '',
        }]
        candidate = {
            'name_en': 'Mohanram', 'name_hi': '', 'rel_name_en': 'Gangaram', 'rel_name_hi': '',
        }
        verified, reason = _is_declared_family_member(candidate, confirmed, _mishra_patterns())
        self.assertTrue(verified)
        self.assertIn('Parent/Spouse', reason)

    def test_surname_less_sibling_included_via_shared_declared_father(self):
        # Two siblings with no surname printed, sharing the same declared
        # father as a confirmed match - neither name matches "mishra" and
        # neither is directly the other's parent/child.
        confirmed = [{
            'name_en': 'Suresh Mishra', 'name_hi': '', 'rel_name_en': 'Gangaram', 'rel_name_hi': '',
        }]
        candidate = {
            'name_en': 'Ramesh', 'name_hi': '', 'rel_name_en': 'Gangaram', 'rel_name_hi': '',
        }
        verified, reason = _is_declared_family_member(candidate, confirmed, _mishra_patterns())
        self.assertTrue(verified)
        self.assertIn('Shared Declared Parent', reason)

    def test_fully_surname_less_untagged_lineage_stays_excluded(self):
        # Gangaram -> Mohanram -> Parsram with nobody ever confirmed as a
        # direct match (nobody carries "Mishra", nobody is caste_tag'd) -
        # there is no confirmed row to chain off of, so this stays excluded.
        # This documents the known data-completeness gap: it is NOT fixed by
        # treating shared housing as evidence.
        confirmed = []
        candidate = {
            'name_en': 'Parsram', 'name_hi': '', 'rel_name_en': 'Mohanram', 'rel_name_hi': '',
        }
        verified, reason = _is_declared_family_member(candidate, confirmed, _mishra_patterns())
        self.assertFalse(verified)
        self.assertIsNone(reason)

    def test_own_name_match_still_works(self):
        candidate = {
            'name_en': 'Ramesh Mishra', 'name_hi': '', 'rel_name_en': '', 'rel_name_hi': '',
        }
        verified, reason = _is_declared_family_member(candidate, [], _mishra_patterns())
        self.assertTrue(verified)
        self.assertIn('Name Match', reason)


class TestCasteIntelligence(unittest.TestCase):
    def test_bheel_presets_and_synonyms(self):
        # 1. Preset check
        self.assertIn("st_bheel", CASTE_PRESETS)
        st_info = CASTE_PRESETS["st_bheel"]
        self.assertIn("bheel", st_info["surnames"])
        self.assertIn("bhil", st_info["surnames"])
        self.assertIn("भील", st_info["surnames"])

        # 2. Synonym expansion check
        self.assertIn("bheel", SURNAME_SYNONYMS)
        self.assertIn("bhil", SURNAME_SYNONYMS)
        self.assertIn("भील", SURNAME_SYNONYMS)

        # Cross-script bidirectional resolution
        self.assertIn("भील", SURNAME_SYNONYMS["bheel"])
        self.assertIn("bheel", SURNAME_SYNONYMS["भील"])
        self.assertIn("bhil", SURNAME_SYNONYMS["bheel"])
        self.assertIn("bheel", SURNAME_SYNONYMS["bhil"])

    def test_garasiya_meena_synonyms(self):
        self.assertIn("garasiya", SURNAME_SYNONYMS)
        self.assertIn("गरासिया", SURNAME_SYNONYMS["garasiya"])
        self.assertIn("meena", SURNAME_SYNONYMS)
        self.assertIn("मीणा", SURNAME_SYNONYMS["meena"])

    def test_caste_presets_endpoint(self):
        from electionapp import create_app
        app = create_app('jelatra', 'postgres')
        app.config['TESTING'] = True
        client = app.test_client()
        res = client.get('/api/caste/presets')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('presets', data)
        self.assertIn('tagged_castes', data)
        self.assertIsInstance(data['presets'], list)
        self.assertIsInstance(data['tagged_castes'], list)

        # Check preset structure
        preset_keys = [p['key'] for p in data['presets']]
        self.assertIn('choudhary_patel', preset_keys)
        self.assertIn('rajput_darbar', preset_keys)

        choudhary = next(p for p in data['presets'] if p['key'] == 'choudhary_patel')
        self.assertIn('short_title', choudhary)
        self.assertEqual(choudhary['short_title'], 'चौधरी / पटेल')
    def test_caste_census_endpoint_and_breakdown(self):
        from electionapp import create_app
        app = create_app('jelatra', 'postgres')
        app.config['TESTING'] = True
        client = app.test_client()

        # Test standard ward_no=1
        res = client.get('/api/caste/census?query=&ward_no=1')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('caste_breakdown', data)
        self.assertIn('ward_caste_breakdown', data)
        self.assertIn('ward_breakdown', data)
        self.assertIn('voters', data)
        self.assertGreater(len(data.get('ward_caste_breakdown', {})), 0)

        # Test prefixed ward_no=ward:1 (from dropdowns)
        res_prefixed = client.get('/api/caste/census?query=&ward_no=ward:1')
        self.assertEqual(res_prefixed.status_code, 200)
        data_prefixed = res_prefixed.get_json()
        self.assertTrue(data_prefixed.get('success'))
        self.assertEqual(data_prefixed['total_voters'], data['total_voters'])
        self.assertEqual(data_prefixed['ward_caste_breakdown'], data['ward_caste_breakdown'])

        # Test booth prefixed part_no=part:1
        res_part = client.get('/api/caste/census?query=&part_no=part:1')
        self.assertEqual(res_part.status_code, 200)
        data_part = res_part.get_json()
        self.assertTrue(data_part.get('success'))
        self.assertGreater(len(data_part.get('ward_caste_breakdown', {})), 0)


class TestCasteAutoTagger(unittest.TestCase):
    """Unit tests for automated caste auto-tagger engine and endpoint."""

    def test_direct_pattern_matching_and_exclusions(self):
        from electionapp.caste_auto_tagger import COMPILED_PATTERNS, EXCLUSIONS

        # 1. Rajput test
        rajput_pat = next(p for c, p in COMPILED_PATTERNS if c == 'राजपूत')
        self.assertTrue(rajput_pat.search('प्रताप सिंह राठौड़'))
        self.assertTrue(rajput_pat.search('सुशीला कंवर'))

        # 2. Bishnoi test
        bishnoi_pat = next(p for c, p in COMPILED_PATTERNS if c == 'बिश्नोई')
        self.assertTrue(bishnoi_pat.search('रमेश बिश्नोई'))
        self.assertTrue(bishnoi_pat.search('सुरेश खिलेरी'))

        # 3. Choudhary / Patel test
        patel_pat = next(p for c, p in COMPILED_PATTERNS if c == 'चौधरी / पटेल')
        self.assertTrue(patel_pat.search('दिनेश पटेल'))
        self.assertTrue(patel_pat.search('भरत राम गेंवा'))

        # 4. Muslim exclusion test: Anjali should not trigger Ali
        muslim_pat = next(p for c, p in COMPILED_PATTERNS if c == 'मुस्लिम')
        anjali_name = 'अंजलि शर्मा'
        is_matched = bool(muslim_pat.search(anjali_name))
        is_excluded = any(ex.search(anjali_name) for ex in EXCLUSIONS.get('मुस्लिम', []))
        self.assertTrue(is_excluded, "Anjali must match exclusion rule for Muslim pattern")

    def test_existing_user_tags_are_never_overridden(self):
        from electionapp.caste_auto_tagger import auto_tag_tenant
        from electionapp import create_app
        app = create_app('jelatra', 'postgres')
        repo = app.blueprints['caste']._get_tenant()[1] if hasattr(app.blueprints['caste'], '_get_tenant') else None
        
        # Test against Ward 82 test DB
        with app.test_request_context('/'):
            from electionapp.blueprints.caste import _get_tenant
            _, repo = _get_tenant()
            with repo.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM voters WHERE tenant_id = 3 AND caste_tag IS NOT NULL AND length(trim(caste_tag)) > 0;")
                original_tagged_count = cur.fetchone()[0]

            # Run auto tagger without force
            res = auto_tag_tenant(3, repo=repo, force=False, dry_run=False)
            
            with repo.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM voters WHERE tenant_id = 3 AND caste_tag IS NOT NULL AND length(trim(caste_tag)) > 0;")
                after_count = cur.fetchone()[0]

            self.assertGreaterEqual(after_count, original_tagged_count)
            self.assertEqual(res['already_tagged'], original_tagged_count)

    def test_auto_tag_endpoint(self):
        from electionapp import create_app
        app = create_app('jelatra', 'postgres')
        app.config['TESTING'] = True
        client = app.test_client()
        res = client.post('/api/caste/auto-tag', json={'dry_run': True})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('data', data)
        self.assertTrue(data['data'].get('dry_run'))
        self.assertIn('caste_breakdown', data['data'])


    def test_caste_filter_boundary_and_khatri_isolation(self):
        """Verifies that explicit caste_filter isolates tagged electors and never cross-contaminates."""
        from electionapp import create_app
        from electionapp.blueprints.caste import _search_caste_family_clusters
        app = create_app('jelatra', 'postgres')
        with app.app_context(), app.test_request_context('/?tenant=jelatra'):
            from electionapp.tenancy import resolve_tenant
            _, repo = resolve_tenant()
            
            # 1. Search for Soni with caste_filter
            res = _search_caste_family_clusters(1, caste_filter='सोनी', repo=repo)
            self.assertTrue(res['success'])
            for v in res['voters']:
                c_tag = (v.get('caste_tag') or '').strip().lower()
                # Must either be Soni or untagged family member, NEVER a conflicting caste (like Rajput)
                self.assertNotIn('राजपूत', c_tag)
                self.assertNotIn('बिश्नोई', c_tag)

            # 2. Khatri in SURNAME_SYNONYMS
            from electionapp.blueprints.caste import SURNAME_SYNONYMS
            self.assertIn('khatri', SURNAME_SYNONYMS)
            self.assertIn('खत्री', SURNAME_SYNONYMS['khatri'])
            self.assertIn('khatri', SURNAME_SYNONYMS['खत्री'])

    def test_untagged_caste_filter_returns_untagged_voters(self):
        """Verifies that caste_filter='अन्य / गैर-टैग' returns only untagged electors without name filtering."""
        from electionapp import create_app
        from electionapp.blueprints.caste import _search_caste_family_clusters
        app = create_app('jelatra', 'postgres')
        app.config['TESTING'] = True
        with app.app_context(), app.test_request_context('/?tenant=jelatra'):
            from electionapp.tenancy import resolve_tenant
            _, repo = resolve_tenant()

            # Ensure at least one untagged elector exists in test database
            try:
                conn = repo.get_connection() if hasattr(repo, 'get_connection') else None
                if conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO voters (voter_id, tenant_id, part_no, sl_no, epic_no, name_en, name_hi, rel_name_en, rel_name_hi, rel_type, age, gender, house_no, booth_name, caste_tag, status)
                            VALUES (999, 1, 1, 999, 'RJ10000999', 'Untagged Elector', 'अटैग्ड वोटर', 'Father Name', 'पिता का नाम', 'F', 30, 'M', '99', 'Booth', NULL, 'ACTIVE')
                            ON CONFLICT DO NOTHING;
                        """)
                    conn.commit()
            except Exception:
                pass

            # 1. Direct search using caste_filter='अन्य / गैर-टैग'
            res = _search_caste_family_clusters(1, caste_filter='अन्य / गैर-टैग', repo=repo)
            self.assertTrue(res['success'])
            self.assertGreater(res['total_voters'], 0)
            for v in res['voters']:
                c_tag = (v.get('caste_tag') or '').strip().lower()
                self.assertIn(c_tag, ('', 'अन्य / गैर-टैग', 'other', 'unknown', 'none', 'null'))
                self.assertEqual(v.get('caste_match_type'), 'untagged')
                self.assertEqual(v.get('match_tag'), '⚪ अनिर्धारित / गैर-टैग')

            # 2. API search via ?caste=अन्य / गैर-टैग
            client = app.test_client()
            res_api = client.get('/api/caste/census?caste=अन्य / गैर-टैग')
            self.assertEqual(res_api.status_code, 200)
            data_api = res_api.get_json()
            self.assertTrue(data_api['success'])
            self.assertEqual(data_api['total_voters'], res['total_voters'])

            # 3. Backward compatibility via ?query=अन्य / गैर-टैग
            res_query = client.get('/api/caste/census?query=अन्य / गैर-टैग')
            self.assertEqual(res_query.status_code, 200)
            data_query = res_query.get_json()
            self.assertTrue(data_query['success'])
            self.assertEqual(data_query['total_voters'], res['total_voters'])

            # 4. English keyword ?caste=untagged
            res_untagged = client.get('/api/caste/census?caste=untagged')
            self.assertEqual(res_untagged.status_code, 200)
            data_untagged = res_untagged.get_json()
            self.assertTrue(data_untagged['success'])
            self.assertEqual(data_untagged['total_voters'], res['total_voters'])


if __name__ == '__main__':
    unittest.main()


