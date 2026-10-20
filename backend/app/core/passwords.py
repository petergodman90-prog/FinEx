"""Password hashing (bcrypt)."""

from __future__ import annotations

import bcrypt

# bcrypt only considers the first 72 bytes; longer inputs are rejected up
# front rather than silently truncated. API schemas also cap at 72 chars.
BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    data = plain.encode("utf-8")
    if len(data) > BCRYPT_MAX_BYTES:
        raise ValueError(f"password exceeds {BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(data, bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt check; never raises on malformed input."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
