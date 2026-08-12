import logging

from sqlalchemy.orm import Session

from . import _common
from .. import length_calc
from ..db.models import Story, StoryGraphEdge, StoryGraphNode
from ..schemas import StoryAnalyzerOutput

logger = logging.getLogger(__name__)


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
    user_content = (
        f"Language: {story.language}\n"
        f"Input type: {story.input_type}\n"
        f"Genre: {story.genre or '(choose one yourself)'}\n"
        f"Target length: {story.total_chapters} chapters, "
        f"{story.target_words} words total, ~{story.words_per_chapter} words/chapter\n\n"
        f"The user's input:\n---\n{story.source_content}\n---\n"
    )
    if feedback:
        user_content += f"{_common.FEEDBACK_HEADER}{feedback}\n"

    # REWRITE: no EVENT nodes needed here — just stable entities.
    # IDEA/PREMISE: entities + ~4 arc EVENT nodes → output is small.
    max_tokens = 65536

    output: StoryAnalyzerOutput = _common.call_agent(
        "story_analyzer",
        user_content=user_content,
        schema=StoryAnalyzerOutput,
        max_tokens=max_tokens,
        thinking=True,
    )

    story.story_bible = output.narrative_summary
    if output.source_spirit:
        story.source_spirit = output.source_spirit

    # For REWRITE, source_chapter_count MUST equal the number of chunks
    # split_source_chapters() produces — the graph_extract loop indexes that
    # exact array (source_chapters[extracted_count]). Trusting the model's
    # self-reported count instead would either over-count → IndexError crash, or
    # under-count → source chapters silently never extracted. The regex split is
    # the single ground truth; the model's count is only logged as a cross-check.
    if story.input_type == "REWRITE":
        actual = len(length_calc.split_source_chapters(story.source_content))
        story.source_chapter_count = actual
        reported = output.source_chapter_count
        if reported and reported != actual:
            logger.warning(
                "[%s] story_analyzer: model reported %d source chapters but split found %d — using %d",
                story.slug, reported, actual, actual,
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
