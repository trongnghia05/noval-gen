"""Pydantic schemas for the structured (JSON-mode) agent outputs.

chapter_writer stays free-form prose — everything else feeds directly into DB
tables or CSV files, so it's requested and parsed as JSON.
"""
from typing import Any, Literal

from pydantic import BaseModel, field_validator


# ── story_analyzer ─────────────────────────────────────────────────────────────

class GraphNodeOut(BaseModel):
    id: str                            # "C001" … "F099" — story-scoped key
    node_type: str                     # character|location|event|object|theme|faction
    label: str
    properties: dict[str, Any] = {}
    chapter_introduced: int | None = None


class GraphEdgeOut(BaseModel):
    source_id: str                     # node key of source
    target_id: str                     # node key of target
    edge_type: str                     # RELATION|PARTICIPATES|CAUSES|FORESHADOWS|
                                       # LOCATED_AT|INVOLVES|OWNS|MEMBER_OF|EMBODIES|ARC_CHANGE
    label: str
    chapter_from: int | None = None
    chapter_to: int | None = None      # None = still active
    trigger_event_id: str | None = None
    condition: str | None = None
    properties: dict[str, Any] = {}


class StoryAnalyzerOutput(BaseModel):
    narrative_summary: str             # short prose summary kept in story.story_bible
    nodes: list[GraphNodeOut]          # CHARACTER, LOCATION, FACTION, THEME, OBJECT
                                       # + key arc EVENT nodes for IDEA/PREMISE
                                       # NO event nodes for REWRITE (those come from chapter_graph_extractor)
    edges: list[GraphEdgeOut]          # initial relations, member_of, embodies
    source_chapter_count: int | None = None  # REWRITE only — how many source chapters exist


class ChapterGraphOutput(BaseModel):
    """Per-source-chapter extraction for REWRITE. One call per chapter, bounded output."""
    event: GraphNodeOut                # the single EVENT node for this source chapter
    new_nodes: list[GraphNodeOut] = [] # new entities discovered in this chapter not yet in DB
    edges: list[GraphEdgeOut] = []     # PARTICIPATES, LOCATED_AT, CAUSES, RELATION change, ARC_CHANGE


# ── character_developer ────────────────────────────────────────────────────────

class CharacterOut(BaseModel):
    name: str
    aliases: list[str] = []
    tier: str
    profile_md: str


class CharacterGraphInitOut(BaseModel):
    """CSV-ready initial state for one character."""
    id: str                  # C001, C002, ... assigned by the model
    name: str                # must match CharacterOut.name exactly
    role: str                # protagonist | antagonist | supporting | minor
    initial_location: str
    initial_emotional_state: str
    initial_goals: str       # comma-separated
    initial_secrets: str     # comma-separated
    speech_pattern: str      # 1-3 sentences describing how they speak


class CharacterDeveloperOutput(BaseModel):
    characters: list[CharacterOut]
    character_graph: list[CharacterGraphInitOut]
    character_voices_md: str  # full markdown voice guide, one section per character


# ── chapter_summarizer ─────────────────────────────────────────────────────────

class StateChangeOut(BaseModel):
    entity: str
    field: str
    old_value: str | None = None
    new_value: str
    reason: str


class WorldStateRowOut(BaseModel):
    entity_type: str
    entity_key: str
    field: str
    value: str

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value_to_str(cls, v):
        return str(v) if not isinstance(v, str) else v


class ForeshadowingOut(BaseModel):
    fid: str
    detail: str
    planted_chapter: int
    status: str
    payoff_chapter: int | None = None


class CharacterStateUpdateOut(BaseModel):
    id: str    # character CSV id (C001, ...)
    field: str  # location | emotional_state | goals | secrets | arc_status
    value: str


class RelationshipChangeOut(BaseModel):
    char_a: str     # character CSV id
    char_b: str     # character CSV id
    type: str       # romantic | rivalry | friendship | family | mentor | professional
    strength: float  # -1.0 to 1.0 absolute new value
    status: str     # active | broken | evolving | secret
    event: str      # one-sentence description of what changed


class PlotThreadOut(BaseModel):
    id: str         # PT001, PT002, ...
    title: str
    type: str       # main | subplot | foreshadowing | mystery
    status: str     # open | resolved | abandoned
    introduced_chapter: int | None = None
    involved_chars: str  # pipe-separated character ids: "C001|C002"
    hint: str | None = None
    resolution_note: str | None = None


class TimelineEventOut(BaseModel):
    story_time: str   # in-story date/time e.g. "Day 3, late evening"
    location: str
    characters: str   # pipe-separated character ids
    summary: str      # one sentence


class ChapterSummaryOutput(BaseModel):
    summary: str
    state_changes: list[StateChangeOut] = []
    world_state_rows: list[WorldStateRowOut] = []
    foreshadowing: list[ForeshadowingOut] = []
    # CSV graph updates
    character_updates: list[CharacterStateUpdateOut] = []
    relationship_changes: list[RelationshipChangeOut] = []
    plot_thread_updates: list[PlotThreadOut] = []  # new threads or status changes
    timeline_event: TimelineEventOut | None = None


# ── chapter_blueprinter ────────────────────────────────────────────────────────

class SceneOut(BaseModel):
    goal: str      # what the POV character wants in this scene
    conflict: str  # what blocks them
    outcome: str   # do they get it? (success | failure | partial)
    disaster: str  # new problem that emerges from this scene


class ChapterBlueprintOutput(BaseModel):
    purpose: str               # one sentence: why does this chapter exist?
    act_position: str          # Act 1 | Act 2a | Act 2b | Act 3
    emotional_arc_start: str   # reader's emotion at chapter open
    emotional_arc_end: str     # reader's emotion at chapter close
    scenes: list[SceneOut]
    hook: str                  # exact nature of the final hook/cliffhanger
    foreshadowing_to_plant: str | None = None  # seed to drop (for future payoff)
    characters_featured: list[str]  # character CSV ids who appear in this chapter


# ── continuity_editor ─────────────────────────────────────────────────────────

class ContinuityIssueOut(BaseModel):
    description: str
    suggestion: str


class ContinuityEditorOutput(BaseModel):
    critical_issues: list[ContinuityIssueOut] = []
    minor_issues: list[ContinuityIssueOut] = []
    batch_note: str


# ── smart_planner ──────────────────────────────────────────────────────────────

class SmartPlannerOutput(BaseModel):
    pacing_note: str
    characters_to_watch: list[str] = []
    threads_to_resolve: list[str] = []
    outline_adjustments: str = ""


# ── chapter_verifier ───────────────────────────────────────────────────────────

class ChapterVerifyIssueOut(BaseModel):
    description: str
    suggestion: str
    severity: Literal["critical", "minor"]


class ChapterVerifierOutput(BaseModel):
    issues: list[ChapterVerifyIssueOut] = []
    verdict_note: str


# ── planning_verifier ──────────────────────────────────────────────────────────

class PlanningVerifyIssueOut(BaseModel):
    artifact: Literal["story_bible", "plot_outline", "characters", "world"]
    description: str
    suggestion: str
    severity: Literal["critical", "minor"]


class PlanningVerifierOutput(BaseModel):
    issues: list[PlanningVerifyIssueOut] = []
    verdict_note: str


# ── quality_reviewer ───────────────────────────────────────────────────────────

class QualityReviewIssueOut(BaseModel):
    dimension: Literal["quality", "originality"]
    description: str
    suggestion: str
    severity: Literal["critical", "minor"]


class QualityReviewerOutput(BaseModel):
    issues: list[QualityReviewIssueOut] = []
    verdict_note: str


# ── graph_verifier ─────────────────────────────────────────────────────────────

class GraphVerifyIssueOut(BaseModel):
    node_key: str | None = None       # which node has the issue (None if general)
    edge_desc: str | None = None      # describe the edge (e.g. "C001→C002 RELATION Ch.1")
    description: str
    suggestion: str
    severity: Literal["critical", "minor"]


class GraphVerifierOutput(BaseModel):
    issues: list[GraphVerifyIssueOut] = []
    verdict_note: str
