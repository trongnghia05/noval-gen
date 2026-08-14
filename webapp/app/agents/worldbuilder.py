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
    # 8192 was the last JSON agent left at the old budget, and it broke: thinking tokens
    # count against the same limit, so a bigger graph going in leaves less room to write
    # with, and the world bible was cut off mid-string ~16k chars in. Real ones run
    # 10-21k characters, and the graph fed in here reached 99k once the content
    # truncations came out. 32768 matches character_developer, whose output is
    # comparable in size.
    world: WorldBibleOut = _common.call_agent(
        "worldbuilder", user_content=user_content, schema=WorldBibleOut,
        max_tokens=32768, thinking=True,
    )
    return json.dumps(world.model_dump(), ensure_ascii=False, indent=2)
