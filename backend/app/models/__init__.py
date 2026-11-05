"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata`` — required
by Alembic (env.py) and by the RLS tests that enumerate tenant tables.
"""

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.department import Department
from app.models.membership import Membership, MembershipStatus
from app.models.organization import Organization
from app.models.proposal import Proposal
from app.models.staff_role import StaffRole
from app.models.touchpoint import Touchpoint
from app.models.user import User

__all__ = [
    "AuditLog",
    "Customer",
    "Department",
    "Membership",
    "MembershipStatus",
    "Organization",
    "Proposal",
    "StaffRole",
    "Touchpoint",
    "User",
]
