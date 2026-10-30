import os
import sys
from unittest.mock import Mock, patch
import pytest
from fastapi.testclient import TestClient
from jose import jwt
import datetime

import main as main_mod

def get_client():
    return TestClient(main_mod.app)

def make_payload(message: str, chat_id: str = "simulation-user@c.us", extra_data: dict | None = None):
    data = {
        "body": message,
        "chatId": chat_id,
        "fromMe": False,
    }
    if extra_data:
        data.update(extra_data)
    return {
        "event": "message.received",
        "data": data,
    }


@patch('requests.post')
def test_simulation_complete_rooftop_enquiry(mock_post):
    """Simulate a complete rooftop solar enquiry from a customer."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    chat_id = "customer-deepak@c.us"
    secret_headers = {"x-webhook-secret": os.getenv('WEBHOOK_SECRET', 'test_webhook_secret')}

    # 1. User starts enquiry
    resp1 = client.post("/webhook", json=make_payload("1", chat_id), headers=secret_headers)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "sent"
    assert "HOME or BUSINESS" in resp1.json()["reply"]

    # 2. User chooses HOME
    resp2 = client.post("/webhook", json=make_payload("HOME", chat_id), headers=secret_headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "sent"
    assert "What is your name?" in resp2.json()["reply"]

    # 3. User provides name
    resp3 = client.post("/webhook", json=make_payload("Deepak Mohanty", chat_id), headers=secret_headers)
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "sent"
    assert "Which city or district" in resp3.json()["reply"]

    # 4. User provides location
    resp4 = client.post("/webhook", json=make_payload("Puri", chat_id), headers=secret_headers)
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "sent"
    assert "Your enquiry has been saved successfully" in resp4.json()["reply"]
    assert "PURI" in resp4.json()["reply"] or "Puri" in resp4.json()["reply"]

    # Verify lead saved in database
    import leads
    leads_list = leads.get_leads()
    saved = next((l for l in leads_list if l["name"] == "Deepak Mohanty"), None)
    assert saved is not None
    assert saved["property_type"] == "home"
    assert saved["location"] == "Puri"
    assert saved["status"] == "New"
    assert mock_post.call_count == 4


@patch('requests.post')
def test_simulation_invalid_inputs_and_recovery(mock_post):
    """Simulate input mistakes at each stage and verify polite recovery."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    chat_id = "customer-anita@c.us"
    secret_headers = {"x-webhook-secret": os.getenv('WEBHOOK_SECRET', 'test_webhook_secret')}

    # Start
    client.post("/webhook", json=make_payload("1", chat_id), headers=secret_headers)

    # Invalid property type
    resp_prop_bad = client.post("/webhook", json=make_payload("villa", chat_id), headers=secret_headers)
    assert "Please reply HOME or BUSINESS" in resp_prop_bad.json()["reply"]

    # Correct property type
    resp_prop_ok = client.post("/webhook", json=make_payload("business", chat_id), headers=secret_headers)
    assert "What is your name?" in resp_prop_ok.json()["reply"]

    # Invalid name (too short)
    resp_name_short = client.post("/webhook", json=make_payload("A", chat_id), headers=secret_headers)
    assert "Please enter your name" in resp_name_short.json()["reply"]

    # Invalid name (too long > 100 chars)
    resp_name_long = client.post("/webhook", json=make_payload("Z" * 105, chat_id), headers=secret_headers)
    assert "Please enter your name" in resp_name_long.json()["reply"]

    # Correct name
    resp_name_ok = client.post("/webhook", json=make_payload("Anita Rao", chat_id), headers=secret_headers)
    assert "Which city or district" in resp_name_ok.json()["reply"]

    # Invalid location (too short)
    resp_loc_short = client.post("/webhook", json=make_payload("X", chat_id), headers=secret_headers)
    assert "Please enter a valid city or district" in resp_loc_short.json()["reply"]

    # Correct location
    resp_loc_ok = client.post("/webhook", json=make_payload("Bhadrak", chat_id), headers=secret_headers)
    assert "Your enquiry has been saved successfully" in resp_loc_ok.json()["reply"]

    # Verify DB
    import leads
    saved = next((l for l in leads.get_leads() if l["name"] == "Anita Rao"), None)
    assert saved is not None
    assert saved["property_type"] == "business"
    assert saved["location"] == "Bhadrak"


@patch('requests.post')
def test_simulation_conversation_reset_variations(mock_post):
    """Verify that greetings (namaskar, hi) and MENU reset conversation at any point."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    chat_id = "reset-tester@c.us"
    secret_headers = {"x-webhook-secret": os.getenv('WEBHOOK_SECRET', 'test_webhook_secret')}

    # Enter enquiry
    client.post("/webhook", json=make_payload("1", chat_id), headers=secret_headers)
    assert chat_id in main_mod.conversation_state

    # Reset via "namaskar"
    resp_reset1 = client.post("/webhook", json=make_payload("namaskar", chat_id), headers=secret_headers)
    assert chat_id not in main_mod.conversation_state
    assert "Namaskar! Welcome to Solarise Odisha" in resp_reset1.json()["reply"]

    # Enter enquiry again and advance
    client.post("/webhook", json=make_payload("1", chat_id), headers=secret_headers)
    client.post("/webhook", json=make_payload("home", chat_id), headers=secret_headers)
    assert main_mod.conversation_state[chat_id]["step"] == "customer_name"

    # Reset via "hi"
    resp_reset2 = client.post("/webhook", json=make_payload("hi", chat_id), headers=secret_headers)
    assert chat_id not in main_mod.conversation_state
    assert "Namaskar! Welcome to Solarise Odisha" in resp_reset2.json()["reply"]


@patch('requests.post')
def test_simulation_duplicate_delivery_idempotency_keys(mock_post):
    """Verify deduplication handles various message ID aliases from webhooks."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    secret_headers = {"x-webhook-secret": os.getenv('WEBHOOK_SECRET', 'test_webhook_secret')}

    # Test with waMessageId
    payload1 = make_payload("hi", "user1@c.us", extra_data={"waMessageId": "wa-unique-991"})
    resp1 = client.post("/webhook", json=payload1, headers=secret_headers)
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "sent"

    # Retry same waMessageId
    resp1_retry = client.post("/webhook", json=payload1, headers=secret_headers)
    assert resp1_retry.status_code == 200
    assert resp1_retry.json()["status"] == "ignored"
    assert resp1_retry.json()["reason"] == "duplicate"

    # Test with idempotencyKey at top level
    payload2 = {
        "event": "message.received",
        "idempotencyKey": "idem-key-772",
        "data": {"body": "hi", "chatId": "user2@c.us", "fromMe": False},
    }
    resp2 = client.post("/webhook", json=payload2, headers=secret_headers)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "sent"

    resp2_retry = client.post("/webhook", json=payload2, headers=secret_headers)
    assert resp2_retry.status_code == 200
    assert resp2_retry.json()["status"] == "ignored"
    assert resp2_retry.json()["reason"] == "duplicate"


@patch('requests.post')
def test_lead_saved_even_if_openwa_send_fails(mock_post):
    """Critical data-integrity test: If OpenWA outgoing send throws an error, the lead MUST still be saved in DB."""
    # First 3 steps succeed, 4th step (outgoing send) fails with network error
    step_call_count = 0
    def side_effect(*args, **kwargs):
        nonlocal step_call_count
        step_call_count += 1
        if step_call_count >= 4:
            raise Exception("OpenWA connection timeout")
        mock_r = Mock()
        mock_r.raise_for_status.return_value = None
        return mock_r

    mock_post.side_effect = side_effect

    client = get_client()
    chat_id = "resilience-user@c.us"
    secret_headers = {"x-webhook-secret": os.getenv('WEBHOOK_SECRET', 'test_webhook_secret')}

    client.post("/webhook", json=make_payload("1", chat_id), headers=secret_headers)
    client.post("/webhook", json=make_payload("home", chat_id), headers=secret_headers)
    client.post("/webhook", json=make_payload("Resilient Customer", chat_id), headers=secret_headers)
    resp4 = client.post("/webhook", json=make_payload("Balasore", chat_id), headers=secret_headers)

    assert resp4.status_code == 200
    assert resp4.json()["status"] == "send_failed"

    # Verify that despite outgoing send failure, customer lead was safely committed to DB
    import leads
    saved = next((l for l in leads.get_leads() if l["name"] == "Resilient Customer"), None)
    assert saved is not None
    assert saved["property_type"] == "home"
    assert saved["location"] == "Balasore"
    assert saved["status"] == "New"


def test_security_jwt_tampering_and_expiry():
    """Verify that forged, expired, or wrong-secret JWT tokens are rejected."""
    client = get_client()

    # 1. Expired token
    expired_token = jwt.encode(
        {"sub": "admin", "exp": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10)},
        os.getenv('JWT_SECRET', 'testjwtsecret'),
        algorithm="HS256"
    )
    resp_exp = client.get("/leads", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp_exp.status_code == 401
    assert "Invalid authentication token" in resp_exp.json()["detail"]

    # 2. Token signed with wrong secret
    tampered_token = jwt.encode(
        {"sub": "admin", "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=60)},
        "completely_wrong_secret",
        algorithm="HS256"
    )
    resp_tamper = client.get("/leads", headers={"Authorization": f"Bearer {tampered_token}"})
    assert resp_tamper.status_code == 401

    # 3. Unauthorized POST /leads (lead creation without JWT)
    resp_unauth_create = client.post("/leads", json={"name": "Attacker", "property_type": "home", "location": "Nowhere"})
    assert resp_unauth_create.status_code == 401

    # 4. Unauthorized PUT /leads/1/status
    resp_unauth_put = client.put("/leads/1/status", json={"status": "Converted"})
    assert resp_unauth_put.status_code == 401


def test_is_sender_allowed_normalization():
    """Verify normalization and fail-closed logic of is_sender_allowed."""
    from main import is_sender_allowed

    # 1. Fail closed if missing or empty
    assert not is_sender_allowed("919876543210@c.us", None)
    assert not is_sender_allowed("919876543210@c.us", "")
    assert not is_sender_allowed("919876543210@c.us", "   ")

    # 2. Universal testing wildcard
    assert is_sender_allowed("919876543210@c.us", "*")
    assert is_sender_allowed("random_sender@c.us", "test, *")

    # 3. Phone number formatting variations matching
    allowed_config = "+91 98765-43210"
    assert is_sender_allowed("919876543210@c.us", allowed_config)
    assert is_sender_allowed("9876543210@c.us", allowed_config)
    assert is_sender_allowed("919876543210@s.whatsapp.net", allowed_config)
    assert is_sender_allowed("+919876543210", allowed_config)
    assert is_sender_allowed("919876543210", allowed_config)

    # 4. Reject unauthorized numbers
    assert not is_sender_allowed("919999999999@c.us", allowed_config)
    assert not is_sender_allowed("9876500000@c.us", allowed_config)
    assert not is_sender_allowed("attacker@c.us", allowed_config)


@patch("requests.post")
def test_webhook_responds_to_any_individual_phone_number(mock_post):
    """Verify that inbound webhook responds to incoming WhatsApp messages from any individual phone number."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    secret_headers = {"x-webhook-secret": os.getenv("WEBHOOK_SECRET", "test_webhook_secret")}

    # Individual phone numbers are processed and replied to
    resp1 = client.post(
        "/webhook",
        json=make_payload("Hello", chat_id="919999911111@c.us"),
        headers=secret_headers,
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "sent"

    resp2 = client.post(
        "/webhook",
        json=make_payload("1", chat_id="919876543210@c.us"),
        headers=secret_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "sent"
    assert mock_post.call_count == 2


@patch("requests.post")
def test_webhook_responds_regardless_of_allowed_test_phone_env(mock_post):
    """Verify webhook responds to individual senders even if ALLOWED_TEST_PHONE is unset, empty, or set."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    secret_headers = {"x-webhook-secret": os.getenv("WEBHOOK_SECRET", "test_webhook_secret")}

    # Case 1: Empty string
    with patch.dict(os.environ, {"ALLOWED_TEST_PHONE": ""}):
        resp = client.post(
            "/webhook",
            json=make_payload("Hello", chat_id="919876543210@c.us"),
            headers=secret_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    # Case 2: Whitespace only
    with patch.dict(os.environ, {"ALLOWED_TEST_PHONE": "   "}):
        resp = client.post(
            "/webhook",
            json=make_payload("Hello", chat_id="919876543211@c.us"),
            headers=secret_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    # Case 3: Missing/Unset
    env_without_phone = os.environ.copy()
    env_without_phone.pop("ALLOWED_TEST_PHONE", None)
    with patch.dict(os.environ, env_without_phone, clear=True):
        resp = client.post(
            "/webhook",
            json=make_payload("Hello", chat_id="919876543212@c.us"),
            headers=secret_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    assert mock_post.call_count == 3


@patch("requests.post")
def test_webhook_preserves_group_status_and_self_filters_even_if_allowlisted(mock_post):
    """Ensure group chats, broadcasts, and fromMe=True messages are always ignored."""
    client = get_client()
    secret_headers = {"x-webhook-secret": os.getenv("WEBHOOK_SECRET", "test_webhook_secret")}

    with patch.dict(os.environ, {"ALLOWED_TEST_PHONE": "*"}):
        # 1. Group message
        resp_grp = client.post(
            "/webhook",
            json=make_payload("Hello", chat_id="123456789-group@g.us"),
            headers=secret_headers,
        )
        assert resp_grp.json() == {"status": "ignored"}

        # 2. Broadcast / status message
        resp_bcast = client.post(
            "/webhook",
            json=make_payload("Hello", chat_id="status@broadcast"),
            headers=secret_headers,
        )
        assert resp_bcast.json() == {"status": "ignored"}

        # 3. fromMe message
        resp_self = client.post(
            "/webhook",
            json=make_payload("Hello", chat_id="919876543210@c.us", extra_data={"fromMe": True}),
            headers=secret_headers,
        )
        assert resp_self.json() == {"status": "ignored"}

    assert mock_post.call_count == 0


@patch('requests.post')
def test_multi_customer_concurrent_state_isolation(mock_post):
    """Verify that multiple customers have independent conversation states without collision."""
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = get_client()
    cust_a = "cust-alpha@c.us"
    cust_b = "cust-beta@c.us"
    secret_headers = {"x-webhook-secret": os.getenv('WEBHOOK_SECRET', 'test_webhook_secret')}

    with patch.dict(os.environ, {"ALLOWED_TEST_PHONE": f"{cust_a},{cust_b}"}):
        # Customer A starts enquiry
        r1_a = client.post("/webhook", json=make_payload("1", chat_id=cust_a), headers=secret_headers)
        assert "HOME or BUSINESS" in r1_a.json()["reply"]

        # Customer B asks for subsidy info
        r1_b = client.post("/webhook", json=make_payload("2", chat_id=cust_b), headers=secret_headers)
        assert "PM Surya Ghar" in r1_b.json()["reply"]

        # Customer A chooses HOME
        r2_a = client.post("/webhook", json=make_payload("home", chat_id=cust_a), headers=secret_headers)
        assert "What is your name?" in r2_a.json()["reply"]

        # Customer B now starts rooftop enquiry
        r2_b = client.post("/webhook", json=make_payload("1", chat_id=cust_b), headers=secret_headers)
        assert "HOME or BUSINESS" in r2_b.json()["reply"]

        # Customer A enters name
        r3_a = client.post("/webhook", json=make_payload("Alpha Customer", chat_id=cust_a), headers=secret_headers)
        assert "Which city or district" in r3_a.json()["reply"]

        # Customer B chooses BUSINESS
        r3_b = client.post("/webhook", json=make_payload("business", chat_id=cust_b), headers=secret_headers)
        assert "What is your name?" in r3_b.json()["reply"]

        # Customer A enters location -> completes enquiry
        r4_a = client.post("/webhook", json=make_payload("Bhubaneswar", chat_id=cust_a), headers=secret_headers)
        assert "saved successfully" in r4_a.json()["reply"]

        # Customer B enters name
        r4_b = client.post("/webhook", json=make_payload("Beta Enterprise", chat_id=cust_b), headers=secret_headers)
        assert "Which city or district" in r4_b.json()["reply"]

        # Customer B enters location -> completes enquiry
        r5_b = client.post("/webhook", json=make_payload("Cuttack", chat_id=cust_b), headers=secret_headers)
        assert "saved successfully" in r5_b.json()["reply"]

