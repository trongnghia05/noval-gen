"""Local reviser — fixes ONLY the flagged issues in an existing chapter, keeping
everything else byte-for-byte. Cheaper and more stable than regenerating the whole
chapter: it preserves the good prose and can't introduce brand-new content drift.

Used as the FIRST repair strategy in the verify loop; if local revision still
can't clear critical issues after a few tries, the loop falls back to a full
rewrite via chapter_writer.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterWriterOutput
from . import chapter_writer

logger = logging.getLogger(__name__)


def run(session: Session, story: Story, chapter: Chapter, feedback: str) -> ChapterWriterOutput:
    """Apply a targeted, local fix for `feedback` to the chapter's current prose,
    changing only the flagged spots. Overwrites chapter.content and returns the
    finalized output (title/content/short_summary/hook)."""
    logger.info("[%s] chapter_reviser ch%d/%d [local fix]",
                story.slug, chapter.number, story.total_chapters)

    heading = f"# Chương {chapter.number}: {chapter.title or ''}".rstrip()
    current = f"{heading}\n\n{chapter.content or ''}"

    system = load_prompt("chapter_reviser")

    # New-graph constraints help it fix factual/identity issues correctly.
    graph_section = ""
    if story.new_graph_built:
        graph_section = (
            "\n## chapter graph constraints (source of truth for events/identities)\n"
            + context_builder.format_chapter_subgraph(
                session, story.id, chapter.number, graph_type="new", max_depth=1
            )
            + "\n"
        )

    user_content = (
        f"language: {story.language}\n"
        f"chapter_number: {chapter.number}\n\n"
        f"## ISSUES TO FIX (change ONLY these; keep everything else identical)\n{feedback}\n"
        f"{graph_section}\n"
        f"## CURRENT CHAPTER (revise in place)\n---\n{current}\n---\n"
    )

    output: ChapterWriterOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_writer"],
        schema=ChapterWriterOutput,
        max_tokens=40000,
        thinking=False,
    )

    chapter.content = chapter_writer.normalize_paragraphs(output.content)
    chapter.title = output.title or chapter.title
    chapter.word_count = chapter_writer._word_count(chapter.content)
    logger.info("[%s] chapter_reviser DONE ch%d: %d words",
                story.slug, chapter.number, chapter.word_count)
    return output
