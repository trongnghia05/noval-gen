"""Verify logical consistency of the NEW story graph.

Checks that character arcs, causal chains, relationship changes, and world
details are internally consistent. On critical issues, calls graph_repair
to apply surgical fixes (update/delete/add specific nodes and edges) rather
than rebuilding the entire graph.

Max 10 iterations total (bounded cost guarantee). Sets story.planning_verified
= True when done (or when max iterations exhausted).

Note: we reuse PlanningVerifyLog for audit trail (artifact="graph") rather
than adding a new table — keeps schema minimal.
"""

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import PlanningVerifyLog, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphVerifierOutput
from . import graph_repair

MAX_ITERATIONS = 10


def run(session: Session, story: Story) -> None:
    """Verify the new graph, rebuilding it with targeted feedback on critical issues.

    Iterates up to MAX_ITERATIONS times. Stops early when no critical issues
    remain. After the loop (whether clean or exhausted), sets
    story.planning_verified = True so the orchestrator gates the planning phase
    as done regardless of outcome — keeps the pipeline from getting stuck.
    """
    for iteration in range(MAX_ITERATIONS):
        new_graph_text = context_builder.format_story_graph(
            session, story.id, graph_type="new"
        )
        system = load_prompt("graph_verifier")
        user_content = (
            f"language: {story.language}\n"
            f"total_chapters: {story.total_chapters}\n\n"
            f"## NEW STORY GRAPH\n{new_graph_text}\n"
        )
        output: GraphVerifierOutput = generate_structured(
            PROVIDER,
            system=system,
            user_content=user_content,
            model=AGENT_MODELS["graph_verifier"],
            schema=GraphVerifierOutput,
            max_tokens=8192,
            thinking=False,  # logic checking, not creative reasoning
        )

        # Log every issue found (all severities) to the audit trail.
        for issue in output.issues:
            session.add(
                PlanningVerifyLog(
                    story_id=story.id,
                    artifact="graph",
                    severity=issue.severity,
                    description=issue.description,
                    suggestion=issue.suggestion,
                    action_taken=(
                        f"rebuild_iter_{iteration + 1}"
                        if issue.severity == "critical"
                        else "logged_only"
                    ),
                )
            )
        session.flush()

        critical = [i for i in output.issues if i.severity == "critical"]
        if not critical:
            break  # graph is internally consistent — done

        # Surgical repair: fix only the affected nodes/edges, not the whole graph.
        graph_repair.run(session, story, critical)
        session.flush()

    # Mark gate as passed whether the graph is clean or iterations exhausted.
    story.planning_verified = True
