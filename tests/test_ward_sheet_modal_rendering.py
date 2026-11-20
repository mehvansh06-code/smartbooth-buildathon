import pytest
from electionapp import create_app

@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['user_id'] = 1
            sess['username'] = 'admin'
            sess['role'] = 'admin'
            sess['role_tier'] = 1
        yield c

def test_single_ward_modal_renders_all_parts(client):
    """Ward 82 (single ward with 9 parts) must render all parts in the modal options."""
    res = client.get('/voters?tenant=ward82')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'id="partFilterModal"' in html
    
    # Must contain options for parts 1 to 9
    for p in range(1, 10):
        assert f'data-ward-value="part:{p}"' in html, f"part:{p} missing in modal HTML"
    
    # Must contain the all-parts global option
    assert 'समस्त भाग (वार्ड 82' in html

def test_multi_ward_modal_renders_wards(client):
    """Chhoti Virol (multi-ward) must render ward options."""
    res = client.get('/voters?tenant=chhoti_virol')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'id="partFilterModal"' in html
    assert 'data-ward-value=""' in html

def test_multi_gp_modal_renders_hierarchy(client):
    """Multi-GP campaign must render area-wise options or fallback smoothly without error."""
    res = client.get('/voters?tenant=baytoo_panchayat_samiti_w1_jasaw')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'id="partFilterModal"' in html


def test_chhotu_sarpanch_single_gp_ward_modal(client):
    """Chhotu Sarpanch (single-GP 7-ward campaign) must render clean ward list with zero 'General' accordions."""
    res = client.get('/voters?tenant=chhotu_sarpanch')
    assert res.status_code == 200
    html = res.data.decode('utf-8')
    assert 'id="partFilterModal"' in html
    
    # Extract modal block
    modal_start = html.find('id="partFilterModal"')
    modal_html = html[modal_start:modal_start + 6000]
    
    # Must NOT have multi-GP accordion artifacts or General GP accordion inside modal
    assert 'gp-accordion-card' not in modal_html
    assert 'General' not in modal_html
    assert 'कुल 2 ग्राम पंचायत' not in modal_html
    
    # Must have clean ward options for all 7 wards
    for w in range(1, 8):
        assert f'data-ward-value="ward:{w}"' in modal_html, f"ward:{w} missing in modal HTML"
    
    # Must have single-GP header
    assert 'वार्ड चुनें (Select Ward)' in modal_html
    assert 'कुल 7 वार्ड' in modal_html
    assert 'सभी वार्ड (All 7 Wards)' in modal_html
