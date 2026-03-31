import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from config import settings
from connectors.email import parse_postmark_inbound
from connectors.form import parse_tally_inbound

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

# Warn once per process if inbound secrets are not configured.
_postmark_token_warned = False
_tally_token_warned = False


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

    Full processing (Gatekeeper) is wired in Slice 5.
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
    logger.debug(
        "Inbound form parsed: source_id=%s sender=%s",
        parsed.source_id,
        parsed.sender_email,
    )
    return {"received": True}
