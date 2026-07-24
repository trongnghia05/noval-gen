from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story
from ..prompts.loader import load_prompt


def run(story: Story, feedback: str | None = None) -> str:
    system = load_prompt("plot_architect")
    user_content = f"""input_type: {story.input_type}
total_chapters (N): {story.total_chapters}
words_per_chapter: {story.words_per_chapter}
Ngôn ngữ: {story.language}

story-bible.md:
---
{story.story_bible}
---
"""
    if feedback:
        user_content += (
            "\n## LỖI TỪ VÒNG KIỂM TRA TRƯỚC — bắt buộc khắc phục, giữ nguyên phần đã đúng\n"
            f"{feedback}\n"
        )
    # Outline for a long novel is itself long output — scale max_tokens with N.
    max_tokens = max(4096, story.total_chapters * 400)
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["plot_architect"],
        max_tokens=max_tokens,
        thinking=True,
    )
    return response.text
