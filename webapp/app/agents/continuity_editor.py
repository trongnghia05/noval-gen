from sqlalchemy.orm import Session

from . import _common
from .. import context_builder
from ..db.models import ContinuityLog, Story
from ..schemas import ContinuityEditorOutput


def run(session: Session, story: Story, batch_end: int) -> None:
    user_content = f"""language: {story.language}
batch_end: {batch_end}

## characters.md (official names + aliases)
{context_builder.format_character_aliases(session, story.id)}

## world-state.md
{context_builder.format_world_state(session, story.id)}

## The last 5 chapters (Ch.{max(1, batch_end - 4)}-{batch_end})
{context_builder.last_n_chapters_text(session, story.id, batch_end, n=5)}
"""
    output = _common.call_agent(
        "continuity_editor",
        user_content=user_content,
        schema=ContinuityEditorOutput,
        max_tokens=32768,
        thinking=True,
    )

    log = session.query(ContinuityLog).filter_by(story_id=story.id).first()
    if not log:
        log = ContinuityLog(story_id=story.id)
        session.add(log)
    log.checkpoint_chapter = batch_end
    log.critical_issues = [issue.model_dump() for issue in output.critical_issues]
    log.minor_issues = [issue.model_dump() for issue in output.minor_issues]
    log.batch_note = output.batch_note
