"""Auth API — register, login, refresh, logout, org switch, current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.cookies import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from app.core.db import get_db
from app.core.dependencies import get_current_user
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    SwitchOrgRequest,
    TokenResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    data: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Self-serve signup: creates user + organization + owner membership."""
    tokens = await auth_service.register(session, data)
    set_auth_cookies(response, tokens)
    return tokens


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    tokens = await auth_service.login(session, data)
    set_auth_cookies(response, tokens)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    data: RefreshRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Rotates the refresh token (from JSON or the HttpOnly cookie)."""
    refresh_token = data.refresh_token if data else None
    refresh_token = refresh_token or request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )
    tokens = await auth_service.refresh(
        session,
        RefreshRequest(
            refresh_token=refresh_token,
            org_id=data.org_id if data else None,
        ),
    )
    set_auth_cookies(response, tokens)
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    data: LogoutRequest | None = None,
    user: TokenPayload = Depends(get_current_user),
) -> None:
    """Revokes the presented access token (+ refresh token when supplied)."""
    refresh_token = data.refresh_token if data else None
    refresh_token = refresh_token or request.cookies.get(REFRESH_COOKIE)
    await auth_service.logout(user, refresh_token)
    clear_auth_cookies(response)


@router.post("/switch-org", response_model=TokenResponse)
async def switch_org(
    data: SwitchOrgRequest,
    response: Response,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    tokens = await auth_service.switch_org(session, user, data.org_id)
    set_auth_cookies(response, tokens)
    return tokens


@router.get("/me", response_model=MeResponse)
async def me(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    return await auth_service.me(session, user)
