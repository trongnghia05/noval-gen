"""Schemas for the knowledge graph: extraction from the source, the reskinned new graph, and every verify/repair pass over either."""
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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


