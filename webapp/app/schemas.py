"""Pydantic schemas for the structured (JSON-mode) agent outputs.

chapter_writer stays free-form prose — everything else feeds directly into DB
tables or CSV files, so it's requested and parsed as JSON.
"""
from pydantic import BaseModel


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
