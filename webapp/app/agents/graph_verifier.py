"""Verify the NEW story graph on two axes:

1. CONSISTENCY (all input types): character arcs, causal chains, relation edges,
   structural integrity. On critical issues → graph_repair (surgical fixes).

2. RESKIN QUALITY (REWRITE only): compare new graph against source graph to
   ensure the creative transformation is genuine — different names, different
   settings, no surface copying. On critical reskin issues → new_graph_builder
   with targeted feedback (rename/redesign, not surgical graph surgery).

Max 10 iterations total (bounded cost). Sets story.planning_verified = True
when done or when iterations exhausted — keeps the pipeline from getting stuck.

Note: PlanningVerifyLog reused for audit trail (artifact="graph") — no new table.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import PlanningVerifyLog, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphVerifierOutput
from . import graph_repair, new_graph_builder

MAX_ITERATIONS = 10
logger = logging.getLogger(__name__)


def run(session: Session, story: Story) -> None:
    """Verify the new graph. Routes critical issues to the right repair agent.

    - consistency critical → graph_repair (node/edge surgery)
    - reskin critical → new_graph_builder with feedback (creative redo)
    """
    # Source graph text only needed for REWRITE reskin-quality check
    source_graph_text = ""
    if story.input_type == "REWRITE":
        source_graph_text = context_builder.format_story_graph(
            session, story.id, graph_type="source"
        )
        logger.info("[%s] graph_verifier: REWRITE — will check consistency + reskin quality vs source",
                    story.slug)
    else:
        logger.info("[%s] graph_verifier: checking consistency only (input_type=%s)",
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
                f"\n## SOURCE GRAPH (for RESKIN QUALITY check — compare labels/surface only)\n"
                f"{source_graph_text}\n"
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
            session.add(
                PlanningVerifyLog(
                    story_id=story.id,
                    artifact="graph",
                    severity=issue.severity,
                    description=f"[{issue.check_type}] {issue.description}",
                    suggestion=issue.suggestion,
                    action_taken=(
                        f"{'rebuild' if issue.check_type == 'reskin' else 'repair'}"
                        f"_iter_{iteration + 1}"
                        if issue.severity == "critical"
                        else "logged_only"
                    ),
                )
            )
        session.flush()

        if not critical:
            break

        consistency_critical = [i for i in critical if i.check_type == "consistency"]
        reskin_critical = [i for i in critical if i.check_type == "reskin"]

        if consistency_critical:
            logger.info("[%s] graph_verifier repairing %d consistency issues",
                        story.slug, len(consistency_critical))
            graph_repair.run(session, story, consistency_critical)
            session.flush()

        if reskin_critical:
            feedback_lines = [
                f"- [{i.node_key or 'general'}] {i.description} → Fix: {i.suggestion}"
                for i in reskin_critical
            ]
            feedback = "\n".join(feedback_lines)
            logger.info("[%s] graph_verifier rebuilding new graph: %d reskin issues",
                        story.slug, len(reskin_critical))
            new_graph_builder.run(session, story, feedback=feedback)
            session.flush()
            # After a full rebuild, source_graph_text doesn't change — reuse it

    # Mark gate as passed whether the graph is clean or iterations exhausted.
    story.planning_verified = True
    logger.info("[%s] graph_verifier: done — planning_verified=True", story.slug)
