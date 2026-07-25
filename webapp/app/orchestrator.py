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

import logging

from sqlalchemy.orm import Session

from .agents import (
    chapter_blueprinter,
    chapter_graph_extractor,
    chapter_summarizer,
    chapter_verifier,
    chapter_writer,
    character_developer,
    continuity_editor,
    graph_verifier,
    new_graph_builder,
    planning_verifier,
    plot_architect,
    quality_reviewer,
    smart_planner,
    source_graph_verifier,
    story_analyzer,
    worldbuilder,
)
from .db.models import Character, Chapter, ChapterVerifyLog, Story

logger = logging.getLogger(__name__)


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

    For REWRITE, graph_extract runs between story_bible and plot_outline:
    the orchestrator dispatches it once per source chapter until all
    source chapters have an EVENT node, then moves to plot_outline.
    """
    if story.phase == "PLANNING":
        if not story.story_bible:
            return "story_bible"
        # REWRITE: extract one source chapter into graph per advance call
        # until all source chapters have a source EVENT node.
        if story.input_type == "REWRITE" and story.source_chapter_count:
            from .db.models import StoryGraphNode
            extracted = (
                session.query(StoryGraphNode)
                .filter_by(story_id=story.id, graph_type="source", node_type="event")
                .count()
            )
            if extracted < story.source_chapter_count:
                return "graph_extract"
            # REWRITE: verify source graph before building new graph.
            if not story.source_graph_verified:
                return "verify_source_graph"
            # REWRITE: build new story graph from source graph (once all source
            # chapters have been extracted and verified).
            if not story.new_graph_built:
                return "new_graph"
        if not story.plot_outline:
            return "plot_outline"
        has_characters = session.query(Character).filter_by(story_id=story.id).first() is not None
        if not has_characters:
            return "characters"
        if not story.world_bible:
            return "world"
        if not story.planning_verified:
            # REWRITE uses graph-based verification; IDEA/PREMISE uses artifact verification.
            if story.input_type == "REWRITE":
                return "verify_graph"
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


def run_graph_extract_step(session: Session, story: Story) -> dict:
    chapter_number = chapter_graph_extractor.run(session, story)
    session.commit()
    from .db.models import StoryGraphNode
    extracted = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source", node_type="event")
        .count()
    )
    return {
        "phase": "PLANNING",
        "step": "graph_extract",
        "chapter_extracted": chapter_number,
        "extracted_total": extracted,
        "source_total": story.source_chapter_count,
    }


def run_verify_source_graph_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START verify_source_graph", story.slug)
    source_graph_verifier.run(session, story)
    session.commit()
    return {"phase": "PLANNING", "step": "verify_source_graph"}


def run_new_graph_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START new_graph", story.slug)
    new_graph_builder.run(session, story)
    session.commit()
    from .db.models import StoryGraphNode
    new_node_count = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new")
        .count()
    )
    return {
        "phase": "PLANNING",
        "step": "new_graph",
        "new_graph_nodes": new_node_count,
    }


def run_verify_graph_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START verify_graph", story.slug)
    graph_verifier.run(session, story)
    session.commit()
    return {"phase": "PLANNING", "step": "verify_graph"}


def run_story_bible_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START story_bible", story.slug)
    story_analyzer.run(session, story)
    session.commit()
    return {"phase": "PLANNING", "step": "story_bible"}


def run_plot_outline_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START plot_outline", story.slug)
    from . import context_builder
    graph_ctx = context_builder.format_story_graph(session, story.id)
    story.plot_outline = plot_architect.run(story, story_graph=graph_ctx)
    session.commit()
    return {"phase": "PLANNING", "step": "plot_outline"}


def run_characters_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START characters", story.slug)
    character_developer.run(session, story)
    session.commit()
    return {"phase": "PLANNING", "step": "characters"}


def run_world_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START world", story.slug)
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
    logger.info("[%s] START checkpoint", story.slug)
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
    logger.info("[%s] START blueprint", story.slug)
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


_MAX_VERIFY_ITERATIONS = 5


def _verify_chapter_loop(session: Session, story: Story, chapter: Chapter) -> int:
    """Run continuity + quality checks in a loop, rewriting on critical issues.

    Each iteration runs both verifiers and collects all critical issues as
    accumulated feedback for the next rewrite — so the writer always sees the
    full history of what went wrong, not just the latest round.

    Graph context (new graph up to current chapter) is passed to
    chapter_verifier so it can cross-check character arcs, relationships, and
    causal chains against structured graph state — not just the flat world-state
    snapshot.

    Returns the number of rewrites performed (0 = clean on first check).
    """
    from . import context_builder

    graph_context = ""
    if story.new_graph_built:
        graph_context = context_builder.format_chapter_subgraph(
            session, story.id, chapter.number, graph_type="new", max_depth=1
        )

    accumulated_feedback: list[str] = []
    rewrites = 0

    for iteration in range(_MAX_VERIFY_ITERATIONS):
        v_issues = chapter_verifier.check(session, story, chapter, graph_context)
        q_issues = quality_reviewer.check(session, story, chapter)

        all_issues = v_issues + q_issues
        critical = [i for i in all_issues if i.severity == "critical"]

        # Log every issue (all severities) to the audit trail.
        action = f"rewrite_iter_{iteration + 1}" if critical else "logged_only"
        for issue in all_issues:
            # QualityReviewIssueOut has a dimension field; ChapterVerifyIssueOut does not.
            dim = getattr(issue, "dimension", "continuity")
            session.add(
                ChapterVerifyLog(
                    story_id=story.id,
                    chapter_number=chapter.number,
                    severity=issue.severity,
                    description=f"[{dim}] {issue.description}",
                    suggestion=issue.suggestion,
                    action_taken=action,
                )
            )
        session.flush()

        if not critical:
            break

        # Accumulate all critical issues across iterations so each rewrite
        # knows the full history of what was wrong.
        for issue in critical:
            dim = getattr(issue, "dimension", "continuity")
            accumulated_feedback.append(
                f"[iter {iteration + 1}][{dim}] {issue.description} → SỬA: {issue.suggestion}"
            )

        feedback_text = "\n".join(accumulated_feedback)
        chapter_writer.run(session, story, chapter, feedback=feedback_text)
        session.flush()
        rewrites += 1

    return rewrites


def run_write_chapter_step(session: Session, story: Story) -> dict:
    logger.info("[%s] START write_chapter", story.slug)
    next_chapter = (
        session.query(Chapter)
        .filter(Chapter.story_id == story.id, Chapter.status == "blueprinted")
        .order_by(Chapter.number)
        .first()
    )
    logger.info("[%s] write_chapter ch%d/%d", story.slug, next_chapter.number, story.total_chapters)
    chapter_writer.run(session, story, next_chapter)
    session.commit()

    # Verify + quality gate before summarizing — a wrong/truncated chapter must
    # be fixed before it enters memory (world-state, chapter-summaries).
    # Loop up to _MAX_VERIFY_ITERATIONS times; each rewrite gets accumulated
    # feedback from all previous iterations.
    _verify_chapter_loop(session, story, next_chapter)
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
    "graph_extract": run_graph_extract_step,
    "verify_source_graph": run_verify_source_graph_step,
    "new_graph": run_new_graph_step,
    "verify_graph": run_verify_graph_step,
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
