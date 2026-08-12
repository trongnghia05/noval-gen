import json

from . import _common
from ..db.models import Story
from ..schemas import WorldBibleOut


def run(story: Story, feedback: str | None = None, story_graph: str = "") -> str:
    """Produce the world bible as a structured JSON string (WorldBibleOut), stored
    verbatim in story.world_bible. Only the output format changed (markdown → JSON);
    the world-building guidance in the prompt is unchanged."""
    user_content = f"""Genre: {story.genre or "(see the story bible)"}
Language: {story.language}

story-bible.md:
---
{story.story_bible}
---
"""
    if story_graph:
        user_content += (
            "\n## Story Knowledge Graph (anchor on its location/faction/theme nodes)\n"
            f"{story_graph}\n"
        )
    if feedback:
        user_content += f"{_common.FEEDBACK_HEADER}{feedback}\n"
    world: WorldBibleOut = _common.call_agent(
        "worldbuilder", user_content=user_content, schema=WorldBibleOut,
        max_tokens=8192, thinking=True,
    )
    return json.dumps(world.model_dump(), ensure_ascii=False, indent=2)
