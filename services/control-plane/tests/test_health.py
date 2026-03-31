from unittest.mock import patch

from fastapi.testclient import TestClient

from config import settings
from main import app

client = TestClient(app)


def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_shape():
    response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "env" in body
    assert "timestamp" in body


def test_inbound_email_returns_202():
    """Email endpoint returns 202 with no secret configured (dev mode)."""
    response = client.post("/inbound/email", json={})
    assert response.status_code == 202
    assert response.json() == {"received": True}


def test_inbound_email_rejects_wrong_token():
    """When secret is configured, wrong token returns 401."""
    with patch.object(settings, "postmark_inbound_webhook_secret", "correct-secret"):
        response = client.post("/inbound/email?token=wrong-token", json={})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_inbound_email_accepts_correct_token():
    """When secret is configured, correct token returns 202."""
    with patch.object(settings, "postmark_inbound_webhook_secret", "correct-secret"):
        response = client.post("/inbound/email?token=correct-secret", json={})
    assert response.status_code == 202
    assert response.json() == {"received": True}


def test_inbound_form_stub_returns_200():
    """Form endpoint is still a stub, returns 200."""
    response = client.post("/inbound/form", json={})
    assert response.status_code == 200
    assert response.json() == {"received": True}
