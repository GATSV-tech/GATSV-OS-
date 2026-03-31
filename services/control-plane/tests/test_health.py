from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from agents.gatekeeper import GatekeeperResult
from config import settings
from main import app

client = TestClient(app)

# Reusable mock for gatekeeper.run — prevents real DB calls in webhook endpoint tests.
_MOCK_GK_RESULT = GatekeeperResult(event_id="event-uuid-1", entity_id="entity-uuid-1", status="created", duration_ms=5)
_PATCH_GK = patch("routers.webhooks.gatekeeper.run", new_callable=AsyncMock, return_value=_MOCK_GK_RESULT)


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
    with _PATCH_GK:
        response = client.post("/inbound/email", json={})
    assert response.status_code == 202
    assert response.json() == {"received": True}


def test_inbound_email_rejects_wrong_token():
    """When secret is configured, wrong token returns 401 (before Gatekeeper is reached)."""
    with patch.object(settings, "postmark_inbound_webhook_secret", "correct-secret"):
        response = client.post("/inbound/email?token=wrong-token", json={})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_inbound_email_accepts_correct_token():
    """When secret is configured, correct token reaches Gatekeeper and returns 202."""
    with patch.object(settings, "postmark_inbound_webhook_secret", "correct-secret"), _PATCH_GK:
        response = client.post("/inbound/email?token=correct-secret", json={})
    assert response.status_code == 202
    assert response.json() == {"received": True}


def test_inbound_form_returns_202():
    """Form endpoint returns 202 with no secret configured (dev mode)."""
    with _PATCH_GK:
        response = client.post("/inbound/form", json={})
    assert response.status_code == 202
    assert response.json() == {"received": True}


def test_inbound_form_rejects_wrong_token():
    """When Tally secret is configured, wrong token returns 401 (before Gatekeeper is reached)."""
    with patch.object(settings, "tally_webhook_secret", "tally-secret"):
        response = client.post("/inbound/form?token=wrong-token", json={})
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_inbound_form_accepts_correct_token():
    """When Tally secret is configured, correct token reaches Gatekeeper and returns 202."""
    with patch.object(settings, "tally_webhook_secret", "tally-secret"), _PATCH_GK:
        response = client.post("/inbound/form?token=tally-secret", json={})
    assert response.status_code == 202
    assert response.json() == {"received": True}
