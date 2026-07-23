from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import ContinuityLog, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ContinuityEditorOutput


def run(session: Session, story: Story, batch_end: int) -> None:
    system = load_prompt("continuity_editor")
    user_content = f"""batch_end: {batch_end}

## characters.md (tên chính thức + aliases)
{context_builder.format_character_aliases(session, story.id)}

## world-state.md
{context_builder.format_world_state(session, story.id)}

## 5 chương gần nhất (Ch.{max(1, batch_end - 4)}-{batch_end})
{context_builder.last_n_chapters_text(session, story.id, batch_end, n=5)}
"""
    output = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["continuity_editor"],
        schema=ContinuityEditorOutput,
        max_tokens=8192,
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
