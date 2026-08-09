"""add stories.stop_requested

Revision ID: 0002_add_stop_requested
Revises: 01d74b032ee3
Create Date: 2026-08-10 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_add_stop_requested"
down_revision: Union[str, None] = "01d74b032ee3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stories", sa.Column("stop_requested", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("stories", "stop_requested")
