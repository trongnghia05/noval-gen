import json

from sqlalchemy.orm import Session

from .. import context_builder, csv_graph
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterBlueprintOutput


def _act_position(chapter_number: int, total_chapters: int) -> str:
    ratio = chapter_number / total_chapters
    if ratio <= 0.25:
        return "Act 1"
    if ratio <= 0.50:
        return "Act 2a"
    if ratio <= 0.75:
        return "Act 2b"
    return "Act 3"


def run(session: Session, story: Story, chapter: Chapter) -> None:
    system = load_prompt("chapter_blueprinter")
    act = _act_position(chapter.number, story.total_chapters)

    graph_section = ""
    if csv_graph.graph_exists(story.id):
        graph_section = f"""
## character-graph (current states)
{csv_graph.format_characters(story.id)}

## relationships
{csv_graph.format_relationships(story.id)}

## open plot threads
{csv_graph.format_open_threads(story.id)}

## recent timeline
{csv_graph.format_recent_timeline(story.id)}
"""

    user_content = f"""chapter_number: {chapter.number}
total_chapters: {story.total_chapters}
act_position: {act}
language: {story.language}
{graph_section}
## chapter-summaries (story so far)
{context_builder.format_chapter_summaries(session, story.id)}

## continuity-log (open issues to address or avoid)
{context_builder.format_continuity_log(session, story.id)}

## plot-outline (what this chapter should cover)
{story.plot_outline}
"""

    blueprint = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS.get("chapter_blueprinter", AGENT_MODELS["chapter_writer"]),
        schema=ChapterBlueprintOutput,
        max_tokens=4096,
        thinking=True,
    )

    chapter.blueprint = blueprint.model_dump_json()
    chapter.status = "blueprinted"
