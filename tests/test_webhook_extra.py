import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

# Ensure env vars for OpenWA (mocked) and test DB
os.environ.setdefault('ADMIN_API_KEY', 'testadminkey')
os.environ.setdefault('JWT_SECRET', 'testjwtsecret')
os.environ.setdefault('OPENWA_URL', 'http://127.0.0.1:2785')
os.environ.setdefault('OPENWA_API_KEY', 'test_openwa_key')
os.environ.setdefault('OPENWA_SESSION_ID', 'test_session')
os.environ.setdefault('WEBHOOK_SECRET', 'test_webhook_secret')

# temporary DB fixture
@pytest.fixture(scope='function')
def temp_client(tmp_path):
    test_db_path = tmp_path / "test_leads.db"
    test_db_path.touch()
    import leads
    original_db = leads.DB_NAME
    leads.DB_NAME = str(test_db_path)
    leads.initialize_database()
    # reload main to pick up DB change
    import importlib, main as main_mod
    importlib.reload(main_mod)
    try:
        yield TestClient(main_mod.app)
    finally:
        leads.DB_NAME = original_db

def make_payload(message: str, chat_id: str = "12345@s.whatsapp.net"):
    return {
        "event": "message.received",
        "data": {
            "body": message,
            "chatId": chat_id,
            "fromMe": False,
        }
    }

@patch('requests.post')
def test_invalid_webhook_secret(mock_post, temp_client):
    payload = make_payload("hi")
    response = temp_client.post(
        "/webhook",
        json=payload,
        headers={"x-webhook-secret": "wrong_secret"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook secret."
    # No outbound request should be made
    mock_post.assert_not_called()

@patch('requests.post')
def test_full_conversation_creates_lead(mock_post, temp_client):
    # Mock outbound send (final reply) to succeed
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    # Simulate the conversation steps via webhook calls
    steps = ["1", "HOME", "John Doe", "Bhubaneswar"]
    for msg in steps:
        payload = make_payload(msg)
        resp = temp_client.post(
            "/webhook",
            json=payload,
            headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')},
        )
        assert resp.status_code == 200
        json_resp = resp.json()
        assert json_resp.get("status") == "sent"
        assert "reply" in json_resp

    # Verify lead was saved in temporary DB
    import leads as leads_mod
    saved = leads_mod.get_leads()
    assert any(l["name"] == "John Doe" and l["location"] == "Bhubaneswar" for l in saved)
    # Verify outbound request called 4 times (once per step) with correct params
    assert mock_post.call_count == 4
    args, kwargs = mock_post.call_args
    expected_url = f"{os.getenv('OPENWA_URL')}/api/sessions/{os.getenv('OPENWA_SESSION_ID')}/messages/send-text"
    assert args[0] == expected_url
    assert kwargs["headers"] == {"X-API-Key": os.getenv('OPENWA_API_KEY')}
    assert kwargs["json"]["chatId"] == "12345@s.whatsapp.net"
    assert "enquiry has been saved" in kwargs["json"]["text"]
    assert kwargs["timeout"] == 20


@patch('requests.post')
def test_conversation_validation_and_recovery(mock_post, temp_client):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    chat_id = "validation-user@c.us"

    # Step 1: Start rooftop solar enquiry
    resp = temp_client.post("/webhook", json=make_payload("1", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert "HOME or BUSINESS" in resp.json()["reply"]

    # Step 2a: Invalid property type
    resp = temp_client.post("/webhook", json=make_payload("apartment", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert "Please reply HOME or BUSINESS" in resp.json()["reply"]

    # Step 2b: Valid property type
    resp = temp_client.post("/webhook", json=make_payload("BUSINESS", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert "What is your name?" in resp.json()["reply"]

    # Step 3a: Name too short
    resp = temp_client.post("/webhook", json=make_payload("X", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert "Please enter your name" in resp.json()["reply"]

    # Step 3b: Valid name
    resp = temp_client.post("/webhook", json=make_payload("Jane Smith", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert "Which city or district" in resp.json()["reply"]

    # Step 4: Valid location -> completes and saves lead
    resp = temp_client.post("/webhook", json=make_payload("Cuttack", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert resp.json().get("status") == "sent"
    assert "enquiry has been saved" in resp.json()["reply"]

    # Verify lead in DB
    import leads as leads_mod
    leads_list = leads_mod.get_leads()
    lead = next((l for l in leads_list if l["name"] == "Jane Smith"), None)
    assert lead is not None
    assert lead["property_type"] == "business"
    assert lead["location"] == "Cuttack"


@patch('requests.post')
def test_conversation_reset_with_menu(mock_post, temp_client):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    chat_id = "reset-user@c.us"
    # Start enquiry
    temp_client.post("/webhook", json=make_payload("1", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})

    # User changes mind and types MENU
    resp = temp_client.post("/webhook", json=make_payload("MENU", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert "Namaskar! Welcome to Solarise Odisha" in resp.json()["reply"]


@patch('requests.post')
def test_informational_menu_options(mock_post, temp_client):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    queries = [
        ("2", "PM Surya Ghar"),
        ("3", "Solar system cost and savings"),
        ("4", "Initial enquiry"),
        ("5", "Net metering"),
        ("6", "maintenance or warranty"),
        ("7", "interest in Solarise Odisha"),
    ]

    for opt, expected_phrase in queries:
        resp = temp_client.post(
            "/webhook",
            json=make_payload(opt, f"info-{opt}@c.us"),
            headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')},
        )
        assert resp.status_code == 200
        assert expected_phrase in resp.json()["reply"]


@patch('requests.post')
def test_chatbot_lead_flow_to_dashboard_lifecycle(mock_post, temp_client):
    """Complete audit: Chatbot conversation -> SQLite DB -> Dashboard API fetch -> Status update -> Dashboard refresh persistence."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    chat_id = "lifecycle-user@c.us"
    # Step 1: Rooftop solar enquiry
    temp_client.post("/webhook", json=make_payload("1", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    # Step 2: Property type
    temp_client.post("/webhook", json=make_payload("business", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    # Step 3: Name
    temp_client.post("/webhook", json=make_payload("Rajesh Sharma", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    # Step 4: Location
    resp_final = temp_client.post("/webhook", json=make_payload("Sambalpur", chat_id), headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp_final.status_code == 200
    assert "enquiry has been saved" in resp_final.json()["reply"]

    # Dashboard Login
    login_resp = temp_client.post("/login", headers={"x-admin-api-key": "testadminkey"})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    # Dashboard Initial Load (GET /leads)
    leads_resp = temp_client.get("/leads", headers=auth_header)
    assert leads_resp.status_code == 200
    leads_list = leads_resp.json()["leads"]
    rajesh_lead = next((l for l in leads_list if l["name"] == "Rajesh Sharma"), None)
    assert rajesh_lead is not None
    assert rajesh_lead["property_type"] == "business"
    assert rajesh_lead["location"] == "Sambalpur"
    assert rajesh_lead["status"] == "New"
    assert rajesh_lead["created_at"] is not None

    lead_id = rajesh_lead["id"]

    # Dashboard Status Update: New -> Contacted
    update_resp1 = temp_client.put(f"/leads/{lead_id}/status", json={"status": "Contacted"}, headers=auth_header)
    assert update_resp1.status_code == 200
    assert update_resp1.json()["new_status"] == "Contacted"

    # Dashboard Refresh (Simulate page reload calling GET /leads)
    refreshed_leads_1 = temp_client.get("/leads", headers=auth_header).json()["leads"]
    persisted_lead_1 = next(l for l in refreshed_leads_1 if l["id"] == lead_id)
    assert persisted_lead_1["status"] == "Contacted"

    # Dashboard Status Update: Contacted -> Converted
    update_resp2 = temp_client.put(f"/leads/{lead_id}/status", json={"status": "Converted"}, headers=auth_header)
    assert update_resp2.status_code == 200
    assert update_resp2.json()["new_status"] == "Converted"

    # Dashboard Refresh 2 (Simulate second page reload calling GET /leads)
    refreshed_leads_2 = temp_client.get("/leads", headers=auth_header).json()["leads"]
    persisted_lead_2 = next(l for l in refreshed_leads_2 if l["id"] == lead_id)
    assert persisted_lead_2["status"] == "Converted"


