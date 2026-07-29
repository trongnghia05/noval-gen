import json
import logging
import re
import textwrap

from sqlalchemy.orm import Session

from .. import context_builder, csv_graph
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterWriterOutput

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^#+\s+.+$")


def _strip_leading_heading(text: str) -> str:
    """Remove a chapter/scene heading from the first line if the model added one
    despite instructions not to (non-first scenes should start with prose)."""
    lines = text.split("\n")
    if lines and _HEADING_RE.match(lines[0].strip()):
        return "\n".join(lines[1:]).lstrip()
    return text


# Double-quote marks only — a paragraph carrying quoted speech is a dialogue turn
# and is left whole (source keeps a speaker's turn intact even when it runs long).
# Apostrophes / single quotes (e.g. the ’ in "Caspian’s") are deliberately excluded
# so a possessive never makes a narration paragraph look like dialogue.
_DIALOGUE_QUOTES = '"“”„«»'
# Sentence end: terminal punctuation + optional closing quote/paren, then whitespace.
_SENT_END_RE = re.compile(r'([.!?]+["“”„«»\'\)]*)(\s+)')
_ABBREVIATIONS = (
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "Lt.", "Sgt.", "Capt.",
    "Gen.", "Col.", "Maj.", "Rev.", "Hon.", "Jr.", "Sr.", "vs.", "etc.",
    "No.", "Dept.", "Inc.", "Ltd.",
)
# A run of consecutive PERIOD-ended narration sentences may stay together up to this
# many; a longer run is broken. A sentence ending in ! or ? always ends its paragraph
# (an emphatic/interrogative beat stands on its own line — like the source's "Slap!").
_MAX_PERIOD_RUN = 2
_EMPHATIC_TRAILING = ' "“”„«»\'’)'
# A closing double-quote immediately preceded by ,.!?… marks the end of a SPOKEN
# sentence (e.g. `pairing," he said` / `stop!"`), i.e. real dialogue. A quoted WORD in
# narration (`meant by "quarantine"?`) has a letter before the closing quote and does
# NOT match — so it is correctly treated as narration and still gets split.
_SPOKEN_QUOTE_RE = re.compile(r'[,.!?…]["”»]')


def _split_into_sentences(para: str) -> list[str]:
    """Split a paragraph into sentences at .!? boundaries, keeping the terminal
    punctuation (and any closing quote) attached and not breaking known abbreviations."""
    protected = para
    for ab in _ABBREVIATIONS:
        protected = protected.replace(ab, ab.replace(".", "\x00"))
    out: list[str] = []
    last = 0
    for m in _SENT_END_RE.finditer(protected):
        out.append(protected[last:m.end(1)])
        last = m.end()
    if protected[last:].strip():
        out.append(protected[last:])
    return [s.replace("\x00", ".").strip() for s in out if s.strip()]


def _ends_emphatic(sentence: str) -> bool:
    """True if the sentence's terminal punctuation is ! or ? (ignoring trailing
    quotes/parens) — those always end a paragraph."""
    s = sentence.rstrip(_EMPHATIC_TRAILING)
    return s.endswith("!") or s.endswith("?")


def _is_dialogue_paragraph(para: str) -> bool:
    """True only for a real DIALOGUE turn (kept whole), not narration that merely
    contains a quoted word. Dialogue = the paragraph starts with a quote, OR a closing
    double-quote is immediately preceded by sentence/clause punctuation (a spoken
    sentence). `meant by "quarantine"?` matches neither → treated as narration."""
    if para.lstrip()[:1] in _DIALOGUE_QUOTES:
        return True
    return bool(_SPOKEN_QUOTE_RE.search(para))


def _split_long_paragraph(para: str) -> list[str]:
    """Re-paragraph a PURE-NARRATION block at sentence boundaries (sentence text never
    altered). A real dialogue turn is returned whole so a speaker's turn (and its
    action beat) stays intact.

    Grouping rule: walk the sentences and start a new paragraph when either
      • the current sentence ends in ! or ? (always breaks — emphatic beat alone), or
      • the current run of period-ended sentences reaches _MAX_PERIOD_RUN (2).
    So 1-2 plain declarative sentences stay together, a 3rd forces a break, and any
    !/? sentence stands on its own line."""
    if _is_dialogue_paragraph(para):
        return [para]
    sentences = _split_into_sentences(para)
    chunks: list[str] = []
    current: list[str] = []
    for sent in sentences:
        current.append(sent)
        if _ends_emphatic(sent) or len(current) >= _MAX_PERIOD_RUN:
            chunks.append(" ".join(current))
            current = []
    if current:
        chunks.append(" ".join(current))
    return chunks if len(chunks) > 1 else [para]


def normalize_paragraphs(text: str) -> str:
    """Force a blank line between paragraphs so Markdown renders them separately, and
    re-paragraph over-long pure-narration blocks at sentence boundaries.

    The model separates paragraphs with a single '\\n' (each non-empty line is one
    paragraph) but ignores per-paragraph length instructions for narration — it obeys
    structural rules (dialogue on its own line) yet not the quantitative "keep
    paragraphs short" rule, producing 5-6 sentence descriptive blocks. We fix that
    deterministically (see _split_long_paragraph): a run of plain declarative
    sentences stays together up to 3, a longer run is broken, and any !/? sentence
    stands on its own line — while dialogue paragraphs are left whole and no sentence
    text is ever changed. Matches the source's short-narration density."""
    out: list[str] = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out.extend(_split_long_paragraph(ln))
    return "\n\n".join(out)


MIN_WORD_RATIO = 0.85
MIN_SCENE_WORD_RATIO = 0.60
MAX_EXPAND_ATTEMPTS = 2


def _word_count(text: str) -> int:
    return len(text.split())


def _pov_directive(bp: dict) -> str:
    """POV instruction for the writer. Single-POV = one imperative line. Multi-POV
    (source switched POV mid-chapter) = list the POV holders and let the writer place
    the switch; we assert WHICH POVs and the person, not the order/where."""
    multi = [p for p in (bp.get("pov_characters") or []) if p]
    if len(multi) > 1:
        names = ", ".join(multi)
        return (
            f"⚠ MULTI-POV CHAPTER. This chapter is narrated from MORE THAN ONE point of "
            f"view: {names}. Switch POV where the narrative naturally shifts, and mark "
            f"each switch with a section break line '---'. Within each segment you ARE "
            f"that one character: narrate in their FIRST person ('I/my') and never refer "
            f"to the current POV character by name or as he/she. Do NOT blend two POVs in "
            f"one segment. Cover every listed POV.\n"
        )
    pov = bp.get("pov_character") or ""
    return (
        f"⚠ POV = {pov}. You ARE {pov}. Narrate in the source's grammatical person "
        f"(see source_spirit POV — first-person = 'I/my'). NEVER refer to {pov} by name "
        f"or as he/she in narration; only other characters get he/she.\n"
        if pov else ""
    )


def _format_blueprint(chapter: Chapter) -> str:
    if not chapter.blueprint:
        return "(no blueprint — write using your best judgment)"
    try:
        bp = json.loads(chapter.blueprint)
        scene_lines = []
        for i, s in enumerate(bp.get("scenes", [])):
            line = (
                f"  Scene {i+1}: goal={s['goal']} | conflict={s['conflict']} "
                f"| outcome={s['outcome']} | disaster={s['disaster']}"
            )
            speakers = s.get("speaking_characters") or []
            if speakers:
                line += f"\n    DIALOGUE — speakers: {', '.join(speakers)}"
                if s.get("dialogue_nuance"):
                    line += f" | tone: {s['dialogue_nuance']}"
                if s.get("dialogue_intent"):
                    line += f" | must achieve: {s['dialogue_intent']}"
            else:
                line += "\n    DIALOGUE — (none planned; interiority/action scene)"
            scene_lines.append(line)
        scenes = "\n".join(scene_lines)
        plant = bp.get("foreshadowing_to_plant") or "none"
        intensity = bp.get("dialogue_intensity", "balanced")
        pov_line = _pov_directive(bp)
        return (
            f"{pov_line}"
            f"PURPOSE: {bp.get('purpose')}\n"
            f"ACT: {bp.get('act_position')} | BEAT: {bp.get('beat_type', '')}\n"
            f"STATE DELTA (this chapter must make this change happen): {bp.get('state_delta', '')}\n"
            f"Emotion start: {bp.get('emotional_arc_start')} → end: {bp.get('emotional_arc_end')}\n"
            f"DIALOGUE INTENSITY: {intensity}\n"
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

    db_graph_section = ""
    if story.new_graph_built:
        db_graph_section = (
            "\n## chapter graph constraints"
            " (verifier checks these — PARTICIPATES = who must appear,"
            " LOCATED_AT = where, ARC_CHANGE = arc shifts to trigger this chapter)\n"
            + context_builder.format_chapter_subgraph(
                session, story.id, chapter.number, graph_type="new", max_depth=1
            )
            + "\n"
        )

    source_spirit = context_builder.format_source_spirit_for_chapter(session, story, chapter.number)
    source_section = f"\n{source_spirit}\n" if source_spirit else ""

    gender_section = ""
    if story.new_graph_built:
        gender_section = (
            "\n## character genders (use the CORRECT pronouns — this is fixed, never flip)\n"
            + context_builder.format_gender_roster(session, story.id)
            + "\n"
        )

    return (
        f"{gender_section}"
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
        f"{db_graph_section}"
        f"{source_section}"
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

    db_graph_section = ""
    if story.new_graph_built:
        db_graph_section = (
            "\n## chapter graph constraints"
            " (verifier checks these — PARTICIPATES = who must appear,"
            " LOCATED_AT = where, ARC_CHANGE = arc shifts to trigger this chapter)\n"
            + context_builder.format_chapter_subgraph(
                session, story.id, chapter.number, graph_type="new", max_depth=1
            )
            + "\n"
        )

    source_spirit = context_builder.format_source_spirit_for_chapter(session, story, chapter.number)
    source_section = f"\n{source_spirit}\n" if source_spirit else ""

    return (
        f"chapter_number: {chapter.number}\n"
        f"words_per_chapter: {story.words_per_chapter}\n"
        f"language: {story.language}\n\n"
        f"## CHAPTER BLUEPRINT (follow this structure)\n{_format_blueprint(chapter)}\n"
        f"{graph_section}\n"
        f"{db_graph_section}"
        f"## chapter-list (bức tranh toàn cảnh — Ch.{chapter.number} là chương đang viết)\n"
        f"{context_builder.format_chapter_list(session, story.id, chapter.number)}\n\n"
        f"## world-state.md (snapshot hiện tại)\n{context_builder.format_world_state(session, story.id)}\n\n"
        f"## continuity-log.md\n{context_builder.format_continuity_log(session, story.id)}\n\n"
        f"## Điều chỉnh outline từ smart-planner (nếu có)\n{context_builder.format_smart_planner_adjustments(session, story.id)}\n\n"
        f"## plot-outline.md\n{story.plot_outline}\n\n"
        f"## characters.md (full profiles)\n{context_builder.format_characters(session, story.id)}\n\n"
        f"## world.md\n{story.world_bible}\n\n"
        f"## story-bible.md (tone, chủ đề)\n{story.story_bible}\n"
        f"{source_section}"
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

    pov_line = _pov_directive(blueprint)
    bp_overview = (
        f"{pov_line}"
        f"CHAPTER PURPOSE: {blueprint.get('purpose')}\n"
        f"ACT: {blueprint.get('act_position')} | "
        f"Emotion: {blueprint.get('emotional_arc_start')} → {blueprint.get('emotional_arc_end')}\n"
        f"DIALOGUE INTENSITY: {blueprint.get('dialogue_intensity', 'balanced')}\n"
        f"HOOK (use in scene 1 opening only): {blueprint.get('hook')}\n"
        f"FORESHADOWING TO PLANT: {blueprint.get('foreshadowing_to_plant') or 'none'}"
    )

    chars = scene_data.get("characters", [])
    loc = scene_data.get("location", "")
    speakers = scene_data.get("speaking_characters") or []
    if speakers:
        dlg_line = (
            f"  DIALOGUE — these characters MUST speak, each in their own distinct voice: "
            f"{', '.join(speakers)}\n"
        )
        if scene_data.get("dialogue_nuance"):
            dlg_line += f"  dialogue tone: {scene_data['dialogue_nuance']}\n"
        if scene_data.get("dialogue_intent"):
            dlg_line += f"  dialogue must achieve: {scene_data['dialogue_intent']}\n"
    else:
        dlg_line = "  DIALOGUE — none planned; this is an interiority/action scene, do not force dialogue\n"
    scene_spec = (
        f"Scene {scene_index + 1} of {total_scenes}:\n"
        f"  goal: {scene_data['goal']}\n"
        f"  conflict: {scene_data['conflict']}\n"
        f"  outcome: {scene_data['outcome']}\n"
        f"  disaster: {scene_data['disaster']}\n"
        + (f"  characters: {', '.join(chars)}\n" if chars else "")
        + (f"  location: {loc}\n" if loc else "")
        + dlg_line
    ).rstrip()

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

_FINALIZE_SYSTEM = """\
You receive a complete, polished chapter. Extract structured metadata and return ONLY a JSON object — no markdown fence, no commentary.

Fields:
- "title": chapter title text only — strip the "Chapter N:" / "Chương N:" prefix completely (e.g. "Whispers of Betrayal Unmasked", NOT "Chapter 5: Whispers of...")
- "content": full prose starting on the line AFTER the heading (do not include the heading line in content)
- "short_summary": 1-2 sentences — who does what, what changes, what is at stake going into next chapter
- "hook": copy the exact last sentence of the chapter verbatim (the cliffhanger or closing image the reader carries into the next chapter)
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


def _finalize_chapter(story: "Story", chapter: "Chapter", full_text: str) -> ChapterWriterOutput:
    """Extract title, content (sans heading), short_summary, hook from the finished chapter."""
    user_content = (
        f"chapter_number: {chapter.number} | language: {story.language}\n\n"
        f"## COMPLETE CHAPTER\n---\n{full_text}\n---\n"
    )
    return generate_structured(
        PROVIDER,
        system=_FINALIZE_SYSTEM,
        user_content=user_content,
        model=AGENT_MODELS["chapter_writer"],
        schema=ChapterWriterOutput,
        max_tokens=40000,
        thinking=False,
    )


_FEEDBACK_HEADER = (
    "## LỖI CONTINUITY CẦN SỬA KHI VIẾT LẠI (từ verifier) — bắt buộc khắc phục\n"
)


def run(session: Session, story: Story, chapter: Chapter, feedback: str | None = None) -> ChapterWriterOutput:
    logger.info("[%s] chapter_writer START ch%d/%d%s",
                story.slug, chapter.number, story.total_chapters,
                " [rewrite]" if feedback else "")
    system = load_prompt("chapter_writer")
    min_words = int(story.words_per_chapter * MIN_WORD_RATIO)
    fb_block = f"{_FEEDBACK_HEADER}{feedback}\n\n" if feedback else ""
    # full_text retains the heading line — needed for _finalize_chapter to extract title
    full_text: str | None = None

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
                    if i > 0:
                        scene_text = _strip_leading_heading(scene_text)
                    if _word_count(scene_text) < min_scene_words:
                        logger.info("[%s] ch%d scene %d short (%d words), expanding",
                                    story.slug, chapter.number, i + 1, _word_count(scene_text))
                        scene_text = _expand_scene(
                            system, story, chapter, i, scene_text, words_per_scene
                        )
                    scene_texts.append(scene_text)

                draft = "\n\n---scene-break---\n\n".join(scene_texts)
                logger.info("[%s] ch%d synthesizing %d scenes", story.slug, chapter.number, len(scenes))
                full_text = _synthesize_chapter(story, chapter, draft)
        except Exception:
            logger.warning("[%s] ch%d scene-by-scene failed, falling back to single call",
                           story.slug, chapter.number, exc_info=True)

    # --- Fallback: single call (no blueprint or exception in scene loop) ---
    if full_text is None:
        logger.info("[%s] ch%d single-call path", story.slug, chapter.number)
        base_context = fb_block + _build_context(session, story, chapter)
        response = PROVIDER.generate(
            system=system, user_content=base_context,
            model=AGENT_MODELS["chapter_writer"],
            max_tokens=40000, thinking=True,
        )
        full_text = response.text.strip()

    # Use content-only text (heading stripped) for word count checks in expand loop
    content_only = _strip_leading_heading(full_text)
    word_count = _word_count(content_only)
    logger.info("[%s] ch%d assembled: %d words (target %d, min %d)",
                story.slug, chapter.number, word_count, story.words_per_chapter, min_words)

    # Safety net: expand if still too short
    attempts = 0
    while word_count < min_words and attempts < MAX_EXPAND_ATTEMPTS:
        attempts += 1
        logger.info("[%s] ch%d expand attempt %d/%d (%d words)",
                    story.slug, chapter.number, attempts, MAX_EXPAND_ATTEMPTS, word_count)
        expand_content = (
            f"chapter_number: {chapter.number} | language: {story.language} "
            f"| words_per_chapter: {story.words_per_chapter}\n\n"
            f"## DRAFT ({word_count}/{story.words_per_chapter} words — needs expansion)\n"
            f"---\n{full_text}\n---\n\n"
            f"The draft is too short. Expand it to ~{story.words_per_chapter} words:\n"
            f"- Keep every scene and event in the EXACT same order.\n"
            f"- Add depth within each scene: sensory detail, internal monologue, dialogue.\n"
            f"- Do NOT add new plot events or restructure the chapter.\n"
            f"- Return the complete expanded chapter starting with the chapter heading.\n"
        )
        response = PROVIDER.generate(
            system=system, user_content=expand_content,
            model=AGENT_MODELS["chapter_writer"],
            max_tokens=40000, thinking=True,
        )
        full_text = response.text.strip()
        content_only = _strip_leading_heading(full_text)
        word_count = _word_count(content_only)
        logger.info("[%s] ch%d after expand: %d words", story.slug, chapter.number, word_count)

    # --- Finalize: structured extraction of title / short_summary / hook ---
    logger.info("[%s] ch%d finalizing metadata", story.slug, chapter.number)
    output = _finalize_chapter(story, chapter, full_text)

    logger.info("[%s] chapter_writer DONE ch%d: %d words | title=%r",
                story.slug, chapter.number, _word_count(output.content), output.title)
    chapter.title = output.title
    chapter.content = normalize_paragraphs(output.content)
    chapter.word_count = _word_count(chapter.content)
    chapter.status = "done"
    return output
