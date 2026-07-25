"""Verify source graph chapter by chapter.

For each chapter, only verifies what that chapter contributed (EVENT node +
new nodes + new edges) plus minimal context (current character states +
active relations for involved characters). This keeps each call O(additions),
not O(accumulated_graph) — scales to any chapter count.

On critical issues, re-extracts only that chapter (safe because later chapters
haven't been applied yet). Max MAX_ITERATIONS per chapter.

Sets story.source_graph_verified = True when done.
"""

import logging

from sqlalchemy.orm import Session

from ..config import AGENT_MODELS, PROVIDER
from ..db.models import PlanningVerifyLog, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphVerifierOutput
from . import chapter_graph_extractor

MAX_ITERATIONS = 10

logger = logging.getLogger(__name__)


def _format_chapter_additions(session: Session, story_id: int, chapter_number: int) -> str:
    """Format EVENT node, new nodes, and new edges introduced at chapter_number."""
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
            np = n.properties or {}
            detail = ""
            if n.node_type == "character":
                detail = f" | arc_stage={np.get('arc_stage','?')} role={np.get('role','')}"
            lines.append(f"  {n.node_key} [{n.node_type}]: {n.label}{detail}")

    new_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source")
        .filter(StoryGraphEdge.chapter_from == chapter_number)
        .all()
    )
    if new_edges:
        lines.append(f"\nNEW EDGES at Ch.{chapter_number}:")
        for e in new_edges:
            ep = e.properties or {}
            detail = ep.get("rel_type") or ep.get("mechanism") or ep.get("role") or e.label or ""
            ch_to = f"→{e.chapter_to}" if e.chapter_to else "→∞"
            lines.append(f"  {e.source_key} ──[{e.edge_type}: {detail} Ch.{e.chapter_from}{ch_to}]──► {e.target_key}")

    return "\n".join(lines)


def _format_chapter_context(session: Session, story_id: int, chapter_number: int) -> str:
    """Format relevant context: character states + active relations for characters
    involved in this chapter's additions.
    """
    new_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source")
        .filter(StoryGraphEdge.chapter_from == chapter_number)
        .all()
    )

    # Collect character keys involved in this chapter
    char_keys: set[str] = set()
    for e in new_edges:
        if e.source_key and e.source_key.startswith("C"):
            char_keys.add(e.source_key)
        if e.target_key and e.target_key.startswith("C"):
            char_keys.add(e.target_key)

    # Also include characters in PARTICIPATES edges targeting this chapter's event
    event_key = f"E{chapter_number:03d}"
    participates = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source",
                   edge_type="PARTICIPATES", target_key=event_key)
        .all()
    )
    for e in participates:
        if e.source_key and e.source_key.startswith("C"):
            char_keys.add(e.source_key)

    if not char_keys:
        return "(không có nhân vật liên quan)"

    lines = []

    # Current character states
    lines.append("## TRẠNG THÁI NHÂN VẬT LIÊN QUAN (trước chương này)")
    for key in sorted(char_keys):
        node = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story_id, graph_type="source", node_key=key)
            .first()
        )
        if node:
            np = node.properties or {}
            lines.append(
                f"  {key}: {node.label} | arc_stage={np.get('arc_stage', '?')} | role={np.get('role', '')}"
            )

    # Active RELATION edges for involved characters
    active = (
        session.query(StoryGraphEdge)
        .filter(
            StoryGraphEdge.story_id == story_id,
            StoryGraphEdge.graph_type == "source",
            StoryGraphEdge.edge_type == "RELATION",
            StoryGraphEdge.chapter_to.is_(None),
            StoryGraphEdge.source_key.in_(char_keys),
        )
        .all()
    )
    if active:
        lines.append("\n## QUAN HỆ ĐANG HOẠT ĐỘNG (active trước chương này)")
        for e in active:
            ep = e.properties or {}
            rel = ep.get("rel_type", "")
            strength = ep.get("strength", "")
            lines.append(
                f"  {e.source_key}↔{e.target_key}: {rel} strength={strength} [từ Ch.{e.chapter_from}→∞]"
            )

    return "\n".join(lines)


def run(session: Session, story: Story) -> None:
    """Verify source graph chapter by chapter. Each call checks only that
    chapter's additions + minimal context — O(additions) per call.
    """
    system = load_prompt("source_graph_chapter_verifier")
    total = story.source_chapter_count or 0

    for chapter_number in range(1, total + 1):
        logger.info("[%s] verify_source_graph ch%d/%d", story.slug, chapter_number, total)
        for iteration in range(MAX_ITERATIONS):
            additions = _format_chapter_additions(session, story.id, chapter_number)
            context = _format_chapter_context(session, story.id, chapter_number)

            user_content = (
                f"language: {story.language}\n"
                f"chapter_number: {chapter_number}\n"
                f"total_chapters: {total}\n\n"
                f"## CHAPTER {chapter_number} ADDITIONS\n{additions}\n\n"
                f"## CONTEXT\n{context}\n"
            )

            output: GraphVerifierOutput = generate_structured(
                PROVIDER,
                system=system,
                user_content=user_content,
                model=AGENT_MODELS["graph_verifier"],
                schema=GraphVerifierOutput,
                max_tokens=8192,
                thinking=False,
            )

            critical = [i for i in output.issues if i.severity == "critical"]
            action = (
                f"reextract_ch{chapter_number}_iter_{iteration + 1}"
                if critical else "logged_only"
            )
            logger.info("[%s] ch%d iter%d: %d issues (%d critical) | %s",
                        story.slug, chapter_number, iteration + 1,
                        len(output.issues), len(critical), output.verdict_note[:80])
            for issue in critical:
                logger.info("  [CRITICAL] node=%s | %s | fix: %s", issue.node_key, issue.description, issue.suggestion)

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
                break

            logger.info("[%s] ch%d critical=%d, re-extracting", story.slug, chapter_number, len(critical))
            chapter_graph_extractor.run_for_chapter(session, story, chapter_number)
            session.flush()

    story.source_graph_verified = True
