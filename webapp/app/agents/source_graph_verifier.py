"""Verify source graph consistency chapter by chapter.

After all source chapters are extracted, loops chapter 1 → N and verifies
each chapter's additions against the accumulated graph state up to that point.
If a chapter has critical issues, re-extracts only that chapter (safe because
chapters after it haven't been applied yet in this verification pass).

Max MAX_ITERATIONS repair attempts per chapter — after that, logs remaining
issues and moves on (never blocks the pipeline).

Sets story.source_graph_verified = True when done.
"""

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import PlanningVerifyLog, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphVerifierOutput
from . import chapter_graph_extractor

MAX_ITERATIONS = 10


def _format_chapter_additions(session: Session, story_id: int, chapter_number: int) -> str:
    """Format only the EVENT node and edges introduced at chapter_number."""
    event_key = f"E{chapter_number:03d}"
    event_node = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source", node_key=event_key)
        .first()
    )
    if not event_node:
        return f"(EVENT node {event_key} không tồn tại)"

    lines = [f"EVENT {event_node.node_key} [Ch.{chapter_number}]: {event_node.label}"]
    p = event_node.properties or {}
    if p.get("summary"):
        lines.append(f"  summary: {p['summary']}")
    if p.get("event_type"):
        lines.append(f"  event_type: {p['event_type']} | emotional_weight: {p.get('emotional_weight', '')}")

    new_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source", chapter_introduced=chapter_number)
        .filter(StoryGraphNode.node_type != "event")
        .all()
    )
    if new_nodes:
        lines.append(f"\nNEW NODES introduced at Ch.{chapter_number}:")
        for n in new_nodes:
            lines.append(f"  {n.node_key} [{n.node_type}]: {n.label}")

    new_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source")
        .filter(StoryGraphEdge.chapter_from == chapter_number)
        .all()
    )
    if new_edges:
        lines.append(f"\nNEW EDGES at Ch.{chapter_number}:")
        for e in new_edges:
            p = e.properties or {}
            detail = p.get("rel_type") or p.get("mechanism") or p.get("role") or e.label or ""
            ch_range = f"Ch.{e.chapter_from}"
            if e.chapter_to:
                ch_range += f"→{e.chapter_to}"
            else:
                ch_range += "→∞"
            lines.append(
                f"  {e.source_key} ──[{e.edge_type}: {detail} {ch_range}]──► {e.target_key}"
            )

    return "\n".join(lines)


def run(session: Session, story: Story) -> None:
    """Verify source graph chapter by chapter, repairing critical issues up to
    MAX_ITERATIONS times per chapter.
    """
    system = load_prompt("graph_verifier")

    for chapter_number in range(1, (story.source_chapter_count or 0) + 1):
        for iteration in range(MAX_ITERATIONS):
            # Accumulated graph state up to (and including) this chapter.
            accumulated_graph = context_builder.format_story_graph(
                session, story.id, chapter_limit=chapter_number, graph_type="source"
            )
            # Isolate what chapter N contributed — this is what we're verifying.
            chapter_additions = _format_chapter_additions(
                session, story.id, chapter_number
            )

            user_content = (
                f"language: {story.language}\n"
                f"total_chapters: {story.source_chapter_count}\n\n"
                f"## ACCUMULATED SOURCE GRAPH (chapters 1 to {chapter_number})\n"
                f"{accumulated_graph}\n\n"
                f"## CHAPTER {chapter_number} ADDITIONS (focus your check here)\n"
                f"{chapter_additions}\n"
            )
            output: GraphVerifierOutput = generate_structured(
                PROVIDER,
                system=system,
                user_content=user_content,
                model=AGENT_MODELS["graph_verifier"],
                schema=GraphVerifierOutput,
                max_tokens=4096,
                thinking=False,
            )

            critical = [i for i in output.issues if i.severity == "critical"]
            action = (
                f"reextract_ch{chapter_number}_iter_{iteration + 1}"
                if critical else "logged_only"
            )
            for issue in output.issues:
                session.add(
                    PlanningVerifyLog(
                        story_id=story.id,
                        artifact="source_graph",
                        severity=issue.severity,
                        description=f"[Ch.{chapter_number}] {issue.description}",
                        suggestion=issue.suggestion,
                        action_taken=action,
                    )
                )
            session.flush()

            if not critical:
                break  # chapter N is consistent — move to chapter N+1

            # Re-extract only chapter N (state from chapters 1..N-1 is untouched).
            chapter_graph_extractor.run_for_chapter(session, story, chapter_number)
            session.flush()

    story.source_graph_verified = True
