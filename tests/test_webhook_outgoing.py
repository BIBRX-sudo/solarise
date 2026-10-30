import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch

# Set required env vars before importing the FastAPI app
os.environ.setdefault('ADMIN_API_KEY', 'testadminkey')
os.environ.setdefault('JWT_SECRET', 'testjwtsecret')
os.environ.setdefault('OPENWA_URL', 'http://127.0.0.1:2785')
os.environ.setdefault('OPENWA_API_KEY', 'test_openwa_key')
os.environ.setdefault('OPENWA_SESSION_ID', 'test_session')
os.environ.setdefault('WEBHOOK_SECRET', 'test_webhook_secret')

# app will be imported lazily in each test after env vars are set

def get_client():
    import importlib, main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


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
def test_webhook_successful_send(mock_post):
    # Mock a successful response from OpenWA
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    payload = make_payload("hi")
    client = get_client()
    response = client.post(
        "/webhook",
        json=payload,
        headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')},
    )
    assert response.status_code == 200
    json_resp = response.json()
    # Should indicate that the message was sent
    assert json_resp.get("status") == "sent"
    # Verify the outgoing request was called with correct URL and headers
    expected_url = f"{os.getenv('OPENWA_URL')}/api/sessions/{os.getenv('OPENWA_SESSION_ID')}/messages/send-text"
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == expected_url
    assert kwargs["headers"] == {"X-API-Key": os.getenv('OPENWA_API_KEY')}
    # The json payload should contain chatId and text keys
    assert kwargs["json"]["chatId"] == "12345@s.whatsapp.net"
    assert isinstance(kwargs["json"]["text"], str)
    assert kwargs["timeout"] == 20

@patch('requests.post')
def test_webhook_send_failure(mock_post):
    # Simulate a request exception from OpenWA
    mock_post.side_effect = Exception("network error")

    payload = make_payload("hello")
    client = get_client()
    response = client.post(
        "/webhook",
        json=payload,
        headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')},
    )
    assert response.status_code == 200
    json_resp = response.json()
    # Failure should be reported with status "send_failed"
    assert json_resp.get("status") == "send_failed"

def test_webhook_missing_credentials():
    # Temporarily clear OpenWA config env vars
    with patch.dict(os.environ, {"OPENWA_API_KEY": "", "OPENWA_SESSION_ID": ""}):
        # Reload the module to pick up env changes
        import importlib, main as main_mod
        importlib.reload(main_mod)
        client_local = TestClient(main_mod.app)
        payload = make_payload("test")
        response = client_local.post(
            "/webhook",
            json=payload,
            headers={"x-webhook-secret": os.getenv('WEBHOOK_SECRET')},
        )
        assert response.status_code == 200
        json_resp = response.json()
        # Should not attempt to send and return reply_generated
        assert json_resp.get("status") == "reply_generated"
        assert "reply" in json_resp
