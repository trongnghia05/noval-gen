"""Standalone graph enricher — called from new_graph_builder Phase 3.

Kept as a separate module so it can be invoked independently if needed
(e.g. re-enrich after a reskin rebuild without re-running Phase 1+2).
"""

import logging

from sqlalchemy.orm import Session

from . import _common
from ..services import context_builder
from ..db.models import Story, StoryGraphEdge, StoryGraphNode
from ..schemas import GraphEnrichmentOutput

logger = logging.getLogger(__name__)


def run(session: Session, story: Story) -> GraphEnrichmentOutput:
    """Add minor characters, objects, foreshadowing to the new graph.

    Returns the enrichment output so callers can log or inspect it.
    """
    new_graph_text = context_builder.format_story_graph(session, story.id, graph_type="new")

    user_content = (
        f"language: {story.language}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## NEW GRAPH (after surface rename)\n{new_graph_text}\n"
    )

    output: GraphEnrichmentOutput = _common.call_agent(
        "graph_enricher",
        user_content=user_content,
        schema=GraphEnrichmentOutput,
        max_tokens=8192,
        thinking=False,
    )

    for node in output.new_nodes:
        session.add(StoryGraphNode(
            story_id=story.id,
            graph_type="new",
            node_key=node.id,
            node_type=node.node_type,
            label=node.label,
            properties=node.properties or {},
            chapter_introduced=node.chapter_introduced,
        ))
    session.flush()

    for edge in output.new_edges:
        if edge.edge_type == "PARTICIPATES":
            db_props = {"role": edge.role}
        elif edge.edge_type == "FORESHADOWS":
            db_props = edge.properties or {}
        else:
            db_props = edge.properties or {}

        session.add(StoryGraphEdge(
            story_id=story.id,
            graph_type="new",
            source_key=edge.source_id,
            target_key=edge.target_id,
            edge_type=edge.edge_type,
            label=edge.label or "",
            chapter_from=edge.chapter_from,
            chapter_to=edge.chapter_to,
            trigger_event_key=edge.trigger_event_id,
            condition=edge.condition,
            properties=db_props,
        ))
    session.flush()

    logger.info("[%s] graph_enricher: +%d nodes, +%d edges — %s",
                story.slug, len(output.new_nodes), len(output.new_edges),
                output.enrichment_note)
    return output
