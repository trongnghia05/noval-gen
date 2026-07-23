from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story
from ..prompts.loader import load_prompt


def run(story: Story) -> str:
    system = load_prompt("worldbuilder")
    user_content = f"""Thể loại: {story.genre or "(xem story-bible)"}
Ngôn ngữ: {story.language}

story-bible.md:
---
{story.story_bible}
---
"""
    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["worldbuilder"],
        max_tokens=4096,
        thinking=True,
    )
    return response.text
