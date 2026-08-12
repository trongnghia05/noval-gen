"""add stories.source_title

Revision ID: 0003_add_source_title
Revises: 0002_add_stop_requested
Create Date: 2026-08-10 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_source_title"
down_revision: Union[str, None] = "0002_add_stop_requested"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stories", sa.Column("source_title", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("stories", "source_title")
