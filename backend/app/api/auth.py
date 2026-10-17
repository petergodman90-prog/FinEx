"""Auth API — register, login, refresh, logout, org switch, current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
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
    data: RegisterRequest, session: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """Self-serve signup: creates user + organization + owner membership."""
    return await auth_service.register(session, data)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest, session: AsyncSession = Depends(get_db)
) -> TokenResponse:
    return await auth_service.login(session, data)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    data: RefreshRequest, session: AsyncSession = Depends(get_db)
) -> TokenResponse:
    """Rotates the refresh token (the presented one is revoked)."""
    return await auth_service.refresh(session, data)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: LogoutRequest | None = None,
    user: TokenPayload = Depends(get_current_user),
) -> None:
    """Revokes the presented access token (+ refresh token when supplied)."""
    await auth_service.logout(user, data.refresh_token if data else None)


@router.post("/switch-org", response_model=TokenResponse)
async def switch_org(
    data: SwitchOrgRequest,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await auth_service.switch_org(session, user, data.org_id)


@router.get("/me", response_model=MeResponse)
async def me(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    return await auth_service.me(session, user)
