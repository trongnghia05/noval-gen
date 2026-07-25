import json
import logging
import re
import textwrap

from sqlalchemy.orm import Session

from .. import context_builder, csv_graph
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..prompts.loader import load_prompt

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(r"^#\s*\S+\.?\s*\d+\s*[:.]?\s*(.+)$")
_HEADING_RE = re.compile(r"^#+\s+.+$")


def _strip_leading_heading(text: str) -> str:
    """Remove a chapter/scene heading from the first line if the model added one
    despite instructions not to (non-first scenes should start with prose)."""
    lines = text.split("\n")
    if lines and _HEADING_RE.match(lines[0].strip()):
        return "\n".join(lines[1:]).lstrip()
    return text

MIN_WORD_RATIO = 0.85
# Expand a single scene inline if it falls below this fraction of its per-scene target.
MIN_SCENE_WORD_RATIO = 0.60
MAX_EXPAND_ATTEMPTS = 2


def _parse_chapter(text: str, chapter_number: int) -> tuple[str, str]:
    text = text.strip()
    first_line, _, rest = text.partition("\n")
    match = _TITLE_RE.match(first_line.strip())
    title = match.group(1).strip() if match else f"Chapter {chapter_number}"
    content = rest.strip() if match else text
    return title, content


def _format_blueprint(chapter: Chapter) -> str:
    if not chapter.blueprint:
        return "(no blueprint — write using your best judgment)"
    try:
        bp = json.loads(chapter.blueprint)
        scenes = "\n".join(
            f"  Scene {i+1}: goal={s['goal']} | conflict={s['conflict']} "
            f"| outcome={s['outcome']} | disaster={s['disaster']}"
            for i, s in enumerate(bp.get("scenes", []))
        )
        plant = bp.get("foreshadowing_to_plant") or "none"
        return (
            f"PURPOSE: {bp.get('purpose')}\n"
            f"ACT: {bp.get('act_position')} | "
            f"Emotion start: {bp.get('emotional_arc_start')} → end: {bp.get('emotional_arc_end')}\n"
            f"SCENES:\n{scenes}\n"
            f"HOOK: {bp.get('hook')}\n"
            f"FORESHADOWING TO PLANT: {plant}"
        )
    except Exception:
        return chapter.blueprint


def _build_shared_context(session: Session, story: Story, chapter: "Chapter") -> str:
    """Heavy reference blocks shared across all per-scene calls (no blueprint section)."""
    graph_section = ""
    if csv_graph.graph_exists(story.id):
        graph_section = (
            "\n## character-graph (live states — use as ground truth)\n"
            + csv_graph.format_characters(story.id)
            + "\n\n## relationships\n"
            + csv_graph.format_relationships(story.id)
            + "\n\n## open plot threads (advance or acknowledge at least one)\n"
            + csv_graph.format_open_threads(story.id)
            + "\n\n## character voices (follow strictly)\n"
            + csv_graph.get_character_voices(story.id)
            + "\n"
        )

    source_spirit_section = context_builder.format_source_spirit_for_chapter(session, story, chapter.number)
    spirit_block = f"\n\n{source_spirit_section}" if source_spirit_section else ""

    return (
        f"## chapter-list (bức tranh toàn cảnh — Ch.{chapter.number} là chương đang viết)\n"
        f"{context_builder.format_chapter_list(session, story.id, chapter.number)}\n\n"
        f"## world-state.md\n{context_builder.format_world_state(session, story.id)}\n\n"
        f"## continuity-log.md\n{context_builder.format_continuity_log(session, story.id)}\n\n"
        f"## Smart-planner adjustments\n{context_builder.format_smart_planner_adjustments(session, story.id)}\n\n"
        f"## plot-outline.md\n{story.plot_outline}\n\n"
        f"## characters.md (full profiles)\n{context_builder.format_characters(session, story.id)}\n\n"
        f"## world.md\n{story.world_bible}\n\n"
        f"## story-bible.md (tone, theme)\n{story.story_bible}\n"
        f"{graph_section}"
        f"{spirit_block}"
    )


def _build_context(session: Session, story: Story, chapter: Chapter) -> str:
    """Full context for single-call fallback (includes blueprint)."""
    graph_section = ""
    if csv_graph.graph_exists(story.id):
        graph_section = (
            "\n## character-graph (live states — use this as ground truth for where characters are)\n"
            + csv_graph.format_characters(story.id)
            + "\n\n## relationships (current strengths)\n"
            + csv_graph.format_relationships(story.id)
            + "\n\n## open plot threads (must advance or acknowledge at least one)\n"
            + csv_graph.format_open_threads(story.id)
            + "\n\n## character voices (how each character speaks — follow strictly)\n"
            + csv_graph.get_character_voices(story.id)
            + "\n"
        )

    source_spirit_section = context_builder.format_source_spirit_for_chapter(session, story, chapter.number)
    spirit_block = f"\n\n{source_spirit_section}" if source_spirit_section else ""

    return (
        f"chapter_number: {chapter.number}\n"
        f"words_per_chapter: {story.words_per_chapter}\n"
        f"language: {story.language}\n\n"
        f"## CHAPTER BLUEPRINT (follow this structure)\n{_format_blueprint(chapter)}\n"
        f"{graph_section}\n"
        f"## chapter-list (bức tranh toàn cảnh — Ch.{chapter.number} là chương đang viết)\n"
        f"{context_builder.format_chapter_list(session, story.id, chapter.number)}\n\n"
        f"## world-state.md (snapshot hiện tại)\n{context_builder.format_world_state(session, story.id)}\n\n"
        f"## continuity-log.md\n{context_builder.format_continuity_log(session, story.id)}\n\n"
        f"## Điều chỉnh outline từ smart-planner (nếu có)\n{context_builder.format_smart_planner_adjustments(session, story.id)}\n\n"
        f"## plot-outline.md\n{story.plot_outline}\n\n"
        f"## characters.md (full profiles)\n{context_builder.format_characters(session, story.id)}\n\n"
        f"## world.md\n{story.world_bible}\n\n"
        f"## story-bible.md (tone, chủ đề)\n{story.story_bible}\n"
        f"{spirit_block}"
    )


def _write_single_scene(
    system: str,
    story: Story,
    chapter: Chapter,
    blueprint: dict,
    scene_index: int,
    total_scenes: int,
    scene_data: dict,
    previous_scenes: list[str],
    shared_context: str,
    words_per_scene: int,
) -> str:
    """One LLM call for one scene. Previous scenes are explicit in the prompt so the
    model cannot repeat content that is already on the page."""
    is_first = scene_index == 0

    bp_overview = (
        f"CHAPTER PURPOSE: {blueprint.get('purpose')}\n"
        f"ACT: {blueprint.get('act_position')} | "
        f"Emotion: {blueprint.get('emotional_arc_start')} → {blueprint.get('emotional_arc_end')}\n"
        f"HOOK (use in scene 1 opening only): {blueprint.get('hook')}\n"
        f"FORESHADOWING TO PLANT: {blueprint.get('foreshadowing_to_plant') or 'none'}"
    )

    scene_spec = (
        f"Scene {scene_index + 1} of {total_scenes}:\n"
        f"  goal: {scene_data['goal']}\n"
        f"  conflict: {scene_data['conflict']}\n"
        f"  outcome: {scene_data['outcome']}\n"
        f"  disaster: {scene_data['disaster']}"
    )

    heading_instruction = (
        f'Start line 1 with the chapter heading: "# Chapter {chapter.number}: [Your invented title]"\n'
        if is_first else
        "Do NOT add any heading — begin prose immediately.\n"
    )

    if previous_scenes:
        prev_block = (
            "\n## CONTENT ALREADY WRITTEN THIS CHAPTER"
            " (read carefully — do NOT repeat, mirror, or re-describe any of these events):\n\n"
            + "\n\n[--- scene break ---]\n\n".join(previous_scenes)
            + "\n\n[END OF PREVIOUSLY WRITTEN CONTENT — your words continue directly from here]\n"
        )
    else:
        prev_block = ""

    user_content = (
        f"chapter_number: {chapter.number}\n"
        f"language: {story.language}\n"
        f"target_words_for_this_scene: ~{words_per_scene}\n\n"
        f"## CHAPTER BLUEPRINT\n{bp_overview}\n\n"
        f"## CURRENT SCENE TO WRITE\n{scene_spec}\n\n"
        f"{shared_context}"
        f"{prev_block}\n\n"
        f"## WRITING INSTRUCTIONS\n"
        f"{heading_instruction}"
        f"Write ONLY scene {scene_index + 1} (~{words_per_scene} words). "
        f"Every event listed under 'CONTENT ALREADY WRITTEN THIS CHAPTER' is done — "
        f"do not restate, re-describe, or echo any of it. Move the story forward.\n"
    )

    max_tokens = 40000
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_writer"],
        max_tokens=max_tokens,
        thinking=True,
    )
    return response.text.strip()


def _expand_scene(
    system: str,
    story: Story,
    chapter: Chapter,
    scene_index: int,
    scene_text: str,
    words_per_scene: int,
) -> str:
    """Deepen a scene that came back too short — more detail, same events, same order."""
    current_words = len(scene_text.split())
    user_content = (
        f"chapter_number: {chapter.number} | language: {story.language}\n\n"
        f"## SHORT SCENE (scene {scene_index + 1}, {current_words} words — target ~{words_per_scene})\n"
        f"---\n{scene_text}\n---\n\n"
        f"Expand this scene to ~{words_per_scene} words by adding:\n"
        f"- Sensory details and atmosphere\n"
        f"- Internal monologue revealing character emotions\n"
        f"- Dialogue with action beats\n"
        f"Keep every event in the SAME order. Do NOT add new plot events. "
        f"Do NOT add a heading unless the original already had one. "
        f"Return the full expanded scene only.\n"
    )
    max_tokens = 40000
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_writer"],
        max_tokens=max_tokens,
        thinking=True,
    )
    return response.text.strip()


_SYNTHESIS_SYSTEM = """\
You are a prose editor. A chapter draft was assembled from independently-written scenes.
Your job: polish it into one seamless chapter.

Rules:
1. Remove any verbatim duplicate sentences or paragraphs (keep the first occurrence).
2. Smooth transitions between scenes — the "---scene-break---" markers show where scenes were joined; replace each marker with natural prose flow (a line break, a transitional sentence, or a section break as fits the tone).
3. Do NOT add new plot events, characters, or facts not already in the draft.
4. Do NOT change character names, outcomes, or any established story detail.
5. Keep the chapter heading on line 1 exactly as written.
6. Return ONLY the polished chapter text — no commentary, no explanation.
"""


def _synthesize_chapter(story: "Story", chapter: "Chapter", draft: str) -> str:
    """Polish the scene-assembled draft: remove duplicates, smooth transitions."""
    user_content = (
        f"chapter_number: {chapter.number} | language: {story.language} "
        f"| target_words: ~{story.words_per_chapter}\n\n"
        f"## DRAFT\n---\n{draft}\n---\n\n"
        f"Return the complete polished chapter starting with the heading.\n"
    )
    response = PROVIDER.generate(
        system=_SYNTHESIS_SYSTEM,
        user_content=user_content,
        model=AGENT_MODELS["chapter_writer"],
        max_tokens=40000,
        thinking=False,
    )
    return response.text.strip()


_FEEDBACK_HEADER = (
    "## LỖI CONTINUITY CẦN SỬA KHI VIẾT LẠI (từ verifier) — bắt buộc khắc phục\n"
)


def run(session: Session, story: Story, chapter: Chapter, feedback: str | None = None) -> None:
    logger.info("[%s] chapter_writer START ch%d/%d%s",
                story.slug, chapter.number, story.total_chapters,
                " [rewrite]" if feedback else "")
    system = load_prompt("chapter_writer")
    min_words = int(story.words_per_chapter * MIN_WORD_RATIO)
    fb_block = f"{_FEEDBACK_HEADER}{feedback}\n\n" if feedback else ""
    title: str | None = None
    content: str | None = None

    # --- Primary path: scene-by-scene when blueprint has scenes ---
    if chapter.blueprint:
        try:
            bp = json.loads(chapter.blueprint)
            scenes = bp.get("scenes", [])
            if scenes:
                shared_context = fb_block + _build_shared_context(session, story, chapter)
                words_per_scene = max(400, story.words_per_chapter // len(scenes))
                min_scene_words = int(words_per_scene * MIN_SCENE_WORD_RATIO)
                scene_texts: list[str] = []

                for i, scene_data in enumerate(scenes):
                    logger.info("[%s] ch%d scene %d/%d (~%d words)",
                                story.slug, chapter.number, i + 1, len(scenes), words_per_scene)
                    scene_text = _write_single_scene(
                        system, story, chapter, bp,
                        i, len(scenes), scene_data,
                        scene_texts, shared_context, words_per_scene,
                    )
                    # Non-first scenes must not start with a heading; strip if model added one.
                    if i > 0:
                        scene_text = _strip_leading_heading(scene_text)
                    # Inline expand if this scene is too short
                    if len(scene_text.split()) < min_scene_words:
                        logger.info("[%s] ch%d scene %d short (%d words), expanding",
                                    story.slug, chapter.number, i + 1, len(scene_text.split()))
                        scene_text = _expand_scene(
                            system, story, chapter, i, scene_text, words_per_scene
                        )
                    scene_texts.append(scene_text)

                draft = "\n\n---scene-break---\n\n".join(scene_texts)
                logger.info("[%s] ch%d synthesizing %d scenes", story.slug, chapter.number, len(scenes))
                full_text = _synthesize_chapter(story, chapter, draft)
                title, content = _parse_chapter(full_text, chapter.number)
        except Exception:
            logger.warning("[%s] ch%d scene-by-scene failed, falling back to single call",
                           story.slug, chapter.number, exc_info=True)

    # --- Fallback: single call (no blueprint or exception in scene loop) ---
    if content is None:
        logger.info("[%s] ch%d single-call path", story.slug, chapter.number)
        base_context = fb_block + _build_context(session, story, chapter)
        max_tokens = 40000
        response = PROVIDER.generate(
            system=system, user_content=base_context,
            model=AGENT_MODELS["chapter_writer"],
            max_tokens=max_tokens, thinking=True,
        )
        title, content = _parse_chapter(response.text, chapter.number)

    word_count = len(content.split())
    logger.info("[%s] ch%d assembled: %d words (target %d, min %d)",
                story.slug, chapter.number, word_count, story.words_per_chapter, min_words)

    # Safety net: if the assembled chapter is still short, deepen existing scenes.
    # The prompt explicitly says "same order, no new events" to prevent restructuring.
    attempts = 0
    while word_count < min_words and attempts < MAX_EXPAND_ATTEMPTS:
        attempts += 1
        logger.info("[%s] ch%d expand attempt %d/%d (%d words)",
                    story.slug, chapter.number, attempts, MAX_EXPAND_ATTEMPTS, word_count)
        max_tokens = 40000
        expand_content = (
            f"chapter_number: {chapter.number} | language: {story.language} "
            f"| words_per_chapter: {story.words_per_chapter}\n\n"
            f"## DRAFT ({word_count}/{story.words_per_chapter} words — needs expansion)\n"
            f"---\n{content}\n---\n\n"
            f"The draft is too short. Expand it to ~{story.words_per_chapter} words:\n"
            f"- Keep every scene and event in the EXACT same order.\n"
            f"- Add depth within each scene: sensory detail, internal monologue, dialogue.\n"
            f"- Do NOT add new plot events or restructure the chapter.\n"
            f"- Return the complete expanded chapter starting with the chapter heading.\n"
        )
        response = PROVIDER.generate(
            system=system, user_content=expand_content,
            model=AGENT_MODELS["chapter_writer"],
            max_tokens=max_tokens, thinking=True,
        )
        title, content = _parse_chapter(response.text, chapter.number)
        word_count = len(content.split())
        logger.info("[%s] ch%d after expand: %d words", story.slug, chapter.number, word_count)

    logger.info("[%s] chapter_writer DONE ch%d: %d words | title=%r",
                story.slug, chapter.number, word_count, title)
    chapter.title = title
    chapter.content = content
    chapter.word_count = word_count
    chapter.status = "done"
