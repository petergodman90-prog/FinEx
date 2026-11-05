"""Schemas for API payloads (thin aliases: model serialization happens in services)."""

from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeResponse,
    MembershipOut,
    RefreshRequest,
    RefreshSessionRequest,
    RegisterRequest,
    SessionResponse,
    SwitchOrgRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.common import Page
from app.schemas.customer import (
    CustomerCreate,
    CustomerMergeRequest,
    CustomerOut,
    CustomerUpdate,
    TouchpointCreate,
    TouchpointOut,
)
from app.schemas.dashboard import DashboardMetrics, RecentTouchpoint
from app.schemas.proposal import (
    ProposalCreate,
    ProposalDecision,
    ProposalOut,
    ProposalListOut,
)

__all__ = [
    "CustomerCreate",
    "CustomerMergeRequest",
    "CustomerOut",
    "CustomerUpdate",
    "DashboardMetrics",
    "LoginRequest",
    "LogoutRequest",
    "MeResponse",
    "MembershipOut",
    "Page",
    "ProposalCreate",
    "ProposalDecision",
    "ProposalListOut",
    "ProposalOut",
    "RecentTouchpoint",
    "RefreshRequest",
    "RefreshSessionRequest",
    "RegisterRequest",
    "SessionResponse",
    "SwitchOrgRequest",
    "TokenResponse",
    "TouchpointCreate",
    "TouchpointOut",
    "UserOut",
]
