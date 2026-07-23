from sqlalchemy.orm import Session

from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import CharacterDeveloperOutput


def run(session: Session, story: Story) -> None:
    system = load_prompt("character_developer")
    user_content = f"""Ngôn ngữ: {story.language}

story-bible.md:
---
{story.story_bible}
---

plot-outline.md:
---
{story.plot_outline}
---
"""
    output = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["character_developer"],
        schema=CharacterDeveloperOutput,
        max_tokens=8192,
        thinking=True,
    )
    for character in output.characters:
        session.add(
            Character(
                story_id=story.id,
                name=character.name,
                aliases=character.aliases,
                tier=character.tier,
                profile_md=character.profile_md,
            )
        )
