import os
import json
from unittest.mock import Mock, patch
import pytest
from fastapi.testclient import TestClient

import main as main_mod
import groq_service
from chatbot import get_reply


@pytest.fixture(autouse=True)
def reset_budget_and_env():
    """Ensure clean budget tracker and isolated environment for each test."""
    groq_service.budget_tracker.reset()
    yield
    groq_service.budget_tracker.reset()


def make_webhook_payload(message: str, chat_id: str = "customer-9876543210@c.us", msg_id: str = None, from_me: bool = False):
    data = {
        "body": message,
        "chatId": chat_id,
        "fromMe": from_me,
    }
    if msg_id:
        data["id"] = msg_id
    return {
        "event": "message.received",
        "data": data,
    }


def make_mock_groq_response(content: str):
    """Helper to mock Groq chat completion response."""
    mock_choice = Mock()
    mock_choice.message.content = content
    mock_resp = Mock()
    mock_resp.choices = [mock_choice]
    return mock_resp


# =====================================================================
# 1. Normal customer questions
# =====================================================================

@patch("groq_service.is_groq_configured", return_value=True)
@patch("groq.Groq")
def test_normal_customer_question(mock_groq_class, mock_configured):
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    ai_answer = (
        "Namaskar! A 3kW rooftop solar system in Bhubaneswar generates around 12-14 units per day, "
        "saving up to 80-90% on electricity bills. Eligible households can also get up to ₹78,000 "
        "subsidy under PM Surya Ghar. Reply 1 for a free site assessment or 7 to talk with our team!"
    )
    mock_client.chat.completions.create.return_value = make_mock_groq_response(ai_answer)

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345", "GROQ_MODEL": "llama-3.3-70b-versatile"}):
        reply = get_reply("How much will a 3kW solar system generate in Bhubaneswar?")
        assert reply == ai_answer
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "llama-3.3-70b-versatile"
        assert call_kwargs["max_tokens"] == groq_service.DEFAULT_MAX_TOKENS
        assert call_kwargs["temperature"] == 0.3
        assert any("Bhubaneswar" in m["content"] for m in call_kwargs["messages"])


# =====================================================================
# 2. Hindi and Hinglish messages
# =====================================================================

@patch("groq_service.is_groq_configured", return_value=True)
@patch("groq.Groq")
def test_hindi_customer_question(mock_groq_class, mock_configured):
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    hindi_reply = (
        "नमस्कार! सोलर ऊर्जा के बारे में पूछने के लिए धन्यवाद। पीएम सूर्य घर योजना के तहत "
        "3kW तक के सिस्टम पर अधिकतम ₹78,000 की सब्सिडी मिलती है। कृपया साइट सर्वे के लिए 1 दबाएं "
        "या हमारी टीम से बात करने के लिए 7 दबाएं।"
    )
    mock_client.chat.completions.create.return_value = make_mock_groq_response(hindi_reply)

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        reply = get_reply("पीएम सूर्य घर योजना में कितनी सब्सिडी मिलती है?")
        assert "नमस्कार!" in reply
        assert "₹78,000" in reply


@patch("groq_service.is_groq_configured", return_value=True)
@patch("groq.Groq")
def test_hinglish_customer_question(mock_groq_class, mock_configured):
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    hinglish_reply = (
        "Hello! 5kW commercial system par PM Surya Ghar residential subsidy apply nahi hoti, "
        "lekin aapko accelerated depreciation aur electricity bill par bhari savings milti hai. "
        "Consultation book karne ke liye 1 reply karein ya team se baat karne ke liye 7 reply karein."
    )
    mock_client.chat.completions.create.return_value = make_mock_groq_response(hinglish_reply)

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        reply = get_reply("Commercial shop ke liye solar lagane par subsidy milegi kya?")
        assert "commercial" in reply.lower()
        assert "1" in reply and "7" in reply


# =====================================================================
# 3. Existing menu commands preserve deterministic behavior
# =====================================================================

@patch("groq_service.is_groq_configured", return_value=True)
@patch("groq.Groq")
def test_menu_commands_do_not_call_ai(mock_groq_class, mock_configured):
    mock_client = Mock()
    mock_groq_class.return_value = mock_client

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        # Menu options
        for cmd in ["hi", "hello", "menu", "1", "2", "3", "4", "5", "6", "7"]:
            reply = get_reply(cmd)
            assert isinstance(reply, str)
            assert len(reply) > 10

        # AI should never be invoked for deterministic menu options
        mock_client.chat.completions.create.assert_not_called()


# =====================================================================
# 4. Missing API key handling
# =====================================================================

def test_missing_api_key_returns_safe_fallback():
    with patch.dict(os.environ, {"GROQ_API_KEY": ""}, clear=False):
        reply = groq_service.get_ai_solar_reply("Can I get solar panels installed on EMI?")
        assert "Namaskar!" in reply
        assert "AI assistant is temporarily unavailable" in reply
        assert "Reply 1" in reply
        assert "Reply 7" in reply


# =====================================================================
# 5. Groq API failure and timeout
# =====================================================================

@patch("groq.Groq")
def test_groq_timeout_returns_safe_fallback(mock_groq_class):
    from groq import APITimeoutError
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = APITimeoutError(request=Mock())

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        reply = groq_service.get_ai_solar_reply("What is the cost of solar inverter?")
        assert "temporarily unavailable" in reply
        assert "Reply 7" in reply


@patch("groq.Groq")
def test_groq_connection_error_returns_safe_fallback(mock_groq_class):
    from groq import APIConnectionError
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = APIConnectionError(request=Mock())

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        reply = groq_service.get_ai_solar_reply("How long does solar installation take?")
        assert "temporarily unavailable" in reply


@patch("groq.Groq")
def test_groq_api_500_error_returns_safe_fallback(mock_groq_class):
    from groq import APIError
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = APIError(
        message="Internal Server Error",
        request=Mock(),
        body=None,
    )

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        reply = groq_service.get_ai_solar_reply("Tell me about net metering.")
        assert "temporarily unavailable" in reply


# =====================================================================
# 6. Rate-limit and quota exhaustion (Remote & Local daily budget)
# =====================================================================

@patch("groq.Groq")
def test_groq_rate_limit_error_returns_capacity_fallback(mock_groq_class):
    from groq import RateLimitError
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = RateLimitError(
        message="Rate limit reached for model",
        response=Mock(status_code=429),
        body=None,
    )

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        reply = groq_service.get_ai_solar_reply("What is your warranty policy?")
        assert "high volume of enquiries" in reply
        assert "Reply 7" in reply


@patch("groq.Groq")
def test_local_daily_budget_exhaustion(mock_groq_class):
    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    mock_client.chat.completions.create.return_value = make_mock_groq_response("Helpful solar answer")

    # Set daily limit to 2
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345", "GROQ_DAILY_LIMIT": "2"}):
        # Request 1: succeeds
        r1 = groq_service.get_ai_solar_reply("Question 1")
        assert r1 == "Helpful solar answer"
        assert mock_client.chat.completions.create.call_count == 1

        # Request 2: succeeds
        r2 = groq_service.get_ai_solar_reply("Question 2")
        assert r2 == "Helpful solar answer"
        assert mock_client.chat.completions.create.call_count == 2

        # Request 3: blocked by local budget safeguard! Groq API not called.
        r3 = groq_service.get_ai_solar_reply("Question 3")
        assert "high volume of enquiries" in r3
        assert mock_client.chat.completions.create.call_count == 2  # Not called again!


# =====================================================================
# 7. Duplicate incoming messages via Webhook
# =====================================================================

@patch("requests.post")
def test_webhook_duplicate_message_handling(mock_post):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    client = TestClient(main_mod.app)
    secret = os.getenv("WEBHOOK_SECRET", "test_webhook_secret")
    payload = make_webhook_payload("hi", msg_id="groq-test-duplicate-999")

    # First delivery
    resp1 = client.post("/webhook", json=payload, headers={"x-webhook-secret": secret})
    assert resp1.status_code == 200
    assert resp1.json().get("status") == "sent"

    # Second delivery (retried delivery of same msg ID)
    resp2 = client.post("/webhook", json=payload, headers={"x-webhook-secret": secret})
    assert resp2.status_code == 200
    assert resp2.json().get("status") == "ignored"
    assert resp2.json().get("reason") == "duplicate"


# =====================================================================
# 8. Bot-originated messages
# =====================================================================

def test_webhook_ignores_from_me_messages():
    client = TestClient(main_mod.app)
    secret = os.getenv("WEBHOOK_SECRET", "test_webhook_secret")
    payload = make_webhook_payload("Hello from bot", from_me=True)

    resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": secret})
    assert resp.status_code == 200
    assert resp.json().get("status") == "ignored"


# =====================================================================
# 9. Webhook security
# =====================================================================

def test_webhook_security_rejection():
    client = TestClient(main_mod.app)
    payload = make_webhook_payload("hi")

    # Missing header
    resp1 = client.post("/webhook", json=payload)
    assert resp1.status_code == 401

    # Wrong secret
    resp2 = client.post("/webhook", json=payload, headers={"x-webhook-secret": "invalid-secret-key"})
    assert resp2.status_code == 401
    assert resp2.json()["detail"] == "Invalid webhook secret."


# =====================================================================
# 10. Webhook end-to-end integration with Groq AI response
# =====================================================================

@patch("requests.post")
@patch("groq_service.is_groq_configured", return_value=True)
@patch("groq.Groq")
def test_webhook_groq_integration_flow(mock_groq_class, mock_configured, mock_post):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    mock_client = Mock()
    mock_groq_class.return_value = mock_client
    ai_reply = "Rooftop solar reduces grid dependency and cuts bills by up to 90%. Reply 7 to speak with our team!"
    mock_client.chat.completions.create.return_value = make_mock_groq_response(ai_reply)

    client = TestClient(main_mod.app)
    secret = os.getenv("WEBHOOK_SECRET", "test_webhook_secret")
    payload = make_webhook_payload("How much will solar reduce my electricity bill?", chat_id="919876543210@c.us")

    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_testkey12345"}):
        resp = client.post("/webhook", json=payload, headers={"x-webhook-secret": secret})
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"
        assert resp.json()["reply"] == ai_reply

        # Verify OpenWA outgoing send payload
        assert mock_post.call_count == 1
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["chatId"] == "919876543210@c.us"
        assert kwargs["json"]["text"] == ai_reply
