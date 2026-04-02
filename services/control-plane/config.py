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

    # Chat memory — rolling context window for the iMessage bot (Slice 8+)
    # Number of messages (user + assistant combined) to load as context.
    # Default 20 = ~10 exchanges. Must be an even number; odd values are fine but
    # may load a partially-paired window.
    chat_history_limit: int = 20

    # Scheduler — proactive outbound reminders and notifications (Slice 9+)
    # How often (in seconds) the scheduler polls Supabase for due tasks.
    scheduler_poll_interval_seconds: int = 60

    # Email dispatcher — outbound send_ack delivery (Slice 14+)
    # How often (in seconds) the dispatcher polls for pending send_ack actions.
    email_poll_interval_seconds: int = 60

    # Slack operator surface (Slice 13+)
    # How often (in seconds) the Slack scheduler polls for new approvals and errors.
    slack_poll_interval_seconds: int = 60
    # Time (HH:MM, 24h, Pacific) the Slack daily summary is posted each day.
    slack_summary_time_pt: str = "08:00"

    # Daily digest — proactive morning summary (Slice 10+)
    # Jake's phone number — required for all proactive outbound sends.
    jake_phone_number: Optional[str] = None
    # Default send time in Pacific time (HH:MM, 24h). Overridable per-user via
    # the daily_brief tool, which stores the preference in Supabase user_prefs.
    digest_send_time_pt: str = "07:00"


# Single shared instance — import this everywhere, never instantiate Settings again
settings = Settings()
