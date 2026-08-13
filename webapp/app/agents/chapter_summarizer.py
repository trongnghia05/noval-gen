import logging

from sqlalchemy.orm import Session

from . import _common
from ..services import context_builder, story_state
from ..db.models import Chapter, ChapterSummary, Foreshadowing, StateLog, Story, WorldState
from ..schemas import ChapterSummaryOutput

logger = logging.getLogger(__name__)


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

    graph_ids_section = ""
    if story_state.graph_exists(story.id):
        chars = story_state.get_characters(story.id)
        id_list = "\n".join(f"  {r['id']}: {r['name']}" for r in chars)
        graph_ids_section = f"\n## character-graph ids (use these ids in character_updates)\n{id_list}\n"

    user_content = f"""language: {story.language}
chapter_number: {chapter.number}

## characters.md (official names + aliases)
{context_builder.format_character_aliases(session, story.id)}
{graph_ids_section}
## world-state.md as it stands (before this chapter)
{context_builder.format_world_state(session, story.id)}

## Chapter {chapter.number}: {chapter.title}
{chapter.content}
"""
    output = _common.call_agent(
        "chapter_summarizer",
        user_content=user_content,
        schema=ChapterSummaryOutput,
        max_tokens=32768,
    )

    # ── DB updates (existing) ──────────────────────────────────────────────────
    session.add(
        ChapterSummary(
            story_id=story.id,
            chapter_number=chapter.number,
            summary_text=output.summary,
            short_summary=output.short_summary,
        )
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

    # ── CSV graph updates (new) ────────────────────────────────────────────────
    if story_state.graph_exists(story.id):
        # Group character_updates by id
        updates_by_char: dict[str, dict] = {}
        for u in output.character_updates:
            updates_by_char.setdefault(u.id, {})[u.field] = u.value
        for char_id, fields in updates_by_char.items():
            story_state.update_character_fields(story.id, char_id, fields, chapter.number)

        for rel in output.relationship_changes:
            story_state.upsert_relationship(
                story.id,
                rel.char_a, rel.char_b,
                rel.type, rel.strength, rel.status, rel.event,
                chapter.number,
            )

        for thread in output.plot_thread_updates:
            story_state.upsert_plot_thread(story.id, {
                "id": thread.id,
                "title": thread.title,
                "type": thread.type,
                "status": thread.status,
                "introduced_chapter": str(thread.introduced_chapter or chapter.number),
                "resolved_chapter": str(thread.resolution_note and chapter.number or ""),
                "involved_chars": thread.involved_chars or "",
                "hint": thread.hint or "",
                "resolution_note": thread.resolution_note or "",
            })

        if output.timeline_event:
            e = output.timeline_event
            story_state.append_timeline(
                story.id, chapter.number,
                e.story_time, e.location, e.characters, e.summary,
            )

    logger.info("[%s] chapter_summarizer DONE ch%d: %d state_changes, %d world_state_rows",
                story.slug, chapter.number, len(output.state_changes), len(output.world_state_rows))
