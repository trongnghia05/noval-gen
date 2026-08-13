"""Schemas for writing one chapter and for the memory and checks around it: blueprint, prose, summary, continuity, quality."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ── chapter_writer ─────────────────────────────────────────────────────────────

class ChapterWriterOutput(BaseModel):
    title: str          # chapter title only — no "Chapter X:" prefix
    content: str        # full prose, starting AFTER the heading line
    short_summary: str  # 1-2 sentences: key event + emotional shift
    hook: str           # exact last sentence / cliffhanger


# ── chapter_summarizer ─────────────────────────────────────────────────────────

class StateChangeOut(BaseModel):
    entity: str = Field(description="the entity that changed (character/object/relationship)")
    field: str = Field(description="the field that changed")
    old_value: str | None = Field(None, description="the old value (null if new)")
    new_value: str = Field(description="the new value")
    reason: str = Field(description="why it changed")


class WorldStateRowOut(BaseModel):
    entity_type: str = Field(description="character | relationship | plot_thread | object | timeline")
    entity_key: str = Field(description="the entity's identifying key")
    field: str = Field(description="the field name")
    value: str = Field(description="the current value (a snapshot; it overwrites)")

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value_to_str(cls, v):
        return str(v) if not isinstance(v, str) else v


class ForeshadowingOut(BaseModel):
    fid: str = Field(description="the foreshadowing id: F1, F2, …")
    detail: str = Field(description="the detail planted")
    planted_chapter: int = Field(description="the chapter it was planted in")
    status: str = Field(description="planted | advancing | resolved")
    payoff_chapter: int | None = Field(None, description="the payoff chapter (null if not yet)")


class CharacterStateUpdateOut(BaseModel):
    id: str = Field(description="character CSV id (C001, …)")
    field: str = Field(description="location | emotional_state | goals | secrets | arc_status")
    value: str = Field(description="the new value")


class RelationshipChangeOut(BaseModel):
    char_a: str = Field(description="character CSV id")
    char_b: str = Field(description="character CSV id")
    type: str = Field(description="romantic | rivalry | friendship | family | mentor | professional")
    strength: float = Field(description="-1.0 → 1.0, the new absolute value")
    status: str = Field(description="active | broken | evolving | secret")
    event: str = Field(description="one sentence on what changed")


class PlotThreadOut(BaseModel):
    id: str = Field(description="PT001, PT002, …")
    title: str = Field(description="the plot thread's name")
    type: str = Field(description="main | subplot | foreshadowing | mystery")
    status: str = Field(description="open | resolved | abandoned")
    introduced_chapter: int | None = Field(None, description="the chapter it was introduced in")
    involved_chars: str = Field(description='character ids separated by "|": "C001|C002"')
    hint: str | None = Field(None, description="the hint (if it is foreshadowing/mystery)")
    resolution_note: str | None = Field(None, description="a note on how it resolved")


class TimelineEventOut(BaseModel):
    story_time: str = Field(description='the moment in the story, e.g. "Day 3, late evening"')
    location: str = Field(description="the location")
    characters: str = Field(description='character ids separated by "|"')
    summary: str = Field(description="a one-sentence summary")


class ChapterSummaryOutput(BaseModel):
    short_summary: str = Field(description="1-2 sentences on the main event, used by chapter_writer")
    summary: str = Field(description="the full 200-300 words, used by continuity/verifier")
    state_changes: list[StateChangeOut] = Field(default=[], description="the state changes to record in the state-log")
    world_state_rows: list[WorldStateRowOut] = Field(default=[], description="the world-state snapshot rows (these overwrite)")
    foreshadowing: list[ForeshadowingOut] = Field(default=[], description="new or updated foreshadowing")
    character_updates: list[CharacterStateUpdateOut] = Field(default=[], description="character CSV updates")
    relationship_changes: list[RelationshipChangeOut] = Field(default=[], description="relationship CSV changes")
    plot_thread_updates: list[PlotThreadOut] = Field(default=[], description="new plot threads, or threads whose status changed")
    timeline_event: TimelineEventOut | None = Field(None, description="this chapter's timeline event")


# ── chapter_blueprinter ────────────────────────────────────────────────────────

class SceneOut(BaseModel):
    goal: str = Field(description="what the POV character wants in this scene")
    conflict: str = Field(description="what stands in their way")
    outcome: str = Field(description="do they get it? (success | failure | partial)")
    disaster: str = Field(description="the new problem this scene creates")
    characters: list[str] = Field(default=[], description="node keys (C001…) or names — who appears in the scene")
    location: str = Field("", description="the location label or node key from LOCATED_AT")
    speaking_characters: list[str] = Field(default=[], description="the new-world names of those who actually SPEAK in the scene")
    dialogue_nuance: str = Field("", description='nuance: the tone/mood of the exchange, e.g. "cold, clipped confrontation"')
    dialogue_intent: str = Field("", description="intent: what the dialogue must achieve in this scene")


class ChapterBlueprintOutput(BaseModel):
    purpose: str = Field(description="one sentence: why does this chapter exist?")
    act_position: str = Field(description="Act 1 | Act 2a | Act 2b | Act 3")
    beat_type: str = Field("", description="the structural function: setup | escalation | revelation | setback | turning_point | confrontation | aftermath | resolution — avoid several chapters of the same kind")
    state_delta: str = Field("", description="the concrete STATE CHANGE this chapter must produce: what is materially different at the last line versus the first (a relationship shifts, a secret surfaces, a plan advances, someone decides or acts). Re-stirring an old emotion with no new delta means the chapter is 'empty'")
    emotional_arc_start: str = Field(description="what the reader feels as the chapter opens")
    emotional_arc_end: str = Field(description="what the reader feels as the chapter closes")
    pov_character: str = Field("", description="alternating multi-POV: the character who 'holds' this chapter's POV (write the whole chapter inside their head, in the source's person). Empty = single-POV / follow source_spirit")
    pov_characters: list[str] = Field(default=[], description="fill this ONLY when the source chapter is told from more than one POV (switching mid-chapter): the set of POV-holders' names. Empty for an ordinary single-POV chapter")
    scenes: list[SceneOut] = Field(description="the chapter's scenes")
    hook: str = Field(description="exactly what the closing hook/cliffhanger is")
    foreshadowing_to_plant: str | None = Field(None, description="the seed to plant (to be paid off later)")
    characters_featured: list[str] = Field(description="the character CSV ids appearing in this chapter")
    dialogue_intensity: str = Field("balanced", description="heavy | balanced | sparse — how dialogue-forward the chapter is. A solitary interior chapter is legitimately 'sparse'")
    motifs_used: list[str] = Field(default=[], description="short canonical tags (≤5 words) for repeatable motifs/beats, e.g. 'possessive-claim'. You MUST reuse an existing tag verbatim when the motif recurs; coin a new tag only for a genuinely new motif")


# ── continuity_editor ─────────────────────────────────────────────────────────

class ContinuityIssueOut(BaseModel):
    description: str = Field(description="a description of the continuity problem")
    suggestion: str = Field(description="the suggested fix")


class ContinuityEditorOutput(BaseModel):
    critical_issues: list[ContinuityIssueOut] = Field(default=[], description="critical issues (real contradictions)")
    minor_issues: list[ContinuityIssueOut] = Field(default=[], description="minor issues")
    batch_note: str = Field(description="an overall note on the 5-chapter batch just reviewed")


# ── smart_planner ──────────────────────────────────────────────────────────────

class SmartPlannerOutput(BaseModel):
    pacing_note: str = Field(description="Average words/chapter: X | Projected total: Y / target W | Action: expand/hold/tighten")
    characters_to_watch: list[str] = Field(default=[], description='each entry "Name: what must happen with them in the coming chapters"')
    threads_to_resolve: list[str] = Field(default=[], description='each entry "Thread: must pay off before Ch.X"')
    outline_adjustments: str = Field("", description="detailed adjustments to the outline for the coming chapters (this replaces the previous content, it does not accumulate) — leave empty if nothing needs changing")


# ── chapter_verifier ───────────────────────────────────────────────────────────

class ChapterVerifyIssueOut(BaseModel):
    description: str = Field(description="a description of the problem")
    suggestion: str = Field(description="the suggested fix")
    severity: Literal["critical", "minor"] = Field(description="critical = a real contradiction; minor = a small fault")


class ChapterVerifierOutput(BaseModel):
    issues: list[ChapterVerifyIssueOut] = Field(default=[], description="every problem found in the chapter just written")
    verdict_note: str = Field(description="a short verdict")


# ── quality_reviewer ───────────────────────────────────────────────────────────

class QualityReviewIssueOut(BaseModel):
    dimension: Literal["quality", "world_consistency", "graph_consistency", "dialogue", "pov"] = Field(
        description="the dimension that was violated")
    description: str = Field(description="a description of the problem")
    suggestion: str = Field(description="the suggested fix")
    severity: Literal["critical", "minor"] = Field(description="critical = must be fixed; minor = recorded only")


class QualityReviewerOutput(BaseModel):
    issues: list[QualityReviewIssueOut] = Field(default=[], description="every quality problem in the chapter")
    verdict_note: str = Field(description="a short verdict")


