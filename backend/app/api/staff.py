"""Staff directory, department, and job-title API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.core.rbac import Role
from app.models.membership import MembershipStatus
from app.schemas.staff import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    StaffDirectoryOut,
    StaffInviteRequest,
    StaffMemberOut,
    StaffRoleCreate,
    StaffRoleOut,
    StaffRoleUpdate,
    StaffUpdate,
)
from app.services import staff_service

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get(
    "",
    response_model=StaffDirectoryOut,
    dependencies=[Depends(require_permission("staff:read"))],
)
async def list_staff(
    q: str | None = Query(None, max_length=200),
    role: Role | None = None,
    staff_status: MembershipStatus | None = Query(None, alias="status"),
    department_id: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StaffDirectoryOut:
    return await staff_service.list_staff(
        session,
        user,
        q=q,
        role=role,
        status=staff_status,
        department_id=department_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=StaffMemberOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def invite_staff(
    data: StaffInviteRequest,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StaffMemberOut:
    """Create a pending invitation for an existing FinEx account.

    The invitee accepts it from their own account; no access is granted until
    then. Email invitations for people without an account are a later slice.
    """
    return await staff_service.invite_staff(session, data, user)


# Static sub-routes must be registered before /{membership_id}.
@router.get(
    "/departments",
    response_model=list[DepartmentOut],
    dependencies=[Depends(require_permission("staff:read"))],
)
async def list_departments(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> list[DepartmentOut]:
    return await staff_service.list_departments(session, user)


@router.post(
    "/departments",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def create_department(
    data: DepartmentCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> DepartmentOut:
    return await staff_service.create_department(session, data, user)


@router.patch(
    "/departments/{department_id}",
    response_model=DepartmentOut,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def update_department(
    department_id: uuid.UUID,
    data: DepartmentUpdate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> DepartmentOut:
    return await staff_service.update_department(
        session, department_id, data, user
    )


@router.delete(
    "/departments/{department_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def delete_department(
    department_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> None:
    await staff_service.delete_department(session, department_id, user)


@router.get(
    "/roles",
    response_model=list[StaffRoleOut],
    dependencies=[Depends(require_permission("staff:read"))],
)
async def list_staff_roles(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> list[StaffRoleOut]:
    return await staff_service.list_staff_roles(session, user)


@router.post(
    "/roles",
    response_model=StaffRoleOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def create_staff_role(
    data: StaffRoleCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StaffRoleOut:
    return await staff_service.create_staff_role(session, data, user)


@router.patch(
    "/roles/{role_id}",
    response_model=StaffRoleOut,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def update_staff_role(
    role_id: uuid.UUID,
    data: StaffRoleUpdate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StaffRoleOut:
    return await staff_service.update_staff_role(session, role_id, data, user)


@router.delete(
    "/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def delete_staff_role(
    role_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> None:
    await staff_service.delete_staff_role(session, role_id, user)


@router.patch(
    "/{membership_id}",
    response_model=StaffMemberOut,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def update_staff(
    membership_id: uuid.UUID,
    data: StaffUpdate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StaffMemberOut:
    return await staff_service.update_staff(session, membership_id, data, user)


@router.delete(
    "/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("staff:manage"))],
)
async def dismiss_staff(
    membership_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> None:
    await staff_service.dismiss_staff(session, membership_id, user)
