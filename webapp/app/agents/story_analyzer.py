from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story
from ..prompts.loader import load_prompt


def run(story: Story, feedback: str | None = None) -> str:
    system = load_prompt("story_analyzer")
    user_content = f"""Ngôn ngữ: {story.language}
Loại input: {story.input_type}
Thể loại: {story.genre or "(AI tự chọn)"}
Độ dài mục tiêu: {story.total_chapters} chương, {story.target_words} từ tổng, ~{story.words_per_chapter} từ/chương

Nội dung input của user:
---
{story.source_content}
---
"""
    if feedback:
        user_content += (
            "\n## LỖI TỪ VÒNG KIỂM TRA TRƯỚC — bắt buộc khắc phục, giữ nguyên phần đã đúng\n"
            f"{feedback}\n"
        )
    # REWRITE emits a chapter-by-chapter plot map inside the bible, so the
    # output is much longer — scale the budget with chapter count. Generous
    # headroom: base bible + ~500 tok/chapter map, and Gemini's dynamic
    # thinking tokens also draw from this same output budget.
    max_tokens = max(8192, story.total_chapters * 600) if story.input_type == "REWRITE" else 4096
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["story_analyzer"],
        max_tokens=max_tokens,
        thinking=True,
    )
    return response.text
