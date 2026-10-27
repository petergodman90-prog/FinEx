from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRODUCTION_ENVS = {"production", "prod"}
_WEAK_JWT_SECRETS = {
    "change_me_local_only",
    "changeme",
    "change-me",
    "secret",
    "password",
    "replace-me",
    "ci-only-not-a-production-secret",
}
_MIN_JWT_SECRET_BYTES = 32
_ALLOWED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


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

    # Comma-separated browser origins allowed to call the API directly.
    # Leave empty when the UI and API share an origin (reverse proxy); this is
    # the production default.  Set it only for local static-server development.
    CORS_ORIGINS: str = ""

    # Serve the static UI from this process (same origin as /api/v1). This is
    # convenient locally and avoids CORS; set false when nginx serves the UI.
    SERVE_FRONTEND: bool = True
    # Cookies are always HttpOnly + SameSite=Lax. Secure is forced when
    # APP_ENV=production; set true explicitly for HTTPS outside that label.
    COOKIE_SECURE: bool = False

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

    # --- Auth rate limiting (Redis; fail closed if Redis is unavailable) ---
    AUTH_RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10
    LOGIN_ACCOUNT_RATE_LIMIT_PER_MINUTE: int = 5
    REGISTER_RATE_LIMIT_PER_HOUR: int = 10
    REFRESH_RATE_LIMIT_PER_MINUTE: int = 30

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

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in _PRODUCTION_ENVS

    @model_validator(mode="after")
    def _validate_security_settings(self) -> "Settings":
        """Reject unsafe combinations before the app can start.

        Local development deliberately allows weak placeholders. Production is
        held to a higher standard because these values protect every session
        and every encrypted PII field.
        """
        if self.ACCESS_TOKEN_EXPIRE_MINUTES <= 0:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be positive")
        if self.REFRESH_TOKEN_EXPIRE_MINUTES <= 0:
            raise ValueError("REFRESH_TOKEN_EXPIRE_MINUTES must be positive")
        if self.REFRESH_TOKEN_EXPIRE_MINUTES < self.ACCESS_TOKEN_EXPIRE_MINUTES:
            raise ValueError(
                "REFRESH_TOKEN_EXPIRE_MINUTES must be >= ACCESS_TOKEN_EXPIRE_MINUTES"
            )

        if self.JWT_ALGORITHM not in _ALLOWED_JWT_ALGORITHMS:
            raise ValueError(
                "JWT_ALGORITHM must be one of "
                + ", ".join(sorted(_ALLOWED_JWT_ALGORITHMS))
            )

        if any(
            origin.strip() == "*"
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ):
            raise ValueError("CORS_ORIGINS must not contain a wildcard")

        if not self.is_production:
            return self

        jwt_secret = self.JWT_SECRET.strip()
        if len(jwt_secret.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"JWT_SECRET must be at least {_MIN_JWT_SECRET_BYTES} bytes in production"
            )
        lowered = jwt_secret.lower()
        if (
            lowered in _WEAK_JWT_SECRETS
            or "change_me" in lowered
            or "placeholder" in lowered
            or "ci-only" in lowered
            or "not-a-production" in lowered
            or len(set(jwt_secret)) < 8
        ):
            raise ValueError("JWT_SECRET is too weak; refusing production startup")

        if not self.PII_ENCRYPTION_KEY or not self.PII_HMAC_KEY:
            raise ValueError(
                "PII_ENCRYPTION_KEY and PII_HMAC_KEY are required in production"
            )
        try:
            Fernet(self.PII_ENCRYPTION_KEY.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("PII_ENCRYPTION_KEY is not a valid Fernet key") from exc
        try:
            hmac_key = bytes.fromhex(self.PII_HMAC_KEY)
        except ValueError as exc:
            raise ValueError("PII_HMAC_KEY must be hexadecimal") from exc
        if len(hmac_key) < 32:
            raise ValueError("PII_HMAC_KEY must contain at least 32 bytes")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
