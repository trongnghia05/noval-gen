"""Pydantic schemas for the structured (JSON-mode) agent outputs.

chapter_writer stays free-form prose — everything else feeds directly into DB
tables or CSV files, so it's requested and parsed as JSON.

Split by what the schema is FOR, not by which agent emits it: several agents share
the graph shapes, and one agent (new_graph_builder) touches three of the four
modules. Everything is re-exported here, so `from ..schemas import X` keeps working
and no caller needs to know which module a schema lives in — that also means moving
one between modules later costs nothing outside this package.
"""

from .chapter import (  # noqa: F401
    ChapterBlueprintOutput,
    ChapterSummaryOutput,
    ChapterVerifierOutput,
    ChapterVerifyIssueOut,
    ChapterWriterOutput,
    CharacterStateUpdateOut,
    ContinuityEditorOutput,
    ContinuityIssueOut,
    ForeshadowingOut,
    PlotThreadOut,
    QualityReviewIssueOut,
    QualityReviewerOutput,
    RelationshipChangeOut,
    SceneOut,
    SmartPlannerOutput,
    StateChangeOut,
    TimelineEventOut,
    WorldStateRowOut,
)
from .graph import (  # noqa: F401
    ArcChangeGroupEnrichOutput,
    ArcChangeSurfaceOut,
    CausesGroupEnrichOutput,
    CausesSurfaceOut,
    ChapterGraphOutput,
    CharacterGroupEnrichOutput,
    CharacterSurfaceOut,
    EdgeAddOut,
    EdgeDeleteOut,
    EdgeSurfaceOut,
    EntityResolveOutput,
    EventGroupEnrichOutput,
    EventSurfaceOut,
    GraphEdgeOut,
    GraphEnrichIssueOut,
    GraphEnrichVerifyOutput,
    GraphEnrichmentOutput,
    GraphNodeOut,
    GraphRepairOutput,
    GraphSurfaceRepairOutput,
    GraphVerifierOutput,
    GraphVerifyIssueOut,
    NameLexiconEntry,
    NameLexiconOutput,
    NewEdgeForRepairOut,
    NewGraphSurfaceOutput,
    NodeSurfaceOut,
    NodeUpdateOut,
    RelationGroupEnrichOutput,
    RelationSurfaceOut,
    StoryAnalyzerOutput,
    StoryBibleLeakCheckOutput,
    SurfaceEdgePatchOut,
    SurfaceNodePatchOut,
    WorldDesignOutput,
    WorldNameCheckOutput,
)
from .planning import (  # noqa: F401
    CharacterDeveloperOutput,
    CharacterGraphInitOut,
    CharacterOut,
    GlossaryTermOut,
    PlanningVerifierOutput,
    PlanningVerifyIssueOut,
    PlotChapterOut,
    PlotOutlineOut,
    PlotSceneOut,
    WorldBibleOut,
    WorldFactionOut,
    WorldLocationOut,
    WorldSystemOut,
)
from .publishing import (  # noqa: F401
    CharacterBlurbOut,
    ImagePromptIssueOut,
    ImagePromptSetOut,
    ImagePromptVerifyOut,
    NovelMetadataOut,
)
