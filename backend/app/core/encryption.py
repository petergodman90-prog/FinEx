"""
PII encryption and blind-index utilities.

- Fernet symmetric encryption for at-rest PII (phone, email).
- HMAC-SHA256 blind indexes for deterministic lookup without decryption.
- Canonicalization helpers so the same email/phone always yields the same
  blind index regardless of formatting.
- Crypto-shred support: overwrite encrypted fields with random bytes.

NEVER log, cache, or expose decrypted PII outside of response serialization.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import os
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


def canonical_email(value: str) -> str:
    """Normalize an email for blind-index lookup: trim + lowercase."""
    return value.strip().lower()


def canonical_phone(value: str) -> str:
    """
    Normalize a phone number for blind-index lookup.

    Keeps an optional leading ``+`` then strips all non-digits, so
    ``+44 7911 123456`` and ``+447911123456`` hash identically.
    """
    value = value.strip()
    digits = re.sub(r"\D", "", value)
    return f"+{digits}" if value.startswith("+") else digits


class PIIEngine:
    """Encrypt, decrypt, and blind-index PII values."""

    __slots__ = ("_fernet", "_hmac_key")

    def __init__(self, encryption_key: str, hmac_key: str) -> None:
        # Fernet expects url-safe base64-encoded 32-byte key
        self._fernet = Fernet(encryption_key.encode("utf-8"))
        # HMAC key stored as raw bytes (from hex)
        self._hmac_key = bytes.fromhex(hmac_key)

    # ── Encrypt / Decrypt ──────────────────────────────────

    def encrypt(self, plaintext: str | None) -> bytes | None:
        """Encrypt a PII string. Returns None for None/empty input."""
        if not plaintext:
            return None
        return self._fernet.encrypt(plaintext.strip().encode("utf-8"))

    def decrypt(self, ciphertext: bytes | None) -> str | None:
        """Decrypt a PII ciphertext. Returns None for None input."""
        if not ciphertext:
            return None
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    def try_decrypt(self, ciphertext: bytes | None) -> str | None:
        """
        Best-effort decrypt for serialization paths: returns None when the
        value was crypto-shredded or the key was rotated. The value itself is
        never logged.
        """
        if not ciphertext:
            return None
        try:
            return self.decrypt(ciphertext)
        except InvalidToken:
            logger.warning("PII decrypt failed (shredded value or rotated key)")
            return None

    # ── Blind Index ────────────────────────────────────────

    def blind_index(self, plaintext: str | None) -> str | None:
        """
        Deterministic HMAC-SHA256 blind index for lookup.
        Input is normalized: stripped + lowercased.
        """
        if not plaintext:
            return None
        normalized = plaintext.strip().lower()
        return _hmac.new(
            self._hmac_key,
            normalized.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ── Crypto-Shred ───────────────────────────────────────

    @staticmethod
    def shred_value() -> bytes:
        """Random bytes to replace encrypted data during crypto-shredding."""
        return os.urandom(64)


@lru_cache(maxsize=1)
def get_pii_engine() -> PIIEngine:
    """Lazy singleton — imported at use-site to avoid circular deps."""
    from app.core.config import get_settings

    s = get_settings()
    if not s.PII_ENCRYPTION_KEY or not s.PII_HMAC_KEY:
        raise RuntimeError(
            "PII_ENCRYPTION_KEY and PII_HMAC_KEY must be set. "
            "Generate with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return PIIEngine(s.PII_ENCRYPTION_KEY, s.PII_HMAC_KEY)
