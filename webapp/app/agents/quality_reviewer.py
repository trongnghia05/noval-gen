"""Per-chapter quality gate. Three dimensions:
- quality: prose quality, word count, coherence (all input types)
- world_consistency: no anachronisms vs world.md/story-bible (all input types)
- graph_consistency: chapter content matches the new graph's planned event (REWRITE only)

Source chapter text is NOT passed here — originality checking against the source
belongs at the new_graph_builder level, not the writing phase. The reviewer only
knows the new story's world and its planned graph event.
"""

import json
import logging

from sqlalchemy.orm import Session

from .. import context_builder, csv_graph
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import QualityReviewIssueOut, QualityReviewerOutput

logger = logging.getLogger(__name__)


def _dialogue_plan_block(chapter: Chapter) -> str:
    """The blueprint's per-scene dialogue plan, for the reviewer to check against."""
    if not chapter.blueprint:
        return ""
    try:
        bp = json.loads(chapter.blueprint)
    except Exception:
        return ""
    lines = [f"dialogue_intensity: {bp.get('dialogue_intensity', 'balanced')}"]
    for i, s in enumerate(bp.get("scenes", [])):
        speakers = s.get("speaking_characters") or []
        if speakers:
            lines.append(
                f"- Scene {i+1}: speakers={', '.join(speakers)}"
                + (f" | tone={s['dialogue_nuance']}" if s.get("dialogue_nuance") else "")
                + (f" | must achieve={s['dialogue_intent']}" if s.get("dialogue_intent") else "")
            )
        else:
            lines.append(f"- Scene {i+1}: (no dialogue planned — interiority/action)")
    return "\n".join(lines)


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

    # Dialogue verification inputs: the planned dialogue, the valid character
    # roster (only these may speak/appear), and each character's voice profile.
    dialogue_plan = _dialogue_plan_block(chapter)
    valid_roster = context_builder.format_character_aliases(session, story.id)
    voices = ""
    if csv_graph.graph_exists(story.id):
        voices = csv_graph.get_character_voices(story.id)
    dialogue_section = (
        f"\n## Dialogue plan for this chapter (from blueprint — the contract to check)\n"
        f"---\n{dialogue_plan or '(no plan)'}\n---\n"
        f"\n## Valid character roster (ONLY these characters may appear/speak — anyone else is invalid)\n"
        f"---\n{valid_roster or '(none)'}\n---\n"
        f"\n## Character voices (each speaker must match their own voice)\n"
        f"---\n{voices or '(not available)'}\n---\n"
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
        f"{graph_event_section}"
        f"{dialogue_section}\n"
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
