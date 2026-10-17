"""Unit tests for PII encryption + blind indexing (no DB needed)."""

from __future__ import annotations

import os
import secrets

from cryptography.fernet import Fernet

from app.core.encryption import (
    PIIEngine,
    canonical_email,
    canonical_phone,
)


def _engine() -> PIIEngine:
    return PIIEngine(Fernet.generate_key().decode(), secrets.token_hex(32))


def test_encrypt_decrypt_roundtrip() -> None:
    engine = _engine()
    ciphertext = engine.encrypt("+44 7911 123456")
    assert ciphertext is not None
    assert b"7911" not in ciphertext  # not plaintext
    assert engine.decrypt(ciphertext) == "+44 7911 123456"


def test_encrypt_none_and_empty() -> None:
    engine = _engine()
    assert engine.encrypt(None) is None
    assert engine.encrypt("") is None
    assert engine.decrypt(None) is None


def test_blind_index_deterministic_and_normalized() -> None:
    engine = _engine()
    a = engine.blind_index(canonical_email("  Billing@Acme.TEST "))
    b = engine.blind_index("billing@acme.test")
    assert a == b and a is not None and len(a) == 64

    p1 = engine.blind_index(canonical_phone("+44 7911 123456"))
    p2 = engine.blind_index(canonical_phone("+447911123456"))
    assert p1 == p2 and p1 is not None


def test_blind_index_none() -> None:
    engine = _engine()
    assert engine.blind_index(None) is None
    assert engine.blind_index("") is None


def test_canonical_phone_keeps_plus() -> None:
    assert canonical_phone(" +1 (555) 010-9999 ") == "+15550109999"
    assert canonical_phone("555-010-9999") == "5550109999"


def test_try_decrypt_shredded_value_returns_none() -> None:
    engine = _engine()
    assert engine.try_decrypt(os.urandom(64)) is None
    assert engine.try_decrypt(None) is None
