from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story
from ..prompts.loader import load_prompt


def run(story: Story) -> str:
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
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["story_analyzer"],
        max_tokens=4096,
        thinking=True,
    )
    return response.text
