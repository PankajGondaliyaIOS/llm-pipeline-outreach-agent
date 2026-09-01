"""
src/config.py - Application configuration and environment variable parsing.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_PATH: str = "data/outreach_state.db"

    # Gemini API
    GEMINI_API_KEY: str

    # Pipeline Tuning
    BATCH_SIZE: int = 10
    RATE_LIMIT_PER_MINUTE: int = 10
    LOG_LEVEL: str = "INFO"

    # SMTP Configuration (Generic Defaults - Real values load from .env)
    SMTP_HOST: str = "smtp-relay.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: str = "outreach@example.com"
    SMTP_FROM_NAME: str = "Outreach Agent"

    # Sending Window & Rate Limits
    DAILY_EMAIL_LIMIT: int = 50
    EMAIL_DELAY_MIN_SECONDS: int = 480
    EMAIL_DELAY_MAX_SECONDS: int = 720
    WORK_HOURS_START: int = 9
    WORK_HOURS_END: int = 17

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()