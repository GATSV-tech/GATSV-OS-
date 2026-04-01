"""
Unit tests for the Slack operator surface.

Three test groups:
  1. agents/slack_surface — post_approvals_queue, handle_approval_action,
                            post_daily_summary, post_error_alert
  2. connectors/slack     — verify_signature (no network calls)
  3. routers/slack_router — interaction webhook endpoint

All external calls (Slack HTTP, DB) are mocked at module boundaries.
asyncio.to_thread is exercised for real.
"""

import hashlib
import hmac
import json
import time
from contextlib import AsyncExitStack, ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ─── Fixtures ────────────────────────────────────────────────────────────────

MOCK_APPROVAL = {
    "id": "approval-uuid-1",
    "action_id": "action-uuid-1",
    "event_id": "event-uuid-1",
    "requested_by": "operator",
    "summary": "Send acknowledgment to marcus@brightpath.io (bucket: support, priority: high).",
    "context": {
        "to_email": "marcus@brightpath.io",
        "subject": "Thanks for reaching out",
        "body": "Hi Marcus, we received your support request.",
        "sender_name": "Marcus Rivera",
        "event_subject": "Billing issue",
        "bucket": "support",
        "priority": "high",
    },
    "decision": None,
    "decided_by": None,
    "decided_at": None,
    "notified_at": None,
    "created_at": "2026-04-01T10:00:00Z",
}

MOCK_ACTION = {"id": "action-uuid-1"}
MOCK_HEALTH_LOG = {"id": "log-uuid-1"}

MOCK_EVENTS_SUMMARY = [
    {"source": "email", "bucket": "sales", "status": "actioned", "created_at": "2026-04-01T10:00:00Z"},
    {"source": "form", "bucket": "support", "status": "routed", "created_at": "2026-04-01T11:00:00Z"},
    {"source": "email", "bucket": "noise", "status": "routed", "created_at": "2026-04-01T12:00:00Z"},
]

MOCK_COSTS = {"token_input": 500, "token_output": 200, "usd_cost": 0.0044}


# ─── 1. agents/slack_surface ─────────────────────────────────────────────────

class TestPostApprovalsQueue:

    @patch("agents.slack_surface.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
    @patch("agents.slack_surface.db_actions.create", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.db_approvals.mark_notified", return_value=MOCK_APPROVAL)
    @patch("agents.slack_surface.slack_connector.post_message", return_value={"ok": True})
    @patch("agents.slack_surface.db_approvals.list_unnotified", return_value=[MOCK_APPROVAL])
    @patch("agents.slack_surface.settings")
    async def test_posts_unnotified_approval(
        self, mock_settings, mock_list, mock_post, mock_notified, mock_action, mock_hl
    ):
        """Happy path: unnotified approval is posted and marked notified."""
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C123"

        from agents.slack_surface import post_approvals_queue
        count = await post_approvals_queue()

        assert count == 1
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["channel"] == "C123" or call_kwargs[0][0] == "C123"
        mock_notified.assert_called_once_with("approval-uuid-1")
        mock_action.assert_called_once()

    @patch("agents.slack_surface.db_approvals.list_unnotified", return_value=[])
    @patch("agents.slack_surface.settings")
    async def test_returns_zero_when_no_unnotified(self, mock_settings, mock_list):
        """No approvals pending — returns 0, no Slack call."""
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C123"

        from agents.slack_surface import post_approvals_queue
        with patch("agents.slack_surface.slack_connector.post_message") as mock_post:
            count = await post_approvals_queue()

        assert count == 0
        mock_post.assert_not_called()

    @patch("agents.slack_surface.settings")
    async def test_skips_when_token_not_configured(self, mock_settings):
        """Returns 0 immediately if Slack token is absent."""
        mock_settings.slack_bot_token = None
        mock_settings.slack_ops_channel_id = "C123"

        from agents.slack_surface import post_approvals_queue
        with patch("agents.slack_surface.db_approvals.list_unnotified") as mock_list:
            count = await post_approvals_queue()

        assert count == 0
        mock_list.assert_not_called()

    @patch("agents.slack_surface.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
    @patch("agents.slack_surface.db_approvals.list_unnotified", return_value=[MOCK_APPROVAL, MOCK_APPROVAL])
    @patch("agents.slack_surface.settings")
    async def test_continues_after_individual_post_failure(
        self, mock_settings, mock_list, mock_hl
    ):
        """If one approval fails to post, the rest still process."""
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C123"

        call_count = 0

        def post_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Slack API timeout")
            return {"ok": True}

        with patch("agents.slack_surface.slack_connector.post_message", side_effect=post_side_effect):
            with patch("agents.slack_surface.db_approvals.mark_notified", return_value=MOCK_APPROVAL):
                with patch("agents.slack_surface.db_actions.create", return_value=MOCK_ACTION):
                    from agents.slack_surface import post_approvals_queue
                    count = await post_approvals_queue()

        # Second approval still posted despite first failure.
        assert count == 1


class TestApprovalBlocks:

    def test_blocks_contain_approve_reject_buttons(self):
        """Block Kit output must contain both action buttons with correct action_ids."""
        from agents.slack_surface import _build_approval_blocks
        blocks = _build_approval_blocks(
            approval_id="approval-uuid-1",
            summary="Send ack to marcus@brightpath.io",
            ctx=MOCK_APPROVAL["context"],
        )

        # Find the actions block.
        action_blocks = [b for b in blocks if b["type"] == "actions"]
        assert len(action_blocks) == 1

        elements = action_blocks[0]["elements"]
        action_ids = {e["action_id"] for e in elements}
        assert "approve_approval-uuid-1" in action_ids
        assert "reject_approval-uuid-1" in action_ids

        # Approve button should have primary style.
        approve = next(e for e in elements if e["action_id"].startswith("approve_"))
        assert approve["style"] == "primary"
        assert approve["value"] == "approval-uuid-1"

    def test_blocks_include_context_fields(self):
        """Email, bucket, priority fields appear in the blocks."""
        from agents.slack_surface import _build_approval_blocks
        blocks = _build_approval_blocks(
            approval_id="x",
            summary="test",
            ctx=MOCK_APPROVAL["context"],
        )
        block_text = json.dumps(blocks)
        assert "marcus@brightpath.io" in block_text
        assert "support" in block_text


class TestHandleApprovalAction:

    @patch("agents.slack_surface.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
    @patch("agents.slack_surface.db_actions.update_status", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.db_actions.create", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.db_approvals.update_decision", return_value=MOCK_APPROVAL)
    @patch("agents.slack_surface.db_approvals.get_by_id", return_value=MOCK_APPROVAL)
    @patch("agents.slack_surface.settings")
    async def test_approve_path(
        self, mock_settings, mock_get, mock_upd_decision, mock_action,
        mock_upd_action_status, mock_hl
    ):
        """Approve: decision set to approved, linked action updated."""
        mock_settings.slack_bot_token = None  # skip ack post in this test

        from agents.slack_surface import handle_approval_action
        result = await handle_approval_action(
            action_id="approve_approval-uuid-1",
            approval_id="approval-uuid-1",
            decided_by="U_JAKE",
        )

        assert result["ok"] is True
        assert result["decision"] == "approved"

        # ApprovalDecision passed to update_decision
        upd_call = mock_upd_decision.call_args[0]
        assert upd_call[1].decision == "approved"
        assert upd_call[1].decided_by == "U_JAKE"

        # Action status updated to approved
        mock_upd_action_status.assert_called_once_with("action-uuid-1", "approved", "U_JAKE")

    @patch("agents.slack_surface.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
    @patch("agents.slack_surface.db_actions.update_status", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.db_actions.create", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.db_approvals.update_decision", return_value=MOCK_APPROVAL)
    @patch("agents.slack_surface.db_approvals.get_by_id", return_value=MOCK_APPROVAL)
    @patch("agents.slack_surface.settings")
    async def test_reject_path(
        self, mock_settings, mock_get, mock_upd_decision, mock_action,
        mock_upd_action_status, mock_hl
    ):
        """Reject: decision set to rejected, linked action status updated."""
        mock_settings.slack_bot_token = None

        from agents.slack_surface import handle_approval_action
        result = await handle_approval_action(
            action_id="reject_approval-uuid-1",
            approval_id="approval-uuid-1",
            decided_by="U_JAKE",
        )

        assert result["ok"] is True
        assert result["decision"] == "rejected"
        mock_upd_action_status.assert_called_once_with("action-uuid-1", "rejected", "U_JAKE")

    @patch("agents.slack_surface.db_approvals.get_by_id", return_value=None)
    @patch("agents.slack_surface.settings")
    async def test_returns_error_when_approval_not_found(self, mock_settings, mock_get):
        """Missing approval returns ok=False, never raises."""
        mock_settings.slack_bot_token = None

        from agents.slack_surface import handle_approval_action
        result = await handle_approval_action(
            action_id="approve_approval-uuid-missing",
            approval_id="approval-uuid-missing",
            decided_by="U_JAKE",
        )

        assert result["ok"] is False
        assert "not found" in result["error"]

    @patch("agents.slack_surface.db_approvals.get_by_id",
           return_value={**MOCK_APPROVAL, "decision": "approved"})
    @patch("agents.slack_surface.settings")
    async def test_returns_error_when_already_decided(self, mock_settings, mock_get):
        """Already-decided approval returns ok=False."""
        mock_settings.slack_bot_token = None

        from agents.slack_surface import handle_approval_action
        result = await handle_approval_action(
            action_id="approve_approval-uuid-1",
            approval_id="approval-uuid-1",
            decided_by="U_JAKE",
        )

        assert result["ok"] is False
        assert "already decided" in result["error"]


class TestPostDailySummary:

    @patch("agents.slack_surface.db_health_logs.create", return_value=MOCK_HEALTH_LOG)
    @patch("agents.slack_surface.db_actions.create", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.db_slack.open_approvals_count", return_value=2)
    @patch("agents.slack_surface.db_slack.actions_cost", return_value=MOCK_COSTS)
    @patch("agents.slack_surface.db_slack.events_summary", return_value=MOCK_EVENTS_SUMMARY)
    @patch("agents.slack_surface.slack_connector.post_message", return_value={"ok": True})
    @patch("agents.slack_surface.settings")
    async def test_posts_summary_with_event_counts(
        self, mock_settings, mock_post, mock_events, mock_costs,
        mock_open, mock_action, mock_hl
    ):
        """Daily summary posts a message containing event and cost data."""
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C123"

        from agents.slack_surface import post_daily_summary
        await post_daily_summary()

        mock_post.assert_called_once()
        # Check the text argument contains event count.
        call_args = mock_post.call_args
        posted_text = call_args[1].get("text") or call_args[0][1]
        assert "3" in posted_text or "3" in json.dumps(call_args[1].get("blocks", []))

        # action row written with reporter agent
        action_arg = mock_action.call_args[0][0]
        assert action_arg.agent == "reporter"
        assert action_arg.action_type == "slack_daily_summary"
        assert action_arg.payload["events_total"] == 3

    @patch("agents.slack_surface.settings")
    async def test_skips_when_not_configured(self, mock_settings):
        """No Slack token — silently returns, no network call."""
        mock_settings.slack_bot_token = None
        mock_settings.slack_ops_channel_id = "C123"

        from agents.slack_surface import post_daily_summary
        with patch("agents.slack_surface.slack_connector.post_message") as mock_post:
            await post_daily_summary()  # must not raise

        mock_post.assert_not_called()


class TestPostErrorAlert:

    @patch("agents.slack_surface.db_actions.create", return_value=MOCK_ACTION)
    @patch("agents.slack_surface.slack_connector.post_message", return_value={"ok": True})
    @patch("agents.slack_surface.settings")
    async def test_posts_alert_with_service_names(self, mock_settings, mock_post, mock_action):
        """Error alert message contains service names and error count."""
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C123"

        errors = [
            {"service": "scheduler", "message": "task abc failed: timeout", "created_at": "2026-04-01T03:00:00Z"},
            {"service": "router", "message": "event not found: xyz", "created_at": "2026-04-01T03:01:00Z"},
        ]

        from agents.slack_surface import post_error_alert
        await post_error_alert(errors)

        mock_post.assert_called_once()
        posted_text = mock_post.call_args[1].get("text") or mock_post.call_args[0][1]
        assert "scheduler" in posted_text or "scheduler" in json.dumps(mock_post.call_args[1].get("blocks", []))

        action_arg = mock_action.call_args[0][0]
        assert action_arg.action_type == "slack_error_alert"
        assert action_arg.payload["error_count"] == 2

    @patch("agents.slack_surface.settings")
    async def test_skips_empty_error_list(self, mock_settings):
        """Empty error list — no Slack call."""
        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_ops_channel_id = "C123"

        from agents.slack_surface import post_error_alert
        with patch("agents.slack_surface.slack_connector.post_message") as mock_post:
            await post_error_alert([])

        mock_post.assert_not_called()


# ─── 2. connectors/slack — verify_signature ──────────────────────────────────

class TestVerifySignature:

    def _make_sig(self, secret: str, timestamp: str, body: str) -> str:
        base = f"v0:{timestamp}:{body}"
        mac = hmac.new(
            key=secret.encode(),
            msg=base.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return f"v0={mac}"

    def test_valid_signature_accepted(self):
        secret = "test_signing_secret"
        body = b'{"type":"block_actions"}'
        timestamp = str(int(time.time()))
        sig = self._make_sig(secret, timestamp, body.decode())

        with patch("connectors.slack.settings") as mock_settings:
            mock_settings.slack_signing_secret = secret
            from connectors.slack import verify_signature
            assert verify_signature(body, timestamp, sig) is True

    def test_wrong_signature_rejected(self):
        secret = "test_signing_secret"
        body = b'{"type":"block_actions"}'
        timestamp = str(int(time.time()))

        with patch("connectors.slack.settings") as mock_settings:
            mock_settings.slack_signing_secret = secret
            from connectors.slack import verify_signature
            assert verify_signature(body, timestamp, "v0=badhash") is False

    def test_stale_timestamp_rejected(self):
        secret = "test_signing_secret"
        body = b'{}'
        old_timestamp = str(int(time.time()) - 400)  # > 5 minutes ago
        sig = self._make_sig(secret, old_timestamp, body.decode())

        with patch("connectors.slack.settings") as mock_settings:
            mock_settings.slack_signing_secret = secret
            from connectors.slack import verify_signature
            assert verify_signature(body, old_timestamp, sig) is False

    def test_passthrough_when_secret_not_configured(self):
        with patch("connectors.slack.settings") as mock_settings:
            mock_settings.slack_signing_secret = None
            from connectors.slack import verify_signature
            assert verify_signature(b"anything", "123", "v0=anything") is True


# ─── 3. routers/slack_router — interactions endpoint ─────────────────────────

class TestSlackInteractionsEndpoint:

    def _make_client(self):
        from main import app
        return TestClient(app)

    def _form_body(self, payload: dict) -> bytes:
        import urllib.parse
        return urllib.parse.urlencode({"payload": json.dumps(payload)}).encode()

    def test_rejects_invalid_signature(self):
        """401 when signature verification fails."""
        client = self._make_client()
        with patch("routers.slack_router.slack_connector.verify_signature", return_value=False):
            resp = client.post(
                "/slack/interactions",
                content=b"payload={}",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 401

    def test_rejects_missing_payload(self):
        """400 when payload field is absent."""
        client = self._make_client()
        with patch("routers.slack_router.slack_connector.verify_signature", return_value=True):
            resp = client.post(
                "/slack/interactions",
                content=b"not_payload=hi",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 400

    def test_accepts_block_action_and_dispatches(self):
        """200 returned immediately; handler dispatched as background task."""
        client = self._make_client()
        payload = {
            "type": "block_actions",
            "user": {"id": "U_JAKE"},
            "actions": [{"action_id": "approve_approval-uuid-1", "value": "approval-uuid-1"}],
        }
        body = self._form_body(payload)

        with patch("routers.slack_router.slack_connector.verify_signature", return_value=True):
            with patch("routers.slack_router.slack_surface.handle_approval_action",
                       new_callable=AsyncMock) as mock_handler:
                resp = client.post(
                    "/slack/interactions",
                    content=body,
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_unknown_interaction_type_returns_ok(self):
        """Unrecognised interaction type is logged and ignored — still 200."""
        client = self._make_client()
        payload = {"type": "shortcut", "callback_id": "whatever"}
        body = self._form_body(payload)

        with patch("routers.slack_router.slack_connector.verify_signature", return_value=True):
            resp = client.post(
                "/slack/interactions",
                content=body,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
