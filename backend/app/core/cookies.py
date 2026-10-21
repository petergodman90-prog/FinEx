"""HttpOnly cookie helpers for the browser interface.

The JSON API still returns bearer tokens for non-browser clients. The browser
UI never sees them: register/login/refresh also set HttpOnly cookies, so an XSS
bug cannot read the session. SameSite=Lax stops cross-site form POSTs from
carrying the cookie (basic CSRF protection for this same-origin UI).

Production forces ``Secure`` cookies; local HTTP development does not.
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import get_settings
from app.schemas.auth import TokenResponse

ACCESS_COOKIE = "finex_access"
REFRESH_COOKIE = "finex_refresh"


def _secure() -> bool:
    settings = get_settings()
    return settings.COOKIE_SECURE or settings.APP_ENV.strip().lower() in {
        "production",
        "prod",
    }


def set_auth_cookies(response: Response, tokens: TokenResponse) -> None:
    settings = get_settings()
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=_secure(),
        samesite="lax",
        path="/",
    )
    if tokens.refresh_token:
        response.set_cookie(
            REFRESH_COOKIE,
            tokens.refresh_token,
            max_age=settings.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
            httponly=True,
            secure=_secure(),
            samesite="lax",
            path="/",
        )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
