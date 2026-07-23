"""Deterministic pipeline logic — the Python replacement for CLAUDE.md's
"LUONG THUC HIEN" section. No LLM call decides what happens next; that's
plain control flow, same as any other backend job runner.
"""

from sqlalchemy.orm import Session

from .agents import (
    chapter_summarizer,
    chapter_writer,
    character_developer,
    continuity_editor,
    plot_architect,
    smart_planner,
    story_analyzer,
    worldbuilder,
)
from .db.models import Character, Chapter, Story


def advance(session: Session, story: Story) -> dict:
    """Runs exactly one step and returns a status dict. Safe to call
    repeatedly (idempotent) — this is the web equivalent of typing
    "bắt đầu" again to resume an interrupted run.
    """
    if story.phase == "PLANNING":
        return _advance_planning(session, story)
    if story.phase == "WRITING":
        return _advance_writing(session, story)
    return {"phase": "COMPLETE", "step": None}


def _advance_planning(session: Session, story: Story) -> dict:
    if not story.story_bible:
        story.story_bible = story_analyzer.run(story)
        session.commit()
        return {"phase": "PLANNING", "step": "story_bible"}

    if not story.plot_outline:
        story.plot_outline = plot_architect.run(story)
        session.commit()
        return {"phase": "PLANNING", "step": "plot_outline"}

    has_characters = session.query(Character).filter_by(story_id=story.id).first() is not None
    if not has_characters:
        character_developer.run(session, story)
        session.commit()
        return {"phase": "PLANNING", "step": "characters"}

    if not story.world_bible:
        story.world_bible = worldbuilder.run(story)
        session.commit()
        return {"phase": "PLANNING", "step": "world"}

    story.phase = "WRITING"
    session.commit()
    return {"phase": "WRITING", "step": "planning_complete"}


def _is_checkpoint_chapter(story: Story, chapter_number: int) -> bool:
    return chapter_number % 5 == 0 or chapter_number == story.total_chapters


def _advance_writing(session: Session, story: Story) -> dict:
    # A checkpoint is its own step, run BEFORE looking at what's pending —
    # this is what makes a failed continuity_editor/smart_planner call
    # retriable: a plain "any chapters left?" check would otherwise silently
    # skip the checkpoint for the last chapter once all chapters are marked
    # done, since nothing would ever ask for it again.
    last_done = (
        session.query(Chapter)
        .filter_by(story_id=story.id, status="done")
        .order_by(Chapter.number.desc())
        .first()
    )
    if last_done is not None and _is_checkpoint_chapter(story, last_done.number):
        if (story.last_checkpoint_chapter or 0) < last_done.number:
            continuity_editor.run(session, story, last_done.number)
            smart_planner.run(session, story, last_done.number)
            story.last_checkpoint_chapter = last_done.number
            session.commit()
            return {"phase": "WRITING", "step": f"checkpoint_{last_done.number}"}

    next_chapter = (
        session.query(Chapter)
        .filter_by(story_id=story.id, status="pending")
        .order_by(Chapter.number)
        .first()
    )
    if next_chapter is None:
        story.phase = "COMPLETE"
        session.commit()
        return {"phase": "COMPLETE", "step": None}

    chapter_writer.run(session, story, next_chapter)
    session.commit()

    chapter_summarizer.run(session, story, next_chapter)
    story.current_words = (story.current_words or 0) + next_chapter.word_count
    session.commit()

    return {
        "phase": "WRITING",
        "step": f"chapter_{next_chapter.number}",
        "chapter_number": next_chapter.number,
        "word_count": next_chapter.word_count,
        "current_words": story.current_words,
        "target_words": story.target_words,
        "checkpoint_pending": _is_checkpoint_chapter(story, next_chapter.number),
    }
