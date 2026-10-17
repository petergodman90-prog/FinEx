"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata`` — required
by Alembic (env.py) and by the RLS tests that enumerate tenant tables.
"""

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.proposal import Proposal
from app.models.touchpoint import Touchpoint
from app.models.user import User

__all__ = [
    "AuditLog",
    "Customer",
    "Membership",
    "Organization",
    "Proposal",
    "Touchpoint",
    "User",
]
