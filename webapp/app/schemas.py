"""Pydantic schemas for the structured (JSON-mode) agent outputs.

chapter_writer stays free-form prose (see prompts/chapter_writer.md) —
everything else here feeds directly into DB tables, so it's requested and
parsed as JSON rather than hand-parsed markdown.
"""

from pydantic import BaseModel


class CharacterOut(BaseModel):
    name: str
    aliases: list[str] = []
    tier: str
    profile_md: str


class CharacterDeveloperOutput(BaseModel):
    characters: list[CharacterOut]


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


class ChapterSummaryOutput(BaseModel):
    summary: str
    state_changes: list[StateChangeOut] = []
    world_state_rows: list[WorldStateRowOut] = []
    foreshadowing: list[ForeshadowingOut] = []


class ContinuityIssueOut(BaseModel):
    description: str
    suggestion: str


class ContinuityEditorOutput(BaseModel):
    critical_issues: list[ContinuityIssueOut] = []
    minor_issues: list[ContinuityIssueOut] = []
    batch_note: str


class SmartPlannerOutput(BaseModel):
    pacing_note: str
    characters_to_watch: list[str] = []
    threads_to_resolve: list[str] = []
    outline_adjustments: str = ""
