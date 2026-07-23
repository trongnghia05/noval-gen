from sqlalchemy.orm import Session

from .. import csv_graph
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
        max_tokens=32768,
        thinking=True,
    )

    # Save to DB (existing logic)
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
