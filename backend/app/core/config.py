"""
backend/app/core/config.py — Add ANTHROPIC_API_KEY

Add the field below to your existing Settings class.
Railway reads this from the environment variable automatically.
"""

# ── Add this field to your existing Settings class ──────────────────────────
#
# class Settings(BaseSettings):
#     ...existing fields...
#
#     # Anthropic — AI News Scoring (Component 1)
#     # Set via Railway Dashboard → Project → Variables
#     ANTHROPIC_API_KEY: str | None = None
#
# ────────────────────────────────────────────────────────────────────────────

# Minimal standalone example for reference:
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    ENVIRONMENT: str = "development"

    # Add this:
    ANTHROPIC_API_KEY: str | None = None

    model_config = {"env_file": ".env", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings