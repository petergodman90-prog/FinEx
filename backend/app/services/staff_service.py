"""Staff directory and organisation membership management.

Security rules enforced here (in addition to RLS in ``core/rls.py``):

* staff reads require manager+; all mutations require owner/admin;
* nobody can change their own role/status or dismiss themselves;
* admins cannot modify owners or other admins, and cannot grant a role at
  or above their own;
* removing the last active owner is refused;
* a suspended/dismissed member's tokens are revoked immediately;
* global user identity fields (name/email) are never editable from one
  tenant's staff screen;
* manager links must stay inside the org and must not form a cycle.
"""

from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.auth import TokenPayload
from app.core.encryption import canonical_email, get_pii_engine
from app.core.rbac import ROLE_WEIGHT, Role
from app.core.rls import bind_session_context
from app.core.sessions import revoke_all_user_tokens
from app.models.department import Department
from app.models.membership import Membership, MembershipStatus
from app.models.organization import Organization
from app.models.staff_role import StaffRole
from app.models.user import User
from app.schemas.staff import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentUpdate,
    InvitationDecisionOut,
    PendingInvitationOut,
    StaffDirectoryOut,
    StaffInviteRequest,
    StaffMemberOut,
    StaffRoleCreate,
    StaffRoleOut,
    StaffRoleUpdate,
    StaffSummary,
    StaffUpdate,
)
from app.services import audit_service
from app.services.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)

# Display-only job titles. These are deliberately separate from RBAC roles.
BUILT_IN_STAFF_ROLES: tuple[dict[str, str], ...] = (
    {
        "id": "manager",
        "name": "Manager",
        "description": "Oversees staff and business operations.",
    },
    {
        "id": "cashier",
        "name": "Cashier",
        "description": "Handles customer payments and sales.",
    },
    {
        "id": "accountant",
        "name": "Accountant",
        "description": "Supports bookkeeping and financial records.",
    },
    {
        "id": "inventory-staff",
        "name": "Inventory Staff",
        "description": "Tracks stock and inventory activity.",
    },
)

_ACTIVE = MembershipStatus.ACTIVE.value
_PENDING = MembershipStatus.PENDING.value
_SUSPENDED = MembershipStatus.SUSPENDED.value


# ── Helpers ────────────────────────────────────────────────


def _clean_name(value: str) -> str:
    return " ".join(value.strip().split())


def _require_name(value: str) -> str:
    name = _clean_name(value)
    if not name:
        raise DomainValidationError("Name cannot be blank")
    return name


def _actor_role(actor: TokenPayload) -> Role:
    try:
        return Role(actor.role)
    except ValueError as exc:  # defensive: a malformed token must not pass
        raise PermissionDeniedError("Unknown caller role") from exc


def _snapshot(membership: Membership) -> dict:
    """PII-free audit snapshot."""
    return {
        "user_id": str(membership.user_id),
        "role": membership.role,
        "status": membership.status,
        "job_title": membership.job_title,
        "department_id": (
            str(membership.department_id) if membership.department_id else None
        ),
        "reports_to_id": (
            str(membership.reports_to_id) if membership.reports_to_id else None
        ),
    }


def _can_manage(actor: TokenPayload, target: Membership) -> bool:
    if str(target.user_id) == actor.sub:
        return False
    actor_role = _actor_role(actor)
    if actor_role is Role.OWNER:
        return True
    if actor_role is Role.ADMIN:
        return target.role not in {Role.OWNER.value, Role.ADMIN.value}
    return False


def _ensure_can_manage(actor: TokenPayload, target: Membership) -> None:
    if str(target.user_id) == actor.sub:
        raise PermissionDeniedError(
            "You cannot change your own staff access from the staff directory"
        )
    actor_role = _actor_role(actor)
    if actor_role is Role.OWNER:
        return
    if actor_role is Role.ADMIN and target.role not in {
        Role.OWNER.value,
        Role.ADMIN.value,
    }:
        return
    raise PermissionDeniedError(
        "Only an owner can manage owners and admins"
    )


def _assert_can_grant_role(actor: TokenPayload, new_role: Role) -> None:
    actor_role = _actor_role(actor)
    if actor_role is Role.OWNER:
        return
    if ROLE_WEIGHT[new_role] >= ROLE_WEIGHT[actor_role]:
        raise PermissionDeniedError(
            "Admins cannot grant a role at or above their own"
        )


async def _assert_owner_remains(
    session: AsyncSession,
    actor: TokenPayload,
    target: Membership,
    *,
    new_role: str | None = None,
    new_status: str | None = None,
) -> None:
    """Refuse to remove/demote the last active owner."""
    if target.role != Role.OWNER.value or target.status != _ACTIVE:
        return
    if new_role in (None, Role.OWNER.value) and new_status in (None, _ACTIVE):
        return
    other_owners = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.org_id == actor.org_id,
            Membership.role == Role.OWNER.value,
            Membership.status == _ACTIVE,
            Membership.id != target.id,
        )
    )
    if not other_owners:
        raise ConflictError("The workspace must retain at least one active owner")


async def _lookup_member(
    session: AsyncSession, actor: TokenPayload, membership_id: uuid.UUID
) -> tuple[Membership, User]:
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.org_id == actor.org_id,
        )
    )
    if membership is None:
        raise NotFoundError("Staff member not found")
    user = await session.get(User, membership.user_id)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("Staff account not found")
    return membership, user


async def _read_member_out(
    session: AsyncSession, actor: TokenPayload, membership_id: uuid.UUID
) -> StaffMemberOut:
    manager = aliased(Membership)
    manager_user = aliased(User)
    row = (
        await session.execute(
            select(Membership, User, Department.name, manager_user.full_name)
            .join(User, User.id == Membership.user_id)
            .outerjoin(Department, Department.id == Membership.department_id)
            .outerjoin(manager, manager.id == Membership.reports_to_id)
            .outerjoin(manager_user, manager_user.id == manager.user_id)
            .where(
                Membership.id == membership_id,
                Membership.org_id == actor.org_id,
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Staff member not found")
    membership, user, department_name, manager_name = row
    pii = get_pii_engine()
    return StaffMemberOut(
        id=membership.id,
        user_id=user.id,
        full_name=user.full_name,
        email=pii.try_decrypt(user.email_encrypted),
        role=Role(membership.role),
        status=MembershipStatus(membership.status),
        job_title=membership.job_title,
        department_id=membership.department_id,
        department_name=department_name,
        reports_to_id=membership.reports_to_id,
        reports_to_name=manager_name,
        last_seen_at=user.last_login_at,
        created_at=membership.created_at,
        is_self=str(user.id) == actor.sub,
        can_manage=_can_manage(actor, membership),
    )


async def _validate_department(
    session: AsyncSession, actor: TokenPayload, department_id: uuid.UUID | None
) -> Department | None:
    if department_id is None:
        return None
    department = await session.scalar(
        select(Department).where(
            Department.id == department_id,
            Department.org_id == actor.org_id,
        )
    )
    if department is None:
        raise DomainValidationError("Department not found in this workspace")
    return department


async def _validate_manager(
    session: AsyncSession,
    actor: TokenPayload,
    reports_to_id: uuid.UUID | None,
    *,
    target_id: uuid.UUID | None = None,
) -> Membership | None:
    if reports_to_id is None:
        return None
    if target_id is not None and reports_to_id == target_id:
        raise DomainValidationError("A staff member cannot report to themselves")
    manager = await session.scalar(
        select(Membership).where(
            Membership.id == reports_to_id,
            Membership.org_id == actor.org_id,
        )
    )
    if manager is None:
        raise DomainValidationError("Manager not found in this workspace")
    if manager.status != _ACTIVE:
        raise DomainValidationError("The selected manager is not active")

    rows = (
        await session.execute(
            select(Membership.id, Membership.reports_to_id).where(
                Membership.org_id == actor.org_id
            )
        )
    ).all()
    parents = {row.id: row.reports_to_id for row in rows}
    visited: set[uuid.UUID] = set()
    cursor = manager.id
    while cursor is not None:
        if target_id is not None and cursor == target_id:
            raise DomainValidationError(
                "That manager link would create a reporting cycle"
            )
        if cursor in visited:
            # Existing bad data must not trap the request in a loop. The API
            # will still reject a new link that points into the cycle.
            break
        visited.add(cursor)
        cursor = parents.get(cursor)
    return manager


async def _validate_job_title(
    session: AsyncSession, actor: TokenPayload, job_title: str | None
) -> str | None:
    if job_title is None:
        return None
    name = _clean_name(job_title)
    if not name:
        return None
    folded = name.casefold()
    for built_in in BUILT_IN_STAFF_ROLES:
        if built_in["name"].casefold() == folded:
            return built_in["name"]
    custom = await session.scalar(
        select(StaffRole).where(
            StaffRole.org_id == actor.org_id,
            func.lower(StaffRole.name) == name.lower(),
        )
    )
    if custom is None:
        raise DomainValidationError(
            "Unknown job title. Create the custom role first."
        )
    return custom.name


async def _revoke_active_member_tokens(target: Membership) -> None:
    if target.status == _ACTIVE:
        await revoke_all_user_tokens(str(target.user_id))


# ── Directory ──────────────────────────────────────────────


async def list_staff(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    q: str | None = None,
    role: Role | None = None,
    status: MembershipStatus | None = None,
    department_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> StaffDirectoryOut:
    conditions = [Membership.org_id == actor.org_id]
    if role is not None:
        conditions.append(Membership.role == role.value)
    if status is not None:
        conditions.append(Membership.status == status.value)
    if department_id is not None:
        conditions.append(Membership.department_id == department_id)

    if q and (term := q.strip()):
        pii = get_pii_engine()
        search = [
            User.full_name.ilike(f"%{term}%"),
            Membership.job_title.ilike(f"%{term}%"),
            cast(Membership.role, String).ilike(f"%{term}%"),
        ]
        if "@" in term:
            search.append(
                User.email_bidx == pii.blind_index(canonical_email(term))
            )
        conditions.append(or_(*search))

    total = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .join(User, User.id == Membership.user_id)
        .where(*conditions)
    )

    manager = aliased(Membership)
    manager_user = aliased(User)
    rows = (
        await session.execute(
            select(Membership, User, Department.name, manager_user.full_name)
            .join(User, User.id == Membership.user_id)
            .outerjoin(Department, Department.id == Membership.department_id)
            .outerjoin(manager, manager.id == Membership.reports_to_id)
            .outerjoin(manager_user, manager_user.id == manager.user_id)
            .where(*conditions)
            .order_by(User.full_name, Membership.created_at)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    pii = get_pii_engine()
    items = [
        StaffMemberOut(
            id=membership.id,
            user_id=user.id,
            full_name=user.full_name,
            email=pii.try_decrypt(user.email_encrypted),
            role=Role(membership.role),
            status=MembershipStatus(membership.status),
            job_title=membership.job_title,
            department_id=membership.department_id,
            department_name=department_name,
            reports_to_id=membership.reports_to_id,
            reports_to_name=manager_name,
            last_seen_at=user.last_login_at,
            created_at=membership.created_at,
            is_self=str(user.id) == actor.sub,
            can_manage=_can_manage(actor, membership),
        )
        for membership, user, department_name, manager_name in rows
    ]

    status_rows = (
        await session.execute(
            select(Membership.status, func.count())
            .where(Membership.org_id == actor.org_id)
            .group_by(Membership.status)
        )
    ).all()
    counts = Counter({status_value: int(count) for status_value, count in status_rows})
    summary = StaffSummary(
        total=sum(counts.values()),
        active=counts.get(_ACTIVE, 0),
        pending=counts.get(_PENDING, 0),
        suspended=counts.get(_SUSPENDED, 0),
    )
    return StaffDirectoryOut(
        items=items,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        summary=summary,
    )


async def invite_staff(
    session: AsyncSession, data: StaffInviteRequest, actor: TokenPayload
) -> StaffMemberOut:
    """Create a pending membership for an existing FinEx account.

    Email delivery is intentionally out of scope for this slice. The invitee
    sees and accepts the pending invitation from their own account.
    """
    _assert_can_grant_role(actor, data.role)

    pii = get_pii_engine()
    email = canonical_email(str(data.email))
    user = await session.scalar(
        select(User).where(
            User.email_bidx == pii.blind_index(email),
            User.deleted_at.is_(None),
        )
    )
    if user is None or not user.is_active:
        raise ConflictError(
            "No active FinEx account uses that email. Ask them to create an "
            "account first; email invitations are not implemented yet.",
            details={"reason": "account_not_found"},
        )

    existing = await session.scalar(
        select(Membership).where(
            Membership.org_id == actor.org_id,
            Membership.user_id == user.id,
        )
    )
    if existing is not None:
        if existing.status == _PENDING:
            raise ConflictError("That account already has a pending invitation")
        raise ConflictError("That account is already a member of this workspace")

    department = await _validate_department(session, actor, data.department_id)
    manager = await _validate_manager(session, actor, data.reports_to_id)
    job_title = await _validate_job_title(session, actor, data.job_title)

    membership = Membership(
        org_id=actor.org_id,
        user_id=user.id,
        role=data.role.value,
        status=_PENDING,
        job_title=job_title,
        department_id=department.id if department else None,
        reports_to_id=manager.id if manager else None,
        is_default=False,
    )
    session.add(membership)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("That account is already a member of this workspace") from exc

    await audit_service.record(
        session,
        action="staff.invited",
        entity_type="membership",
        entity_id=membership.id,
        after_state=_snapshot(membership),
    )
    return await _read_member_out(session, actor, membership.id)


# ── Member mutations ───────────────────────────────────────


async def update_staff(
    session: AsyncSession,
    membership_id: uuid.UUID,
    data: StaffUpdate,
    actor: TokenPayload,
) -> StaffMemberOut:
    target, _user = await _lookup_member(session, actor, membership_id)
    _ensure_can_manage(actor, target)
    before = _snapshot(target)
    was_active = target.status == _ACTIVE
    payload = data.model_dump(exclude_unset=True)
    if not payload:
        return await _read_member_out(session, actor, target.id)

    changed_access = False

    if "role" in payload:
        if payload["role"] is None:
            raise DomainValidationError("Role cannot be null")
        new_role = Role(payload["role"])
        _assert_can_grant_role(actor, new_role)
        if target.role != new_role.value:
            await _assert_owner_remains(
                session, actor, target, new_role=new_role.value
            )
            target.role = new_role.value
            changed_access = True

    if "status" in payload:
        if payload["status"] is None:
            raise DomainValidationError("Status cannot be null")
        if target.status == _PENDING:
            raise ConflictError(
                "Pending invitations must be accepted or cancelled by the invitee"
            )
        new_status = str(payload["status"])
        if new_status not in {_ACTIVE, _SUSPENDED}:
            raise DomainValidationError("Status must be active or suspended")
        if target.status != new_status:
            await _assert_owner_remains(
                session, actor, target, new_status=new_status
            )
            target.status = new_status
            changed_access = True

    if "job_title" in payload:
        target.job_title = await _validate_job_title(
            session, actor, payload["job_title"]
        )
    if "department_id" in payload:
        department = await _validate_department(
            session, actor, payload["department_id"]
        )
        target.department_id = department.id if department else None
    if "reports_to_id" in payload:
        manager = await _validate_manager(
            session, actor, payload["reports_to_id"], target_id=target.id
        )
        target.reports_to_id = manager.id if manager else None

    # Revoke before commit: if Redis is unavailable the DB transaction rolls
    # back rather than leaving a suspended/demoted user with a live token.
    if changed_access and was_active:
        await revoke_all_user_tokens(str(target.user_id))

    await session.flush()
    await audit_service.record(
        session,
        action="staff.updated",
        entity_type="membership",
        entity_id=target.id,
        before_state=before,
        after_state=_snapshot(target),
    )
    return await _read_member_out(session, actor, target.id)


async def dismiss_staff(
    session: AsyncSession, membership_id: uuid.UUID, actor: TokenPayload
) -> None:
    target, _user = await _lookup_member(session, actor, membership_id)
    _ensure_can_manage(actor, target)
    await _assert_owner_remains(session, actor, target)
    before = _snapshot(target)
    await _revoke_active_member_tokens(target)
    await session.delete(target)
    await session.flush()
    await audit_service.record(
        session,
        action="staff.dismissed",
        entity_type="membership",
        entity_id=target.id,
        before_state=before,
    )


# ── Invitee-side invitation flow ───────────────────────────


async def list_my_invitations(
    session: AsyncSession, actor: TokenPayload
) -> list[PendingInvitationOut]:
    user_id = uuid.UUID(actor.sub)
    await bind_session_context(session, user_id=user_id)
    rows = (
        await session.execute(
            select(Membership, Organization.name)
            .join(Organization, Organization.id == Membership.org_id)
            .where(
                Membership.user_id == user_id,
                Membership.status == _PENDING,
            )
            .order_by(Membership.created_at)
        )
    ).all()
    return [
        PendingInvitationOut(
            membership_id=membership.id,
            org_id=membership.org_id,
            org_name=org_name,
            role=Role(membership.role),
            job_title=membership.job_title,
            invited_at=membership.created_at,
        )
        for membership, org_name in rows
    ]


async def accept_invitation(
    session: AsyncSession, membership_id: uuid.UUID, actor: TokenPayload
) -> InvitationDecisionOut:
    user_id = uuid.UUID(actor.sub)
    await bind_session_context(session, user_id=user_id)
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.user_id == user_id,
            Membership.status == _PENDING,
        )
    )
    if membership is None:
        raise NotFoundError("Pending invitation not found")

    org_id, role = membership.org_id, membership.role
    result = await session.execute(
        update(Membership)
        .where(
            Membership.id == membership_id,
            Membership.user_id == user_id,
            Membership.status == _PENDING,
        )
        .values(status=_ACTIVE)
    )
    if result.rowcount != 1:
        raise ConflictError("Invitation is no longer pending")

    # Bind the invitation org only after the access change, so self-acceptance
    # cannot accidentally run under an owner/admin database context.
    await bind_session_context(
        session, org_id=org_id, user_id=user_id, role=role
    )
    await audit_service.record(
        session,
        action="staff.invitation_accepted",
        entity_type="membership",
        entity_id=membership_id,
        after_state={"role": role, "status": _ACTIVE},
    )
    await session.commit()
    return InvitationDecisionOut(accepted=True, org_id=org_id, role=Role(role))


async def decline_invitation(
    session: AsyncSession, membership_id: uuid.UUID, actor: TokenPayload
) -> None:
    user_id = uuid.UUID(actor.sub)
    await bind_session_context(session, user_id=user_id)
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.user_id == user_id,
            Membership.status == _PENDING,
        )
    )
    if membership is None:
        raise NotFoundError("Pending invitation not found")

    before = _snapshot(membership)
    await bind_session_context(
        session, org_id=membership.org_id, user_id=user_id, role=membership.role
    )
    await session.delete(membership)
    await session.flush()
    await audit_service.record(
        session,
        action="staff.invitation_declined",
        entity_type="membership",
        entity_id=membership_id,
        before_state=before,
    )
    await session.commit()


# ── Departments ────────────────────────────────────────────


def _department_out(department: Department) -> DepartmentOut:
    return DepartmentOut(
        id=department.id,
        name=department.name,
        description=department.description,
        created_at=department.created_at,
        updated_at=department.updated_at,
    )


async def list_departments(
    session: AsyncSession, actor: TokenPayload
) -> list[DepartmentOut]:
    rows = (
        await session.scalars(
            select(Department)
            .where(Department.org_id == actor.org_id)
            .order_by(Department.name)
        )
    ).all()
    return [_department_out(department) for department in rows]


async def create_department(
    session: AsyncSession, data: DepartmentCreate, actor: TokenPayload
) -> DepartmentOut:
    name = _require_name(data.name)
    duplicate = await session.scalar(
        select(Department.id).where(
            Department.org_id == actor.org_id,
            func.lower(Department.name) == name.lower(),
        )
    )
    if duplicate is not None:
        raise ConflictError("A department with that name already exists")
    department = Department(
        org_id=actor.org_id,
        name=name,
        description=data.description.strip() if data.description else None,
    )
    session.add(department)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A department with that name already exists") from exc
    await audit_service.record(
        session,
        action="department.created",
        entity_type="department",
        entity_id=department.id,
        after_state={"name": department.name},
    )
    return _department_out(department)


async def update_department(
    session: AsyncSession,
    department_id: uuid.UUID,
    data: DepartmentUpdate,
    actor: TokenPayload,
) -> DepartmentOut:
    department = await session.scalar(
        select(Department).where(
            Department.id == department_id,
            Department.org_id == actor.org_id,
        )
    )
    if department is None:
        raise NotFoundError("Department not found")
    before = {"name": department.name, "description": department.description}
    payload = data.model_dump(exclude_unset=True)

    if "name" in payload and payload["name"] is not None:
        name = _require_name(payload["name"])
        duplicate = await session.scalar(
            select(Department.id).where(
                Department.org_id == actor.org_id,
                func.lower(Department.name) == name.lower(),
                Department.id != department.id,
            )
        )
        if duplicate is not None:
            raise ConflictError("A department with that name already exists")
        department.name = name
    if "description" in payload:
        department.description = (
            payload["description"].strip() if payload["description"] else None
        )

    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A department with that name already exists") from exc
    await audit_service.record(
        session,
        action="department.updated",
        entity_type="department",
        entity_id=department.id,
        before_state=before,
        after_state={"name": department.name, "description": department.description},
    )
    return _department_out(department)


async def delete_department(
    session: AsyncSession, department_id: uuid.UUID, actor: TokenPayload
) -> None:
    department = await session.scalar(
        select(Department).where(
            Department.id == department_id,
            Department.org_id == actor.org_id,
        )
    )
    if department is None:
        raise NotFoundError("Department not found")
    assigned = await session.scalar(
        select(func.count())
        .select_from(Membership)
        .where(
            Membership.org_id == actor.org_id,
            Membership.department_id == department.id,
        )
    )
    if assigned:
        raise ConflictError(
            "Reassign staff members before deleting this department",
            details={"assigned_staff": int(assigned)},
        )
    await session.delete(department)
    await session.flush()
    await audit_service.record(
        session,
        action="department.deleted",
        entity_type="department",
        entity_id=department.id,
        before_state={"name": department.name},
    )


# ── Job-title catalogue ────────────────────────────────────


async def _assigned_counts(
    session: AsyncSession, actor: TokenPayload
) -> Counter:
    rows = (
        await session.execute(
            select(Membership.job_title, func.count())
            .where(
                Membership.org_id == actor.org_id,
                Membership.job_title.is_not(None),
            )
            .group_by(Membership.job_title)
        )
    ).all()
    return Counter({title.casefold(): int(count) for title, count in rows})


async def list_staff_roles(
    session: AsyncSession, actor: TokenPayload
) -> list[StaffRoleOut]:
    counts = await _assigned_counts(session, actor)
    custom = (
        await session.scalars(
            select(StaffRole)
            .where(StaffRole.org_id == actor.org_id)
            .order_by(StaffRole.name)
        )
    ).all()
    return [
        *[
            StaffRoleOut(
                id=role["id"],
                name=role["name"],
                description=role["description"],
                is_custom=False,
                assigned_count=counts.get(role["name"].casefold(), 0),
            )
            for role in BUILT_IN_STAFF_ROLES
        ],
        *[
            StaffRoleOut(
                id=str(role.id),
                name=role.name,
                description=role.description,
                is_custom=True,
                assigned_count=counts.get(role.name.casefold(), 0),
            )
            for role in custom
        ],
    ]


async def _assert_unique_role_name(
    session: AsyncSession,
    actor: TokenPayload,
    name: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    if any(
        role["name"].casefold() == name.casefold() for role in BUILT_IN_STAFF_ROLES
    ):
        raise ConflictError("That name is reserved for a built-in role")
    stmt = select(StaffRole.id).where(
        StaffRole.org_id == actor.org_id,
        func.lower(StaffRole.name) == name.lower(),
    )
    if exclude_id is not None:
        stmt = stmt.where(StaffRole.id != exclude_id)
    if await session.scalar(stmt) is not None:
        raise ConflictError("A staff role with that name already exists")


async def create_staff_role(
    session: AsyncSession, data: StaffRoleCreate, actor: TokenPayload
) -> StaffRoleOut:
    name = _require_name(data.name)
    await _assert_unique_role_name(session, actor, name)
    role = StaffRole(
        org_id=actor.org_id,
        name=name,
        description=data.description.strip() if data.description else None,
    )
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("A staff role with that name already exists") from exc
    await audit_service.record(
        session,
        action="staff_role.created",
        entity_type="staff_role",
        entity_id=role.id,
        after_state={"name": role.name},
    )
    return StaffRoleOut(
        id=str(role.id),
        name=role.name,
        description=role.description,
        is_custom=True,
    )


async def update_staff_role(
    session: AsyncSession,
    role_id: uuid.UUID,
    data: StaffRoleUpdate,
    actor: TokenPayload,
) -> StaffRoleOut:
    role = await session.scalar(
        select(StaffRole).where(
            StaffRole.id == role_id,
            StaffRole.org_id == actor.org_id,
        )
    )
    if role is None:
        raise NotFoundError("Staff role not found")
    before = {"name": role.name, "description": role.description}
    payload = data.model_dump(exclude_unset=True)

    if "name" in payload and payload["name"] is not None:
        name = _require_name(payload["name"])
        if name != role.name:
            await _assert_unique_role_name(
                session, actor, name, exclude_id=role.id
            )
            counts = await _assigned_counts(session, actor)
            if counts.get(role.name.casefold()):
                raise ConflictError(
                    "Reassign staff members before renaming this role"
                )
            role.name = name
    if "description" in payload:
        role.description = (
            payload["description"].strip() if payload["description"] else None
        )

    await session.flush()
    await audit_service.record(
        session,
        action="staff_role.updated",
        entity_type="staff_role",
        entity_id=role.id,
        before_state=before,
        after_state={"name": role.name, "description": role.description},
    )
    counts = await _assigned_counts(session, actor)
    return StaffRoleOut(
        id=str(role.id),
        name=role.name,
        description=role.description,
        is_custom=True,
        assigned_count=counts.get(role.name.casefold(), 0),
    )


async def delete_staff_role(
    session: AsyncSession, role_id: uuid.UUID, actor: TokenPayload
) -> None:
    role = await session.scalar(
        select(StaffRole).where(
            StaffRole.id == role_id,
            StaffRole.org_id == actor.org_id,
        )
    )
    if role is None:
        raise NotFoundError("Staff role not found")
    counts = await _assigned_counts(session, actor)
    if counts.get(role.name.casefold()):
        raise ConflictError(
            "Reassign staff members before deleting this role",
            details={"assigned_staff": counts[role.name.casefold()]},
        )
    await session.delete(role)
    await session.flush()
    await audit_service.record(
        session,
        action="staff_role.deleted",
        entity_type="staff_role",
        entity_id=role.id,
        before_state={"name": role.name},
    )
