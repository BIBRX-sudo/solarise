
import os
import json
import logging
import secrets
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Header, Depends, Security, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse
import datetime
from jose import JWTError, jwt

from chatbot import get_reply
from leads import save_lead, get_leads, update_lead_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_OPENWA_URL = "http://127.0.0.1:2785"
DEFAULT_OPENWA_SESSION_ID = "b9290ec0-2954-4be2-a32d-111512c06f7e"

OPENWA_URL = os.getenv("OPENWA_URL", DEFAULT_OPENWA_URL).rstrip("/")
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY", "")
OPENWA_SESSION_ID = os.getenv("OPENWA_SESSION_ID", DEFAULT_OPENWA_SESSION_ID)
SESSION_ID = OPENWA_SESSION_ID
BUSINESS_NUMBER = os.getenv("BUSINESS_NUMBER", "")  # optional dedicated business WhatsApp number
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

PROCESSED_MESSAGES_LIMIT = 2000
processed_message_ids: set[str] = set()
processed_message_order: list[str] = []


def is_duplicate_message(msg_id: str | None) -> bool:
    if not msg_id:
        return False
    if msg_id in processed_message_ids:
        return True
    processed_message_ids.add(msg_id)
    processed_message_order.append(msg_id)
    if len(processed_message_order) > PROCESSED_MESSAGES_LIMIT:
        oldest = processed_message_order.pop(0)
        processed_message_ids.discard(oldest)
    return False


def is_sender_allowed(chat_id: str, allowed_config: str | None) -> bool:
    """Enforces sender allowlist for safe personal testing.
    If allowed_config is missing or empty, rejects all messages by default (fail closed).
    Normalizes phone numbers, country codes, and chat ID variations.
    """
    if not allowed_config or not allowed_config.strip():
        return False

    allowed_tokens = [t.strip() for t in allowed_config.split(",") if t.strip()]
    if "*" in allowed_tokens:
        return True

    chat_clean = chat_id.split("@")[0].strip()
    chat_digits = "".join(ch for ch in chat_clean if ch.isdigit())

    for token in allowed_tokens:
        token_clean = token.split("@")[0].strip()
        token_digits = "".join(ch for ch in token_clean if ch.isdigit())

        # Exact match on identifier or prefix
        if token.lower() == chat_id.lower() or token_clean.lower() == chat_clean.lower():
            return True

        # Normalized phone number matching (e.g. +91 98765-43210 vs 919876543210@c.us)
        if token_digits and chat_digits:
            if token_digits == chat_digits or chat_digits.endswith(token_digits) or token_digits.endswith(chat_digits):
                return True

    return False

ALLOWED_STATUSES = {"New", "Contacted", "Converted"}
ALLOWED_PROPERTY_TYPES = {"home", "business"}

app = FastAPI(
    title="Solarise Odisha Chatbot",
    description="Chatbot and lead management for ADP Green Energy Solutions",
    version="1.3.0",
)

# CORS configuration – origins are read from CORS_ALLOWED_ORIGINS env var.
# If the variable is unset, default to the local dashboard origin.
origins_env = os.getenv("CORS_ALLOWED_ORIGINS", "http://127.0.0.1:8009")
allowed_origins = [origin.strip() for origin in origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/dashboard.html", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def serve_dashboard():
    dashboard_path = Path(__file__).resolve().parent / "dashboard.html"
    if not dashboard_path.is_file():
        raise HTTPException(status_code=404, detail="Dashboard file not found.")
    return FileResponse(dashboard_path, media_type="text/html")


conversation_state: dict[str, dict[str, Any]] = {}
leads_state: dict[str, dict[str, Any]] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class LeadStatusRequest(BaseModel):
    status: str


class ManualLeadRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    property_type: str = Field(min_length=1, max_length=20)
    location: str = Field(min_length=2, max_length=150)


def require_admin(
    x_admin_api_key: str | None = Header(default=None),
):
    current_admin_key = os.getenv("ADMIN_API_KEY", ADMIN_API_KEY)
    if not current_admin_key:
        raise HTTPException(
            status_code=503,
            detail="Admin authentication is not configured.",
        )

    if not x_admin_api_key or not secrets.compare_digest(
        x_admin_api_key, current_admin_key
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing admin API key.",
        )

# JWT utilities
security = HTTPBearer(auto_error=False)

def create_access_token(data: dict, expires_delta: datetime.timedelta | None = None) -> str:
    current_jwt_secret = os.getenv("JWT_SECRET", JWT_SECRET)
    if not current_jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="JWT authentication is not configured.",
        )
    to_encode = data.copy()
    expire = datetime.datetime.now(datetime.timezone.utc) + (expires_delta or datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, current_jwt_secret, algorithm=JWT_ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    raw_token = credentials.credentials
    if not raw_token or not raw_token.strip():
        raise HTTPException(status_code=401, detail="Missing authentication token")
    token = raw_token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    current_jwt_secret = os.getenv("JWT_SECRET", JWT_SECRET)
    if not current_jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="JWT authentication is not configured.",
        )
    try:
        payload = jwt.decode(token, current_jwt_secret, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    return payload


def process_message(chat_id: str, message: str) -> str:
    text = message.strip()
    normalized = text.lower()

    if not text:
        return "Please enter a message. Type MENU to see the options."

    if normalized in {
        "hi", "hello", "hey", "namaste", "namaskar", "menu"
    }:
        conversation_state.pop(chat_id, None)
        return get_reply(text)

    state = conversation_state.get(chat_id, {"step": "menu"})

    if state["step"] == "menu" and (
        normalized == "1" or normalized in {"rooftop solar", "rooftop solar enquiry"}
    ):
        conversation_state[chat_id] = {"step": "property_type"}
        return (
            "Let's explore rooftop solar for your property! ☀️\n\n"
            "Is this for a home or a business?\n"
            "Reply HOME or BUSINESS."
        )

    if state["step"] == "property_type":
        if normalized not in ALLOWED_PROPERTY_TYPES:
            return "Please reply HOME or BUSINESS."

        state["property_type"] = normalized
        state["step"] = "customer_name"
        conversation_state[chat_id] = state
        return "Great! What is your name?"

    if state["step"] == "customer_name":
        if len(text) < 2 or len(text) > 100:
            return "Please enter your name (2–100 characters)."

        state["customer_name"] = text
        state["step"] = "location"
        conversation_state[chat_id] = state
        return "Thanks! Which city or district is your property in?"

    if state["step"] == "location":
        if len(text) < 2 or len(text) > 150:
            return "Please enter a valid city or district."

        lead = conversation_state[chat_id]

        try:
            save_lead(
                name=lead["customer_name"],
                property_type=lead["property_type"],
                location=text,
            )
        except Exception:
            logger.exception("Could not save customer enquiry")
            return (
                "Sorry, we couldn't save your enquiry right now. "
                "Please try again later or type MENU."
            )

        conversation_state.pop(chat_id, None)

        return (
            "Thank you for your solar enquiry! ☀️\n\n"
            f"Name: {lead['customer_name']}\n"
            f"Property: {lead['property_type'].upper()}\n"
            f"Location: {text}\n\n"
            "Your enquiry has been saved successfully.\n"
            "Our team can follow up once lead notifications "
            "are configured.\n\n"
            "Type MENU to start again."
        )

    return get_reply(text)


@app.get("/")
def home():
    return {
        "company": "Solarise Odisha",
        "parent_company": "ADP Green Energy Solutions",
        "message": "Chatbot backend is running!",
        "status": "success",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/business-number")
def get_business_number():
    """Return the configured dedicated business WhatsApp number.
    If not set, returns an empty string. This endpoint allows frontend
    components to read the number without exposing secrets.
    """
    return {"business_number": BUSINESS_NUMBER}


@app.get("/leads", dependencies=[Depends(get_current_user)])
def read_leads():
    try:
        return {"leads": get_leads()}
    except Exception:
        logger.exception("Could not retrieve customer leads")
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve leads.",
        )


@app.post(
    "/leads",
    status_code=201,
    dependencies=[Depends(get_current_user)],
)
def create_lead(request: ManualLeadRequest):
    name = request.name.strip()
    property_type = request.property_type.strip().lower()
    location = request.location.strip()

    if len(name) < 2 or len(location) < 2:
        raise HTTPException(
            status_code=400,
            detail="Enter a valid name and location.",
        )

    if property_type not in ALLOWED_PROPERTY_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Property type must be home or business.",
        )

    try:
        save_lead(
            name=name,
            property_type=property_type,
            location=location,
        )
    except Exception:
        logger.exception("Could not create manual lead")
        raise HTTPException(
            status_code=500,
            detail="Could not save the customer lead.",
        )

    return {
        "message": "Customer lead created successfully.",
        "lead": {
            "name": name,
            "property_type": property_type,
            "location": location,
            "status": "New",
        },
    }


@app.put(
    "/leads/{lead_id}/status",
    dependencies=[Depends(get_current_user)],
)
def change_lead_status(
    lead_id: int,
    request: LeadStatusRequest,
):
    status = request.status.strip().title()

    if status not in ALLOWED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Status must be New, Contacted, or Converted.",
        )

    try:
        updated = update_lead_status(lead_id, status)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        logger.exception("Could not update lead status")
        raise HTTPException(
            status_code=500,
            detail="Could not update lead status.",
        )

    if not updated:
        raise HTTPException(status_code=404, detail="Lead not found.")

    return {
        "message": "Lead status updated successfully.",
        "lead_id": lead_id,
        "new_status": status,
    }


@app.post("/chat")
def chat(request: ChatRequest):
    try:
        return {
            "reply": process_message("local-test-chat", request.message)
        }
    except Exception:
        logger.exception("Local chat request failed")
        raise HTTPException(
            status_code=500,
            detail="Could not process the chat message.",
        )


@app.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    x_openwa_signature: str | None = Header(default=None),
):
    current_webhook_secret = os.getenv("WEBHOOK_SECRET") or WEBHOOK_SECRET
    if not current_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Webhook secret is not configured.",
        )

    body_bytes = await request.body()
    try:
        payload: dict[str, Any] = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON payload.",
        )

    # Secure authentication verification
    valid_secret = False
    if x_webhook_secret and secrets.compare_digest(x_webhook_secret, current_webhook_secret):
        valid_secret = True
    elif x_openwa_signature:
        import hmac, hashlib
        try:
            # First check against raw incoming request body (OpenWA standard)
            expected_sig = "sha256=" + hmac.new(
                current_webhook_secret.encode("utf-8"),
                body_bytes,
                hashlib.sha256,
            ).hexdigest()
            if secrets.compare_digest(x_openwa_signature, expected_sig):
                valid_secret = True
            else:
                # Fallback to compact serialized JSON for test clients
                fallback_sig = "sha256=" + hmac.new(
                    current_webhook_secret.encode("utf-8"),
                    json.dumps(payload, separators=(',', ':')).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                if secrets.compare_digest(x_openwa_signature, fallback_sig):
                    valid_secret = True
        except Exception:
            pass

    if not valid_secret:
        logger.warning("Unauthorized webhook request rejected: invalid secret or signature")
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook secret.",
        )

    event = payload.get("event")
    if event != "message.received":
        logger.info("Ignoring non-message webhook event: %s", event)
        return {"status": "ignored"}

    data = payload.get("data")
    if not isinstance(data, dict):
        logger.info("Ignoring webhook payload with non-dict data object")
        return {"status": "ignored"}

    # Ignore bot-sent / outgoing messages
    if data.get("direction") == "outgoing" or data.get("fromMe") is True or data.get("self") is True:
        logger.info("Ignoring outgoing/bot-sent message")
        return {"status": "ignored"}

    # Ignore group chats, status broadcasts, and channel/newsletter updates
    if (
        data.get("isGroup") is True
        or data.get("isStatusBroadcast") is True
        or data.get("isNewsletter") is True
    ):
        logger.info("Ignoring group/status/newsletter message flag")
        return {"status": "ignored"}

    # Ignore non-text messages
    msg_type = data.get("type", "text")
    if msg_type and msg_type != "text":
        logger.info("Ignoring non-text message type: %s", msg_type)
        return {"status": "ignored"}

    # Duplicate message check for retried webhook deliveries
    msg_id = (
        data.get("id")
        or data.get("waMessageId")
        or data.get("messageId")
        or payload.get("deliveryId")
        or payload.get("idempotencyKey")
    )
    if msg_id and is_duplicate_message(str(msg_id)):
        logger.info("Ignoring duplicate webhook delivery (message ID: %s)", msg_id)
        return {"status": "ignored", "reason": "duplicate"}

    # Extract message text (flat 'body', 'text', or nested 'message.text' / 'message.conversation')
    message = data.get("body")
    if message is None:
        message = data.get("text")
    if message is None:
        nested_msg = data.get("message") or {}
        if isinstance(nested_msg, dict):
            message = nested_msg.get("text") or nested_msg.get("conversation") or ""
        else:
            message = ""

    if not isinstance(message, str) or not message.strip():
        logger.info("Ignoring empty message body")
        return {"status": "ignored"}

    # Extract chat identifier (individual, group, or broadcast)
    chat_id = data.get("chatId") or data.get("from") or ""
    if not isinstance(chat_id, str) or not chat_id.strip():
        logger.info("Ignoring message without valid chat identifier")
        return {"status": "ignored"}

    chat_id = chat_id.strip()

    # Filter out group, broadcast, and newsletter chat domains
    if any(suffix in chat_id for suffix in ("@g.us", "@broadcast", "@newsletter")):
        target_kind = chat_id.split("@")[-1] if "@" in chat_id else "unknown"
        logger.info("Ignoring message from excluded chat target: %s", target_kind)
        return {"status": "ignored"}

    # Generate chatbot reply
    sanitized_chat_suffix = chat_id[-4:] if len(chat_id) >= 4 else "anon"
    logger.info("Processing incoming message for chat ...%s", sanitized_chat_suffix)
    reply = process_message(chat_id, message)

    # Resolve OpenWA configuration
    openwa_api_key = os.getenv("OPENWA_API_KEY", OPENWA_API_KEY)
    openwa_url = (os.getenv("OPENWA_URL", OPENWA_URL) or DEFAULT_OPENWA_URL).rstrip("/")
    session_id = os.getenv("OPENWA_SESSION_ID", OPENWA_SESSION_ID) or DEFAULT_OPENWA_SESSION_ID

    if not openwa_api_key or not session_id:
        logger.warning("OpenWA credentials or session ID not configured; reply generated without sending")
        return {"status": "reply_generated", "reply": reply}

    # Send the final reply via OpenWA send-text endpoint
    endpoint = f"{openwa_url}/api/sessions/{session_id}/messages/send-text"
    try:
        response = requests.post(
            endpoint,
            headers={"X-API-Key": openwa_api_key},
            json={"chatId": chat_id, "text": reply},
            timeout=20,
        )
        response.raise_for_status()
        logger.info("WhatsApp reply successfully sent via OpenWA")
        return {"status": "sent", "reply": reply}
    except requests.exceptions.Timeout:
        logger.error("OpenWA request timed out while sending reply")
        return {"status": "send_failed", "message": "OpenWA request timed out."}
    except requests.exceptions.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", "N/A")
        logger.error("WhatsApp reply could not be sent (HTTP status: %s)", status_code)
        return {"status": "send_failed", "message": "Could not send the WhatsApp reply."}
    except Exception as exc:
        logger.error("Unexpected error sending WhatsApp reply: %s", type(exc).__name__)
        return {"status": "send_failed", "message": "Could not send the WhatsApp reply."}


@app.post("/login")
def login(x_admin_api_key: str | None = Header(default=None)):
    current_admin_key = os.getenv("ADMIN_API_KEY", ADMIN_API_KEY)
    if not current_admin_key:
        raise HTTPException(status_code=503, detail="Admin authentication not configured.")
    if not x_admin_api_key or not secrets.compare_digest(x_admin_api_key, current_admin_key):
        raise HTTPException(status_code=401, detail="Invalid admin API key.")
    access_token = create_access_token(data={"sub": "admin"})
    return {"access_token": access_token, "token_type": "bearer"}
