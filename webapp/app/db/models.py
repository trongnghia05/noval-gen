from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Story(Base):
    __tablename__ = "stories"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    language = Column(String, nullable=False)
    input_type = Column(String, nullable=False)  # IDEA | PREMISE | REWRITE
    genre = Column(String)
    source_content = Column(Text)  # raw user input, kept for reference

    total_chapters = Column(Integer, nullable=False)
    target_words = Column(Integer, nullable=False)
    words_per_chapter = Column(Integer, nullable=False)
    current_words = Column(Integer, default=0)

    phase = Column(String, default="PLANNING")  # PLANNING | WRITING | COMPLETE
    last_checkpoint_chapter = Column(Integer, default=0)  # last chapter continuity_editor/smart_planner actually ran for
    is_running = Column(Boolean, default=False)  # True while a graph.run_story_to_completion() background run is active
    planning_verified = Column(Boolean, default=False)  # True once planning_verifier has gated the 4 planning artifacts before WRITING

    # Planning-phase outputs. Free-form markdown blobs — chapter_writer just
    # needs them as context, no per-field querying required, so a text
    # column is simpler than modeling their internal structure in tables.
    story_bible = Column(Text)
    plot_outline = Column(Text)
    world_bible = Column(Text)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    name = Column(String, nullable=False)
    aliases = Column(JSON, default=list)
    tier = Column(String)  # core | important | secondary
    profile_md = Column(Text)

    __table_args__ = (UniqueConstraint("story_id", "name", name="uq_character_name"),)


class WorldState(Base):
    """Live snapshot only — one row per (entity_type, entity_key, field).
    The unique constraint is what makes "overwrite, don't accumulate" a DB
    guarantee instead of a prompt-discipline convention.
    """

    __tablename__ = "world_state"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    entity_type = Column(String, nullable=False)  # character | relationship | plot_thread | object | timeline
    entity_key = Column(String, nullable=False)
    field = Column(String, nullable=False)
    value = Column(Text)
    updated_at_chapter = Column(Integer)

    __table_args__ = (
        UniqueConstraint("story_id", "entity_type", "entity_key", "field", name="uq_world_state_row"),
    )


class StateLog(Base):
    """Append-only structured audit trail — the exact source of truth
    continuity_editor cross-checks against instead of re-reading old chapters."""

    __tablename__ = "state_log"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    entity = Column(String, nullable=False)
    field = Column(String, nullable=False)
    old_value = Column(Text)
    new_value = Column(Text)
    reason = Column(Text)
    created_at = Column(DateTime, default=_utcnow)


class Foreshadowing(Base):
    __tablename__ = "foreshadowing"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    fid = Column(String, nullable=False)  # F1, F2, ...
    detail = Column(Text)
    planted_chapter = Column(Integer)
    status = Column(String)  # planted | advancing | resolved
    payoff_chapter = Column(Integer)

    __table_args__ = (UniqueConstraint("story_id", "fid", name="uq_foreshadow"),)


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    number = Column(Integer, nullable=False)
    title = Column(String)
    content = Column(Text)
    word_count = Column(Integer)
    status = Column(String, default="pending")  # pending | blueprinted | done
    blueprint = Column(Text)  # JSON — ChapterBlueprintOutput, set by chapter_blueprinter

    __table_args__ = (UniqueConstraint("story_id", "number", name="uq_chapter_number"),)


class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    summary_text = Column(Text)

    __table_args__ = (UniqueConstraint("story_id", "chapter_number", name="uq_chapter_summary"),)


class ContinuityLog(Base):
    """One row per story — living checkpoint state, overwritten in place."""

    __tablename__ = "continuity_log"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), unique=True, nullable=False)
    checkpoint_chapter = Column(Integer)
    critical_issues = Column(JSON, default=list)
    minor_issues = Column(JSON, default=list)
    batch_note = Column(Text)


class SmartPlannerState(Base):
    """One row per story — living checkpoint state, overwritten in place."""

    __tablename__ = "smart_planner_state"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), unique=True, nullable=False)
    checkpoint_chapter = Column(Integer)
    pacing_note = Column(Text)
    characters_to_watch = Column(JSON, default=list)
    threads_to_resolve = Column(JSON, default=list)
    outline_adjustments = Column(Text)


class ChapterVerifyLog(Base):
    """Append-only — one row per issue chapter_verifier finds, every chapter.
    Unlike ContinuityLog (one row/story, overwritten each 5-chapter batch),
    this keeps the full history since it runs far more often."""

    __tablename__ = "chapter_verify_log"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    severity = Column(String)  # critical | minor
    description = Column(Text)
    suggestion = Column(Text)
    action_taken = Column(String)  # rewritten | logged_only
    created_at = Column(DateTime, default=_utcnow)


class PlanningVerifyLog(Base):
    """Append-only history of what the planning gate caught, one row per issue.
    Planning verification runs once per story (not per chapter), but issues are
    kept rather than overwritten so a later audit can see what was flagged and
    which artifact got regenerated in response."""

    __tablename__ = "planning_verify_log"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    artifact = Column(String)  # story_bible | plot_outline | characters | world
    severity = Column(String)  # critical | minor
    description = Column(Text)
    suggestion = Column(Text)
    action_taken = Column(String)  # rewrite_1|2|3 | regenerated_fresh | accepted | logged_only
    created_at = Column(DateTime, default=_utcnow)
