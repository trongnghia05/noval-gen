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
    source_title = Column(String)  # REWRITE only: title of the ORIGINAL story (story.title is the new, reskinned one)
    source_content = Column(Text)  # raw user input, kept for reference

    total_chapters = Column(Integer, nullable=False)
    target_words = Column(Integer, nullable=False)
    words_per_chapter = Column(Integer, nullable=False)
    current_words = Column(Integer, default=0)

    phase = Column(String, default="PLANNING")  # PLANNING | WRITING | COMPLETE
    last_checkpoint_chapter = Column(Integer, default=0)  # last chapter continuity_editor/smart_planner actually ran for
    is_running = Column(Boolean, default=False)  # True while a graph.run_story_to_completion() background run is active
    stop_requested = Column(Boolean, default=False)  # set by POST /stop; the graph loop checks it between steps and halts cleanly
    planning_verified = Column(Boolean, default=False)  # True once planning_verifier has gated the 4 planning artifacts before WRITING
    new_graph_built = Column(Boolean, default=False)    # True once new_graph_builder has built graph_type="new" nodes/edges
    new_graph_verified = Column(Boolean, default=False)  # True once graph_verifier has checked the new graph
    source_graph_verified = Column(Boolean, default=False)  # True once source_graph_verifier has checked the source graph

    # Planning-phase outputs. Free-form markdown blobs — chapter_writer just
    # needs them as context, no per-field querying required, so a text
    # column is simpler than modeling their internal structure in tables.
    story_bible = Column(Text)
    plot_outline = Column(Text)
    world_bible = Column(Text)
    source_chapter_count = Column(Integer)   # REWRITE only: number of source chapters to graph-extract
    source_spirit = Column(Text)             # REWRITE only: overall tone + excerpts, passed to chapter_writer

    # Front matter, written once when the book completes. It used to be generated on
    # every export and thrown away with the file, so the same book could get a
    # different logline each time, the API could not serve either without reading a
    # file, and image regeneration — which has no export step — lost them entirely.
    # `summary` is what tells the poster designer what the plot actually contains.
    author = Column(String)
    tags = Column(JSON, default=list)     # 3-6 genre/theme tags
    logline = Column(Text)                # 1-2 sentence premise
    summary = Column(Text)                # back-cover blurb, 120-180 words
    cast_blurbs = Column(JSON, default=list)  # [{name, role, blurb}] for the export

    # The whole cast's voice guide, as one document — that is the shape it is written
    # in and the shape the writer is handed. Minted once during planning and not
    # touched again; it lives here rather than on Character because it is a single
    # blob about the ensemble, not a field of one person.
    character_voices = Column(Text)

    # Poster-regeneration job state. It used to be published as marker files
    # (.running / .error) inside the output dir, which worked only because the
    # playground had that dir mounted and could watch it. Object storage cannot be
    # mounted, so the state moves here and the UI asks the API for it.
    image_job_running = Column(Boolean, default=False)
    image_job_error = Column(Text)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(Text)
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
    short_summary = Column(Text)  # 1-2 câu, dùng cho chapter_writer
    hook = Column(Text)           # exact last sentence / cliffhanger, dùng cho chapter_list

    __table_args__ = (UniqueConstraint("story_id", "chapter_number", name="uq_chapter_summary"),)


class ChapterTrace(Base):
    """Full reproducibility record for one written chapter: every input the writer
    saw, SNAPSHOTTED at write time (world-state / continuity / smart-planner / CSV
    graph are live and overwritten each chapter, so they can only be captured then),
    plus the produced output. One row per chapter."""

    __tablename__ = "chapter_traces"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    chapter_number = Column(Integer, nullable=False)
    trace = Column(JSON)  # {meta, inputs, output} — see orchestrator.build_chapter_trace
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (UniqueConstraint("story_id", "chapter_number", name="uq_chapter_trace"),)


class CharacterState(Base):
    """Where a character stands RIGHT NOW — overwritten every chapter.

    Distinct from `Character`, which is the stable dossier set once during planning,
    and from the story graph, which is the PLAN. This is the running position the
    chapter writer is handed as "live states — use as ground truth": don't put
    someone in a room they left, don't have them ignorant of what they just learned.
    """

    __tablename__ = "character_state"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    char_id = Column(String, nullable=False)          # C001, C002 …
    name = Column(String)
    gender = Column(String)
    aliases = Column(Text)
    role = Column(String)
    arc_status = Column(String)
    location = Column(Text)
    emotional_state = Column(Text)
    goals = Column(Text)
    secrets = Column(Text)
    speech_pattern = Column(Text)
    last_seen_chapter = Column(Integer)

    __table_args__ = (UniqueConstraint("story_id", "char_id", name="uq_character_state"),)


class Relationship(Base):
    """Current state of one pair. Overwritten; the history of how it got here is
    `state_log`."""

    __tablename__ = "relationship"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    char_a = Column(String, nullable=False)
    char_b = Column(String, nullable=False)
    type = Column(String)
    # Text, not Float: the writer renders this verbatim, and the formatter has always
    # carried a fallback for a non-numeric value, so the model evidently writes one
    # sometimes. Postgres can still cast it for a query.
    strength = Column(String)
    status = Column(String)
    last_event = Column(Text)
    last_updated_chapter = Column(Integer)

    __table_args__ = (UniqueConstraint("story_id", "char_a", "char_b", name="uq_relationship"),)


class PlotThread(Base):
    """An open or resolved storyline. The writer is told to advance or acknowledge at
    least one open thread per chapter, which is what this table is for."""

    __tablename__ = "plot_thread"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    thread_id = Column(String, nullable=False)        # PT001, PT002 …
    title = Column(Text)
    type = Column(String)
    status = Column(String)
    introduced_chapter = Column(Integer)
    resolved_chapter = Column(Integer)
    involved_chars = Column(Text)                     # pipe-separated character ids
    hint = Column(Text)
    resolution_note = Column(Text)

    __table_args__ = (UniqueConstraint("story_id", "thread_id", name="uq_plot_thread"),)


class TimelineEvent(Base):
    """One row per chapter — append-only. Only the blueprinter reads it, and only the
    most recent handful, to keep consecutive chapters from repeating a beat."""

    __tablename__ = "timeline_event"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    chapter = Column(Integer, nullable=False)
    story_time = Column(String)
    location = Column(Text)
    characters = Column(Text)
    summary = Column(Text)
    created_at = Column(DateTime, default=_utcnow)


class StoryImage(Base):
    """One poster image in object storage.

    The art used to live only as files under output/<slug>/image/, where the
    filesystem was doing four separate jobs: holding the bytes, saying which set is
    live (the `.preview/` directory), telling the UI which file was newly generated
    (mtime), and keying the zip cache (also mtime). Object storage replaces only the
    first of those, so the other three move onto this row: `state`, and `updated_at`.

    `object_key` carries a per-version random suffix, so a regenerated image lands on
    a NEW key. A fixed key would keep the signed URL byte-identical and the browser
    would keep serving the old picture from cache after new art was approved.
    """

    __tablename__ = "story_images"

    id = Column(Integer, primary_key=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    stem = Column(String, nullable=False)   # cover | thumbnail1 | thumbnail2
    state = Column(String, nullable=False, default="live")  # live | preview
    object_key = Column(String, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    content_type = Column(String)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # One live and at most one preview per stem. Accepting a preview is then an
    # UPDATE of `state` inside one transaction, instead of a loop of file moves that
    # could half-fail and leave a mixed set of old and new art.
    __table_args__ = (
        UniqueConstraint("story_id", "stem", "state", name="uq_story_image"),
    )


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


# ── Story Knowledge Graph ──────────────────────────────────────────────────────
#
# story_bible used to be a free-form markdown blob. The graph replaces the
# structured parts (chapter event map, character arcs, causal chains) with
# queryable rows. story.story_bible is kept as a short narrative summary for
# agents that need prose context; everything else lives here.
#
# Node types : character | location | event | object | theme | faction
# Edge types : RELATION | PARTICIPATES | CAUSES | FORESHADOWS |
#              LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES | ARC_CHANGE
#
# Edges carry chapter_from / chapter_to so two nodes can have multiple edges
# (e.g. A and B are friends in Ch.1-19, then rivals from Ch.20 onward).

class StoryGraphNode(Base):
    __tablename__ = "story_graph_nodes"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    story_id           = Column(Integer, ForeignKey("stories.id"), nullable=False)
    graph_type         = Column(String, nullable=False, default="source")  # "source" | "new"
    node_key           = Column(String, nullable=False)   # "C001" … "F099" — story-scoped
    node_type          = Column(String, nullable=False)   # character|location|event|object|theme|faction
    label              = Column(String, nullable=False)
    properties         = Column(JSON, default=dict)
    # CHARACTER : { role, status, wants, fears, arc_stage, aliases[] }
    # EVENT     : { summary, event_type, emotional_weight }
    #               event_type: revelation|conflict|turning_point|consequence|decision
    # LOCATION  : { description, significance }
    # OBJECT    : { description, symbolic_meaning }
    # THEME     : { description, central_question }
    # FACTION   : { goal, opposing_faction }
    chapter_introduced = Column(Integer)   # first chapter this node appears (NULL = pre-story)

    __table_args__ = (UniqueConstraint("story_id", "graph_type", "node_key", name="uq_graph_node"),)


class StoryGraphEdge(Base):
    __tablename__ = "story_graph_edges"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    story_id           = Column(Integer, ForeignKey("stories.id"), nullable=False)
    graph_type         = Column(String, nullable=False, default="source")  # "source" | "new"
    source_key         = Column(String, nullable=False)   # node_key of source node
    target_key         = Column(String, nullable=False)   # node_key of target node
    edge_type          = Column(String, nullable=False)
    label              = Column(String)                   # short human-readable description
    # ── Temporal context ──────────────────────────────────────────────────────
    chapter_from       = Column(Integer)   # chapter where this edge state begins
    chapter_to         = Column(Integer)   # chapter where it ends (NULL = still active)
    trigger_event_key  = Column(String)    # node_key of the event that caused this edge
    condition          = Column(Text)      # circumstances: "after B denounced A publicly"
    # ── Payload ───────────────────────────────────────────────────────────────
    properties         = Column(JSON, default=dict)
    # RELATION     : { rel_type, strength }   rel_type: friendship|rivalry|love|family|mentor|debt
    #                                          strength: -1.0 (hostile) → 1.0 (devoted)
    # PARTICIPATES : { role }                  cause|victim|witness|ally|bystander
    # CAUSES       : { mechanism }             why A leads to B
    # FORESHADOWS  : { hint }                  what it foreshadows
    # ARC_CHANGE   : { field, old_val, new_val }
    # OWNS         : { how_acquired }
    # MEMBER_OF    : { role, chapter_left }
