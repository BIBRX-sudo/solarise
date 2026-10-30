import os
import sqlite3
import pytest
from datetime import datetime, timezone, timedelta
from jose import jwt
from fastapi.testclient import TestClient

import leads
import main as main_module

ADMIN_KEY = "testadminkey"
JWT_SECRET = "testjwtsecret"

@pytest.fixture
def auth_token():
    """Generates a valid test JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=30)
    return jwt.encode({"sub": "admin", "exp": expire}, JWT_SECRET, algorithm="HS256")

@pytest.fixture
def client():
    return TestClient(main_module.app)


# 1. Database Initialization Tests
def test_database_initialization(tmp_path):
    custom_db = tmp_path / "init_test.db"
    leads.DB_NAME = str(custom_db)
    leads.initialize_database()

    assert custom_db.exists()
    with sqlite3.connect(str(custom_db)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
        assert {"id", "name", "property_type", "location", "status", "created_at"}.issubset(columns)


def test_database_migration_adds_status_column(tmp_path):
    legacy_db = tmp_path / "legacy_test.db"
    # Create legacy table without status column
    with sqlite3.connect(str(legacy_db)) as conn:
        conn.execute("""
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                property_type TEXT NOT NULL,
                location TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("INSERT INTO leads (name, property_type, location) VALUES ('Old Lead', 'home', 'Cuttack')")

    leads.DB_NAME = str(legacy_db)
    leads.initialize_database()

    with sqlite3.connect(str(legacy_db)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
        assert "status" in columns
        row = conn.execute("SELECT name, status FROM leads WHERE id = 1").fetchone()
        assert row[0] == "Old Lead"
        assert row[1] == "New"


# 2. Creating and Retrieving Leads
def test_create_and_retrieve_leads(tmp_path):
    custom_db = tmp_path / "crud_test.db"
    leads.DB_NAME = str(custom_db)

    lead_id = leads.save_lead(name="Suresh Kumar", property_type="business", location="Rourkela")
    assert lead_id is not None
    assert lead_id > 0

    all_leads = leads.get_leads()
    assert len(all_leads) == 1
    lead = all_leads[0]
    assert lead["name"] == "Suresh Kumar"
    assert lead["property_type"] == "business"
    assert lead["location"] == "Rourkela"
    assert lead["status"] == "New"
    assert "created_at" in lead


# 3. Updating Lead Status
def test_update_lead_status(tmp_path):
    custom_db = tmp_path / "status_test.db"
    leads.DB_NAME = str(custom_db)

    lead_id = leads.save_lead(name="Deepak Nayak", property_type="home", location="Sambalpur")

    # Update to Contacted
    updated = leads.update_lead_status(lead_id, "Contacted")
    assert updated is True
    lead = next(l for l in leads.get_leads() if l["id"] == lead_id)
    assert lead["status"] == "Contacted"

    # Update to Converted (with lowercase input test)
    updated = leads.update_lead_status(lead_id, "converted")
    assert updated is True
    lead = next(l for l in leads.get_leads() if l["id"] == lead_id)
    assert lead["status"] == "Converted"

    # Non-existent lead ID
    updated_fake = leads.update_lead_status(999999, "New")
    assert updated_fake is False

    # Invalid status
    with pytest.raises(ValueError, match="Status must be New, Contacted, or Converted"):
        leads.update_lead_status(lead_id, "InvalidStatus")


# 4. Database Path Configuration via LEADS_DB_PATH
def test_database_path_configuration(tmp_path, monkeypatch):
    custom_db_path = str(tmp_path / "custom_env_leads.db")
    monkeypatch.setenv("LEADS_DB_PATH", custom_db_path)
    leads.DB_NAME = "solarise_leads.db"  # Reset to default to test env pickup

    active_path = leads.get_db_path()
    assert active_path == custom_db_path

    lead_id = leads.save_lead("Env Customer", "home", "Berhampur")
    assert os.path.exists(custom_db_path)

    saved_leads = leads.get_leads()
    assert any(l["name"] == "Env Customer" for l in saved_leads)


# 5. JWT Authentication: Missing, Invalid, and Valid Tokens
def test_jwt_auth_scenarios(client, auth_token):
    # Missing token -> 401
    resp_missing = client.get("/leads")
    assert resp_missing.status_code == 401
    assert resp_missing.json()["detail"] == "Missing authentication token"

    # Invalid / forged token -> 401
    resp_invalid = client.get("/leads", headers={"Authorization": "Bearer forged_invalid_token"})
    assert resp_invalid.status_code == 401
    assert resp_invalid.json()["detail"] == "Invalid authentication token"

    # Expired token -> 401
    expired = datetime.now(timezone.utc) - timedelta(minutes=10)
    expired_token = jwt.encode({"sub": "admin", "exp": expired}, JWT_SECRET, algorithm="HS256")
    resp_expired = client.get("/leads", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp_expired.status_code == 401
    assert resp_expired.json()["detail"] == "Invalid authentication token"

    # Swagger Authorize edge case (Bearer Bearer <token>) -> 200
    resp_double_bearer = client.get("/leads", headers={"Authorization": f"Bearer Bearer {auth_token}"})
    assert resp_double_bearer.status_code == 200

    # Valid token -> 200
    resp_valid = client.get("/leads", headers={"Authorization": f"Bearer {auth_token}"})
    assert resp_valid.status_code == 200
    assert "leads" in resp_valid.json()


# 6. Protected FastAPI Endpoints
def test_leads_endpoints_full_lifecycle(client, auth_token, tmp_path):
    leads.DB_NAME = str(tmp_path / "api_test.db")
    leads.initialize_database()
    auth_header = {"Authorization": f"Bearer {auth_token}"}

    # 1. GET /leads (initially empty)
    get_resp = client.get("/leads", headers=auth_header)
    assert get_resp.status_code == 200
    assert get_resp.json()["leads"] == []

    # 2. POST /leads valid
    post_resp = client.post(
        "/leads",
        json={"name": "Priyanka Sahoo", "property_type": "home", "location": "Bhubaneswar"},
        headers=auth_header,
    )
    assert post_resp.status_code == 201
    assert post_resp.json()["lead"]["name"] == "Priyanka Sahoo"
    assert post_resp.json()["lead"]["status"] == "New"

    # 3. POST /leads validation failures
    # Invalid property type
    bad_type = client.post(
        "/leads",
        json={"name": "Priyanka", "property_type": "factory", "location": "Bhubaneswar"},
        headers=auth_header,
    )
    assert bad_type.status_code == 400

    # Name too short (Pydantic Field(min_length=2) returns 422)
    bad_name = client.post(
        "/leads",
        json={"name": "A", "property_type": "home", "location": "Bhubaneswar"},
        headers=auth_header,
    )
    assert bad_name.status_code in (400, 422)

    # 4. GET /leads to verify creation
    leads_list = client.get("/leads", headers=auth_header).json()["leads"]
    assert len(leads_list) == 1
    lead_id = leads_list[0]["id"]

    # 5. PUT /leads/{id}/status valid update
    put_resp = client.put(f"/leads/{lead_id}/status", json={"status": "Contacted"}, headers=auth_header)
    assert put_resp.status_code == 200
    assert put_resp.json()["new_status"] == "Contacted"

    # 6. PUT /leads/{id}/status invalid status
    put_bad = client.put(f"/leads/{lead_id}/status", json={"status": "Invalid"}, headers=auth_header)
    assert put_bad.status_code == 400

    # 7. PUT /leads/{id}/status non-existent lead
    put_404 = client.put("/leads/999999/status", json={"status": "Converted"}, headers=auth_header)
    assert put_404.status_code == 404


# 7. Chatbot-to-Lead-Saving Integration
def test_chatbot_to_lead_saving_integration(tmp_path):
    leads.DB_NAME = str(tmp_path / "chatbot_leads.db")
    leads.initialize_database()

    chat_id = "test-chat-integration@c.us"
    from main import process_message

    # Step 1: Start rooftop solar enquiry
    msg1 = process_message(chat_id, "1")
    assert "HOME or BUSINESS" in msg1

    # Step 2: Select home
    msg2 = process_message(chat_id, "HOME")
    assert "What is your name?" in msg2

    # Step 3: Enter customer name
    msg3 = process_message(chat_id, "Anita Mishra")
    assert "Which city or district" in msg3

    # Step 4: Enter location -> Lead should be saved
    msg4 = process_message(chat_id, "Puri")
    assert "enquiry has been saved" in msg4
    assert "Anita Mishra" in msg4
    assert "HOME" in msg4
    assert "Puri" in msg4

    # Verify directly in database
    saved = leads.get_leads()
    anita_lead = next((l for l in saved if l["name"] == "Anita Mishra"), None)
    assert anita_lead is not None
    assert anita_lead["property_type"] == "home"
    assert anita_lead["location"] == "Puri"
    assert anita_lead["status"] == "New"
