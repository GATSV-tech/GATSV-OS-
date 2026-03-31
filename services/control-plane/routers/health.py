from datetime import datetime, timezone

from fastapi import APIRouter

from config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "ok",
        "version": settings.app_version,
        "env": settings.app_env,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
