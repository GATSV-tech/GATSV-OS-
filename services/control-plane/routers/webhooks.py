import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from agents import gatekeeper
from agents import router as router_agent
from agents import operator as operator_agent
from agents import chat
from config import settings
from connectors.email import parse_postmark_inbound
from connectors.form import parse_tally_inbound
from connectors.imessage import parse_sendblue_inbound

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# Warn once per process if inbound secrets are not configured.
_postmark_token_warned = False
_tally_token_warned = False
_sendblue_token_warned = False


@router.post("/email", status_code=202)
async def inbound_email(
    request: Request,
    token: Annotated[str | None, Query()] = None,
) -> Any:
    """
    Receives inbound email payloads from Postmark.

    Token validation: if POSTMARK_INBOUND_WEBHOOK_SECRET is set, the request
    must include ?token=<secret> or it is rejected with 401. Configure the
    Postmark inbound server webhook URL as:
        https://yourdomain.com/inbound/email?token=YOUR_SECRET
    """
    global _postmark_token_warned

    if settings.postmark_inbound_webhook_secret:
        if token != settings.postmark_inbound_webhook_secret:
            logger.warning("Inbound email rejected: invalid or missing token")
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    else:
        if not _postmark_token_warned:
            logger.warning(
                "POSTMARK_INBOUND_WEBHOOK_SECRET is not set — "
                "email webhook endpoint is unauthenticated. Set this in production."
            )
            _postmark_token_warned = True

    raw = await request.json()
    parsed = parse_postmark_inbound(raw)
    gk_result = await gatekeeper.run(parsed)
    logger.debug(
        "Gatekeeper result: status=%s event_id=%s source_id=%s",
        gk_result.status, gk_result.event_id, parsed.source_id,
    )
    rt_result = await router_agent.run(gk_result)
    logger.debug(
        "Router result: status=%s event_id=%s bucket=%s priority=%s",
        rt_result.status, rt_result.event_id, rt_result.bucket, rt_result.priority,
    )
    await operator_agent.run(rt_result)
    return {"received": True}


@router.post("/form", status_code=202)
async def inbound_form(
    request: Request,
    token: Annotated[str | None, Query()] = None,
) -> Any:
    """
    Receives form submission payloads from Tally.

    Token validation: if TALLY_WEBHOOK_SECRET is set, the request must include
    ?token=<secret> or it is rejected with 401. Configure the Tally webhook URL as:
        https://yourdomain.com/inbound/form?token=YOUR_SECRET
    """
    global _tally_token_warned

    if settings.tally_webhook_secret:
        if token != settings.tally_webhook_secret:
            logger.warning("Inbound form rejected: invalid or missing token")
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    else:
        if not _tally_token_warned:
            logger.warning(
                "TALLY_WEBHOOK_SECRET is not set — "
                "form webhook endpoint is unauthenticated. Set this in production."
            )
            _tally_token_warned = True

    raw = await request.json()
    parsed = parse_tally_inbound(raw)
    gk_result = await gatekeeper.run(parsed)
    logger.debug(
        "Gatekeeper result: status=%s event_id=%s source_id=%s",
        gk_result.status, gk_result.event_id, parsed.source_id,
    )
    rt_result = await router_agent.run(gk_result)
    logger.debug(
        "Router result: status=%s event_id=%s bucket=%s priority=%s",
        rt_result.status, rt_result.event_id, rt_result.bucket, rt_result.priority,
    )
    await operator_agent.run(rt_result)
    return {"received": True}


@router.post("/imessage", status_code=202)
async def inbound_imessage(
    request: Request,
    token: Annotated[str | None, Query()] = None,
) -> Any:
    """
    Receives inbound iMessage/SMS/RCS payloads from Sendblue.

    Token validation: if SENDBLUE_WEBHOOK_SECRET is set, the request must include
    ?token=<secret> or it is rejected with 401. Register the webhook URL in Sendblue as:
        https://yourdomain.com/inbound/imessage?token=YOUR_SECRET
    """
    global _sendblue_token_warned

    if settings.sendblue_webhook_secret:
        if token != settings.sendblue_webhook_secret:
            logger.warning("Inbound iMessage rejected: invalid or missing token")
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
    else:
        if not _sendblue_token_warned:
            logger.warning(
                "SENDBLUE_WEBHOOK_SECRET is not set — "
                "iMessage webhook endpoint is unauthenticated. Set this in production."
            )
            _sendblue_token_warned = True

    raw = await request.json()
    parsed = parse_sendblue_inbound(raw)
    result = await gatekeeper.run(parsed)
    logger.debug(
        "Gatekeeper result: status=%s event_id=%s source_id=%s",
        result.status, result.event_id, parsed.source_id,
    )

    if parsed.body:
        await chat.run(parsed)

    return {"received": True}


@router.post("/imessage/status", status_code=202)
async def inbound_imessage_status(
    request: Request,
    token: Annotated[str | None, Query()] = None,
) -> Any:
    """
    Receives Sendblue delivery status callbacks for outbound messages.
    Status updates (SENT, DELIVERED, ERROR, etc.) are logged for observability.
    Slice 7: log only. Slice 8+ will persist to DB for cost and delivery tracking.
    """
    if settings.sendblue_webhook_secret:
        if token != settings.sendblue_webhook_secret:
            logger.warning("iMessage status callback rejected: invalid or missing token")
            return JSONResponse(status_code=401, content={"error": "unauthorized"})

    raw = await request.json()
    logger.info(
        "Sendblue delivery status: handle=%s status=%s to=%s",
        raw.get("message_handle"),
        raw.get("status"),
        raw.get("number"),
    )
    return {"received": True}
