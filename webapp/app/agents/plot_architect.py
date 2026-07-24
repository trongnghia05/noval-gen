import re

from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story
from ..prompts.loader import load_prompt

# Matches a chapter heading in the outline, either language, any heading level:
# "## CHƯƠNG 12: ...", "### Chapter 3 - ...", etc.
_CHAPTER_HEADING_RE = re.compile(r"(?im)^#{1,4}\s*(?:CH\S*NG|CHAPTER)\s*(\d+)")

# A single generation can be cut off by the output-token ceiling before all N
# chapters are written. Rather than accept a truncated outline, keep asking the
# model to continue from the last chapter until every chapter is covered.
_MAX_CONTINUATIONS = 6


def _highest_chapter(text: str) -> int:
    nums = [int(m) for m in _CHAPTER_HEADING_RE.findall(text)]
    return max(nums, default=0)


def run(story: Story, feedback: str | None = None) -> str:
    system = load_prompt("plot_architect")
    base = f"""input_type: {story.input_type}
total_chapters (N): {story.total_chapters}
words_per_chapter: {story.words_per_chapter}
Ngôn ngữ: {story.language}

story-bible.md:
---
{story.story_bible}
---
"""
    if feedback:
        base += (
            "\n## LỖI TỪ VÒNG KIỂM TRA TRƯỚC — bắt buộc khắc phục, giữ nguyên phần đã đúng\n"
            f"{feedback}\n"
        )

    # A detailed per-chapter outline runs ~1200+ output tokens/chapter, and
    # Gemini's dynamic thinking draws from the same budget — a small cap
    # truncates long novels mid-chapter. Scale generously, but stay under the
    # model's hard output ceiling (Gemini 2.5 Flash ~65k).
    max_tokens = min(60000, max(8192, story.total_chapters * 1600))

    outline = PROVIDER.generate(
        system=system,
        user_content=base,
        model=AGENT_MODELS["plot_architect"],
        max_tokens=max_tokens,
        thinking=True,
    ).text.strip()

    # Completeness loop: if the outline stops short of N chapters (truncation),
    # ask the model to continue from where it left off instead of accepting a
    # cut-off outline. Bounded so a model that never reaches N can't loop forever.
    attempts = 0
    while _highest_chapter(outline) < story.total_chapters and attempts < _MAX_CONTINUATIONS:
        attempts += 1
        last = _highest_chapter(outline)
        continuation_prompt = base + f"""
## OUTLINE ĐÃ VIẾT (đã tới hết Chương {last}) — cần VIẾT TIẾP, KHÔNG lặp lại phần đã có
---
{outline}
---

Outline trên bị dừng ở Chương {last}, CHƯA đủ {story.total_chapters} chương. Hãy viết TIẾP
từ **Chương {last + 1}** cho đến hết **Chương {story.total_chapters}**, đúng định dạng đã dùng,
giữ nguyên mạch và bản đồ cốt truyện. CHỈ xuất phần từ Chương {last + 1} trở đi, KHÔNG lặp lại
các chương đã viết, KHÔNG thêm lời dẫn.
"""
        more = PROVIDER.generate(
            system=system,
            user_content=continuation_prompt,
            model=AGENT_MODELS["plot_architect"],
            max_tokens=max_tokens,
            thinking=True,
        ).text.strip()
        if _highest_chapter(more) <= last:
            break  # continuation added no new chapter — stop rather than spin
        outline = f"{outline}\n\n{more}"

    return outline
