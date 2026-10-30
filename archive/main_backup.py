import os
import logging
import secrets
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from chatbot import get_reply
from leads import save_lead, get_leads, update_lead_status

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENWA_URL = "http://127.0.0.1:2785"
OPENWA_API_KEY = os.getenv("OPENWA_API_KEY", "")
SESSION_ID = os.getenv("OPENWA_SESSION_ID", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

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

conversation_state: dict[str, dict[str, Any]] = {}

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

class LeadStatusRequest(BaseModel):
    status: str

class ManualLeadRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    property_type: str = Field(min_length=1, max_length=20)
    location: str = Field(min_length=2, max_length=150)

# Placeholder for JWT-related imports and utilities – will be added later.
