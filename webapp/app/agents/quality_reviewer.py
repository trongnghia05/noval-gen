"""Per-chapter quality gate. Three dimensions:
- quality: prose quality, word count, coherence (all input types)
- world_consistency: no anachronisms vs world.md/story-bible (all input types)
- graph_consistency: chapter content matches the new graph's planned event (REWRITE only)

Source chapter text is NOT passed here — originality checking against the source
belongs at the new_graph_builder level, not the writing phase. The reviewer only
knows the new story's world and its planned graph event.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import QualityReviewIssueOut, QualityReviewerOutput

logger = logging.getLogger(__name__)


def check(session: Session, story: Story, chapter: Chapter) -> list[QualityReviewIssueOut]:
    system = load_prompt("quality_reviewer")

    # For REWRITE, include the new graph's planned event node for this chapter
    graph_event_section = ""
    if story.input_type == "REWRITE" and story.new_graph_built:
        event_context = context_builder.format_chapter_context_from_graph(
            session, story.id, chapter.number
        )
        graph_event_section = (
            f"\n## New graph — planned event for this chapter\n"
            f"(Check that the chapter follows this plan, not the source story)\n"
            f"---\n{event_context}\n---\n"
        )

    user_content = (
        f"language: {story.language}\n"
        f"input_type: {story.input_type}\n"
        f"chapter_number: {chapter.number}\n"
        f"words_per_chapter (target): {story.words_per_chapter}\n"
        f"word_count actual: {chapter.word_count}\n\n"
        f"## world.md — new story world (standard for world-consistency)\n"
        f"---\n{story.world_bible or '(not yet available)'}\n---\n\n"
        f"## story-bible.md — tone, setting, genre of new story\n"
        f"---\n{story.story_bible or '(not yet available)'}\n---\n"
        f"{graph_event_section}\n"
        f"## Chapter just written (title: {chapter.title})\n"
        f"---\n{chapter.content}\n---\n"
    )

    output: QualityReviewerOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["quality_reviewer"],
        schema=QualityReviewerOutput,
        max_tokens=8192,
        thinking=False,
    )
    critical = [i for i in output.issues if i.severity == "critical"]
    logger.info("[%s] quality_reviewer ch%d: %d issues (%d critical)",
                story.slug, chapter.number, len(output.issues), len(critical))
    for issue in output.issues:
        logger.info("  [%s][%s] %s | fix: %s",
                    issue.severity.upper(), getattr(issue, "dimension", "quality"),
                    issue.description, issue.suggestion)
    return output.issues
