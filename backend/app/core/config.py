from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings, loaded from environment / ``.env``.

    ``.env`` is looked up in the common run locations (repo root when running
    from ``backend/`` and vice versa), so uvicorn, alembic and pytest all
    resolve the same file regardless of the working directory.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: str = "local"
    APP_NAME: str = "FINEX"

    DATABASE_URL: str
    # Used by Alembic only; falls back to DATABASE_URL when unset. Point this
    # at an owner/migration role so the runtime app role can stay
    # RLS-restricted (see docs/security-charter.md).
    MIGRATION_DATABASE_URL: str | None = None

    REDIS_URL: str

    # --- Auth / tokens ---
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 14  # 14 days

    # --- PII encryption ---
    # Fernet key (at-rest encryption) + hex HMAC key (blind index).
    # Empty by default so the app can boot before keys are provisioned; any
    # code path that actually touches PII fails loudly with a clear error.
    PII_ENCRYPTION_KEY: str = ""
    PII_HMAC_KEY: str = ""

    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
