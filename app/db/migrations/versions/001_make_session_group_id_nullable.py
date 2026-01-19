"""Make session group_id nullable for private sessions

Revision ID: 001
Create Date: 2026-01-19

Private sessions can exist without a group (solo user, no partner yet).
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make group_id nullable in sessions table."""
    op.alter_column(
        'sessions',
        'group_id',
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=True
    )


def downgrade() -> None:
    """Revert group_id to NOT NULL (will fail if NULL values exist)."""
    op.alter_column(
        'sessions',
        'group_id',
        existing_type=sa.dialects.postgresql.UUID(),
        nullable=False
    )
