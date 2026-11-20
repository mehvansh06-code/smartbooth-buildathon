"""
tests/test_campaign_lifecycle.py
Comprehensive test suite verifying Campaign Lifecycle operations:
- Modify campaign (slogan, designation, office_address, status)
- Safe Campaign Archival (Soft Delete)
- Campaign Restoration (Re-activation)
- Permanent Campaign Purge (Cascade partition drop & file cleanup with safety lock)
- RBAC permissions & confirmation validation
"""

import os
import json
import shutil
import pytest
from flask import Flask

from electionapp.config import TENANTS_DIR, DATA_DIR
from electionapp import campaign_manager
from electionapp import campaign_lifecycle
from electionapp import create_app


@pytest.fixture
def app():
    old_force = os.environ.get('FORCE_AUTH_CHECK')
    old_backend = os.environ.get('STORAGE_BACKEND')
    os.environ['FORCE_AUTH_CHECK'] = '0'
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
def temp_campaign():
    """Creates a temporary isolated test campaign and cleans it up after."""
    slug = "test_lifecycle_campaign"
    tenant_dir = os.path.join(TENANTS_DIR, slug)
    data_dir = os.path.join(DATA_DIR, slug)

    os.makedirs(os.path.join(tenant_dir, "assets"), exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    cfg = {
        "candidate": {
            "name_hi": "परीक्षण प्रत्याशी",
            "name_en": "Test Candidate",
            "designation": "समाजसेवी",
            "phone": "9829012345",
            "office_address": "सांचौर"
        },
        "election": {
            "election_type": "MUNICIPAL",
            "ward_no": "99",
            "ward_name_hi": "वार्ड 99",
            "state": "Rajasthan",
            "district": "Jalore"
        },
        "party": {
            "name_hi": "निर्दलीय",
            "slogan": "जनसेवा ही संकल्प",
            "ballot_serial": "1"
        },
        "status": "ACTIVE",
        "max_sub_accounts": 50,
        "campaign_message": "परीक्षण संदेश"
    }

    with open(os.path.join(tenant_dir, "campaign.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    yield slug

    # Cleanup in case test didn't purge
    if os.path.exists(tenant_dir):
        shutil.rmtree(tenant_dir, ignore_errors=True)
    if os.path.exists(data_dir):
        shutil.rmtree(data_dir, ignore_errors=True)


def test_modify_campaign_fields(client, temp_campaign):
    """Verifies modifying campaign fields including slogan, designation, office_address, and status."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'super_admin'

    payload = {
        "campaign_slug": temp_campaign,
        "candidate_name_hi": "अपडेटेड प्रत्याशी",
        "slogan": "नया चुनावी नारा",
        "designation": "वरिष्ठ नेता",
        "office_address": "नई दिल्ली मार्ग",
        "status": "PAUSED"
    }

    resp = client.post("/api/admin/campaigns/update", data=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    # Verify campaign.json was updated
    cfg_file = os.path.join(TENANTS_DIR, temp_campaign, "campaign.json")
    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    assert cfg["candidate"]["name_hi"] == "अपडेटेड प्रत्याशी"
    assert cfg["party"]["slogan"] == "नया चुनावी नारा"
    assert cfg["candidate"]["designation"] == "वरिष्ठ नेता"
    assert cfg["candidate"]["office_address"] == "नई दिल्ली मार्ग"
    assert cfg["status"] == "PAUSED"


def test_archive_and_restore_campaign(client, temp_campaign):
    """Verifies soft-delete (archive) and subsequent restoration."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'super_admin'

    # 1. Archive campaign
    resp = client.post(f"/api/admin/campaigns/{temp_campaign}/delete", json={"mode": "archive"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["status"] == "ARCHIVED"

    # Verify status in file
    cfg_file = os.path.join(TENANTS_DIR, temp_campaign, "campaign.json")
    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["status"] == "ARCHIVED"

    # 2. Restore campaign
    resp_restore = client.post(f"/api/admin/campaigns/{temp_campaign}/restore")
    assert resp_restore.status_code == 200
    data_restore = resp_restore.get_json()
    assert data_restore["success"] is True
    assert data_restore["status"] == "ACTIVE"

    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["status"] == "ACTIVE"


def test_permanent_purge_safety_guard(client, temp_campaign):
    """Verifies permanent purge rejects mismatched confirmation tokens and protected system slugs."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'super_admin'

    # 1. Mismatched confirmation slug must fail with 400
    resp = client.post(
        f"/api/admin/campaigns/{temp_campaign}/delete",
        json={"mode": "permanent", "confirmation_slug": "wrong_slug"}
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "पुष्टि स्लग" in data["error"] or "Confirmation" in data["error"]

    # Campaign must still exist
    assert os.path.exists(os.path.join(TENANTS_DIR, temp_campaign))

    # 2. Protected system slug cannot be deleted
    resp_sys = client.post(
        "/api/admin/campaigns/master/delete",
        json={"mode": "permanent", "confirmation_slug": "master"}
    )
    assert resp_sys.status_code == 403
    assert "संरक्षित" in resp_sys.get_json()["error"] or "Protected" in resp_sys.get_json()["error"]


def test_permanent_purge_execution(client, temp_campaign):
    """Verifies that a valid permanent purge completely deletes the tenant directory and assets."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['role'] = 'super_admin'

    tenant_dir = os.path.join(TENANTS_DIR, temp_campaign)
    data_dir = os.path.join(DATA_DIR, temp_campaign)

    assert os.path.exists(tenant_dir)

    resp = client.post(
        f"/api/admin/campaigns/{temp_campaign}/delete",
        json={"mode": "permanent", "confirmation_slug": temp_campaign}
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    # Directories must be cleanly removed
    assert not os.path.exists(tenant_dir)
    assert not os.path.exists(data_dir)


def test_reseller_cannot_permanently_delete_campaign(client, temp_campaign):
    """Verifies that reseller role can archive but can NEVER permanently delete a campaign."""
    with client.session_transaction() as sess:
        sess['user_id'] = 42
        sess['role'] = 'reseller'

    # Reseller attempt to permanently delete must be rejected with 403 Forbidden
    resp = client.post(
        f"/api/admin/campaigns/{temp_campaign}/delete",
        json={"mode": "permanent", "confirmation_slug": temp_campaign}
    )
    assert resp.status_code == 403
    data = resp.get_json()
    assert data["success"] is False
    assert "सुपर एडमिन" in data["error"] or "Super Admin" in data["error"]

    # Campaign must still exist
    assert os.path.exists(os.path.join(TENANTS_DIR, temp_campaign))
