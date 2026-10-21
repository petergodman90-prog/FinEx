"""Same-origin UI and HttpOnly session-cookie tests.

These protect two things that are easy to regress:
  1. Only the public frontend directories are served — never the repo root
     (which contains .env, backend/ and db/).
  2. Browser sessions live in HttpOnly cookies, so page JavaScript cannot read
     the access token.
"""

from __future__ import annotations

import pytest

from app.core.cookies import ACCESS_COOKIE


async def test_public_ui_is_served(client) -> None:
    index = await client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "FinEx" in index.text
    assert index.headers["cache-control"] == "no-store"

    alias = await client.get("/index.html")
    assert alias.status_code == 200
    assert "text/html" in alias.headers["content-type"]

    for path in ("/css/style.css", "/css/dashboard.css", "/js/api.js", "/pages/register.html", "/pages/dashboard.html"):
        response = await client.get(path)
        assert response.status_code == 200, path
        expected_cache = "no-cache" if path.endswith((".css", ".js")) else "no-store"
        assert response.headers["cache-control"] == expected_cache, path


@pytest.mark.parametrize(
    "path",
    [
        "/.env",
        "/.env.example",
        "/README.md",
        "/backend/app/main.py",
        "/backend/.env",
        "/db/migrations/0001_person_a_financial_core.sql",
        "/docs/security-charter.md",
        "/scripts/deploy.ps1",
    ],
)
async def test_repository_files_are_not_exposed(client, path: str) -> None:
    response = await client.get(path)
    assert response.status_code == 404, f"{path} must never be served"


async def test_browser_security_headers(client) -> None:
    page = await client.get("/")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["referrer-policy"] == "no-referrer"

    api = await client.get("/api/v1/auth/me")
    assert api.status_code == 401
    assert api.headers["cache-control"] == "no-store"


async def test_cookie_session_refresh_and_logout(client, register_user) -> None:
    reg = await register_user(full_name="Cookie User")

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": reg["user"]["email"], "password": "password123"},
    )
    assert login.status_code == 200, login.text

    set_cookies = login.headers.get_list("set-cookie")
    assert any(
        cookie.startswith(f"{ACCESS_COOKIE}=")
        and "HttpOnly" in cookie
        and "SameSite=lax" in cookie
        for cookie in set_cookies
    ), set_cookies

    # No Authorization header: the cookie authenticates /me.
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["id"] == reg["user"]["id"]

    # Drop only the access cookie; refresh rotates the session from the
    # HttpOnly refresh cookie and returns a fresh access cookie.
    client.cookies.pop(ACCESS_COOKIE, None)
    refreshed = await client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    me_again = await client.get("/api/v1/auth/me")
    assert me_again.status_code == 200

    # Logout revokes the session and clears the browser cookies.
    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    cleared = logout.headers.get_list("set-cookie")
    assert any(cookie.startswith(f"{ACCESS_COOKIE}=") for cookie in cleared)
    after = await client.get("/api/v1/auth/me")
    assert after.status_code == 401
