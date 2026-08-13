"""move the live story state off CSV files and into the database

The state a chapter is written against — where everyone is, how each pair stands,
which threads are open, what happened when — lived in five CSV files under
/data/graphs/<story_id>/. That put half the story's memory outside the database:
deleting a story meant remembering to delete a directory too, nothing was
queryable, and the two halves could drift apart with nothing to notice.

`relationship_history.csv` is NOT recreated as a table. It was written every
chapter and read by nobody — no reader anywhere in the codebase, not in the export
zip, not in the reproducibility trace. Its content (a pair's numeric strength
moving from one value to the next) is preserved into `state_log`, which is the
append-only trail continuity_editor already reads.

`character_voices.md` becomes `stories.character_voices`: it is one document about
the whole ensemble, not a field of one character. Moving it also defuses a latent
bug — re-seeding the graph rewrote that file and truncated every other one with
it, so any future call after writing had begun would have wiped the live state.

Revision ID: 0008_story_state_tables
Revises: 0007_add_story_images
Create Date: 2026-08-13 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_story_state_tables"
down_revision: Union[str, None] = "0007_add_story_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "character_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("char_id", sa.String(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("gender", sa.String()),
        sa.Column("aliases", sa.Text()),
        sa.Column("role", sa.String()),
        sa.Column("arc_status", sa.String()),
        sa.Column("location", sa.Text()),
        sa.Column("emotional_state", sa.Text()),
        sa.Column("goals", sa.Text()),
        sa.Column("secrets", sa.Text()),
        sa.Column("speech_pattern", sa.Text()),
        sa.Column("last_seen_chapter", sa.Integer()),
        sa.UniqueConstraint("story_id", "char_id", name="uq_character_state"),
    )
    op.create_index("ix_character_state_story", "character_state", ["story_id"])

    op.create_table(
        "relationship",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("char_a", sa.String(), nullable=False),
        sa.Column("char_b", sa.String(), nullable=False),
        sa.Column("type", sa.String()),
        # String, not Float: the formatter renders this verbatim and has always
        # carried a fallback for a non-numeric value.
        sa.Column("strength", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("last_event", sa.Text()),
        sa.Column("last_updated_chapter", sa.Integer()),
        sa.UniqueConstraint("story_id", "char_a", "char_b", name="uq_relationship"),
    )
    op.create_index("ix_relationship_story", "relationship", ["story_id"])

    op.create_table(
        "plot_thread",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("type", sa.String()),
        sa.Column("status", sa.String()),
        sa.Column("introduced_chapter", sa.Integer()),
        sa.Column("resolved_chapter", sa.Integer()),
        sa.Column("involved_chars", sa.Text()),
        sa.Column("hint", sa.Text()),
        sa.Column("resolution_note", sa.Text()),
        sa.UniqueConstraint("story_id", "thread_id", name="uq_plot_thread"),
    )
    op.create_index("ix_plot_thread_story", "plot_thread", ["story_id"])

    op.create_table(
        "timeline_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("chapter", sa.Integer(), nullable=False),
        sa.Column("story_time", sa.String()),
        sa.Column("location", sa.Text()),
        sa.Column("characters", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_timeline_event_story", "timeline_event", ["story_id"])

    op.add_column("stories", sa.Column("character_voices", sa.Text()))


def downgrade() -> None:
    op.drop_column("stories", "character_voices")
    for t in ("timeline_event", "plot_thread", "relationship", "character_state"):
        op.drop_index(f"ix_{t}_story", table_name=t)
        op.drop_table(t)
