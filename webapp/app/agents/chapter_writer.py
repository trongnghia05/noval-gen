import re

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..prompts.loader import load_prompt

# Matches "# Chapter 1: Title", "# Chương 1: Tiêu đề", "# Ch.1: Title", etc. —
# the word for "chapter" varies by story language, so match on the numeral instead.
_TITLE_RE = re.compile(r"^#\s*\S+\.?\s*\d+\s*[:.]?\s*(.+)$")

# Mirrors the CLI project's error-recovery agent's CHAPTER_TOO_SHORT threshold.
MIN_WORD_RATIO = 0.85
MAX_EXPAND_ATTEMPTS = 2


def _parse_chapter(text: str, chapter_number: int) -> tuple[str, str]:
    text = text.strip()
    first_line, _, rest = text.partition("\n")
    match = _TITLE_RE.match(first_line.strip())
    title = match.group(1).strip() if match else f"Chapter {chapter_number}"
    content = rest.strip() if match else text
    return title, content


def _build_context(session: Session, story: Story, chapter: Chapter) -> str:
    return f"""chapter_number: {chapter.number}
words_per_chapter: {story.words_per_chapter}
Ngôn ngữ: {story.language}

## world-state.md (snapshot hiện tại)
{context_builder.format_world_state(session, story.id)}

## chapter-summaries.md
{context_builder.format_chapter_summaries(session, story.id)}

## continuity-log.md
{context_builder.format_continuity_log(session, story.id)}

## Điều chỉnh outline từ smart-planner (nếu có)
{context_builder.format_smart_planner_adjustments(session, story.id)}

## plot-outline.md
{story.plot_outline}

## characters.md
{context_builder.format_characters(session, story.id)}

## world.md
{story.world_bible}

## story-bible.md (tone, chủ đề)
{story.story_bible}
"""


def run(session: Session, story: Story, chapter: Chapter) -> None:
    system = load_prompt("chapter_writer")
    base_context = _build_context(session, story, chapter)
    # ~2.5 tokens/word of headroom for prose in Vietnamese/English plus the
    # model's own formatting; thinking is separate from max_tokens/adaptive.
    max_tokens = max(2048, int(story.words_per_chapter * 3))
    min_words = int(story.words_per_chapter * MIN_WORD_RATIO)

    response = PROVIDER.generate(
        system=system, user_content=base_context, model=AGENT_MODELS["chapter_writer"],
        max_tokens=max_tokens, thinking=True,
    )
    title, content = _parse_chapter(response.text, chapter.number)
    word_count = len(content.split())

    # A chapter can come back well short of words_per_chapter even with a
    # generous max_tokens budget — e.g. an overloaded free-tier upstream
    # cutting the stream early — with no exception raised anywhere to catch
    # it. Nothing else validates chapter_writer's output, so check here and
    # ask for a fully rewritten, expanded version instead of silently saving
    # a broken chapter as "done".
    attempts = 0
    while word_count < min_words and attempts < MAX_EXPAND_ATTEMPTS:
        attempts += 1
        expand_content = f"""{base_context}

## BẢN NHÁP CHƯƠNG NÀY BẠN VỪA VIẾT (chỉ đạt {word_count}/{story.words_per_chapter} từ — QUÁ NGẮN, có thể đã bị cắt cụt giữa chừng)
---
{content}
---

Bản nháp trên chưa đạt yêu cầu độ dài. Viết LẠI HOÀN CHỈNH toàn bộ chương này (không phải tiếp nối bản nháp) sao cho đạt khoảng {story.words_per_chapter} từ. Giữ nguyên cốt truyện/tình tiết/nhân vật đã có ở bản nháp trên — nếu bản nháp bị cắt giữa chừng, hãy hoàn thiện trọn vẹn tới cảnh kết chương theo outline — chỉ MỞ RỘNG bằng cách thêm chi tiết cảnh, đối thoại, nội tâm nhân vật, mô tả giác quan. KHÔNG đổi tình tiết đã thiết lập trong bản nháp.
"""
        response = PROVIDER.generate(
            system=system, user_content=expand_content, model=AGENT_MODELS["chapter_writer"],
            max_tokens=max_tokens, thinking=True,
        )
        title, content = _parse_chapter(response.text, chapter.number)
        word_count = len(content.split())

    chapter.title = title
    chapter.content = content
    chapter.word_count = word_count
    chapter.status = "done"
