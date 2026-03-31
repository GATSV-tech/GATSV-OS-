import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from config import settings
from connectors.email import parse_postmark_inbound

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# Warn once per process if the inbound email secret is not configured.
_postmark_token_warned = False


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

    Full processing (Gatekeeper) is wired in Slice 5.
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
    logger.debug(
        "Inbound email parsed: source_id=%s sender=%s subject=%r",
        parsed.source_id,
        parsed.sender_email,
        parsed.subject,
    )
    return {"received": True}


@router.post("/form")
async def inbound_form(request: Request) -> dict:
    """
    Receives form submission payloads from Tally.
    Stub — processing added in Slice 4 (Tally connector) and Slice 5 (Gatekeeper).
    """
    logger.info("Inbound form webhook received (stub)")
    return {"received": True}
