import logging

from sqlalchemy.orm import Session

from . import _common
from ..services import context_builder
from ..db.models import Chapter, Story
from ..schemas import ChapterVerifierOutput, ChapterVerifyIssueOut

logger = logging.getLogger(__name__)


def check(
    session: Session,
    story: Story,
    chapter: Chapter,
    graph_context: str = "",
) -> list[ChapterVerifyIssueOut]:
    """Run continuity check on the just-written chapter.

    Returns all issues found (critical + minor). Logging and rewrite logic
    live in the orchestrator's verification loop — this function only checks.

    graph_context: BFS subgraph from the new graph's event node for this chapter
    (characters, relations, arc changes, causal chains) — used to cross-check
    structured graph state in addition to the flat world-state snapshot.
    """
    graph_section = (
        f"\n## Story graph (new) — structured state to cross-check against\n{graph_context}\n"
        if graph_context
        else ""
    )
    blueprint_section = (
        f"\n## Blueprint — the approved plan for this chapter\n{chapter.blueprint}\n"
        if chapter.blueprint
        else ""
    )
    user_content = (
        f"language: {story.language}\n"
        f"chapter_number: {chapter.number}\n\n"
        f"## world.md (the world definition — the standard for world-consistency)\n"
        f"{story.world_bible or '(none yet)'}\n\n"
        f"## story-bible.md (tone, genre, theme)\n"
        f"{story.story_bible or '(none yet)'}\n\n"
        f"## Characters (full)\n{context_builder.format_characters(session, story.id)}\n\n"
        f"## world-state as it stands\n{context_builder.format_world_state(session, story.id)}\n\n"
        f"## Open continuity problems (from the last deep pass, if any)\n"
        f"{context_builder.format_continuity_log(session, story.id)}\n"
        f"{graph_section}"
        f"{blueprint_section}\n"
        f"## The last 3 chapters (including the one just written, "
        f"Ch.{max(1, chapter.number - 2)}-{chapter.number})\n"
        f"{context_builder.last_n_chapters_text(session, story.id, chapter.number, n=3)}\n"
    )
    output: ChapterVerifierOutput = _common.call_agent(
        "chapter_verifier",
        user_content=user_content,
        schema=ChapterVerifierOutput,
        max_tokens=8192,
        thinking=False,
    )
    critical = [i for i in output.issues if i.severity == "critical"]
    logger.info("[%s] chapter_verifier ch%d: %d issues (%d critical)",
                story.slug, chapter.number, len(output.issues), len(critical))
    for issue in output.issues:
        logger.info("  [%s] %s | fix: %s", issue.severity.upper(), issue.description, issue.suggestion)
    return output.issues
