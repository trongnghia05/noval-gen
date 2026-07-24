"""Deterministic pipeline — one advance() call = one LLM step.

Writing flow per chapter (3 advances):
  1. blueprint_{N}  — chapter_blueprinter plans structure + scenes
  2. chapter_{N}    — chapter_writer writes prose, chapter_verifier (continuity)
                     + quality_reviewer (quality; originality vs source for
                     REWRITE) gate it, then chapter_summarizer updates memory
  3. checkpoint_{N} — continuity_editor + smart_planner (every 5 chapters or last)

_decide_next_step() (what runs next) and the run_*_step() functions below
(what each step does) are the single source of truth both consumers dispatch
through: advance() drives the single-step HTTP API, graph.py drives the
continuous LangGraph run. Neither duplicates the other's logic.
"""

from sqlalchemy.orm import Session

from .agents import (
    chapter_blueprinter,
    chapter_summarizer,
    chapter_verifier,
    chapter_writer,
    character_developer,
    continuity_editor,
    planning_verifier,
    plot_architect,
    quality_reviewer,
    smart_planner,
    story_analyzer,
    worldbuilder,
)
from .db.models import Character, Chapter, Story


def _is_checkpoint_chapter(story: Story, chapter_number: int) -> bool:
    return chapter_number % 5 == 0 or chapter_number == story.total_chapters


def _decide_next_step(session: Session, story: Story) -> str:
    """Pure decision, no execution. Mirrors the old _advance_planning /
    _advance_writing if-chains, including the checkpoint-before-pending
    invariant: a pending checkpoint is checked before scanning for the next
    pending chapter, so a failed checkpoint stays retriable even after all
    chapters are marked done.

    Planning ends with a verify_planning gate (once all 4 artifacts exist but
    story.planning_verified is still False) before phase flips to WRITING.
    """
    if story.phase == "PLANNING":
        if not story.story_bible:
            return "story_bible"
        if not story.plot_outline:
            return "plot_outline"
        has_characters = session.query(Character).filter_by(story_id=story.id).first() is not None
        if not has_characters:
            return "characters"
        if not story.world_bible:
            return "world"
        if not story.planning_verified:
            return "verify_planning"
        return "planning_complete"

    if story.phase == "WRITING":
        last_done = (
            session.query(Chapter)
            .filter_by(story_id=story.id, status="done")
            .order_by(Chapter.number.desc())
            .first()
        )
        if last_done is not None and _is_checkpoint_chapter(story, last_done.number):
            if (story.last_checkpoint_chapter or 0) < last_done.number:
                return "checkpoint"

        next_chapter = (
            session.query(Chapter)
            .filter(
                Chapter.story_id == story.id,
                Chapter.status.in_(["pending", "blueprinted"]),
            )
            .order_by(Chapter.number)
            .first()
        )
        if next_chapter is None:
            return "complete"
        if next_chapter.status == "pending":
            return "blueprint"
        return "write_chapter"

    return "complete"


def run_story_bible_step(session: Session, story: Story) -> dict:
    story.story_bible = story_analyzer.run(story)
    session.commit()
    return {"phase": "PLANNING", "step": "story_bible"}


def run_plot_outline_step(session: Session, story: Story) -> dict:
    story.plot_outline = plot_architect.run(story)
    session.commit()
    return {"phase": "PLANNING", "step": "plot_outline"}


def run_characters_step(session: Session, story: Story) -> dict:
    character_developer.run(session, story)
    session.commit()
    return {"phase": "PLANNING", "step": "characters"}


def run_world_step(session: Session, story: Story) -> dict:
    story.world_bible = worldbuilder.run(story)
    session.commit()
    return {"phase": "PLANNING", "step": "world"}


def run_verify_planning_step(session: Session, story: Story) -> dict:
    planning_verifier.run(session, story)
    story.planning_verified = True
    session.commit()
    return {"phase": "PLANNING", "step": "verify_planning"}


def run_planning_complete_step(session: Session, story: Story) -> dict:
    story.phase = "WRITING"
    session.commit()
    return {"phase": "WRITING", "step": "planning_complete"}


def run_checkpoint_step(session: Session, story: Story) -> dict:
    last_done = (
        session.query(Chapter)
        .filter_by(story_id=story.id, status="done")
        .order_by(Chapter.number.desc())
        .first()
    )
    continuity_editor.run(session, story, last_done.number)
    smart_planner.run(session, story, last_done.number)
    story.last_checkpoint_chapter = last_done.number
    session.commit()
    return {"phase": "WRITING", "step": f"checkpoint_{last_done.number}"}


def run_blueprint_step(session: Session, story: Story) -> dict:
    next_chapter = (
        session.query(Chapter)
        .filter(Chapter.story_id == story.id, Chapter.status == "pending")
        .order_by(Chapter.number)
        .first()
    )
    chapter_blueprinter.run(session, story, next_chapter)
    session.commit()
    return {
        "phase": "WRITING",
        "step": f"blueprint_{next_chapter.number}",
        "chapter_number": next_chapter.number,
    }


def run_write_chapter_step(session: Session, story: Story) -> dict:
    next_chapter = (
        session.query(Chapter)
        .filter(Chapter.story_id == story.id, Chapter.status == "blueprinted")
        .order_by(Chapter.number)
        .first()
    )
    chapter_writer.run(session, story, next_chapter)
    session.commit()

    # Verify against established state BEFORE summarizing — summarizing first
    # would let a wrong chapter taint world-state with its own errors, which
    # a later verify pass would then wrongly treat as "already agreed truth".
    chapter_verifier.run(session, story, next_chapter)
    session.commit()

    # Quality gate (+ originality vs source for REWRITE), also before summarize
    # so a low-quality/too-similar chapter is fixed before it enters memory.
    quality_reviewer.run(session, story, next_chapter)
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


def run_complete_step(session: Session, story: Story) -> dict:
    if story.phase != "COMPLETE":
        story.phase = "COMPLETE"
        session.commit()
    return {"phase": "COMPLETE", "step": None}


_STEP_EXECUTORS = {
    "story_bible": run_story_bible_step,
    "plot_outline": run_plot_outline_step,
    "characters": run_characters_step,
    "world": run_world_step,
    "verify_planning": run_verify_planning_step,
    "planning_complete": run_planning_complete_step,
    "checkpoint": run_checkpoint_step,
    "blueprint": run_blueprint_step,
    "write_chapter": run_write_chapter_step,
    "complete": run_complete_step,
}


def advance(session: Session, story: Story) -> dict:
    step = _decide_next_step(session, story)
    return _STEP_EXECUTORS[step](session, story)
