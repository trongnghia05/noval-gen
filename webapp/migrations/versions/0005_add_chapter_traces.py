"""add chapter_traces

Revision ID: 0005_add_chapter_traces
Revises: 0004_add_app_settings
Create Date: 2026-08-11 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_add_chapter_traces"
down_revision: Union[str, None] = "0004_add_app_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chapter_traces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("story_id", sa.Integer(), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["story_id"], ["stories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("story_id", "chapter_number", name="uq_chapter_trace"),
    )


def downgrade() -> None:
    op.drop_table("chapter_traces")
