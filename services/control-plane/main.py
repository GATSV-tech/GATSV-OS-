import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from routers import health, webhooks

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
    yield
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
