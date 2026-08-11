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
    source_id: str = Field(description="node_key của node nguồn")
    target_id: str = Field(description="node_key của node đích. LOCATED_AT: source=EVENT(E###), target=LOCATION(L###)")
    edge_type: str = Field(description="RELATION | ARC_CHANGE | PARTICIPATES | LOCATED_AT | CAUSES | MEMBER_OF | EMBODIES | FORESHADOWS | INVOLVES | OWNS")
    label: str = Field(default="", description="mô tả ngắn về cạnh này")
    chapter_from: int | None = Field(default=None)
    chapter_to: int | None = Field(default=None)
    trigger_event_id: str | None = Field(default=None)
    condition: str | None = Field(default=None)

    # ── Typed fields — bắt buộc tùy edge_type ──────────────────────────────
    # [RELATION] loại quan hệ giữa hai nhân vật
    rel_type: str | None = Field(
        default=None,
        description="[RELATION — BẮT BUỘC] loại quan hệ: friendship|rivalry|romantic|mentor_student|family|distrust|alliance|betrayal|professional|..."
    )
    strength: str = Field(
        default="medium",
        description="[RELATION] mức độ quan hệ: weak|medium|strong"
    )

    # [ARC_CHANGE] thay đổi trạng thái nội tâm nhân vật
    old_val: str | None = Field(
        default=None,
        description="[ARC_CHANGE — BẮT BUỘC] arc_stage HIỆN TẠI của nhân vật TRƯỚC chương này. Lấy từ 'arc=' trong ENTITY LIST. Nếu nhân vật mới ra mắt lần đầu thì dùng 'introduction'"
    )
    new_val: str | None = Field(
        default=None,
        description="[ARC_CHANGE — BẮT BUỘC] arc_stage MỚI sau sự kiện chương này — mô tả trạng thái nội tâm thay đổi"
    )
    arc_field: str = Field(
        default="arc_stage",
        description="[ARC_CHANGE] trường đang thay đổi, luôn là 'arc_stage'"
    )

    # [PARTICIPATES] vai trò nhân vật trong sự kiện
    role: str | None = Field(
        default=None,
        description="[PARTICIPATES — BẮT BUỘC] vai trò: cause (kẻ gây ra) | victim (nạn nhân) | witness (chứng kiến) | ally (hỗ trợ) | bystander (ngoại vi)"
    )

    # [CAUSES] cơ chế nhân quả giữa hai sự kiện
    mechanism: str | None = Field(
        default=None,
        description="[CAUSES — BẮT BUỘC] giải thích nhân quả: tại sao event trước trực tiếp dẫn đến event này"
    )

    # Fallback cho MEMBER_OF, EMBODIES, FORESHADOWS, v.v.
    properties: dict[str, Any] = Field(
        default={},
        description="[MEMBER_OF|EMBODIES|FORESHADOWS|INVOLVES|OWNS] thông tin bổ sung; không dùng cho RELATION/ARC_CHANGE/PARTICIPATES/CAUSES — dùng các field riêng ở trên"
    )

    @model_validator(mode="after")
    def check_required_by_type(self) -> "GraphEdgeOut":
        t = self.edge_type
        if t == "ARC_CHANGE":
            if not self.old_val:
                raise ValueError(
                    "ARC_CHANGE edge PHẢI có 'old_val' (arc_stage trước thay đổi — lấy từ ENTITY LIST). "
                    "Ví dụ: old_val='introduction'"
                )
            if not self.new_val:
                raise ValueError(
                    "ARC_CHANGE edge PHẢI có 'new_val' (arc_stage sau thay đổi)"
                )
        elif t == "RELATION":
            if not self.rel_type:
                raise ValueError(
                    "RELATION edge PHẢI có 'rel_type' (friendship|rivalry|romantic|mentor_student|family|distrust|alliance|betrayal|...)"
                )
        elif t == "PARTICIPATES":
            if not self.role:
                raise ValueError(
                    "PARTICIPATES edge PHẢI có 'role' (cause|victim|witness|ally|bystander)"
                )
        elif t == "CAUSES":
            if not self.mechanism:
                raise ValueError(
                    "CAUSES edge PHẢI có 'mechanism' (giải thích tại sao event trước dẫn đến event này)"
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


# ── novel metadata (front matter for the exported file) ─────────────────────────

class CharacterBlurbOut(BaseModel):
    """One cast entry for the end of summarize.txt."""
    name: str = Field(description="copied exactly from the cast list given in the prompt")
    role: str = Field(description="protagonist | love_interest | antagonist | supporting")
    blurb: str = Field(description="2-3 sentences: what this character DOES in the plot")


class NovelMetadataOut(BaseModel):
    author: str = Field(description="a fitting pen name (invented), in the story's language/culture")
    tags: list[str] = Field(default=[], description='3-6 genre/theme tags, e.g. ["Dark Fantasy", "Gothic Romance"]')
    logline: str = Field(description="cốt truyện — 1-2 sentence premise/hook")
    summary: str = Field(description="back-cover blurb, 120-180 words, no ending spoilers")
    characters: list[CharacterBlurbOut] = Field(default=[], description="main cast, minor walk-ons excluded")


class ImagePromptIssueOut(BaseModel):
    """One fault found in a drafted poster prompt, phrased so it can be fixed."""
    image: str          # cover | thumb1 | thumb2 | all
    check: str          # era | protagonist | age | dynamic | title | wardrobe |
                        # variety | colour | cast — which rule was broken
    description: str    # what the prompt currently says that is wrong — quote it
    fix: str            # the concrete correction to make


class ImagePromptVerifyOut(BaseModel):
    issues: list[ImagePromptIssueOut] = []
    verdict_note: str = ""


class ImagePromptSetOut(BaseModel):
    """Three text-to-image prompts (English) for the poster art. Each describes a
    cinematic, photorealistic drama-poster composition in the STORY'S world, with
    NO text/letters/watermarks (the title is overlaid separately by Pillow)."""
    cover: str    # wide: montage of the story's main characters in-world
    thumb1: str   # portrait: the protagonist (optionally + one secondary character)
    thumb2: str   # portrait: the central pair / key relationship


# ── chapter_writer ─────────────────────────────────────────────────────────────

class ChapterWriterOutput(BaseModel):
    title: str          # chapter title only — no "Chapter X:" prefix
    content: str        # full prose, starting AFTER the heading line
    short_summary: str  # 1-2 sentences: key event + emotional shift
    hook: str           # exact last sentence / cliffhanger


# ── chapter_summarizer ─────────────────────────────────────────────────────────

class StateChangeOut(BaseModel):
    entity: str = Field(description="tên thực thể thay đổi (nhân vật/vật thể/quan hệ)")
    field: str = Field(description="trường thay đổi")
    old_value: str | None = Field(None, description="giá trị cũ (null nếu mới)")
    new_value: str = Field(description="giá trị mới")
    reason: str = Field(description="lý do thay đổi")


class WorldStateRowOut(BaseModel):
    entity_type: str = Field(description="character | relationship | plot_thread | object | timeline")
    entity_key: str = Field(description="khóa định danh thực thể")
    field: str = Field(description="tên trường")
    value: str = Field(description="giá trị hiện tại (snapshot, ghi đè)")

    @field_validator("value", mode="before")
    @classmethod
    def coerce_value_to_str(cls, v):
        return str(v) if not isinstance(v, str) else v


class ForeshadowingOut(BaseModel):
    fid: str = Field(description="mã gài cắm: F1, F2, …")
    detail: str = Field(description="chi tiết được gài")
    planted_chapter: int = Field(description="chương gài")
    status: str = Field(description="planted | advancing | resolved")
    payoff_chapter: int | None = Field(None, description="chương trả (null nếu chưa)")


class CharacterStateUpdateOut(BaseModel):
    id: str = Field(description="character CSV id (C001, …)")
    field: str = Field(description="location | emotional_state | goals | secrets | arc_status")
    value: str = Field(description="giá trị mới")


class RelationshipChangeOut(BaseModel):
    char_a: str = Field(description="character CSV id")
    char_b: str = Field(description="character CSV id")
    type: str = Field(description="romantic | rivalry | friendship | family | mentor | professional")
    strength: float = Field(description="-1.0 → 1.0, giá trị tuyệt đối mới")
    status: str = Field(description="active | broken | evolving | secret")
    event: str = Field(description="một câu mô tả điều đã thay đổi")


class PlotThreadOut(BaseModel):
    id: str = Field(description="PT001, PT002, …")
    title: str = Field(description="tên tuyến truyện")
    type: str = Field(description="main | subplot | foreshadowing | mystery")
    status: str = Field(description="open | resolved | abandoned")
    introduced_chapter: int | None = Field(None, description="chương giới thiệu")
    involved_chars: str = Field(description='character ids ngăn bằng "|": "C001|C002"')
    hint: str | None = Field(None, description="gợi ý (nếu là foreshadowing/mystery)")
    resolution_note: str | None = Field(None, description="ghi chú khi giải quyết")


class TimelineEventOut(BaseModel):
    story_time: str = Field(description='thời điểm trong truyện, VD "Day 3, late evening"')
    location: str = Field(description="địa điểm")
    characters: str = Field(description='character ids ngăn bằng "|"')
    summary: str = Field(description="một câu tóm tắt")


class ChapterSummaryOutput(BaseModel):
    short_summary: str = Field(description="1-2 câu mô tả sự kiện chính, dùng cho chapter_writer")
    summary: str = Field(description="200-300 từ đầy đủ, dùng cho continuity/verifier")
    state_changes: list[StateChangeOut] = Field(default=[], description="các thay đổi trạng thái ghi vào state-log")
    world_state_rows: list[WorldStateRowOut] = Field(default=[], description="các dòng snapshot world-state (ghi đè)")
    foreshadowing: list[ForeshadowingOut] = Field(default=[], description="gài cắm mới/cập nhật")
    character_updates: list[CharacterStateUpdateOut] = Field(default=[], description="cập nhật CSV nhân vật")
    relationship_changes: list[RelationshipChangeOut] = Field(default=[], description="thay đổi quan hệ CSV")
    plot_thread_updates: list[PlotThreadOut] = Field(default=[], description="tuyến truyện mới hoặc đổi trạng thái")
    timeline_event: TimelineEventOut | None = Field(None, description="sự kiện dòng thời gian của chương này")


# ── chapter_blueprinter ────────────────────────────────────────────────────────

class SceneOut(BaseModel):
    goal: str              # what the POV character wants in this scene
    conflict: str          # what blocks them
    outcome: str           # do they get it? (success | failure | partial)
    disaster: str          # new problem that emerges from this scene
    characters: list[str] = []  # node keys (C001...) or names — who appears in this scene
    location: str = ""          # location label or node key from graph LOCATED_AT
    # ── dialogue plan (new-world; derived from the new graph, never source) ──
    speaking_characters: list[str] = []  # new-world names who actually SPEAK in this scene
    dialogue_nuance: str = ""   # sắc thái: tone/mood of the exchange (e.g. "cold, clipped confrontation")
    dialogue_intent: str = ""   # hướng đến: what the dialogue must accomplish this scene


class ChapterBlueprintOutput(BaseModel):
    purpose: str               # one sentence: why does this chapter exist?
    act_position: str          # Act 1 | Act 2a | Act 2b | Act 3
    # The chapter's structural function — setup | escalation | revelation |
    # setback | turning_point | confrontation | aftermath | resolution. Used to
    # avoid stringing together several chapters of the same kind.
    beat_type: str = ""
    # The concrete STATE CHANGE this chapter must produce: what is materially
    # different in the world by the last line vs. the first (a relationship shifts,
    # a secret is exposed, a plan advances, someone decides/acts). A chapter that
    # only re-explores an already-established feeling without a new delta is "empty".
    state_delta: str = ""
    emotional_arc_start: str   # reader's emotion at chapter open
    emotional_arc_end: str     # reader's emotion at chapter close
    # For a source that uses alternating multi-POV (per source_spirit's POV
    # section): which character "holds" this chapter's point of view. The writer
    # renders the whole chapter inside this character's head, in the source's
    # grammatical person. Empty = single-POV / let the writer follow source_spirit.
    pov_character: str = ""
    # Populated ONLY when the source chapter narrates from more than one POV
    # (a mid-chapter switch). Full set of POV-holder names — order not significant,
    # the writer places the switch where the narrative flows. Empty for the common
    # single-POV chapter, in which case pov_character alone applies.
    pov_characters: list[str] = []
    scenes: list[SceneOut]
    hook: str                  # exact nature of the final hook/cliffhanger
    foreshadowing_to_plant: str | None = None  # seed to drop (for future payoff)
    characters_featured: list[str]  # character CSV ids who appear in this chapter
    # heavy | balanced | sparse — how dialogue-driven this chapter should be.
    # A solitary-introspection chapter is legitimately "sparse"; the verifier
    # checks dialogue against THIS target, not a global constant.
    dialogue_intensity: str = "balanced"
    # Short canonical tags (<=5 words each) for the recurring dramatic beats /
    # motifs this chapter uses, e.g. "possessive-claim", "rescue-from-thug". The
    # blueprinter MUST reuse an existing tag verbatim when the motif recurs (so a
    # cumulative count is meaningful) and only mint a new tag for a genuinely new
    # motif. A tag at its cap must be dropped or escalated, not repeated flat.
    motifs_used: list[str] = []


# ── continuity_editor ─────────────────────────────────────────────────────────

class ContinuityIssueOut(BaseModel):
    description: str = Field(description="mô tả vấn đề continuity")
    suggestion: str = Field(description="cách sửa đề xuất")


class ContinuityEditorOutput(BaseModel):
    critical_issues: list[ContinuityIssueOut] = Field(default=[], description="lỗi nghiêm trọng (mâu thuẫn thật)")
    minor_issues: list[ContinuityIssueOut] = Field(default=[], description="lỗi nhỏ")
    batch_note: str = Field(description="nhận xét chung cho batch 5 chương vừa rà")


# ── smart_planner ──────────────────────────────────────────────────────────────

class SmartPlannerOutput(BaseModel):
    pacing_note: str = Field(description="Trung bình từ/chương: X | Dự báo tổng: Y / mục tiêu W | Hành động: mở rộng/giữ nguyên/cắt gọn")
    characters_to_watch: list[str] = Field(default=[], description='mỗi mục "Tên: cần làm gì trong các chương tiếp theo"')
    threads_to_resolve: list[str] = Field(default=[], description='mỗi mục "Thread: phải payoff trước Ch.X"')
    outline_adjustments: str = Field("", description="điều chỉnh chi tiết cho outline các chương sắp tới (thay thế nội dung cũ, không cộng dồn) — để trống nếu không cần đổi gì")


# ── chapter_verifier ───────────────────────────────────────────────────────────

class ChapterVerifyIssueOut(BaseModel):
    description: str = Field(description="mô tả vấn đề")
    suggestion: str = Field(description="cách sửa đề xuất")
    severity: Literal["critical", "minor"] = Field(description="critical = mâu thuẫn thật; minor = lỗi nhỏ")


class ChapterVerifierOutput(BaseModel):
    issues: list[ChapterVerifyIssueOut] = Field(default=[], description="mọi vấn đề tìm thấy ở chương vừa viết")
    verdict_note: str = Field(description="kết luận ngắn gọn")


# ── planning_verifier ──────────────────────────────────────────────────────────

class PlanningVerifyIssueOut(BaseModel):
    artifact: Literal["story_bible", "plot_outline", "characters", "world"] = Field(
        description="artifact CẦN SỬA để khắc phục lỗi này")
    description: str = Field(description="mô tả lỗi cụ thể")
    suggestion: str = Field(description="cần sửa thành gì")
    severity: Literal["critical", "minor"] = Field(description="critical = phá vỡ tác phẩm; minor = nên cải thiện")


class PlanningVerifierOutput(BaseModel):
    issues: list[PlanningVerifyIssueOut] = Field(default=[], description="mọi lỗi ở bộ kế hoạch")
    verdict_note: str = Field(description="kết luận ngắn gọn")


# ── quality_reviewer ───────────────────────────────────────────────────────────

class QualityReviewIssueOut(BaseModel):
    dimension: Literal["quality", "world_consistency", "graph_consistency", "dialogue", "pov"] = Field(
        description="chiều đánh giá bị vi phạm")
    description: str = Field(description="mô tả vấn đề")
    suggestion: str = Field(description="cách sửa đề xuất")
    severity: Literal["critical", "minor"] = Field(description="critical = phải sửa; minor = ghi nhận")


class QualityReviewerOutput(BaseModel):
    issues: list[QualityReviewIssueOut] = Field(default=[], description="mọi vấn đề chất lượng của chương")
    verdict_note: str = Field(description="kết luận ngắn gọn")


# ── new_graph_builder Phase 0 (world design) ─────────────────────────────────

class WorldDesignOutput(BaseModel):
    setting: str              # e.g. "1990s Hong Kong financial district"
    time_period: str          # e.g. "1994-1997, pre-handover"
    genre: str                # e.g. "corporate thriller with political undercurrent"
    tone: str                 # e.g. "tense, morally ambiguous, atmospheric"
    protagonist_archetype: str  # e.g. "junior auditor who discovers embezzlement"
    antagonist_archetype: str   # e.g. "senior partner exploiting political transition"
    location_concepts: list[str]  # 3-5 key settings in new world
    thematic_core: str          # e.g. "loyalty vs integrity when institutions collapse"
    narrative_summary: str      # 300-400 word prose summary of the new story


class WorldNameCheckOutput(BaseModel):
    """Verdict from the world-design name checker: which invented PROPER NAMES (of
    people / places / clans / factions / objects) still appear, so the world design
    can be regenerated until it is fully name-free (roles only)."""
    proper_names: list[str] = []   # every invented proper name found (empty = clean)
    note: str = ""


class EntityResolveOutput(BaseModel):
    """Verdict from the entity resolver during source extraction: is a newly-extracted
    node actually the SAME real entity as one already in the graph? A code pre-filter
    finds same-name candidates; this decides if the new node should REUSE an existing
    key (`same_as`) or is genuinely distinct (`same_as` = null)."""
    same_as: str | None = None   # node_key of the existing entity, or null if new
    note: str = ""


class StoryBibleLeakCheckOutput(BaseModel):
    """Second-stage verdict on story-bible name leaks. Python first finds SOURCE names
    that appear as whole words in the new story-bible prose (candidates); this LLM
    pass judges which candidates are ACTUAL leaks — the source character re-appearing
    — vs false positives (a coincidental common word, or a name legitimately reused by
    the new world). Only confirmed real leaks should trigger a rewrite."""
    real_leaks: list[str] = []   # candidates confirmed as genuine source-name leaks
    note: str = ""


# ── new_graph_builder Phase 1 (name lexicon) ─────────────────────────────────

class NameLexiconEntry(BaseModel):
    node_key: str       # e.g. "C001"
    node_type: str      # character | location | faction | object
    source_label: str   # original name from source
    new_label: str      # new name for the reimagined world


class NameLexiconOutput(BaseModel):
    entries: list[NameLexiconEntry]
    world_note: str     # 1-2 sentences on naming convention chosen

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
    node_key: str
    new_label: str | None = None
    new_summary: str | None = None       # EVENT
    new_profile_md: str | None = None    # CHARACTER
    new_description: str | None = None   # LOCATION / FACTION / THEME / OBJECT
    # CHARACTER prose fields — commonly flagged as near-verbatim translations of
    # the source and previously un-patchable, so reskin never converged on them.
    new_arc_stage: str | None = None     # CHARACTER
    new_background: str | None = None    # CHARACTER
    new_wants: str | None = None         # CHARACTER
    new_fears: str | None = None         # CHARACTER


class SurfaceEdgePatchOut(BaseModel):
    source_key: str
    target_key: str
    edge_type: str                       # RELATION | PARTICIPATES | CAUSES | ARC_CHANGE | FORESHADOWS | LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES
    new_mechanism: str | None = None     # CAUSES
    new_label: str | None = None
    new_rel_type: str | None = None      # RELATION only — friendship|rivalry|love|family|mentor|debt|alliance|betrayal|distrust
    new_condition: str | None = None     # RELATION condition text
    # ARC_CHANGE edges carry the arc description in old_val/new_val — the single
    # most-flagged reskin field; previously no patch path existed for it.
    new_old_val: str | None = None       # ARC_CHANGE
    new_new_val: str | None = None       # ARC_CHANGE


class NewEdgeForRepairOut(BaseModel):
    source_key: str
    target_key: str
    edge_type: str                       # PARTICIPATES | RELATION | LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES
    label: str = ""
    rel_type: str | None = None          # for RELATION
    role: str | None = None              # for PARTICIPATES: cause|victim|witness|ally|bystander
    chapter_from: int | None = None


class GraphSurfaceRepairOutput(BaseModel):
    node_patches: list[SurfaceNodePatchOut] = []
    edge_patches: list[SurfaceEdgePatchOut] = []
    add_edges: list[NewEdgeForRepairOut] = []   # edges that are missing and must be created
    repair_note: str


# ── new_graph_builder per-group enrichment ─────────────────────────────────────

class CharacterSurfaceOut(BaseModel):
    node_key: str
    # Cast role in the NEW story. Assigned here because this is the only step that
    # sees the whole cast at once; before it existed, role was merely inherited from
    # whatever the source extraction happened to record, so a new graph could end up
    # with nobody marked protagonist at all. Downstream this drives character tier
    # (who reaches the cover art and the per-chapter context) and tells the name
    # lexicon which character deserves a memorable name.
    new_role: str = Field(
        default="",
        description="EXACTLY ONE OF: protagonist | antagonist | love_interest | "
                    "supporting | minor. No other value, no compound labels.",
    )
    # Gender for the NEW world, chosen to be self-consistent: it must match the new
    # name's gender signal, the character's role/relationships in the new plot, and
    # every pronoun used in arc/background/voice/appearance. male|female|nonbinary.
    new_gender: str = ""
    new_arc_stage: str
    new_wants: str
    new_fears: str
    new_background: str = ""
    new_speech_pattern: str = ""      # short one-liner (CSV column)
    # Rich, multi-line voice guide the chapter_writer uses to make dialogue
    # distinct: register, vocabulary, sentence rhythm, verbal tic/"tell",
    # and 2-3 sample lines — all in the NEW world, no source prose.
    new_voice_profile: str = ""
    # Distinctive PHYSICAL appearance for the poster/image art. Attractive, but a
    # SPECIFIC individual (heritage/ethnicity, face shape, hair, eye colour, one or
    # two memorable-but-still-good-looking features) that differs from a generic
    # model-default face — so different stories yield clearly different-looking people.
    new_appearance: str = ""


class CharacterGroupEnrichOutput(BaseModel):
    characters: list[CharacterSurfaceOut]
    note: str = ""


class EventSurfaceOut(BaseModel):
    node_key: str
    new_summary: str


class EventGroupEnrichOutput(BaseModel):
    events: list[EventSurfaceOut]
    note: str = ""


class ArcChangeSurfaceOut(BaseModel):
    source_key: str
    chapter_from: int | None
    new_old_val: str
    new_new_val: str


class ArcChangeGroupEnrichOutput(BaseModel):
    arc_changes: list[ArcChangeSurfaceOut]
    note: str = ""


class RelationSurfaceOut(BaseModel):
    source_key: str
    target_key: str
    chapter_from: int | None
    new_rel_type: str    # friendship|rivalry|love|family|mentor|debt|alliance|betrayal|distrust
    new_label: str


class RelationGroupEnrichOutput(BaseModel):
    relations: list[RelationSurfaceOut]
    note: str = ""


class CausesSurfaceOut(BaseModel):
    source_key: str
    target_key: str
    new_mechanism: str
    new_label: str = ""


class CausesGroupEnrichOutput(BaseModel):
    causes: list[CausesSurfaceOut]
    note: str = ""


# ── graph_enrich_verifier (per-enricher quality/logic/naming gate) ─────────────

class GraphEnrichIssueOut(BaseModel):
    """One problem the enrich-verifier found in a group's enriched output."""
    target: str = ""        # which node_key / edge (e.g. "C003" or "C003→C007")
    dimension: Literal["naming", "logic", "quality"] = "naming"
    problem: str            # what is wrong
    fix: str = ""           # concrete instruction for the re-enrichment


class GraphEnrichVerifyOutput(BaseModel):
    """Verdict on one enrichment group. `issues` empty ⇒ the group passed."""
    issues: list[GraphEnrichIssueOut] = []
    note: str = ""


# ── graph_verifier ─────────────────────────────────────────────────────────────

class GraphVerifyIssueOut(BaseModel):
    check_type: Literal["narrative_logic", "reskin", "enrichment", "cast_role"] = "narrative_logic"
    node_key: str | None = None       # which node has the issue (None if general)
    edge_desc: str | None = None      # describe the edge (e.g. "C001→C002 CAUSES Ch.5")
    description: str
    suggestion: str
    severity: Literal["critical", "minor"]


class GraphVerifierOutput(BaseModel):
    issues: list[GraphVerifyIssueOut] = []
    verdict_note: str


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
    id: str = Field("", description='đánh số cảnh "1.1", "1.2"…')
    name: str = Field("", description="tên cảnh ngắn gọn")
    location: str = Field("", description="địa điểm diễn ra cảnh")
    characters: list[str] = Field(default=[], description="nhân vật có mặt trong cảnh")
    what_happens: str = Field("", description="điều xảy ra trong cảnh")
    scene_end: str = Field("", description="kết thúc cảnh bằng gì (hook để đọc tiếp)")


class PlotChapterOut(BaseModel):
    number: int = Field(description="số thứ tự chương (1..N), đúng thứ tự")
    title: str = Field("", description="tiêu đề chương")
    act: int | None = Field(None, description="Hồi: 1 | 2 | 3")
    target_words: int | None = Field(None, description="mục tiêu số từ chương (số nguyên)")
    arc_position: str = Field("", description="vị trí trong arc: Hook / Mở đầu / Midpoint / Climax…")
    goals: list[str] = Field(default=[], description="điều phải được thiết lập/xảy ra trong chương")
    scenes: list[PlotSceneOut] = Field(default=[], description="3-4 cảnh của chương")
    character_notes: str = Field("", description="trạng thái nội tâm nhân vật chính + vai nhân vật phụ trong chương")
    plot_threads: list[str] = Field(default=[], description='thread mở/tiến, VD "Mở: …", "Tiến: …"')
    cliffhanger: str = Field("", description="câu hỏi/căng thẳng để lại cuối chương")


class PlotOutlineOut(BaseModel):
    title: str = Field("", description="tên truyện")
    arc_overview: str = Field("", description="2-3 câu mô tả hành trình tổng thể")
    chapters: list[PlotChapterOut] = Field(default=[], description="đủ tất cả N chương, đúng thứ tự number 1→N")


class WorldLocationOut(BaseModel):
    name: str = Field(description="tên địa điểm")
    physical: str = Field("", description="mô tả vật lý bằng giác quan — màu sắc, âm thanh, mùi")
    plot_significance: str = Field("", description="ý nghĩa trong plot")
    signature: str = Field("", description="chi tiết đặc trưng dễ nhớ")


class WorldSystemOut(BaseModel):
    name: str = Field(description="tên hệ thống: ma pháp / võ công / công nghệ / xã hội")
    rules: str = Field("", description="làm được gì; KHÔNG làm được gì (giới hạn quan trọng)")
    origin: str = Field("", description="nguồn gốc & lịch sử ngắn gọn")
    role_in_plot: str = Field("", description="ảnh hưởng thế nào đến xung đột câu chuyện")


class WorldFactionOut(BaseModel):
    name: str = Field(description="tên phe phái / tổ chức")
    goal: str = Field("", description="mục tiêu")
    strengths: str = Field("", description="sức mạnh")
    weaknesses: str = Field("", description="điểm yếu")


class GlossaryTermOut(BaseModel):
    term: str = Field(description="tên gọi/danh hiệu/địa danh đặc biệt")
    meaning: str = Field("", description="nghĩa / cách dùng nhất quán")


class WorldBibleOut(BaseModel):
    title: str = Field("", description="tên truyện")
    overview: str = Field("", description="2-3 đoạn mô tả cảm giác chung của thế giới — vibe, atmosphere")
    locations: list[WorldLocationOut] = Field(default=[], description="các địa điểm chính sẽ xuất hiện")
    systems: list[WorldSystemOut] = Field(default=[], description="chỉ điền nếu thể loại thực sự có hệ thống (magic/tech/võ công…); truyện hiện thực để rỗng")
    factions: list[WorldFactionOut] = Field(default=[], description="các phe phái/tổ chức, nếu có")
    power_structure: str = Field("", description="ai kiểm soát ai, tại sao")
    culture: str = Field("", description="chi tiết văn hóa sẽ xuất hiện — lễ nghi, trang phục, ngôn ngữ đặc trưng")
    history: str = Field("", description="chỉ sự kiện lịch sử ảnh hưởng trực tiếp plot — không encyclopaedia")
    glossary: list[GlossaryTermOut] = Field(default=[], description="thuật ngữ đặc biệt để chapter-writer dùng nhất quán")
