"""Targeted surface rewriter — fixes specific nodes/edges flagged by graph_verifier.

Unlike new_graph_builder (which rebuilds everything), this only patches the
exact items listed in the issues, leaving all other surface content intact.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphSurfaceRepairOutput, GraphVerifyIssueOut

logger = logging.getLogger(__name__)


def run(
    session: Session,
    story: Story,
    issues: list[GraphVerifyIssueOut],
) -> None:
    """Rewrite surface content for nodes/edges flagged as narrative_logic issues.

    Only patches what's listed — structure (chapter_from, chapter_to, etc.) is
    never touched.
    """
    new_graph_text = context_builder.format_story_graph(session, story.id, graph_type="new")

    issue_lines = [
        f"- [{i.node_key or 'general'}] {i.edge_desc or ''} | {i.description} → Fix: {i.suggestion}"
        for i in issues
    ]
    issues_text = "\n".join(issue_lines)

    system = load_prompt("graph_surface_rewriter")
    user_content = (
        f"language: {story.language}\n\n"
        f"## NEW STORY GRAPH\n{new_graph_text}\n\n"
        f"## ISSUES TO FIX\n{issues_text}\n"
    )

    output: GraphSurfaceRepairOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["graph_surface_rewriter"],
        schema=GraphSurfaceRepairOutput,
        max_tokens=8192,
        thinking=False,
    )

    node_map = {
        n.node_key: n
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new")
        .all()
    }

    for patch in output.node_patches:
        node = node_map.get(patch.node_key)
        if not node:
            logger.warning("[%s] graph_surface_rewriter: node %s not found", story.slug, patch.node_key)
            continue
        if patch.new_label:
            node.label = patch.new_label
        props = dict(node.properties or {})
        if patch.new_summary:      props["summary"]     = patch.new_summary
        if patch.new_profile_md:   props["profile_md"]  = patch.new_profile_md
        if patch.new_description:  props["description"] = patch.new_description
        node.properties = props

    for patch in output.edge_patches:
        edge = (
            session.query(StoryGraphEdge)
            .filter_by(
                story_id=story.id, graph_type="new",
                source_key=patch.source_key, target_key=patch.target_key,
                edge_type=patch.edge_type,
            )
            .first()
        )
        if not edge:
            # LLM often embeds full edge descriptions in edge_type (e.g.
            # "RELATION ?→∞" or "betrayal strength=strong"). Fall back to
            # matching by source/target only, preferring the edge whose
            # edge_type is a prefix of the LLM's raw string.
            candidates = (
                session.query(StoryGraphEdge)
                .filter_by(story_id=story.id, graph_type="new",
                           source_key=patch.source_key, target_key=patch.target_key)
                .all()
            )
            raw_upper = patch.edge_type.upper()
            edge = next((e for e in candidates if raw_upper.startswith(e.edge_type)), None)
            if not edge and candidates:
                edge = candidates[0]
            if edge:
                logger.info("[%s] graph_surface_rewriter: edge %s→%s matched by fallback"
                            " (patch.edge_type=%r → actual %r)",
                            story.slug, patch.source_key, patch.target_key,
                            patch.edge_type, edge.edge_type)
            else:
                logger.warning("[%s] graph_surface_rewriter: edge %s→%s %s not found",
                               story.slug, patch.source_key, patch.target_key, patch.edge_type)
                continue
        if patch.new_label:
            edge.label = patch.new_label
        if patch.new_mechanism:
            props = dict(edge.properties or {})
            props["mechanism"] = patch.new_mechanism
            edge.properties = props

    session.flush()
    logger.info("[%s] graph_surface_rewriter: patched %d nodes, %d edges — %s",
                story.slug, len(output.node_patches), len(output.edge_patches),
                output.repair_note)
