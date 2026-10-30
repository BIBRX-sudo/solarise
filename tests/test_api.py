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
    # Always use a fresh isolated temporary database
    test_db_path = tmp_path / "test_leads.db"
    test_db_path.touch()
    import leads
    original_db = leads.DB_NAME
    leads.DB_NAME = str(test_db_path)
    leads.initialize_database()
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


def test_dashboard_endpoint(client):
    response = client.get("/dashboard.html")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in response.text
    assert "Solarise Odisha" in response.text
    assert "loginForm" in response.text
    assert "adminApiKey" in response.text


def test_dashboard_alias_endpoint(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in response.text


def test_login_success(client):
    response = client.post("/login", headers={"x-admin-api-key": "testadminkey"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_invalid_key(client):
    response = client.post("/login", headers={"x-admin-api-key": "wrongkey"})
    assert response.status_code == 401
