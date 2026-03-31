import logging
from functools import lru_cache
from typing import Optional

from supabase import Client, create_client

from config import settings

logger = logging.getLogger(__name__)

_client: Optional[Client] = None


def get_client() -> Client:
    """
    Returns the shared Supabase client, initializing it on first call.
    Raises clearly if credentials are not configured.
    Called by DB modules in Slice 2+, not during startup.
    """
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set before using the database client."
            )
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
        logger.info("Supabase client initialized")
    return _client
