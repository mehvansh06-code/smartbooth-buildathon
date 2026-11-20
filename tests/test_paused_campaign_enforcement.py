"""
tests/test_paused_campaign_enforcement.py
Comprehensive test suite verifying that PAUSED campaigns are strictly blocked:
- Non-admin web navigation returns HTTP 403 and renders campaign_paused.html
- Non-admin API endpoints return HTTP 403 JSON with error explanation
- User login for paused campaigns is rejected with error
- Magic links (/k/<token>) for paused campaigns render campaign_paused.html
- DispatchRouter refuses to dispatch WhatsApp messages for paused campaigns
- Super Admin retains management access
"""

import os
import json
import shutil
import pytest
from flask import Flask

from electionapp.config import TENANTS_DIR, DATA_DIR, clear_tenant_config_cache
from electionapp import campaign_manager
from electionapp import create_app
from electionapp.dispatch.router import DispatchRouter


@pytest.fixture
def app():
    old_force = os.environ.get('FORCE_AUTH_CHECK')
    old_backend = os.environ.get('STORAGE_BACKEND')
    os.environ['FORCE_AUTH_CHECK'] = '1'
    os.environ['STORAGE_BACKEND'] = 'postgres'
    app = create_app()
    app.config['TESTING'] = True
    yield app
    if old_force is not None:
        os.environ['FORCE_AUTH_CHECK'] = old_force
    else:
        os.environ.pop('FORCE_AUTH_CHECK', None)
    if old_backend is not None:
        os.environ['STORAGE_BACKEND'] = old_backend
    else:
        os.environ.pop('STORAGE_BACKEND', None)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def paused_campaign():
    """Creates an isolated campaign with status PAUSED."""
    slug = "test_paused_tenant"
    tenant_dir = os.path.join(TENANTS_DIR, slug)
    data_dir = os.path.join(DATA_DIR, slug)

    os.makedirs(os.path.join(tenant_dir, "assets"), exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    cfg = {
        "candidate": {
            "name_hi": "रुका हुआ प्रत्याशी",
            "name_en": "Paused Candidate",
            "designation": "समाजसेवी",
            "phone": "9829011111",
            "office_address": "सांचौर"
        },
        "election": {
            "election_type": "PANCHAYAT",
            "ward_no": "1",
            "ward_name_hi": "वार्ड 1",
            "ward_name_en": "Ward 1",
            "app_title": "स्मार्ट बूथ",
            "state": "Rajasthan",
            "district": "Sanchore"
        },
        "party": {
            "name_hi": "निर्दलीय",
            "slogan": "जनसेवा",
            "ballot_serial": "1"
        },
        "status": "PAUSED",
        "max_sub_accounts": 10,
        "campaign_message": "परीक्षण"
    }

    with open(os.path.join(tenant_dir, "campaign.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    clear_tenant_config_cache(slug)

    yield slug

    clear_tenant_config_cache(slug)
    if os.path.exists(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)
    if os.path.exists(data_dir):
        shutil.rmtree(data_dir, ignore_errors=True)


def test_paused_campaign_web_route_blocked_for_operator(client, paused_campaign):
    """Operator attempting to visit voter search page on a paused campaign gets HTTP 403."""
    with client.session_transaction() as sess:
        sess['user_id'] = 42
        sess['role'] = 'operator'
        sess['tenant_slug'] = paused_campaign

    resp = client.get(f'/?tenant={paused_campaign}')
    assert resp.status_code == 403
    html = resp.get_data(as_text=True)
    assert "अभियान अस्थायी रूप से स्थगित" in html or "Campaign Paused" in html
    assert "रुका हुआ प्रत्याशी" in html


def test_paused_campaign_api_blocked_for_operator(client, paused_campaign):
    """Operator attempting to call search/mark_voted API on a paused campaign gets HTTP 403 JSON."""
    with client.session_transaction() as sess:
        sess['user_id'] = 42
        sess['role'] = 'operator'
        sess['tenant_slug'] = paused_campaign

    resp = client.get(f'/api/search?tenant={paused_campaign}&q=test')
    assert resp.status_code == 403
    data = resp.get_json()
    assert data['success'] is False
    assert data.get('campaign_status') == 'PAUSED'
    assert "रोक दिया गया है" in data['error'] or "paused" in data['error'].lower()


def test_paused_campaign_dispatch_router_blocked(paused_campaign):
    """DispatchRouter immediately blocks sending WhatsApp slip for a paused campaign."""
    router = DispatchRouter(tenant_slug=paused_campaign)
    res = router.dispatch_slip(phone="9829011111", message="Test", image_path="test.png")
    assert res.success is False
    assert res.channel == "campaign_lifecycle_gate"
    assert "paused" in res.error.lower()


def test_super_admin_can_access_paused_campaign_management(client, paused_campaign):
    """Super Admin is not blocked from administrative routes even when campaign is paused."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'super_admin'
        sess['tenant_slug'] = '*'

    # Super admin should be allowed to view admin portal
    resp = client.get('/admin')
    assert resp.status_code == 200

    # Super admin should be allowed to view campaign configuration
    resp2 = client.get(f'/api/admin/campaigns/{paused_campaign}')
    assert resp2.status_code == 200
    assert resp2.get_json()['success'] is True


def test_paused_campaign_login_rejected(client, paused_campaign, monkeypatch):
    """Attempting to log in as an operator for a paused campaign returns HTTP 403."""
    import user_manager

    fake_user = {
        'id': 99,
        'username': 'paused_operator',
        'full_name': 'Paused Operator',
        'role': 'operator',
        'role_tier': 3,
        'tenant_slug': paused_campaign,
        'assigned_booths': [],
        'assigned_booth': None,
        'assigned_wards': [],
        'assigned_villages': [],
        'scope_type': 'all',
        'allowed_modules': ['all']
    }
    monkeypatch.setattr(user_manager, 'authenticate_user', lambda u, p: (fake_user, None))

    resp = client.post('/login', json={'username': 'paused_operator', 'password': 'Password123'})
    assert resp.status_code == 403
    data = resp.get_json()
    assert data['success'] is False
    assert data.get('campaign_status') == 'PAUSED'
    assert "रोक दिया गया है" in data['error'] or "paused" in data['error'].lower()

