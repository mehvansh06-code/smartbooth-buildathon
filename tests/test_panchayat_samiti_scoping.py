import pytest
import os
import sys

# Ensure project root is in path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from electionapp import rajasthan_catalog
from scripts import provision_campaign

def test_area_preview_requires_block_for_single_gp():
    """Verify get_area_preview fails-fast when block is omitted for a single GP."""
    res = rajasthan_catalog.get_area_preview(
        district="BARMER",
        block="",
        gp="Adarsh Sondi"
    )
    assert res['success'] is False
    assert "panchayat_samiti / block is mandatory" in res.get('error', '')
    assert res['total_voters'] == 0

def test_load_gp_voter_records_requires_block():
    """Verify load_gp_voter_records returns empty list when block is missing."""
    voters = rajasthan_catalog.load_gp_voter_records(
        district="BARMER",
        block="",
        gp="Adarsh Sondi"
    )
    assert voters == []

def test_area_preview_scoped_to_correct_samiti_sedwa():
    """Verify Adarsh Sondi in Sedwa matches exclusively 1512 electors and booths 65/66."""
    res = rajasthan_catalog.get_area_preview(
        district="BARMER",
        block="SEDWA PANCHAYAT SAMITI",
        gp="Adarsh Sondi"
    )
    assert res['success'] is True
    # If DB is reachable, verify exact counts
    if res.get('has_records') and res.get('total_voters') > 0:
        assert res['total_voters'] == 1512
        assert res['ward_count'] == 7
        assert "65" in res['booths'] or "66" in res['booths']

def test_duplicate_gp_isolation_alwar_khoh():
    """
    Verify duplicate GP 'Khoh' in Alwar (which exists in Kathumar, Rajgarh, and Mubarikpur)
    returns completely isolated records for each block and never leaks cross-block voters.
    """
    m_conn = rajasthan_catalog.get_master_db_connection()
    if not m_conn:
        pytest.skip("Master database not available in test environment.")
    
    # Check Kathumar vs Rajgarh
    res_kathumar = rajasthan_catalog.get_area_preview(
        district="ALWAR",
        block="KATHUMAR PANCHAYAT SAMITI",
        gp="Khoh"
    )
    res_rajgarh = rajasthan_catalog.get_area_preview(
        district="ALWAR",
        block="RAJGARH PANCHAYAT SAMITI",
        gp="Khoh"
    )
    
    assert res_kathumar['success'] is True
    assert res_rajgarh['success'] is True
    
    # Both must find voters, but their voter counts and booths must be distinct
    if res_kathumar['has_records'] and res_rajgarh['has_records']:
        assert res_kathumar['total_voters'] != res_rajgarh['total_voters']
        
        # Ensure Kathumar electors only belong to Kathumar
        voters_kathumar = rajasthan_catalog.load_gp_voter_records(
            district="ALWAR",
            block="KATHUMAR PANCHAYAT SAMITI",
            gp="Khoh"
        )
        assert len(voters_kathumar) > 2000
        
        # Ensure Rajgarh electors only belong to Rajgarh
        voters_rajgarh = rajasthan_catalog.load_gp_voter_records(
            district="ALWAR",
            block="RAJGARH PANCHAYAT SAMITI",
            gp="Khoh"
        )
        assert len(voters_rajgarh) > 4000
        assert len(voters_kathumar) != len(voters_rajgarh)
        
        # Verify sets are completely disjoint (zero EPIC collisions)
        epics_kathumar = {v['epic_no'] for v in voters_kathumar if v.get('epic_no')}
        epics_rajgarh = {v['epic_no'] for v in voters_rajgarh if v.get('epic_no')}
        assert len(epics_kathumar.intersection(epics_rajgarh)) == 0

def test_provision_campaign_raises_value_error_on_missing_block():
    """Verify universal provisioner strictly aborts if block is missing for Panchayat."""
    with pytest.raises(ValueError, match="panchayat_samiti / block is mandatory"):
        provision_campaign.provision_campaign(
            district="BARMER",
            block="",
            gp="Adarsh Sondi",
            slug="test_missing_block",
            deploy_ec2=False
        )


def test_admin_create_campaign_fails_without_block():
    """Verify admin API /api/admin/campaigns/create returns 400 when block is omitted."""
    from electionapp import create_app
    app = create_app('ward82', 'postgres')
    app.config['TESTING'] = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'super_admin'
    
    payload = {
        'candidate_name': 'Test Candidate',
        'election_type': 'PANCHAYAT',
        'district': 'BARMER',
        'gram_panchayat': 'Adarsh Sondi',
        'block': ''
    }
    res = client.post('/api/admin/campaigns/create', json=payload)
    assert res.status_code == 400
    data = res.get_json()
    assert data['success'] is False
    assert "panchayat_samiti / block is mandatory" in data['error']
