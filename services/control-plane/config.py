from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Control plane
    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    # Supabase — required for DB operations (Slices 2+)
    supabase_url: Optional[str] = None
    supabase_service_key: Optional[str] = None

    # AI providers — required for agent operations (Slices 5+)
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None

    # Slack — required for notifications (Slice 8+)
    slack_bot_token: Optional[str] = None
    slack_signing_secret: Optional[str] = None
    slack_ops_channel_id: Optional[str] = None

    # Postmark — required for email connector (Slice 3+)
    postmark_inbound_webhook_secret: Optional[str] = None
    postmark_server_token: Optional[str] = None

    # Tally — required for form connector (Slice 4+)
    tally_webhook_secret: Optional[str] = None

    # Sendblue — required for iMessage connector (Slice 6+)
    sendblue_api_key: Optional[str] = None
    sendblue_api_secret: Optional[str] = None
    sendblue_from_number: Optional[str] = None
    sendblue_webhook_secret: Optional[str] = None

    # Base URL of this service — used to construct status_callback URLs (Slice 7+)
    # Example: https://yourdomain.com
    app_base_url: Optional[str] = None


# Single shared instance — import this everywhere, never instantiate Settings again
settings = Settings()
