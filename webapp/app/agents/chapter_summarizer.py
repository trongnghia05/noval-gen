from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, ChapterSummary, Foreshadowing, StateLog, Story, WorldState
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterSummaryOutput


def _upsert_world_state_row(session: Session, story_id: int, chapter_number: int, row) -> None:
    existing = (
        session.query(WorldState)
        .filter_by(story_id=story_id, entity_type=row.entity_type, entity_key=row.entity_key, field=row.field)
        .first()
    )
    if existing:
        existing.value = row.value
        existing.updated_at_chapter = chapter_number
    else:
        session.add(
            WorldState(
                story_id=story_id,
                entity_type=row.entity_type,
                entity_key=row.entity_key,
                field=row.field,
                value=row.value,
                updated_at_chapter=chapter_number,
            )
        )


def _upsert_foreshadowing(session: Session, story_id: int, f) -> None:
    existing = session.query(Foreshadowing).filter_by(story_id=story_id, fid=f.fid).first()
    if existing:
        existing.detail = f.detail
        existing.status = f.status
        existing.payoff_chapter = f.payoff_chapter
    else:
        session.add(
            Foreshadowing(
                story_id=story_id,
                fid=f.fid,
                detail=f.detail,
                planted_chapter=f.planted_chapter,
                status=f.status,
                payoff_chapter=f.payoff_chapter,
            )
        )


def run(session: Session, story: Story, chapter: Chapter) -> None:
    system = load_prompt("chapter_summarizer")
    user_content = f"""chapter_number: {chapter.number}

## characters.md (tên chính thức + aliases)
{context_builder.format_character_aliases(session, story.id)}

## world-state.md hiện tại (trước chương này)
{context_builder.format_world_state(session, story.id)}

## Nội dung chương {chapter.number}: {chapter.title}
{chapter.content}
"""
    output = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_summarizer"],
        schema=ChapterSummaryOutput,
        max_tokens=32768,
    )

    session.add(
        ChapterSummary(story_id=story.id, chapter_number=chapter.number, summary_text=output.summary)
    )
    for change in output.state_changes:
        session.add(
            StateLog(
                story_id=story.id,
                chapter_number=chapter.number,
                entity=change.entity,
                field=change.field,
                old_value=change.old_value,
                new_value=change.new_value,
                reason=change.reason,
            )
        )
    for row in output.world_state_rows:
        _upsert_world_state_row(session, story.id, chapter.number, row)
    for f in output.foreshadowing:
        _upsert_foreshadowing(session, story.id, f)
