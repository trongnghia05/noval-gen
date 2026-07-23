import re

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..prompts.loader import load_prompt

# Matches "# Chapter 1: Title", "# Chương 1: Tiêu đề", "# Ch.1: Title", etc. —
# the word for "chapter" varies by story language, so match on the numeral instead.
_TITLE_RE = re.compile(r"^#\s*\S+\.?\s*\d+\s*[:.]?\s*(.+)$")


def run(session: Session, story: Story, chapter: Chapter) -> None:
    system = load_prompt("chapter_writer")
    user_content = f"""chapter_number: {chapter.number}
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
    # ~2.5 tokens/word of headroom for prose in Vietnamese/English plus the
    # model's own formatting; thinking is separate from max_tokens/adaptive.
    max_tokens = max(2048, int(story.words_per_chapter * 3))
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_writer"],
        max_tokens=max_tokens,
        thinking=True,
    )
    text = response.text.strip()
    first_line, _, rest = text.partition("\n")
    match = _TITLE_RE.match(first_line.strip())
    title = match.group(1).strip() if match else f"Chapter {chapter.number}"
    content = rest.strip() if match else text

    chapter.title = title
    chapter.content = content
    chapter.word_count = len(content.split())
    chapter.status = "done"
