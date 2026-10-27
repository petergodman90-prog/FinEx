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
from app.core.rate_limit import (
    enforce_login_rate_limit,
    enforce_refresh_rate_limit,
    enforce_register_rate_limit,
)
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RefreshSessionRequest,
    RegisterRequest,
    SessionResponse,
    SwitchOrgRequest,
    TokenResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    data: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Self-serve signup: creates user + organization + owner membership."""
    await enforce_register_rate_limit(request)
    tokens = await auth_service.register(session, data)
    set_auth_cookies(response, tokens)
    return tokens.session()


@router.post("/login", response_model=SessionResponse)
async def login(
    data: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    await enforce_login_rate_limit(request, str(data.email))
    tokens = await auth_service.login(session, data)
    set_auth_cookies(response, tokens)
    return tokens.session()


@router.post("/token", response_model=TokenResponse)
async def token(
    data: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Non-browser login: returns bearer tokens instead of setting cookies."""
    await enforce_login_rate_limit(request, str(data.email))
    return await auth_service.login(session, data)


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    request: Request,
    response: Response,
    data: RefreshSessionRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Rotates the browser refresh token from the HttpOnly cookie."""
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )
    await enforce_refresh_rate_limit(request)
    tokens = await auth_service.refresh(
        session,
        RefreshRequest(
            refresh_token=refresh_token,
            org_id=data.org_id if data else None,
        ),
    )
    set_auth_cookies(response, tokens)
    return tokens.session()


@router.post("/token/refresh", response_model=TokenResponse)
async def token_refresh(
    data: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Non-browser refresh: the caller supplies its own bearer refresh token."""
    await enforce_refresh_rate_limit(request)
    return await auth_service.refresh(session, data)


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


@router.post("/switch-org", response_model=SessionResponse)
async def switch_org(
    data: SwitchOrgRequest,
    response: Response,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> SessionResponse:
    tokens = await auth_service.switch_org(session, user, data.org_id)
    set_auth_cookies(response, tokens)
    return tokens.session()


@router.post("/token/switch-org", response_model=TokenResponse)
async def token_switch_org(
    data: SwitchOrgRequest,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Non-browser org switch: returns a new bearer access token."""
    return await auth_service.switch_org(session, user, data.org_id)


@router.get("/me", response_model=MeResponse)
async def me(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    return await auth_service.me(session, user)
