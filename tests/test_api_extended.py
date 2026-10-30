import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pytest
from fastapi.testclient import TestClient

# Set required environment variables before importing the app
os.environ.setdefault('ADMIN_API_KEY', 'testadminkey')
os.environ.setdefault('JWT_SECRET', 'testjwtsecret')

from main import app
from leads import initialize_database, DB_NAME
import tempfile, sqlite3

# Helper to use a temporary SQLite DB for isolation
@pytest.fixture(scope="function")
def client(tmp_path):
    # Always start with a fresh temporary database
    test_db_path = tmp_path / "test_leads.db"
    test_db_path.touch()
    # Override DB_NAME in leads module for temporary DB
    import leads
    original_db = leads.DB_NAME
    leads.DB_NAME = str(test_db_path)
    # Re‑initialize DB schema on temp DB
    leads.initialize_database()
    # Create TestClient
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        leads.DB_NAME = original_db

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_unauthorized_leads_access(client):
    # No admin header – should be 401 or 503 depending on env config
    response = client.get("/leads")
    assert response.status_code in (401, 503)

def test_jwt_login_and_protected_endpoints(client):
    # Login to obtain JWT token
    login_resp = client.post("/login", headers={"x-admin-api-key": "testadminkey"})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}
    # Access leads list (should be empty initially)
    leads_resp = client.get("/leads", headers=auth_header)
    assert leads_resp.status_code == 200
    assert leads_resp.json()["leads"] == []
    # Create a new lead
    new_lead = {"name": "John Doe", "property_type": "home", "location": "Bhubaneswar"}
    create_resp = client.post("/leads", json=new_lead, headers=auth_header)
    assert create_resp.status_code == 201
    # Verify lead appears in list
    leads_resp2 = client.get("/leads", headers=auth_header)
    assert leads_resp2.status_code == 200
    leads_data = leads_resp2.json()["leads"]
    assert any(l["name"] == "John Doe" for l in leads_data)
def test_status_update(client):
    # Login to obtain token
    login_resp = client.post("/login", headers={"x-admin-api-key": "testadminkey"})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}
    # Create a lead
    new_lead = {"name": "Alice", "property_type": "business", "location": "Cuttack"}
    create_resp = client.post("/leads", json=new_lead, headers=auth_header)
    assert create_resp.status_code == 201
    # Get lead ID
    leads_resp = client.get("/leads", headers=auth_header)
    lead_id = next(l["id"] for l in leads_resp.json()["leads"] if l["name"] == "Alice")
    # Update status to Contacted
    update_resp = client.put(f"/leads/{lead_id}/status", json={"status": "Contacted"}, headers=auth_header)
    assert update_resp.status_code == 200
    assert update_resp.json()["new_status"] == "Contacted"

    # Simulate dashboard refresh (GET /leads) and verify status persists
    leads_after_contacted = client.get("/leads", headers=auth_header).json()["leads"]
    alice_lead = next(l for l in leads_after_contacted if l["id"] == lead_id)
    assert alice_lead["status"] == "Contacted"
    assert alice_lead["name"] == "Alice"
    assert alice_lead["property_type"] == "business"
    assert alice_lead["location"] == "Cuttack"

    # Update status to Converted
    update_resp2 = client.put(f"/leads/{lead_id}/status", json={"status": "Converted"}, headers=auth_header)
    assert update_resp2.status_code == 200
    assert update_resp2.json()["new_status"] == "Converted"

    # Simulate another dashboard refresh and verify status persists as Converted
    leads_after_converted = client.get("/leads", headers=auth_header).json()["leads"]
    alice_lead_converted = next(l for l in leads_after_converted if l["id"] == lead_id)
    assert alice_lead_converted["status"] == "Converted"

    # Test invalid status rejection
    invalid_resp = client.put(f"/leads/{lead_id}/status", json={"status": "InvalidStatus"}, headers=auth_header)
    assert invalid_resp.status_code == 400

    # Test non-existent lead rejection
    not_found_resp = client.put("/leads/999999/status", json={"status": "Contacted"}, headers=auth_header)
    assert not_found_resp.status_code == 404

