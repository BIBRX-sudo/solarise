import os
import sys
from unittest.mock import Mock, patch
import pytest
from fastapi.testclient import TestClient

# Ensure env vars are set for test imports
os.environ.setdefault('ADMIN_API_KEY', 'testadminkey')
os.environ.setdefault('JWT_SECRET', 'testjwtsecret')
os.environ.setdefault('OPENWA_URL', 'http://127.0.0.1:2785')
os.environ.setdefault('OPENWA_API_KEY', 'test_openwa_key')
os.environ.setdefault('OPENWA_SESSION_ID', 'test_session')
os.environ.setdefault('WEBHOOK_SECRET', 'test_webhook_secret')

# Reload the FastAPI app after setting env vars
import importlib, main as main_mod
importlib.reload(main_mod)
client = TestClient(main_mod.app)

def make_payload_flat(message: str, chat_id: str = "12345@s.whatsapp.net"):
    return {
        "event": "message.received",
        "data": {
            "body": message,
            "chatId": chat_id,
            "fromMe": False,
        },
    }

def make_payload_nested(message: str, chat_id: str = "12345@s.whatsapp.net"):
    return {
        "event": "message.received",
        "data": {
            "message": {"text": message},
            "chatId": chat_id,
            "fromMe": False,
        },
    }

@patch('requests.post')
def test_webhook_flat_payload_success(mock_post):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response
    payload = make_payload_flat("hi")
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    json_resp = resp.json()
    assert json_resp["status"] == "sent"
    expected_url = f"{os.getenv('OPENWA_URL')}/api/sessions/{os.getenv('OPENWA_SESSION_ID')}/messages/send-text"
    args, kwargs = mock_post.call_args
    assert args[0] == expected_url
    assert kwargs["headers"] == {"X-API-Key": os.getenv('OPENWA_API_KEY')}
    assert kwargs["json"]["chatId"] == "12345@s.whatsapp.net"
    assert isinstance(kwargs["json"]["text"], str)

@patch('requests.post')
def test_webhook_nested_payload_success(mock_post):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response
    payload = make_payload_nested("hello")
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    json_resp = resp.json()
    assert json_resp["status"] == "sent"
    expected_url = f"{os.getenv('OPENWA_URL')}/api/sessions/{os.getenv('OPENWA_SESSION_ID')}/messages/send-text"
    args, kwargs = mock_post.call_args
    assert args[0] == expected_url
    assert isinstance(kwargs["json"]["text"], str)

def test_webhook_missing_body_and_message():
    payload = {
        "event": "message.received",
        "data": {"chatId": "12345@s.whatsapp.net", "fromMe": False},
    }
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"

def test_webhook_invalid_secret():
    payload = make_payload_flat("test")
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid webhook secret."

def test_webhook_self_sent_message():
    payload = {
        "event": "message.received",
        "data": {"body": "hi", "chatId": "12345@s.whatsapp.net", "fromMe": True},
    }
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"

def test_webhook_group_message():
    payload = {
        "event": "message.received",
        "data": {"body": "hi", "chatId": "12345@g.us", "fromMe": False},
    }
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_group_flag_message():
    payload = {
        "event": "message.received",
        "data": {"body": "hi", "chatId": "12345@s.whatsapp.net", "fromMe": False, "isGroup": True},
    }
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_broadcast_message():
    payload = {
        "event": "message.received",
        "data": {"body": "hi", "chatId": "status@broadcast", "fromMe": False},
    }
    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


@patch('requests.post')
def test_webhook_duplicate_delivery(mock_post):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    payload = {
        "event": "message.received",
        "data": {
            "id": "unique-msg-test-12345",
            "body": "hi",
            "chatId": "test-dup-user@c.us",
            "fromMe": False,
        },
    }
    # First delivery
    resp1 = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "sent"

    # Second delivery (duplicate retry)
    resp2 = client.post("/webhook", json=payload, headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')})
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "ignored"
    assert resp2.json()["reason"] == "duplicate"


def test_webhook_hmac_signature():
    import hmac, hashlib, json
    payload = {
        "event": "message.received",
        "data": {"body": "hi", "chatId": "test-hmac@c.us", "fromMe": True},
    }
    secret = os.getenv('WEBHOOK_SECRET', '')
    raw_body = json.dumps(payload, separators=(',', ':')).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    resp = client.post("/webhook", json=payload, headers={"x-openwa-signature": sig})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"

