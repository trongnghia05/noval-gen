from sqlalchemy.orm import Session

from .. import length_calc
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import StoryAnalyzerOutput


def _clear_graph(session: Session, story_id: int) -> None:
    session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="source").delete()
    session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="source").delete()


def run(session: Session, story: Story, feedback: str | None = None) -> None:
    """Extract stable entities into the story graph + write narrative summary.

    REWRITE : extracts CHARACTER / LOCATION / FACTION / THEME / OBJECT nodes only.
              EVENT nodes are extracted one-per-chapter by chapter_graph_extractor,
              which runs after this step. source_chapter_count is set here so the
              orchestrator knows how many graph_extract steps to dispatch.

    IDEA/PREMISE : extracts entities + high-level planned arc EVENT nodes
                   (Act 1 end, midpoint, Act 2 end, climax — not per-chapter detail).
                   No chapter_graph_extractor step follows.
    """
    system = load_prompt("story_analyzer")
    user_content = (
        f"Ngôn ngữ: {story.language}\n"
        f"Loại input: {story.input_type}\n"
        f"Thể loại: {story.genre or '(AI tự chọn)'}\n"
        f"Độ dài mục tiêu: {story.total_chapters} chương, "
        f"{story.target_words} từ tổng, ~{story.words_per_chapter} từ/chương\n\n"
        f"Nội dung input của user:\n---\n{story.source_content}\n---\n"
    )
    if feedback:
        user_content += (
            "\n## LỖI TỪ VÒNG KIỂM TRA TRƯỚC — bắt buộc khắc phục, giữ nguyên phần đã đúng\n"
            f"{feedback}\n"
        )

    # REWRITE: no EVENT nodes needed here — just stable entities.
    # IDEA/PREMISE: entities + ~4 arc EVENT nodes → output is small.
    max_tokens = 65536

    output: StoryAnalyzerOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["story_analyzer"],
        schema=StoryAnalyzerOutput,
        max_tokens=max_tokens,
        thinking=True,
    )

    story.story_bible = output.narrative_summary
    if output.source_spirit:
        story.source_spirit = output.source_spirit

    # For REWRITE, store source chapter count so orchestrator knows how many
    # graph_extract steps to run. Use the model's self-reported count if given;
    # fall back to regex-based detection from the raw source text.
    if story.input_type == "REWRITE":
        if output.source_chapter_count and output.source_chapter_count > 0:
            story.source_chapter_count = output.source_chapter_count
        else:
            story.source_chapter_count = len(
                length_calc.split_source_chapters(story.source_content)
            )

    # Repopulate entity nodes (clear first — safe pre-WRITING, idempotent on regen).
    _clear_graph(session, story.id)

    for node in output.nodes:
        session.add(StoryGraphNode(
            story_id=story.id,
            graph_type="source",
            node_key=node.id,
            node_type=node.node_type,
            label=node.label,
            properties=node.properties or {},
            chapter_introduced=node.chapter_introduced,
        ))
    session.flush()

    for edge in output.edges:
        session.add(StoryGraphEdge(
            story_id=story.id,
            graph_type="source",
            source_key=edge.source_id,
            target_key=edge.target_id,
            edge_type=edge.edge_type,
            label=edge.label,
            chapter_from=edge.chapter_from,
            chapter_to=edge.chapter_to,
            trigger_event_key=edge.trigger_event_id,
            condition=edge.condition,
            properties=edge.properties or {},
        ))
