"""
seed_electors.py
================
Single source of truth for the synthetic test electorate.

Read by tests/init_test_postgres.py to seed synthetic test electors
for deterministic integration and unit testing in PostgreSQL.

Every name, EPIC and phone number below is invented. No row here comes from a
real electoral roll, so this file is safe to track in git (see .gitignore --
real voter data is never committed to this repo).

Voter ids are pinned, not arbitrary. tests/test_audit_log.py:195 hardcodes
ward82 voter_id 3514, so that row has to keep existing with that exact id.
Renumbering these breaks tests -- add electors rather than renumber them.
PostgreSQL keys voters on (tenant_id, voter_id),
so ids only need to be unique within a tenant.
"""

# (tenant_id, slug, candidate_name_hi, state, election_type)
TENANTS = (
    (1, 'jelatra', 'जेलातरा सरपंच', 'RAJASTHAN', 'PANCHAYAT'),
    (3, 'ward82', 'वार्ड 82 पार्षद', 'RAJASTHAN', 'MUNICIPAL'),
)

# Column order used by both initializers.
ELECTOR_FIELDS = (
    'voter_id', 'part_no', 'sl_no', 'epic_no',
    'name_en', 'name_hi', 'rel_name_en', 'rel_name_hi', 'rel_type',
    'age', 'gender', 'house_no', 'booth_name',
    'contact_phone', 'caste_tag', 'family_id', 'is_voted', 'is_dispatched',
)

# Synthetic electors, keyed by tenant_id. Households share house_no + family_id
# so get_family_voters() has something real to resolve.
ELECTORS = {
    1: (
        (101, 1, 1, 'RJ10000001', 'Prahlad Ram', 'प्रहलाद राम', 'Babu Ram', 'बाबू राम', 'F',
         35, 'M', '1', 'रा.उ.प्रा.वि. जेलातरा', '9414127201', 'Bishnoi', 'JEL-F001', False, False),
        (102, 1, 2, 'RJ10000002', 'Santosh Devi', 'संतोष देवी', 'Prahlad Ram', 'प्रहलाद राम', 'H',
         32, 'F', '1', 'रा.उ.प्रा.वि. जेलातरा', None, 'Bishnoi', 'JEL-F001', False, False),
        (103, 1, 3, 'RJ10000003', 'Ramesh Kumar', 'रमेश कुमार', 'Babu Ram', 'बाबू राम', 'F',
         28, 'M', '1', 'रा.उ.प्रा.वि. जेलातरा', None, 'Bishnoi', 'JEL-F001', False, False),
        (104, 2, 1, 'RJ10000004', 'Nenu Devi', 'नेनु देवी', 'Harchand Ram', 'हरचंद राम', 'H',
         45, 'F', '14', 'रा.प्रा.वि. गोलिया', '9414127202', 'Meghwal', 'JEL-F014', False, False),
        (105, 2, 2, 'RJ10000005', 'Harchand Ram', 'हरचंद राम', 'Ganesha Ram', 'गणेशा राम', 'F',
         50, 'M', '14', 'रा.प्रा.वि. गोलिया', None, 'Meghwal', 'JEL-F014', False, False),
        (106, 2, 3, 'RJ10000006', 'Dinesh Kumar', 'दिनेश कुमार', 'Prakash Ram', 'प्रकाश राम', 'F',
         24, 'M', '20', 'रा.प्रा.वि. गोलिया', None, None, 'JEL-F020', False, False),
    ),
    3: (
        (301, 1, 1, 'WD82000001', 'Vikram Singh', 'विक्रम सिंह', 'Kalyan Singh', 'कल्याण सिंह', 'F',
         40, 'M', '101', 'कम्युनिटी सेंटर वार्ड 82', '9829012345', 'Rajput', 'W82-F101', False, False),
        (302, 1, 2, 'WD82000002', 'Suman Kanwar', 'सुमन कंवर', 'Vikram Singh', 'विक्रम सिंह', 'H',
         38, 'F', '101', 'कम्युनिटी सेंटर वार्ड 82', None, 'Rajput', 'W82-F101', False, False),
        (303, 2, 1, 'WD82000003', 'Anil Sharma', 'अनिल शर्मा', 'Ram Gopal', 'राम गोपाल', 'F',
         52, 'M', '205', 'राजकीय स्कूल सेक्टर 2', '9829054321', 'Brahmin', 'W82-F205', False, False),
        # voter_id 3514 is load-bearing: tests/test_audit_log.py:195 pins it.
        (3514, 3, 14, 'WD82003514', 'Mahendra Kumar', 'महेन्द्र कुमार', 'Sohan Lal', 'सोहन लाल', 'F',
         34, 'M', '402', 'सामुदायिक भवन महामंदिर', '9829099887', 'OBC', 'W82-F402', False, False),
    ),
}


def as_dict(row):
    """Turns one ELECTORS tuple into a field-keyed dict."""
    return dict(zip(ELECTOR_FIELDS, row))


def build_search_doc(elector):
    """
    Recomputes the compound search column exactly as the PostgreSQL trigger does
    (voters_search_doc_trigger in migrations/postgres/0001_master_partitioned_schema.sql:
    CONCAT_WS over the name/relative/house/epic/caste fields, dropping blanks).
    """
    e = elector if isinstance(elector, dict) else as_dict(elector)
    parts = [
        e.get('name_hi'), e.get('name_en'), e.get('name_norm'),
        e.get('rel_name_hi'), e.get('rel_name_en'), e.get('rel_norm'),
        e.get('house_no'), e.get('epic_no'), e.get('caste_tag'),
    ]
    return ' '.join(str(p).strip() for p in parts if p is not None and str(p).strip())
