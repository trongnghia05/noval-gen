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
    name: str = Field(description="tên nhân vật")
    aliases: list[str] = Field(default=[], description="các cách gọi khác/biệt danh")
    tier: str = Field(description="core | important | secondary")
    profile_md: str = Field(description="hồ sơ markdown đầy đủ: Thông tin cơ bản (tuổi, ngoại hình, nghề/vai); Tâm lý & Tính cách (điểm mạnh, điểm yếu/vết thương, nỗi sợ lớn nhất, khao khát sâu nhất, niềm tin sai lầm); Backstory (2-3 đoạn core/important, 1 đoạn secondary); Arc (bắt đầu/midpoint/kết/bài học — bỏ nếu secondary); Quan hệ với nhân vật khác")


class CharacterGraphInitOut(BaseModel):
    """CSV-ready initial state for one character."""
    id: str = Field(description="C001, C002, … do model gán")
    name: str = Field(description="phải khớp CharacterOut.name chính xác")
    role: str = Field(description="protagonist | antagonist | supporting | minor")
    initial_location: str = Field(description="vị trí ban đầu")
    initial_emotional_state: str = Field(description="trạng thái cảm xúc ban đầu")
    initial_goals: str = Field(description="mục tiêu, ngăn bằng dấu phẩy")
    initial_secrets: str = Field(description="bí mật, ngăn bằng dấu phẩy")
    speech_pattern: str = Field(description="1-3 câu mô tả cách nói")


class CharacterDeveloperOutput(BaseModel):
    characters: list[CharacterOut] = Field(description="danh sách nhân vật với hồ sơ")
    character_graph: list[CharacterGraphInitOut] = Field(description="trạng thái CSV ban đầu mỗi nhân vật")
    character_voices_md: str = Field(description="hướng dẫn giọng markdown đầy đủ, mỗi nhân vật một section ## Tên")


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
    image: str = Field(description="cover | thumb1 | thumb2 | all")
    check: str = Field(description="era | protagonist | age | dynamic | title | wardrobe | variety | colour | cast — rule bị vi phạm")
    description: str = Field(description="prompt hiện đang sai chỗ nào — trích dẫn")
    fix: str = Field(description="cách sửa cụ thể")


class ImagePromptVerifyOut(BaseModel):
    issues: list[ImagePromptIssueOut] = Field(default=[], description="rỗng ⇒ đạt")
    verdict_note: str = Field("", description="kết luận ngắn")


class ImagePromptSetOut(BaseModel):
    """Three text-to-image prompts (English) for the poster art. Each describes a
    cinematic, photorealistic drama-poster composition in the STORY'S world, with
    NO text/letters/watermarks (the title is overlaid separately by Pillow)."""
    cover: str = Field(description="wide: montage các nhân vật chính trong thế giới truyện")
    thumb1: str = Field(description="portrait: nhân vật chính (tuỳ chọn + một nhân vật phụ)")
    thumb2: str = Field(description="portrait: cặp đôi trung tâm / quan hệ then chốt")


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
    goal: str = Field(description="điều nhân vật POV muốn trong cảnh này")
    conflict: str = Field(description="điều cản trở họ")
    outcome: str = Field(description="họ có đạt được không? (success | failure | partial)")
    disaster: str = Field(description="vấn đề mới nảy sinh từ cảnh này")
    characters: list[str] = Field(default=[], description="node keys (C001…) hoặc tên — ai xuất hiện trong cảnh")
    location: str = Field("", description="nhãn địa điểm hoặc node key từ LOCATED_AT")
    speaking_characters: list[str] = Field(default=[], description="tên thế giới mới thực sự NÓI trong cảnh")
    dialogue_nuance: str = Field("", description='sắc thái: tone/mood của trao đổi, VD "cold, clipped confrontation"')
    dialogue_intent: str = Field("", description="hướng đến: thoại phải đạt được gì trong cảnh này")


class ChapterBlueprintOutput(BaseModel):
    purpose: str = Field(description="một câu: vì sao chương này tồn tại?")
    act_position: str = Field(description="Act 1 | Act 2a | Act 2b | Act 3")
    beat_type: str = Field("", description="chức năng cấu trúc: setup | escalation | revelation | setback | turning_point | confrontation | aftermath | resolution — tránh nhiều chương cùng loại")
    state_delta: str = Field("", description="THAY ĐỔI TRẠNG THÁI cụ thể chương phải tạo: cái gì khác biệt thực chất ở dòng cuối vs đầu (quan hệ dịch, bí mật lộ, kế hoạch tiến, ai đó quyết/hành động). Chỉ khơi lại cảm xúc cũ mà không có delta mới = chương 'rỗng'")
    emotional_arc_start: str = Field(description="cảm xúc người đọc lúc mở chương")
    emotional_arc_end: str = Field(description="cảm xúc người đọc lúc đóng chương")
    pov_character: str = Field("", description="đa-POV luân phiên: nhân vật 'giữ' POV chương này (viết cả chương trong đầu họ, đúng ngôi của nguồn). Rỗng = single-POV / theo source_spirit")
    pov_characters: list[str] = Field(default=[], description="CHỈ điền khi chương gốc kể từ >1 POV (đổi giữa chương): tập tên các POV-holder. Rỗng cho chương single-POV thường")
    scenes: list[SceneOut] = Field(description="danh sách cảnh của chương")
    hook: str = Field(description="bản chất chính xác của hook/cliffhanger cuối")
    foreshadowing_to_plant: str | None = Field(None, description="hạt cần gieo (để trả về sau)")
    characters_featured: list[str] = Field(description="character CSV ids xuất hiện trong chương")
    dialogue_intensity: str = Field("balanced", description="heavy | balanced | sparse — chương thoại-dẫn tới đâu. Chương độc thoại-nội tâm là 'sparse' hợp lệ")
    motifs_used: list[str] = Field(default=[], description="tag canonical ngắn (≤5 từ) cho các motif/beat lặp, VD 'possessive-claim'. PHẢI dùng lại tag cũ nguyên văn khi motif tái diễn; chỉ đặt tag mới cho motif thật sự mới")


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
    setting: str = Field(description='bối cảnh, VD "1990s Hong Kong financial district"')
    time_period: str = Field(description='thời kỳ, VD "1994-1997, pre-handover"')
    genre: str = Field(description='thể loại, VD "corporate thriller with political undercurrent"')
    tone: str = Field(description='tông, VD "tense, morally ambiguous, atmospheric"')
    protagonist_archetype: str = Field(description="nhân vật chính THEO VAI (KHÔNG tên riêng) + tình huống họ đối mặt")
    antagonist_archetype: str = Field(description="phản diện THEO VAI (KHÔNG tên riêng) + động cơ đối kháng")
    location_concepts: list[str] = Field(description="3-5 địa điểm then chốt, mỗi cái mô tả THEO VAI + mục đích tự sự, TUYỆT ĐỐI KHÔNG tên riêng")
    thematic_core: str = Field(description="câu hỏi/chân lý trung tâm truyện khám phá")
    narrative_summary: str = Field(description="tóm tắt prose 300-400 từ về truyện MỚI, kể HOÀN TOÀN theo VAI (nhân vật chính, phe đối địch…), KHÔNG tên riêng cho người/nơi/phe, KHÔNG nhắc truyện gốc, viết bằng ngôn ngữ được yêu cầu")


class WorldNameCheckOutput(BaseModel):
    """Verdict from the world-design name checker: which invented PROPER NAMES (of
    people / places / clans / factions / objects) still appear, so the world design
    can be regenerated until it is fully name-free (roles only)."""
    proper_names: list[str] = Field(default=[], description="mọi tên riêng bịa còn sót (rỗng = sạch)")
    note: str = Field("", description="ghi chú tuỳ chọn")


class EntityResolveOutput(BaseModel):
    """Verdict from the entity resolver during source extraction: is a newly-extracted
    node actually the SAME real entity as one already in the graph? A code pre-filter
    finds same-name candidates; this decides if the new node should REUSE an existing
    key (`same_as`) or is genuinely distinct (`same_as` = null)."""
    same_as: str | None = Field(None, description="node_key của thực thể đã có, hoặc null nếu là thực thể mới")
    note: str = Field("", description="ghi chú tuỳ chọn")


class StoryBibleLeakCheckOutput(BaseModel):
    """Second-stage verdict on story-bible name leaks. Python first finds SOURCE names
    that appear as whole words in the new story-bible prose (candidates); this LLM
    pass judges which candidates are ACTUAL leaks — the source character re-appearing
    — vs false positives (a coincidental common word, or a name legitimately reused by
    the new world). Only confirmed real leaks should trigger a rewrite."""
    real_leaks: list[str] = Field(default=[], description="candidates được xác nhận là rò tên nguồn thật")
    note: str = Field("", description="ghi chú tuỳ chọn")


# ── new_graph_builder Phase 1 (name lexicon) ─────────────────────────────────

class NameLexiconEntry(BaseModel):
    node_key: str = Field(description='node_key nguồn, VD "C001"')
    node_type: str = Field(description="character | location | faction | object")
    source_label: str = Field(description="tên gốc, copy nguyên văn từ danh sách node")
    new_label: str = Field(description="tên đầy đủ bạn đặt cho thế giới mới (không dùng lại từ nào của tên gốc)")


class NameLexiconOutput(BaseModel):
    entries: list[NameLexiconEntry] = Field(description="một entry cho mỗi node cần đổi tên")
    world_note: str = Field(description="1-2 câu về quy ước đặt tên đã chọn")

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
    node_key: str = Field(description="node cần vá")
    new_label: str | None = Field(None, description="tên mới (nếu đổi)")
    new_summary: str | None = Field(None, description="EVENT: tóm tắt viết lại")
    new_profile_md: str | None = Field(None, description="CHARACTER: profile viết lại")
    new_description: str | None = Field(None, description="LOCATION/FACTION/THEME/OBJECT: mô tả viết lại")
    new_arc_stage: str | None = Field(None, description="CHARACTER: trạng thái nội tâm hiện tại viết lại")
    new_background: str | None = Field(None, description="CHARACTER: backstory viết lại")
    new_wants: str | None = Field(None, description="CHARACTER: mục tiêu viết lại")
    new_fears: str | None = Field(None, description="CHARACTER: nỗi sợ/điểm yếu viết lại")


class SurfaceEdgePatchOut(BaseModel):
    source_key: str = Field(description="node nguồn của cạnh")
    target_key: str = Field(description="node đích của cạnh")
    edge_type: str = Field(description="RELATION | PARTICIPATES | CAUSES | ARC_CHANGE | FORESHADOWS | LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES")
    new_mechanism: str | None = Field(None, description="CAUSES: cơ chế nhân quả viết lại")
    new_label: str | None = Field(None, description="nhãn cạnh viết lại")
    new_rel_type: str | None = Field(None, description="RELATION: friendship|rivalry|love|family|mentor|debt|alliance|betrayal|distrust")
    new_condition: str | None = Field(None, description="RELATION: text điều kiện/bối cảnh viết lại")
    new_old_val: str | None = Field(None, description="ARC_CHANGE: trạng thái trước viết lại")
    new_new_val: str | None = Field(None, description="ARC_CHANGE: trạng thái sau viết lại")


class NewEdgeForRepairOut(BaseModel):
    source_key: str = Field(description="node nguồn")
    target_key: str = Field(description="node đích")
    edge_type: str = Field(description="PARTICIPATES | RELATION | LOCATED_AT | INVOLVES | OWNS | MEMBER_OF | EMBODIES")
    label: str = Field("", description="nhãn cạnh")
    rel_type: str | None = Field(None, description="dành cho RELATION")
    role: str | None = Field(None, description="dành cho PARTICIPATES: cause|victim|witness|ally|bystander")
    chapter_from: int | None = Field(None, description="chương bắt đầu (nếu có)")


class GraphSurfaceRepairOutput(BaseModel):
    node_patches: list[SurfaceNodePatchOut] = Field(default=[], description="vá node")
    edge_patches: list[SurfaceEdgePatchOut] = Field(default=[], description="vá cạnh")
    add_edges: list[NewEdgeForRepairOut] = Field(default=[], description="cạnh còn thiếu cần tạo mới")
    repair_note: str = Field(description="ghi chú ngắn về những gì đã sửa")


# ── new_graph_builder per-group enrichment ─────────────────────────────────────

class CharacterSurfaceOut(BaseModel):
    node_key: str = Field(description="node nhân vật cần enrich")
    new_role: str = Field(
        default="",
        description="ĐÚNG MỘT TRONG: protagonist | antagonist | love_interest | "
                    "supporting | minor. Không giá trị khác, không nhãn ghép.",
    )
    new_gender: str = Field("", description="male|female|nonbinary — khớp tên mới + mọi đại từ dùng ở arc/background/voice/appearance")
    new_arc_stage: str = Field(description="trạng thái nội tâm hiện tại (thế giới mới)")
    new_wants: str = Field(description="mục tiêu/khao khát (thế giới mới)")
    new_fears: str = Field(description="nỗi sợ/điểm yếu (thế giới mới)")
    new_background: str = Field("", description="backstory (thế giới mới)")
    new_speech_pattern: str = Field("", description="một dòng ngắn (cột CSV)")
    new_voice_profile: str = Field("", description='hướng dẫn giọng nhiều dòng theo format "REGISTER: …\\nVOCABULARY: …\\nRHYTHM: …\\nTIC/TELL: …\\nSAMPLE LINES:\\n- \\"…\\"\\n- \\"…\\"" — thế giới mới, không lấy prose gốc')
    new_appearance: str = Field("", description="ngoại hình vật lý ĐẶC TRƯNG cho ảnh bìa: một cá thể cụ thể (heritage, dáng mặt, tóc, màu mắt, 1-2 nét dễ nhớ) khác gương mặt mặc định")


class CharacterGroupEnrichOutput(BaseModel):
    characters: list[CharacterSurfaceOut] = Field(description="mỗi nhân vật một entry")
    note: str = Field("", description="ghi chú tuỳ chọn")


class EventSurfaceOut(BaseModel):
    node_key: str = Field(description="node sự kiện")
    new_summary: str = Field(description="tóm tắt sự kiện đọc như một beat của thế giới mới")


class EventGroupEnrichOutput(BaseModel):
    events: list[EventSurfaceOut] = Field(description="mỗi sự kiện một entry")
    note: str = Field("", description="ghi chú tuỳ chọn")


class ArcChangeSurfaceOut(BaseModel):
    source_key: str = Field(description="node nhân vật (self-loop ARC_CHANGE)")
    chapter_from: int | None = Field(description="chương xảy ra thay đổi")
    new_old_val: str = Field(description="trạng thái trước (thế giới mới)")
    new_new_val: str = Field(description="trạng thái sau (thế giới mới)")


class ArcChangeGroupEnrichOutput(BaseModel):
    arc_changes: list[ArcChangeSurfaceOut] = Field(description="mỗi arc-change một entry")
    note: str = Field("", description="ghi chú tuỳ chọn")


class RelationSurfaceOut(BaseModel):
    source_key: str = Field(description="node nhân vật nguồn")
    target_key: str = Field(description="node nhân vật đích")
    chapter_from: int | None = Field(description="chương quan hệ này bắt đầu")
    new_rel_type: str = Field(description="friendship|rivalry|love|family|mentor|debt|alliance|betrayal|distrust")
    new_label: str = Field(description="mô tả ngắn quan hệ (thế giới mới)")


class RelationGroupEnrichOutput(BaseModel):
    relations: list[RelationSurfaceOut] = Field(description="mỗi quan hệ một entry")
    note: str = Field("", description="ghi chú tuỳ chọn")


class CausesSurfaceOut(BaseModel):
    source_key: str = Field(description="node sự kiện nguyên nhân")
    target_key: str = Field(description="node sự kiện kết quả")
    new_mechanism: str = Field(description="cơ chế nhân quả (thế giới mới)")
    new_label: str = Field("", description="nhãn ngắn cho liên kết")


class CausesGroupEnrichOutput(BaseModel):
    causes: list[CausesSurfaceOut] = Field(description="mỗi liên kết nhân quả một entry")
    note: str = Field("", description="ghi chú tuỳ chọn")


# ── graph_enrich_verifier (per-enricher quality/logic/naming gate) ─────────────

class GraphEnrichIssueOut(BaseModel):
    """One problem the enrich-verifier found in a group's enriched output."""
    target: str = Field("", description='node_key/cạnh liên quan, VD "C003" hoặc "C003→C007"')
    dimension: Literal["naming", "logic", "quality"] = Field("naming", description="loại lỗi")
    problem: str = Field(description="lỗi là gì")
    fix: str = Field("", description="chỉ dẫn cụ thể để re-enrich")


class GraphEnrichVerifyOutput(BaseModel):
    """Verdict on one enrichment group. `issues` empty ⇒ the group passed."""
    issues: list[GraphEnrichIssueOut] = Field(default=[], description="rỗng ⇒ nhóm đạt")
    note: str = Field("", description="ghi chú tuỳ chọn")


# ── graph_verifier ─────────────────────────────────────────────────────────────

class GraphVerifyIssueOut(BaseModel):
    check_type: Literal["narrative_logic", "reskin", "enrichment", "cast_role"] = Field("narrative_logic", description="loại kiểm tra bị vi phạm")
    node_key: str | None = Field(None, description="node có vấn đề (null nếu chung)")
    edge_desc: str | None = Field(None, description='mô tả cạnh, VD "C001→C002 CAUSES Ch.5"')
    description: str = Field(description="mô tả vấn đề")
    suggestion: str = Field(description="cách sửa đề xuất")
    severity: Literal["critical", "minor"] = Field(description="critical | minor")


class GraphVerifierOutput(BaseModel):
    issues: list[GraphVerifyIssueOut] = Field(default=[], description="mọi vấn đề tìm thấy ở graph mới")
    verdict_note: str = Field(description="kết luận ngắn")


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
