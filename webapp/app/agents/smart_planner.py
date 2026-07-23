from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import SmartPlannerState, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import SmartPlannerOutput


def run(session: Session, story: Story, current_chapter: int) -> None:
    system = load_prompt("smart_planner")
    user_content = f"""current_chapter: {current_chapter}
total_chapters (N): {story.total_chapters}
target_words (W): {story.target_words}
current_words: {story.current_words}

## chapter-summaries.md
{context_builder.format_chapter_summaries(session, story.id)}

## world-state.md
{context_builder.format_world_state(session, story.id)}

## plot-outline.md (gốc)
{story.plot_outline}
"""
    output = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["smart_planner"],
        schema=SmartPlannerOutput,
        max_tokens=32768,
        thinking=True,
    )

    state = session.query(SmartPlannerState).filter_by(story_id=story.id).first()
    if not state:
        state = SmartPlannerState(story_id=story.id)
        session.add(state)
    state.checkpoint_chapter = current_chapter
    state.pacing_note = output.pacing_note
    state.characters_to_watch = output.characters_to_watch
    state.threads_to_resolve = output.threads_to_resolve
    state.outline_adjustments = output.outline_adjustments
