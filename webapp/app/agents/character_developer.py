from sqlalchemy.orm import Session

from . import _common
from ..services import csv_graph
from ..db.models import Character, Story
from ..schemas import CharacterDeveloperOutput


def run(session: Session, story: Story, feedback: str | None = None) -> None:
    user_content = f"""Language: {story.language}

story-bible.md:
---
{story.story_bible}
---

plot-outline.md:
---
{story.plot_outline}
---
"""
    if feedback:
        user_content += f"{_common.FEEDBACK_HEADER}{feedback}\n"
    output = _common.call_agent(
        "character_developer",
        user_content=user_content,
        schema=CharacterDeveloperOutput,
        max_tokens=32768,
        thinking=True,
    )

    seen_names: set[str] = set()
    for character in output.characters:
        name_key = character.name.strip().lower()
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        session.add(
            Character(
                story_id=story.id,
                name=character.name,
                aliases=character.aliases,
                tier=character.tier,
                profile_md=character.profile_md,
            )
        )
    session.flush()

    # Init CSV knowledge graph
    graph_rows = []
    for g in output.character_graph:
        graph_rows.append({
            "id": g.id,
            "name": g.name,
            "aliases": "",
            "role": g.role,
            "arc_status": "active",
            "location": g.initial_location,
            "emotional_state": g.initial_emotional_state,
            "goals": g.initial_goals,
            "secrets": g.initial_secrets,
            "speech_pattern": g.speech_pattern,
            "last_seen_chapter": "0",
        })

    csv_graph.init_graph(story.id, graph_rows, output.character_voices_md)
