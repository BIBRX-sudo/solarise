import os
import json
import hmac
import hashlib
from unittest.mock import Mock, patch
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_API_KEY", "testadminkey")
os.environ.setdefault("JWT_SECRET", "testjwtsecret")
os.environ.setdefault("OPENWA_URL", "http://127.0.0.1:2785")
os.environ.setdefault("OPENWA_API_KEY", "test_openwa_key")
os.environ.setdefault("OPENWA_SESSION_ID", "b9290ec0-2954-4be2-a32d-111512c06f7e")
os.environ.setdefault("WEBHOOK_SECRET", "test_webhook_signing_secret_12345")

import main as main_module

@pytest.fixture
def client():
    return TestClient(main_module.app)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_chat_endpoint(client):
    response = client.post("/chat", json={"message": "menu"})
    assert response.status_code == 200
    assert "reply" in response.json()
    assert "Solarise Odisha" in response.json()["reply"]


def test_webhook_rejects_missing_auth(client):
    payload = {"event": "message.received", "data": {"body": "hi", "chatId": "918144676855@c.us"}}
    response = client.post("/webhook", json=payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook secret."


def test_webhook_rejects_wrong_secret(client):
    payload = {"event": "message.received", "data": {"body": "hi", "chatId": "918144676855@c.us"}}
    response = client.post("/webhook", json=payload, headers={"x-webhook-secret": "wrong-secret"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook secret."


def test_webhook_rejects_wrong_hmac_signature(client):
    payload = {"event": "message.received", "data": {"body": "hi", "chatId": "918144676855@c.us"}}
    response = client.post(
        "/webhook",
        json=payload,
        headers={"x-openwa-signature": "sha256=0000000000000000000000000000000000000000000000000000000000000000"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook secret."


@patch("requests.post")
def test_webhook_openwa_exact_format_success(mock_post, client):
    """Matches the exact OpenWA MessageListItemDto payload dispatched by OpenWA gateway."""
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_post.return_value = mock_resp

    openwa_payload = {
        "event": "message.received",
        "timestamp": "2026-10-25T10:00:00.000Z",
        "sessionId": "b9290ec0-2954-4be2-a32d-111512c06f7e",
        "deliveryId": "dlv_test_12345",
        "idempotencyKey": "msg_test_key_12345",
        "data": {
            "id": "msg_uuid_0001",
            "sessionId": "b9290ec0-2954-4be2-a32d-111512c06f7e",
            "waMessageId": "false_918144676855@c.us_3EB0123456789",
            "chatId": "918144676855@c.us",
            "chatName": "Bishwajit Bag",
            "author": None,
            "from": "918144676855@c.us",
            "to": "919999999999@c.us",
            "body": "hi",
            "type": "text",
            "direction": "incoming",
            "timestamp": 1700000010,
            "status": "delivered",
        },
    }

    # Verify using raw HMAC signature
    secret = os.environ["WEBHOOK_SECRET"]
    raw_bytes = json.dumps(openwa_payload, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=raw_bytes,
        headers={"content-type": "application/json", "x-openwa-signature": signature},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert "Solarise Odisha" in response.json()["reply"]

    # Verify mock outbound call
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    call_json = mock_post.call_args[1]["json"]
    call_headers = mock_post.call_args[1]["headers"]
    expected_session_id = os.getenv("OPENWA_SESSION_ID", "b9290ec0-2954-4be2-a32d-111512c06f7e")
    assert call_url == f"http://127.0.0.1:2785/api/sessions/{expected_session_id}/messages/send-text"
    assert call_json["chatId"] == "918144676855@c.us"
    assert "Solarise Odisha" in call_json["text"]
    assert call_headers["X-API-Key"] == os.environ["OPENWA_API_KEY"]


@patch("requests.post")
def test_webhook_ignores_outgoing_direction(mock_post, client):
    """Messages with direction='outgoing' (bot-sent) must be ignored."""
    openwa_payload = {
        "event": "message.received",
        "sessionId": "b9290ec0-2954-4be2-a32d-111512c06f7e",
        "data": {
            "id": "msg_outgoing_0001",
            "chatId": "918144676855@c.us",
            "body": "Hello from bot",
            "type": "text",
            "direction": "outgoing",
        },
    }
    response = client.post(
        "/webhook",
        json=openwa_payload,
        headers={"x-webhook-secret": os.environ["WEBHOOK_SECRET"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    mock_post.assert_not_called()


@patch("requests.post")
def test_webhook_ignores_non_text_messages(mock_post, client):
    """Non-text messages (e.g. image, voice, audio) must be ignored."""
    openwa_payload = {
        "event": "message.received",
        "sessionId": "b9290ec0-2954-4be2-a32d-111512c06f7e",
        "data": {
            "id": "msg_image_0001",
            "chatId": "918144676855@c.us",
            "body": None,
            "type": "image",
            "direction": "incoming",
        },
    }
    response = client.post(
        "/webhook",
        json=openwa_payload,
        headers={"x-webhook-secret": os.environ["WEBHOOK_SECRET"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    mock_post.assert_not_called()


@patch("requests.post")
def test_webhook_ignores_newsletter_channel_messages(mock_post, client):
    """WhatsApp newsletter/channel updates must be ignored."""
    openwa_payload = {
        "event": "message.received",
        "sessionId": "b9290ec0-2954-4be2-a32d-111512c06f7e",
        "data": {
            "id": "msg_news_0001",
            "chatId": "120363176029234895@newsletter",
            "body": "News headline",
            "type": "text",
            "direction": "incoming",
        },
    }
    response = client.post(
        "/webhook",
        json=openwa_payload,
        headers={"x-webhook-secret": os.environ["WEBHOOK_SECRET"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    mock_post.assert_not_called()


@patch("requests.post")
def test_webhook_unicode_hmac_verification(mock_post, client):
    """Verifies that unicode characters (e.g. Odia/Hindi script and emojis) verify correctly under HMAC."""
    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_post.return_value = mock_resp

    openwa_payload = {
        "event": "message.received",
        "sessionId": "b9290ec0-2954-4be2-a32d-111512c06f7e",
        "data": {
            "id": "msg_unicode_0001",
            "chatId": "918144676855@c.us",
            "body": "ନମସ୍କାର ☀️",
            "type": "text",
            "direction": "incoming",
        },
    }
    secret = os.environ["WEBHOOK_SECRET"]
    raw_bytes = json.dumps(openwa_payload, ensure_ascii=False).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook",
        content=raw_bytes,
        headers={"content-type": "application/json", "x-openwa-signature": sig},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    mock_post.assert_called_once()
