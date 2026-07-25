"""Surgical repair of specific issues in the new story graph.

Instead of rebuilding the entire new graph, reads the affected nodes and
their local subgraphs (from both source and new graph), then outputs only
the minimal node/edge changes needed to fix the critical issues.
"""

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import PlanningVerifyLog, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphRepairOutput, GraphVerifyIssueOut


def run(session: Session, story: Story, issues: list[GraphVerifyIssueOut]) -> None:
    """Apply surgical fixes for a list of critical graph issues.

    For each affected node_key, fetches the 1-hop subgraph from both source
    and new graph so the repair agent has structural ground truth alongside
    the current broken state. Applies node_updates, edge_deletes, edge_adds.
    """
    if not issues:
        return

    affected_keys = list({i.node_key for i in issues if i.node_key})
    source_subgraphs = []
    new_subgraphs = []
    for key in affected_keys:
        source_subgraphs.append(
            context_builder.format_node_subgraph(session, story.id, key, graph_type="source", max_depth=1)
        )
        new_subgraphs.append(
            context_builder.format_node_subgraph(session, story.id, key, graph_type="new", max_depth=1)
        )

    full_new_graph = context_builder.format_story_graph(session, story.id, graph_type="new")

    issues_text = "\n".join(
        f"- node_key={i.node_key or 'N/A'} | edge={i.edge_desc or 'N/A'}\n"
        f"  ISSUE: {i.description}\n"
        f"  FIX: {i.suggestion}"
        for i in issues
    )

    system = load_prompt("graph_repair")
    source_block = "\n\n".join(source_subgraphs) if source_subgraphs else "(no source subgraphs)"
    new_block = "\n\n".join(new_subgraphs) if new_subgraphs else "(no new subgraphs)"
    user_content = (
        f"language: {story.language}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## ISSUES TO FIX\n{issues_text}\n\n"
        f"## SOURCE SUBGRAPHS (ground truth for structure)\n{source_block}\n\n"
        f"## NEW SUBGRAPHS (current state — what needs to change)\n{new_block}\n\n"
        f"## FULL NEW GRAPH (for context)\n{full_new_graph}\n"
    )

    output: GraphRepairOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["graph_verifier"],
        schema=GraphRepairOutput,
        max_tokens=8192,
        thinking=False,
    )

    for upd in output.node_updates:
        node = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story.id, graph_type="new", node_key=upd.node_key)
            .first()
        )
        if node:
            node.properties = upd.properties

    for d in output.edge_deletes:
        q = session.query(StoryGraphEdge).filter_by(
            story_id=story.id,
            graph_type="new",
            source_key=d.source_key,
            target_key=d.target_key,
            edge_type=d.edge_type,
        )
        if d.chapter_from is not None:
            q = q.filter(StoryGraphEdge.chapter_from == d.chapter_from)
        q.delete(synchronize_session=False)

    for a in output.edge_adds:
        src_exists = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story.id, graph_type="new", node_key=a.source_id)
            .first()
        )
        tgt_exists = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story.id, graph_type="new", node_key=a.target_id)
            .first()
        )
        if not src_exists or not tgt_exists:
            continue
        session.add(StoryGraphEdge(
            story_id=story.id,
            graph_type="new",
            source_key=a.source_id,
            target_key=a.target_id,
            edge_type=a.edge_type,
            label=a.label,
            chapter_from=a.chapter_from,
            chapter_to=a.chapter_to,
            trigger_event_key=a.trigger_event_id,
            condition=a.condition,
            properties=a.properties or {},
        ))

    session.flush()

    session.add(PlanningVerifyLog(
        story_id=story.id,
        artifact="graph",
        severity="minor",
        description=f"Surgical repair applied: {output.repair_note}",
        suggestion="",
        action_taken=f"surgical_repair_{len(issues)}_issues",
    ))
