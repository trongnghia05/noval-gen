"""Local reviser — fixes ONLY the flagged issues in an existing chapter, keeping
everything else byte-for-byte. Cheaper and more stable than regenerating the whole
chapter: it preserves the good prose and can't introduce brand-new content drift.

Used as the FIRST repair strategy in the verify loop; if local revision still
can't clear critical issues after a few tries, the loop falls back to a full
rewrite via chapter_writer.
"""

import logging
import re

from sqlalchemy.orm import Session

from .. import context_builder, csv_graph
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

    # Reference truth so the reviser can JUDGE whether each flagged issue is real
    # (reject false positives) and fix the real ones CORRECTLY — not guess. Same
    # blocks the writer/quality_reviewer see, scoped to what fixes typically need.
    graph_section = ""
    pov_line = ""
    if story.new_graph_built:
        graph_section = (
            "\n## chapter graph constraints (source of truth for events/identities)\n"
            + context_builder.format_chapter_subgraph(
                session, story.id, chapter.number, graph_type="new", max_depth=1
            )
            + "\n"
        )
        try:
            import json as _json
            pov_line = chapter_writer._pov_directive(_json.loads(chapter.blueprint)) if chapter.blueprint else ""
        except Exception:
            pov_line = ""

    gender_roster = context_builder.format_gender_roster(session, story.id) if story.new_graph_built else ""
    gender_section = (
        f"\n## Character genders (correct pronouns — the truth to check gender flags against)\n{gender_roster}\n"
        if gender_roster and gender_roster != "(no gender data)" else ""
    )
    voices = csv_graph.get_character_voices(story.id) if csv_graph.graph_exists(story.id) else ""
    voices_section = f"\n## Character voices (the truth to check voice/dialogue flags against)\n{voices}\n" if voices else ""
    world_section = (
        f"\n## world.md (the truth to check world/anachronism flags against)\n{(story.world_bible or '')[:2000]}\n"
        if story.world_bible else ""
    )
    pov_section = f"\n## POV contract (the truth to check POV flags against)\n{pov_line}\n" if pov_line else ""

    user_content = (
        f"language: {story.language}\n"
        f"chapter_number: {chapter.number}\n\n"
        f"## ISSUES FLAGGED (verify each against the reference truth below; fix only the REAL ones)\n{feedback}\n"
        f"{graph_section}{gender_section}{voices_section}{pov_section}{world_section}\n"
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
    if output.title:
        chapter.title = re.sub(r"^\s*(?:chapter|chương)\s*\d+\s*[:.\-–]\s*", "",
                               output.title, flags=re.IGNORECASE).strip() or output.title
    chapter.word_count = chapter_writer._word_count(chapter.content)
    logger.info("[%s] chapter_reviser DONE ch%d: %d words",
                story.slug, chapter.number, chapter.word_count)
    return output
