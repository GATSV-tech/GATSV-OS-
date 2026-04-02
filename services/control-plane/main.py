import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from routers import health, webhooks, slack_router
from scheduler import runner as task_scheduler
from scheduler import digest as digest_scheduler
from scheduler import slack_scheduler
from scheduler import email_dispatcher
from scheduler import reporter_scheduler
from scheduler import auditor_scheduler

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "GATSV OS Control Plane starting",
        extra={"env": settings.app_env, "version": settings.app_version},
    )
    task_scheduler.start()
    email_dispatcher.start()

    if settings.jake_phone_number:
        digest_scheduler.start(settings.jake_phone_number)
    else:
        logger.warning(
            "JAKE_PHONE_NUMBER is not set — daily digest will not run. "
            "Set this in .env to enable proactive sends."
        )

    slack_scheduler.start()
    reporter_scheduler.start()
    auditor_scheduler.start()

    yield

    task_scheduler.stop()
    email_dispatcher.stop()
    digest_scheduler.stop()
    slack_scheduler.stop()
    reporter_scheduler.stop()
    auditor_scheduler.stop()
    logger.info("GATSV OS Control Plane shutting down")


app = FastAPI(
    title="GATSV OS Control Plane",
    version=settings.app_version,
    docs_url="/docs" if settings.app_env == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(webhooks.router, prefix="/inbound")
app.include_router(slack_router.router)
