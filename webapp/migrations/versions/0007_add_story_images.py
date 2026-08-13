"""add story_images + poster job state on stories

Poster art moves from local files to object storage. The filesystem was doing four
jobs at once — holding bytes, marking which set is live (the `.preview/` directory),
telling the UI which file was just generated (mtime), and keying the zip cache
(mtime again). Storage replaces only the first, so the rest move into the database:
`story_images.state` and `story_images.updated_at`, plus two columns on `stories`
for the regeneration job, which used to be published as `.running` / `.error` marker
files that the playground could only see because it had the output dir mounted.

Revision ID: 0007_add_story_images
Revises: 0006_add_story_front_matter
Create Date: 2026-08-13 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_add_story_images"
down_revision: Union[str, None] = "0006_add_story_front_matter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "story_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("story_id", sa.Integer(), sa.ForeignKey("stories.id"), nullable=False),
        sa.Column("stem", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="live"),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("content_type", sa.String()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        # One live and at most one preview per stem, so accepting a preview is a
        # single UPDATE rather than a loop of moves that can half-fail.
        sa.UniqueConstraint("story_id", "stem", "state", name="uq_story_image"),
    )
    op.create_index("ix_story_images_story", "story_images", ["story_id"])

    op.add_column("stories", sa.Column("image_job_running", sa.Boolean(), server_default=sa.false()))
    op.add_column("stories", sa.Column("image_job_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("stories", "image_job_error")
    op.drop_column("stories", "image_job_running")
    op.drop_index("ix_story_images_story", table_name="story_images")
    op.drop_table("story_images")
