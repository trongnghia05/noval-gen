"""Pydantic schemas for the structured (JSON-mode) agent outputs.

chapter_writer stays free-form prose — everything else feeds directly into DB
tables or CSV files, so it's requested and parsed as JSON.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── story_analyzer ─────────────────────────────────────────────────────────────

class GraphNodeOut(BaseModel):
    id: str                            # "C001" … "F099" — story-scoped key
    node_type: str                     # character|location|event|object|theme|faction
    label: str
    properties: dict[str, Any] = {}
    chapter_introduced: int | None = None


class GraphEdgeOut(BaseModel):
    source_id: str = Field(description="node_key of the source node")
    target_id: str = Field(description="node_key of the target node. LOCATED_AT: source=EVENT(E###), target=LOCATION(L###)")
    edge_type: str = Field(description="RELATION | ARC_CHANGE | PARTICIPATES | LOCATED_AT | CAUSES | MEMBER_OF | EMBODIES | FORESHADOWS | INVOLVES | OWNS")
    label: str = Field(default="", description="a short description of this edge")
    chapter_from: int | None = Field(default=None)
    chapter_to: int | None = Field(default=None)
    trigger_event_id: str | None = Field(default=None)
    condition: str | None = Field(default=None)

    # ── Typed fields — required depending on edge_type ─────────────────────
    # [RELATION] the kind of relationship between two characters
    rel_type: str | None = Field(
        default=None,
        description="[RELATION — REQUIRED] relationship type: friendship|rivalry|romantic|mentor_student|family|distrust|alliance|betrayal|professional|..."
    )
    strength: str = Field(
        default="medium",
        description="[RELATION] relationship strength: weak|medium|strong"
    )

    # [ARC_CHANGE] a change in a character's inner state
    old_val: str | None = Field(
        default=None,
        description="[ARC_CHANGE — REQUIRED] the character's CURRENT arc_stage BEFORE this chapter. Take it from 'arc=' in the ENTITY LIST. For a character appearing for the first time, use 'introduction'"
    )
    new_val: str | None = Field(
        default=None,
        description="[ARC_CHANGE — REQUIRED] the NEW arc_stage after this chapter's event — describe the changed inner state"
    )
    arc_field: str = Field(
        default="arc_stage",
        description="[ARC_CHANGE] the field being changed; always 'arc_stage'"
    )

    # [PARTICIPATES] the character's role in the event
    role: str | None = Field(
        default=None,
        description="[PARTICIPATES — REQUIRED] role: cause | victim | witness | ally | bystander"
    )

    # [CAUSES] the causal mechanism between two events
    mechanism: str | None = Field(
        default=None,
        description="[CAUSES — REQUIRED] explain the causality: why the earlier event leads directly to this one"
    )

    # Fallback cho MEMBER_OF, EMBODIES, FORESHADOWS, v.v.
    properties: dict[str, Any] = Field(
        default={},
        description="[MEMBER_OF|EMBODIES|FORESHADOWS|INVOLVES|OWNS] extra information; not for RELATION/ARC_CHANGE/PARTICIPATES/CAUSES — use their dedicated fields above"
    )

    @model_validator(mode="after")
    def check_required_by_type(self) -> "GraphEdgeOut":
        t = self.edge_type
        if t == "ARC_CHANGE":
            if not self.old_val:
                raise ValueError(
                    "an ARC_CHANGE edge MUST carry 'old_val' (the arc_stage before the change — take it from the ENTITY LIST). "
                    "Example: old_val='introduction'"
                )
            if not self.new_val:
                raise ValueError(
                    "an ARC_CHANGE edge MUST carry 'new_val' (the arc_stage after the change)"
                )
        elif t == "RELATION":
            if not self.rel_type:
                raise ValueError(
                    "a RELATION edge MUST carry 'rel_type' (friendship|rivalry|romantic|mentor_student|family|distrust|alliance|betrayal|...)"
                )
        elif t == "PARTICIPATES":
            if not self.role:
                raise ValueError(
                    "a PARTICIPATES edge MUST carry 'role' (cause|victim|witness|ally|bystander)"
                )
        elif t == "CAUSES":
            if not self.mechanism:
                raise ValueError(
                    "a CAUSES edge MUST carry 'mechanism' (why the earlier event leads to this one)"
                )
        elif t == "LOCATED_AT":
            src = (self.source_id or "")
            tgt = (self.target_id or "")
            # Auto-swap if LLM reverses direction (L→E instead of E→L)
            if src and tgt and src[0].upper() == "L" and tgt[0].upper() == "E":
                self.source_id, self.target_id = self.target_id, self.source_id
        return self


class StoryAnalyzerOutput(BaseModel):
    narrative_summary: str             # short prose summary kept in story.story_bible
    source_spirit: str = ""            # REWRITE only — overall tone/mood + 2-3 verbatim excerpts from source
    nodes: list[GraphNodeOut]          # CHARACTER, LOCATION, FACTION, THEME, OBJECT
                                       # + key arc EVENT nodes for IDEA/PREMISE
                                       # NO event nodes for REWRITE (those come from chapter_graph_extractor)
    edges: list[GraphEdgeOut]          # initial relations, member_of, embodies
    source_chapter_count: int | None = None  # REWRITE only — how many source chapters exist

    @model_validator(mode="after")
    def check_unique_node_labels(self) -> "StoryAnalyzerOutput":
        seen: dict[str, str] = {}  # normalised_label → first node_id
        for n in self.nodes:
            key = n.label.strip().lower()
            if key in seen:
                raise ValueError(
                    f"Duplicate node label '{n.label}': used by both node {seen[key]} and node {n.id}. "
                    f"Every node must have a completely unique label. "
                    f"Assign a different name/label to node {n.id}."
                )
            seen[key] = n.id
        return self


class ChapterGraphOutput(BaseModel):
    """Per-source-chapter extraction for REWRITE. One call per chapter, bounded output."""
    event: GraphNodeOut                # the single EVENT node for this source chapter
    new_nodes: list[GraphNodeOut] = [] # new entities discovered in this chapter not yet in DB
    edges: list[GraphEdgeOut] = []     # PARTICIPATES, LOCATED_AT, CAUSES, RELATION change, ARC_CHANGE


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


# ── novel metadata (front matter for the exported file) ─────────────────────────

class CharacterBlurbOut(BaseModel):
    """One cast entry for the end of summarize.txt."""
    name: str = Field(description="copied exactly from the cast list given in the prompt")
    role: str = Field(description="protagonist | love_interest | antagonist | supporting")
    blurb: str = Field(description="2-3 sentences: what this character DOES in the plot")


class NovelMetadataOut(BaseModel):
    author: str = Field(description="a fitting pen name (invented), in the story's language/culture")
    tags: list[str] = Field(default=[], description='3-6 genre/theme tags, e.g. ["Dark Fantasy", "Gothic Romance"]')
    logline: str = Field(description="the plot — a 1-2 sentence premise/hook")
    summary: str = Field(description="back-cover blurb, 120-180 words, no ending spoilers")
    characters: list[CharacterBlurbOut] = Field(default=[], description="main cast, minor walk-ons excluded")


class ImagePromptIssueOut(BaseModel):
    """One fault found in a drafted poster prompt, phrased so it can be fixed."""
    image: str = Field(description="cover | thumb1 | thumb2 | all")
    check: str = Field(description="era | protagonist | age | dynamic | title | wardrobe | variety | colour | cast — the rule that was broken")
    description: str = Field(description="where the prompt is currently wrong — quote it")
    fix: str = Field(description="the specific fix")


class ImagePromptVerifyOut(BaseModel):
    issues: list[ImagePromptIssueOut] = Field(default=[], description="empty ⇒ passes")
    verdict_note: str = Field("", description="a short verdict")


class ImagePromptSetOut(BaseModel):
    """Three text-to-image prompts (English) for the poster art. Each describes a
    cinematic, photorealistic drama-poster composition in the STORY'S world, with
    NO text/letters/watermarks (the title is overlaid separately by Pillow)."""
    cover: str = Field(description="wide: a montage of the main characters in the story's world")
    thumb1: str = Field(description="portrait: the lead (optionally with one supporting character)")
    thumb2: str = Field(description="portrait: the central pair / the pivotal relationship")


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


# ── new_graph_builder Phase 0 (world design) ─────────────────────────────────

class WorldDesignOutput(BaseModel):
    setting: str = Field(description='the setting, e.g. "1990s Hong Kong financial district"')
    time_period: str = Field(description='the period, e.g. "1994-1997, pre-handover"')
    genre: str = Field(description='the genre, e.g. "corporate thriller with political undercurrent"')
    tone: str = Field(description='the tone, e.g. "tense, morally ambiguous, atmospheric"')
    protagonist_archetype: str = Field(description="the lead BY ROLE (NO proper names) + the situation they face")
    antagonist_archetype: str = Field(description="the antagonist BY ROLE (NO proper names) + what drives their opposition")
    location_concepts: list[str] = Field(description="3-5 key locations, each described BY ROLE + its narrative purpose, ABSOLUTELY NO proper names")
    thematic_core: str = Field(description="the central question or truth the story explores")
    narrative_summary: str = Field(description="a 300-400 word prose summary of the NEW story, told ENTIRELY by ROLE (the lead, the opposing side…), with NO proper names for people, places or factions, NO reference to the source story, written in the requested language")


class WorldNameCheckOutput(BaseModel):
    """Verdict from the world-design name checker: which invented PROPER NAMES (of
    people / places / clans / factions / objects) still appear, so the world design
    can be regenerated until it is fully name-free (roles only)."""
    proper_names: list[str] = Field(default=[], description="any invented proper names still present (empty = clean)")
    note: str = Field("", description="an optional note")


class EntityResolveOutput(BaseModel):
    """Verdict from the entity resolver during source extraction: is a newly-extracted
    node actually the SAME real entity as one already in the graph? A code pre-filter
    finds same-name candidates; this decides if the new node should REUSE an existing
    key (`same_as`) or is genuinely distinct (`same_as` = null)."""
    same_as: str | None = Field(None, description="the node_key of the existing entity, or null if this is a new one")
    note: str = Field("", description="an optional note")


class StoryBibleLeakCheckOutput(BaseModel):
    """Second-stage verdict on story-bible name leaks. Python first finds SOURCE names
    that appear as whole words in the new story-bible prose (candidates); this LLM
    pass judges which candidates are ACTUAL leaks — the source character re-appearing
    — vs false positives (a coincidental common word, or a name legitimately reused by
    the new world). Only confirmed real leaks should trigger a rewrite."""
    real_leaks: list[str] = Field(default=[], description="candidates confirmed as genuine source-name leaks")
    note: str = Field("", description="an optional note")


# ── new_graph_builder Phase 1 (name lexicon) ─────────────────────────────────

class NameLexiconEntry(BaseModel):
    node_key: str = Field(description='the source node_key, e.g. "C001"')
    node_type: str = Field(description="character | location | faction | object")
    source_label: str = Field(description="the original name, copied verbatim from the node list")
    new_label: str = Field(description="the full name you assign in the new world (reusing no word from the original)")


class NameLexiconOutput(BaseModel):
    entries: list[NameLexiconEntry] = Field(description="one entry per node that needs renaming")
    world_note: str = Field(description="1-2 sentences on the naming convention you chose")

    @model_validator(mode="after")
    def check_unique_labels(self) -> "NameLexiconOutput":
        seen: dict[str, str] = {}
        for e in self.entries:
            key = e.new_label.strip().lower()
            if key in seen:
                raise ValueError(
                    f"Duplicate new label '{e.new_label}': used by {seen[key]} and {e.node_key}. "
                    f"Every node must have a completely unique label."
                )
            seen[key] = e.node_key
        return self


# ── new_graph_builder Phase 2 (surface rename) ────────────────────────────────

class NodeSurfaceOut(BaseModel):
    node_key: str
    new_label: str
    # CHARACTER text fields
    new_profile_md: str | None = None
    new_wants: str | None = None
    new_fears: str | None = None
    new_arc_stage: str | None = None
    new_background: str | None = None
    new_speech_pattern: str | None = None
    # EVENT text fields
    new_summary: str | None = None
    # LOCATION / FACTION / THEME / OBJECT
    new_description: str | None = None


class EdgeSurfaceOut(BaseModel):
    source_key: str
    target_key: str
    edge_type: str
    new_label: str = ""
    new_mechanism: str | None = None   # CAUSES
    new_condition: str | None = None   # RELATION
    new_old_val: str | None = None     # ARC_CHANGE
    new_new_val: str | None = None     # ARC_CHANGE


class NewGraphSurfaceOutput(BaseModel):
    narrative_summary: str
    node_surfaces: list[NodeSurfaceOut]
    edge_surfaces: list[EdgeSurfaceOut] = []

    @model_validator(mode="after")
    def check_unique_labels(self) -> "NewGraphSurfaceOutput":
        seen: dict[str, str] = {}
        for s in self.node_surfaces:
            key = s.new_label.strip().lower()
            if key in seen:
                raise ValueError(
                    f"Duplicate new label '{s.new_label}': used by both {seen[key]} and {s.node_key}. "
                    f"Every node must have a completely unique label — assign a different name to {s.node_key}."
                )
            seen[key] = s.node_key
        return self


# ── graph_enricher Phase 3 (creative enrichment) ──────────────────────────────

class GraphEnrichmentOutput(BaseModel):
    new_nodes: list[GraphNodeOut] = []
    new_edges: list[GraphEdgeOut] = []
    enrichment_note: str

    @model_validator(mode="after")
    def check_enrichment_constraints(self) -> "GraphEnrichmentOutput":
        for n in self.new_nodes:
            if n.node_type == "event":
                raise ValueError(
                    f"Enrichment cannot add EVENT nodes (node {n.id}: '{n.label}'). "
                    "Only character, location, object, theme, faction nodes are allowed."
                )
        for e in self.new_edges:
            if e.edge_type in ("CAUSES", "ARC_CHANGE"):
                raise ValueError(
                    f"Enrichment cannot add {e.edge_type} edges ({e.source_id}→{e.target_id}). "
                    "Allowed edge types: FORESHADOWS, MEMBER_OF, INVOLVES, OWNS, EMBODIES, PARTICIPATES, RELATION."
                )
        return self


# ── graph_surface_rewriter (targeted surface repair) ──────────────────────────

class SurfaceNodePatchOut(BaseModel):
    node_key: str = Field(description="the node to patch")
    new_label: str | None = Field(None, description="the new name (if it changes)")
    new_summary: str | None = Field(None, description="EVENT: the rewritten summary")
    new_profile_md: str | None = Field(None, description="CHARACTER: the rewritten profile")
    new_description: str | None = Field(None, description="LOCATION/FACTION/THEME/OBJECT: the rewritten description")
    new_arc_stage: str | None = Field(None, description="CHARACTER: the rewritten current inner state")
    new_background: str | None = Field(None, description="CHARACTER: the rewritten backstory")
    new_wants: str | None = Field(None, description="CHARACTER: the rewritten goal")
    new_fears: str | None = Field(None, description="CHARACTER: the rewritten fear/weakness")


class SurfaceEdgePatchOut(BaseModel):
    source_key: str = Field(description="the edge's source node")
    target_key: str = Field(description="the edge's target node")
    edge_type: str = Field(description="RELATION | PARTICIPATES | CAUSES | ARC_CHANGE | FORESHADOWS | LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES")
    new_mechanism: str | None = Field(None, description="CAUSES: the rewritten causal mechanism")
    new_label: str | None = Field(None, description="the rewritten edge label")
    new_rel_type: str | None = Field(None, description="RELATION: friendship|rivalry|love|family|mentor|debt|alliance|betrayal|distrust")
    new_condition: str | None = Field(None, description="RELATION: the rewritten condition/context text")
    new_old_val: str | None = Field(None, description="ARC_CHANGE: the rewritten prior state")
    new_new_val: str | None = Field(None, description="ARC_CHANGE: the rewritten subsequent state")


class NewEdgeForRepairOut(BaseModel):
    source_key: str = Field(description="the source node")
    target_key: str = Field(description="the target node")
    edge_type: str = Field(description="PARTICIPATES | RELATION | LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES")
    label: str = Field("", description="the edge label")
    rel_type: str | None = Field(None, description="for RELATION")
    role: str | None = Field(None, description="for PARTICIPATES: cause|victim|witness|ally|bystander")
    chapter_from: int | None = Field(None, description="the starting chapter (if any)")


class GraphSurfaceRepairOutput(BaseModel):
    node_patches: list[SurfaceNodePatchOut] = Field(default=[], description="node patches")
    edge_patches: list[SurfaceEdgePatchOut] = Field(default=[], description="edge patches")
    add_edges: list[NewEdgeForRepairOut] = Field(default=[], description="missing edges that must be created")
    repair_note: str = Field(description="a short note on what you repaired")


# ── new_graph_builder per-group enrichment ─────────────────────────────────────

class CharacterSurfaceOut(BaseModel):
    node_key: str = Field(description="the character node to enrich")
    new_role: str = Field(
        default="",
        description="EXACTLY ONE OF: protagonist | antagonist | love_interest | "
                    "supporting | minor. No other value, no compound label.",
    )
    new_gender: str = Field("", description="male|female|nonbinary — must match the new name and every pronoun used in arc/background/voice/appearance")
    new_arc_stage: str = Field(description="the current inner state (new world)")
    new_wants: str = Field(description="the goal or want (new world)")
    new_fears: str = Field(description="the fear or weakness (new world)")
    new_background: str = Field("", description="the backstory (new world)")
    new_speech_pattern: str = Field("", description="one short line (a CSV column)")
    new_voice_profile: str = Field("", description='a multi-line voice guide in the format "REGISTER: …\\nVOCABULARY: …\\nRHYTHM: …\\nTIC/TELL: …\\nSAMPLE LINES:\\n- \\"…\\"\\n- \\"…\\"" — new world only, never taken from the source prose')
    new_appearance: str = Field("", description="DISTINCTIVE physical appearance for the cover art: a specific individual (heritage, face shape, hair, eye colour, 1-2 memorable features), not a default face")


class CharacterGroupEnrichOutput(BaseModel):
    characters: list[CharacterSurfaceOut] = Field(description="one entry per character")
    note: str = Field("", description="an optional note")


class EventSurfaceOut(BaseModel):
    node_key: str = Field(description="the event node")
    new_summary: str = Field(description="the event summary, reading as a beat of the new world")


class EventGroupEnrichOutput(BaseModel):
    events: list[EventSurfaceOut] = Field(description="one entry per event")
    note: str = Field("", description="an optional note")


class ArcChangeSurfaceOut(BaseModel):
    source_key: str = Field(description="the character node (a self-loop ARC_CHANGE)")
    chapter_from: int | None = Field(description="the chapter the change happens in")
    new_old_val: str = Field(description="the prior state (new world)")
    new_new_val: str = Field(description="the subsequent state (new world)")


class ArcChangeGroupEnrichOutput(BaseModel):
    arc_changes: list[ArcChangeSurfaceOut] = Field(description="one entry per arc-change")
    note: str = Field("", description="an optional note")


class RelationSurfaceOut(BaseModel):
    source_key: str = Field(description="the source character node")
    target_key: str = Field(description="the target character node")
    chapter_from: int | None = Field(description="the chapter this relationship begins")
    new_rel_type: str = Field(description="friendship|rivalry|love|family|mentor|debt|alliance|betrayal|distrust")
    new_label: str = Field(description="a short description of the relationship (new world)")


class RelationGroupEnrichOutput(BaseModel):
    relations: list[RelationSurfaceOut] = Field(description="one entry per relationship")
    note: str = Field("", description="an optional note")


class CausesSurfaceOut(BaseModel):
    source_key: str = Field(description="the causing event node")
    target_key: str = Field(description="the resulting event node")
    new_mechanism: str = Field(description="the causal mechanism (new world)")
    new_label: str = Field("", description="a short label for the link")


class CausesGroupEnrichOutput(BaseModel):
    causes: list[CausesSurfaceOut] = Field(description="one entry per causal link")
    note: str = Field("", description="an optional note")


# ── graph_enrich_verifier (per-enricher quality/logic/naming gate) ─────────────

class GraphEnrichIssueOut(BaseModel):
    """One problem the enrich-verifier found in a group's enriched output."""
    target: str = Field("", description='the node_key or edge concerned, e.g. "C003" or "C003→C007"')
    dimension: Literal["naming", "logic", "quality"] = Field("naming", description="the kind of fault")
    problem: str = Field(description="what the fault is")
    fix: str = Field("", description="specific instructions for re-enriching")


class GraphEnrichVerifyOutput(BaseModel):
    """Verdict on one enrichment group. `issues` empty ⇒ the group passed."""
    issues: list[GraphEnrichIssueOut] = Field(default=[], description="empty ⇒ the group passes")
    note: str = Field("", description="an optional note")


# ── graph_verifier ─────────────────────────────────────────────────────────────

class GraphVerifyIssueOut(BaseModel):
    check_type: Literal["narrative_logic", "reskin", "enrichment", "cast_role"] = Field("narrative_logic", description="the check that was violated")
    node_key: str | None = Field(None, description="the node with the problem (null if it is general)")
    edge_desc: str | None = Field(None, description='a description of the edge, e.g. "C001→C002 CAUSES Ch.5"')
    description: str = Field(description="a description of the problem")
    suggestion: str = Field(description="the suggested fix")
    severity: Literal["critical", "minor"] = Field(description="critical | minor")


class GraphVerifierOutput(BaseModel):
    issues: list[GraphVerifyIssueOut] = Field(default=[], description="every problem found in the new graph")
    verdict_note: str = Field(description="a short verdict")


# ── graph_repair ───────────────────────────────────────────────────────────────

class NodeUpdateOut(BaseModel):
    node_key: str
    properties: dict[str, Any]


class EdgeDeleteOut(BaseModel):
    source_key: str
    target_key: str
    edge_type: str
    chapter_from: int | None = None


class EdgeAddOut(BaseModel):
    source_id: str
    target_id: str
    edge_type: str
    label: str = ""
    chapter_from: int | None = None
    chapter_to: int | None = None
    trigger_event_id: str | None = None
    condition: str | None = None
    properties: dict[str, Any] = {}


class GraphRepairOutput(BaseModel):
    node_updates: list[NodeUpdateOut] = []
    edge_deletes: list[EdgeDeleteOut] = []
    edge_adds: list[EdgeAddOut] = []
    repair_note: str


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
