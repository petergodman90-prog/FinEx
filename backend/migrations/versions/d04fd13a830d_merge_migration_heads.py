"""merge migration heads

Revision ID: d04fd13a830d
Revises: 005_proposal_posting_state
Create Date: 2026-10-27 14:55:18.591492

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd04fd13a830d'
down_revision: Union[str, Sequence[str], None] = '005_proposal_posting_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
