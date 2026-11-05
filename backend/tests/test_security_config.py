"""Startup configuration must fail closed in production."""

from __future__ import annotations

import secrets

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings

_GOOD_JWT = secrets.token_urlsafe(48)


def _settings(**overrides) -> Settings:
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://finex:test@127.0.0.1/finex",
        "REDIS_URL": "redis://127.0.0.1:6379/0",
        "JWT_SECRET": _GOOD_JWT,
        "PII_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "PII_HMAC_KEY": secrets.token_hex(32),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_valid_production_settings_boot() -> None:
    settings = _settings()
    assert settings.is_production


@pytest.mark.parametrize(
    "secret",
    [
        "change_me_local_only",
        "changeme",
        "ci-only-not-a-production-secret",
        "short",
        "a" * 48,
    ],
)
def test_production_rejects_weak_jwt_secrets(secret: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(JWT_SECRET=secret)


def test_production_requires_pii_keys() -> None:
    with pytest.raises(ValidationError, match="PII_ENCRYPTION_KEY"):
        _settings(PII_ENCRYPTION_KEY="", PII_HMAC_KEY="")


def test_production_rejects_invalid_fernet_key() -> None:
    with pytest.raises(ValidationError, match="Fernet"):
        _settings(PII_ENCRYPTION_KEY="not-a-fernet-key")


def test_production_rejects_invalid_hmac_key() -> None:
    with pytest.raises(ValidationError, match="PII_HMAC_KEY"):
        _settings(PII_HMAC_KEY="abc")


def test_wildcard_cors_is_rejected() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        _settings(CORS_ORIGINS="https://app.example.com,*")


def test_unsafe_jwt_algorithm_is_rejected() -> None:
    with pytest.raises(ValidationError, match="JWT_ALGORITHM"):
        _settings(JWT_ALGORITHM="none")


def test_local_development_may_use_placeholders() -> None:
    settings = _settings(
        APP_ENV="local",
        JWT_SECRET="change_me_local_only",
        PII_ENCRYPTION_KEY="",
        PII_HMAC_KEY="",
    )
    assert not settings.is_production
