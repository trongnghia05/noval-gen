from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story
from ..prompts.loader import load_prompt


def run(story: Story, feedback: str | None = None, story_graph: str = "") -> str:
    system = load_prompt("worldbuilder")
    user_content = f"""Thể loại: {story.genre or "(xem story-bible)"}
Ngôn ngữ: {story.language}

story-bible.md:
---
{story.story_bible}
---
"""
    if story_graph:
        user_content += (
            "\n## Story Knowledge Graph (dùng location/faction/theme nodes làm anchor)\n"
            f"{story_graph}\n"
        )
    if feedback:
        user_content += (
            "\n## LỖI TỪ VÒNG KIỂM TRA TRƯỚC — bắt buộc khắc phục, giữ nguyên phần đã đúng\n"
            f"{feedback}\n"
        )
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["worldbuilder"],
        max_tokens=4096,
        thinking=True,
    )
    return response.text
