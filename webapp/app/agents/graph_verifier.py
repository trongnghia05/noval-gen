"""Verify the new story graph on three axes:

1. NARRATIVE LOGIC (all input types): check that surface content is coherent —
   CAUSES mechanisms make sense, ARC_CHANGE events justify arc shifts, overall
   story has a clear shape. Critical → graph_surface_rewriter (targeted patch).

2. RESKIN QUALITY (REWRITE only): surface is genuinely different from source —
   no copied names, no near-verbatim summaries. Critical → new_graph_builder
   surface rebuild with feedback.

3. ENRICHMENT VALIDITY (always): Phase 3 additions don't violate constraints —
   no EVENT nodes added, no CAUSES/ARC_CHANGE from enrichment nodes. Critical →
   remove the offending enrichment node/edge directly.

Max 5 iterations. Sets story.new_graph_verified = True when done or exhausted.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import PlanningVerifyLog, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphVerifierOutput
from . import graph_surface_rewriter, new_graph_builder

MAX_ITERATIONS = 5
logger = logging.getLogger(__name__)


def _remove_enrichment_nodes(session: Session, story_id: int, node_keys: list[str]) -> None:
    """Remove invalid enrichment nodes and their edges."""
    for key in node_keys:
        session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="new").filter(
            (StoryGraphEdge.source_key == key) | (StoryGraphEdge.target_key == key)
        ).delete(synchronize_session="fetch")
        session.query(StoryGraphNode).filter_by(
            story_id=story_id, graph_type="new", node_key=key
        ).delete(synchronize_session="fetch")
    session.flush()


def run(session: Session, story: Story) -> None:
    """Verify the new graph. Routes critical issues to the right repair agent."""
    source_graph_text = ""
    if story.input_type == "REWRITE":
        source_graph_text = context_builder.format_story_graph(
            session, story.id, graph_type="source"
        )
        logger.info("[%s] graph_verifier: REWRITE — checking narrative_logic + reskin + enrichment",
                    story.slug)
    else:
        logger.info("[%s] graph_verifier: checking narrative_logic + enrichment (input_type=%s)",
                    story.slug, story.input_type)

    for iteration in range(MAX_ITERATIONS):
        new_graph_text = context_builder.format_story_graph(
            session, story.id, graph_type="new"
        )
        system = load_prompt("graph_verifier")
        user_content = (
            f"language: {story.language}\n"
            f"input_type: {story.input_type}\n"
            f"total_chapters: {story.total_chapters}\n\n"
            f"## NEW STORY GRAPH\n{new_graph_text}\n"
        )
        if source_graph_text:
            user_content += (
                f"\n## SOURCE GRAPH (for RESKIN QUALITY check)\n{source_graph_text}\n"
            )

        output: GraphVerifierOutput = generate_structured(
            PROVIDER,
            system=system,
            user_content=user_content,
            model=AGENT_MODELS["graph_verifier"],
            schema=GraphVerifierOutput,
            max_tokens=32768,
            thinking=False,
        )

        critical = [i for i in output.issues if i.severity == "critical"]
        logger.info("[%s] graph_verifier iter%d: %d issues (%d critical) | %s",
                    story.slug, iteration + 1, len(output.issues), len(critical),
                    output.verdict_note)
        for issue in output.issues:
            logger.info("  [%s][%s] node=%s | %s | fix: %s",
                        issue.severity.upper(), issue.check_type, issue.node_key,
                        issue.description, issue.suggestion)

        for issue in output.issues:
            session.add(PlanningVerifyLog(
                story_id=story.id,
                artifact="graph",
                severity=issue.severity,
                description=f"[{issue.check_type}] {issue.description}",
                suggestion=issue.suggestion,
                action_taken=(
                    f"{issue.check_type}_repair_iter_{iteration + 1}"
                    if issue.severity == "critical"
                    else "logged_only"
                ),
            ))
        session.flush()

        if not critical:
            break

        narrative_critical = [i for i in critical if i.check_type == "narrative_logic"]
        reskin_critical    = [i for i in critical if i.check_type == "reskin"]
        enrichment_critical = [i for i in critical if i.check_type == "enrichment"]

        if narrative_critical:
            logger.info("[%s] graph_verifier: surface-rewriting %d narrative_logic issues",
                        story.slug, len(narrative_critical))
            graph_surface_rewriter.run(session, story, narrative_critical)
            session.flush()

        if reskin_critical:
            feedback_lines = [
                f"- [{i.node_key or 'general'}] {i.description} → Fix: {i.suggestion}"
                for i in reskin_critical
            ]
            feedback = "\n".join(feedback_lines)
            logger.info("[%s] graph_verifier: rebuilding surface for %d reskin issues",
                        story.slug, len(reskin_critical))
            new_graph_builder.run(session, story, feedback=feedback)
            session.flush()

        if enrichment_critical:
            bad_keys = [i.node_key for i in enrichment_critical if i.node_key]
            if bad_keys:
                logger.info("[%s] graph_verifier: removing %d invalid enrichment nodes: %s",
                            story.slug, len(bad_keys), bad_keys)
                _remove_enrichment_nodes(session, story.id, bad_keys)

    story.new_graph_verified = True
    logger.info("[%s] graph_verifier: done — new_graph_verified=True", story.slug)
