import os
import json
import shutil
import pytest
from electionapp import rajasthan_catalog, campaign_manager

def test_rajasthan_assembly_constituencies_200():
    acs = rajasthan_catalog.get_assembly_constituencies()
    assert len(acs) == 200, f"Expected 200 ACs, got {len(acs)}"
    ac_nos = [a['ac_no'] for a in acs]
    assert len(set(ac_nos)) == 200, "Duplicate AC numbers found"
    
    # Check Sanchore
    sanchore_acs = rajasthan_catalog.get_assembly_constituencies('SANCHORE')
    assert any(a['ac_no'] == 144 and 'सांचौर' in a['name_hi'] for a in sanchore_acs), "AC 144 Sanchore missing"
    
    # Check Jaipur
    jaipur_acs = rajasthan_catalog.get_assembly_constituencies('JAIPUR')
    assert any('हवामहल' in a['name_hi'] for a in jaipur_acs), "Hawa Mahal missing in Jaipur ACs"

def test_create_assembly_campaign_mock():
    slug = "test_ac144_unit"
    tenant_dir = os.path.join(campaign_manager.TENANTS_DIR, slug)
    if os.path.exists(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)
        
    try:
        data = {
            "campaign_slug": slug,
            "election_type": "ASSEMBLY",
            "district": "SANCHORE",
            "ward_no": "144",
            "ac_no": "144",
            "ward_name_hi": "सांचौर विधान सभा",
            "candidate_name_hi": "सुरेश कुमार",
            "candidate_name_en": "Suresh Kumar",
            "party_name_hi": "भारतीय राष्ट्रीय कांग्रेस",
            "party_name_en": "INC",
            "symbol_name_hi": "हाथ",
            "ballot_serial": "2",
            "create_admin_user": False,
            "auto_ingest_rolls": False
        }
        ok, res, code = campaign_manager.create_campaign(data)
        assert ok is True, f"Assembly campaign creation failed: {res}"
        assert res.get('campaign_slug') == slug
        assert res.get('election_type') == 'ASSEMBLY'
        assert res.get('voting_method') == 'EVM'
        
        cfg_file = os.path.join(tenant_dir, 'campaign.json')
        assert os.path.exists(cfg_file)
        with open(cfg_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            
        assert cfg['election']['election_type'] == 'ASSEMBLY'
        assert cfg['election']['ward_no'] == '144'
        assert cfg['election']['voting_method'] == 'EVM'
        assert "सांचौर विधान सभा" in cfg['campaign_message']
    finally:
        if os.path.exists(tenant_dir):
            shutil.rmtree(tenant_dir, ignore_errors=True)

def test_create_municipal_campaign_mock():
    slug = "test_muni_w12_unit"
    tenant_dir = os.path.join(campaign_manager.TENANTS_DIR, slug)
    if os.path.exists(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)
        
    try:
        data = {
            "campaign_slug": slug,
            "election_type": "MUNICIPAL",
            "district": "SANCHORE",
            "block": "सांचौर नगर पालिका",
            "ulb_name": "सांचौर नगर पालिका",
            "ward_no": "12",
            "ward_name_hi": "सांचौर नगर पालिका वार्ड 12",
            "candidate_name_hi": "रमेश पटेल",
            "party_name_hi": "भारतीय जनता पार्टी",
            "party_name_en": "BJP",
            "symbol_name_hi": "कमल",
            "ballot_serial": "1",
            "create_admin_user": False,
            "auto_ingest_rolls": False
        }
        ok, res, code = campaign_manager.create_campaign(data)
        assert ok is True, f"Municipal campaign creation failed: {res}"
        assert res.get('campaign_slug') == slug
        assert res.get('election_type') == 'MUNICIPAL'
        assert res.get('voting_method') == 'EVM'
        
        cfg_file = os.path.join(tenant_dir, 'campaign.json')
        assert os.path.exists(cfg_file)
        with open(cfg_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            
        assert cfg['election']['election_type'] == 'MUNICIPAL'
        assert cfg['election']['ward_no'] == '12'
        assert cfg['election']['voting_method'] == 'EVM'
        assert "पार्षद" in cfg['campaign_message']
    finally:
        if os.path.exists(tenant_dir):
            shutil.rmtree(tenant_dir, ignore_errors=True)

def test_create_panchayat_campaign_mock():
    slug = "test_gp_sarpanch_unit"
    tenant_dir = os.path.join(campaign_manager.TENANTS_DIR, slug)
    if os.path.exists(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)
        
    try:
        data = {
            "campaign_slug": slug,
            "election_type": "PANCHAYAT",
            "district": "SANCHORE",
            "block": "SANCHORE",
            "rajasthan_gp": "Jelatra",
            "candidate_name_hi": "नेनु देवी",
            "symbol_name_hi": "ट्रैक्टर",
            "ballot_serial": "3",
            "create_admin_user": False,
            "auto_ingest_rolls": False
        }
        ok, res, code = campaign_manager.create_campaign(data)
        assert ok is True, f"Panchayat campaign creation failed: {res}"
        assert res.get('campaign_slug') == slug
        assert res.get('election_type') == 'PANCHAYAT'
        assert res.get('voting_method') == 'BALLOT_PAPER'
        
        cfg_file = os.path.join(tenant_dir, 'campaign.json')
        assert os.path.exists(cfg_file)
        with open(cfg_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            
        assert cfg['election']['election_type'] == 'PANCHAYAT'
        assert cfg['election']['voting_method'] == 'BALLOT_PAPER'
        assert "सरपंच" in cfg['campaign_message']
    finally:
        if os.path.exists(tenant_dir):
            shutil.rmtree(tenant_dir, ignore_errors=True)

def test_municipal_cross_ward_isolation():
    """Verify that municipal auto-ingest does NOT import all city voters when ward_no is unmatched."""
    from electionapp import rajasthan_catalog
    recs = rajasthan_catalog.load_gp_voter_records(
        district="JODHPUR",
        block="JODHPUR NAGAR NIGAM",
        gp="JODHPUR NAGAR NIGAM",
        ward_no="99999"  # Non-existent ward
    )
    assert len(recs) == 0, f"Expected 0 electors for non-existent ward, got {len(recs)}"

