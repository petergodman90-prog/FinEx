"""Staff directory, department, and job-title schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.rbac import Role
from app.models.membership import MembershipStatus
from app.schemas.auth import EmailStr

StaffName = Annotated[str, StringConstraints(min_length=1, max_length=60)]
StaffDescription = Annotated[str, StringConstraints(max_length=240)]
JobTitle = Annotated[str, StringConstraints(max_length=80)]


class StaffMemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str | None = None
    role: Role
    status: MembershipStatus
    job_title: str | None = None
    department_id: uuid.UUID | None = None
    department_name: str | None = None
    reports_to_id: uuid.UUID | None = None
    reports_to_name: str | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    is_self: bool = False
    can_manage: bool = False


class StaffSummary(BaseModel):
    total: int = 0
    active: int = 0
    pending: int = 0
    suspended: int = 0


class StaffDirectoryOut(BaseModel):
    items: list[StaffMemberOut] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0
    summary: StaffSummary = Field(default_factory=StaffSummary)


class StaffInviteRequest(BaseModel):
    """Invite an existing FinEx account into the active workspace."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Role = Role.STAFF
    job_title: JobTitle | None = None
    department_id: uuid.UUID | None = None
    reports_to_id: uuid.UUID | None = None


class StaffUpdate(BaseModel):
    """Partial staff update. Name/email are intentionally not editable here.

    Users are global identities; changing their name or email belongs to the
    account owner's auth profile, not to one tenant's staff screen.
    """

    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    status: Literal["active", "suspended"] | None = None
    job_title: JobTitle | None = None
    department_id: uuid.UUID | None = None
    reports_to_id: uuid.UUID | None = None


class PendingInvitationOut(BaseModel):
    membership_id: uuid.UUID
    org_id: uuid.UUID
    org_name: str
    role: Role
    job_title: str | None = None
    invited_at: datetime


class InvitationDecisionOut(BaseModel):
    accepted: bool
    org_id: uuid.UUID
    role: Role


class DepartmentCreate(BaseModel):
    name: StaffName
    description: StaffDescription | None = None


class DepartmentUpdate(BaseModel):
    name: StaffName | None = None
    description: StaffDescription | None = None


class DepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class StaffRoleCreate(BaseModel):
    name: StaffName
    description: StaffDescription | None = None


class StaffRoleUpdate(BaseModel):
    name: StaffName | None = None
    description: StaffDescription | None = None


class StaffRoleOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    is_custom: bool = False
    assigned_count: int = 0
