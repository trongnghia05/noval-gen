"""Schemas for the four planning artifacts — characters, plot outline, world bible — and the gate that checks them before any chapter is written."""
from typing import Literal

from pydantic import BaseModel, Field

# ── character_developer ────────────────────────────────────────────────────────

class CharacterOut(BaseModel):
    name: str = Field(description="the character's name")
    aliases: list[str] = Field(default=[], description="other forms of address / nicknames")
    tier: str = Field(description="core | important | secondary")
    profile_md: str = Field(description="the full markdown dossier: Basics (age, appearance, job/role); Psychology & Personality (strengths, flaw/wound, deepest fear, deepest want, false belief); Backstory (2-3 paragraphs for core/important, 1 for secondary); Arc (start/midpoint/end/lesson — omit for secondary); Relationships with other characters")


class CharacterGraphInitOut(BaseModel):
    """CSV-ready initial state for one character."""
    id: str = Field(description="C001, C002, … assigned by you")
    name: str = Field(description="must match CharacterOut.name exactly")
    role: str = Field(description="protagonist | antagonist | supporting | minor")
    initial_location: str = Field(description="starting location")
    initial_emotional_state: str = Field(description="starting emotional state")
    initial_goals: str = Field(description="goals, comma-separated")
    initial_secrets: str = Field(description="secrets, comma-separated")
    speech_pattern: str = Field(description="1-3 sentences on how they speak")


class CharacterDeveloperOutput(BaseModel):
    characters: list[CharacterOut] = Field(description="the character list with dossiers")
    character_graph: list[CharacterGraphInitOut] = Field(description="each character's initial CSV state")
    character_voices_md: str = Field(description="the full markdown voice guide, one ## Name section per character")


# ── planning_verifier ──────────────────────────────────────────────────────────

class PlanningVerifyIssueOut(BaseModel):
    artifact: Literal["story_bible", "plot_outline", "characters", "world"] = Field(
        description="the artifact that MUST BE FIXED to resolve this issue")
    description: str = Field(description="a specific description of the fault")
    suggestion: str = Field(description="what it must be changed to")
    severity: Literal["critical", "minor"] = Field(description="critical = breaks the book; minor = worth improving")


class PlanningVerifierOutput(BaseModel):
    issues: list[PlanningVerifyIssueOut] = Field(default=[], description="every fault in the planning set")
    verdict_note: str = Field(description="a short verdict")


# ── Planning artifacts as structured JSON (plot_outline, world_bible) ──────────
# These replace the free-text markdown blobs. Same information the markdown
# template carried, just structured. Downstream agents receive the JSON string.

class PlotSceneOut(BaseModel):
    id: str = Field("", description='the scene number: "1.1", "1.2"…')
    name: str = Field("", description="a short scene name")
    location: str = Field("", description="where the scene takes place")
    characters: list[str] = Field(default=[], description="the characters present in the scene")
    what_happens: str = Field("", description="what happens in the scene")
    scene_end: str = Field("", description="how the scene ends (the hook that pulls the reader on)")


class PlotChapterOut(BaseModel):
    number: int = Field(description="the chapter number (1..N), in order")
    title: str = Field("", description="the chapter title")
    act: int | None = Field(None, description="Act: 1 | 2 | 3")
    target_words: int | None = Field(None, description="the chapter's word target (an integer)")
    arc_position: str = Field("", description="position in the arc: Hook / Setup / Midpoint / Climax…")
    goals: list[str] = Field(default=[], description="what must be established or happen in the chapter")
    scenes: list[PlotSceneOut] = Field(default=[], description="the chapter's 3-4 scenes")
    character_notes: str = Field("", description="the lead's inner state + what the supporting cast does in this chapter")
    plot_threads: list[str] = Field(default=[], description='threads opened or advanced, e.g. "Open: …", "Advance: …"')
    cliffhanger: str = Field("", description="the question or tension left at the chapter's end")


class PlotOutlineOut(BaseModel):
    title: str = Field("", description="the story title")
    arc_overview: str = Field("", description="2-3 sentences describing the overall journey")
    chapters: list[PlotChapterOut] = Field(default=[], description="all N chapters, numbered 1→N in order")


class WorldLocationOut(BaseModel):
    name: str = Field(description="the location name")
    physical: str = Field("", description="a sensory physical description — colour, sound, smell")
    plot_significance: str = Field("", description="its significance to the plot")
    signature: str = Field("", description="a memorable signature detail")


class WorldSystemOut(BaseModel):
    name: str = Field(description="the system's name: magic / martial arts / technology / social")
    rules: str = Field("", description="what it can do; what it CANNOT do (the limits matter most)")
    origin: str = Field("", description="its origin and a brief history")
    role_in_plot: str = Field("", description="how it bears on the story's conflict")


class WorldFactionOut(BaseModel):
    name: str = Field(description="the faction or organisation's name")
    goal: str = Field("", description="its goal")
    strengths: str = Field("", description="its strengths")
    weaknesses: str = Field("", description="its weaknesses")


class GlossaryTermOut(BaseModel):
    term: str = Field(description="a special term, title or place name")
    meaning: str = Field("", description="its meaning and consistent usage")


class WorldBibleOut(BaseModel):
    title: str = Field("", description="the story title")
    overview: str = Field("", description="2-3 paragraphs on how the world feels — its vibe and atmosphere")
    locations: list[WorldLocationOut] = Field(default=[], description="the main locations that will appear")
    systems: list[WorldSystemOut] = Field(default=[], description="fill this only if the genre genuinely has a system (magic/tech/martial arts…); leave it empty for a realist story")
    factions: list[WorldFactionOut] = Field(default=[], description="the factions or organisations, if any")
    power_structure: str = Field("", description="who controls whom, and why")
    culture: str = Field("", description="the cultural details that will appear — rituals, dress, characteristic speech")
    history: str = Field("", description="only the history that bears directly on the plot — not an encyclopaedia")
    glossary: list[GlossaryTermOut] = Field(default=[], description="special terminology for the chapter-writer to use consistently")
