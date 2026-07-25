"""Build the NEW story graph from the source graph.

For REWRITE: reads the source graph (extracted from original chapters) and
produces a creative transformation — same narrative structure (arc, causality,
relationship changes) but completely different surface (settings, how events
happen, specific details).

Output: StoryGraphNode/Edge rows with graph_type="new", plus populates
story.plot_outline, story.world_bible, and Character rows so existing
chapter_writer/blueprinter agents keep working without modification.

DB schema note: requires graph_type column on StoryGraphNode and StoryGraphEdge
(added alongside this file). Run `docker compose down -v` to reset the DB
after pulling these schema changes.
"""

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import StoryAnalyzerOutput  # reuse — same node/edge schema


def _clear_new_graph(session: Session, story_id: int) -> None:
    session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="new").delete()
    session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="new").delete()


def _build_plot_outline(session: Session, story_id: int) -> str:
    """Build plot_outline text from new graph EVENT nodes, ordered by chapter."""
    events = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="new", node_type="event")
        .order_by(StoryGraphNode.chapter_introduced)
        .all()
    )
    lines = ["# Plot Outline\n"]
    for e in events:
        p = e.properties or {}
        ch = e.chapter_introduced or "?"
        lines.append(f"## Chapter {ch}: {e.label}")
        lines.append(p.get("summary", ""))
        if p.get("event_type"):
            lines.append(f"Type: {p['event_type']} | Weight: {p.get('emotional_weight', '')}")
        lines.append("")
    return "\n".join(lines)


def _build_world_bible(session: Session, story_id: int) -> str:
    """Build world_bible text from LOCATION/FACTION/THEME/OBJECT nodes in new graph."""
    lines = ["# World\n"]
    for ntype, header in [
        ("location", "Locations"),
        ("faction", "Factions"),
        ("theme", "Themes"),
        ("object", "Key Objects"),
    ]:
        nodes = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story_id, graph_type="new", node_type=ntype)
            .all()
        )
        if nodes:
            lines.append(f"## {header}")
            for n in nodes:
                p = n.properties or {}
                desc = (
                    p.get("description")
                    or p.get("goal")
                    or p.get("central_question")
                    or p.get("symbolic_meaning")
                    or ""
                )
                lines.append(f"### {n.label} ({n.node_key})")
                lines.append(desc)
                lines.append("")
    return "\n".join(lines)


def _rebuild_characters(session: Session, story: Story) -> None:
    """Rebuild Character rows from CHARACTER nodes in the new graph.

    Safe to wipe+rebuild here because this runs before WRITING starts.
    """
    session.query(Character).filter_by(story_id=story.id).delete()
    session.flush()
    char_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .all()
    )
    for n in char_nodes:
        p = n.properties or {}
        role = p.get("role", "minor")
        tier = (
            "core"
            if role == "protagonist"
            else ("important" if role in ("antagonist", "supporting") else "secondary")
        )
        # Use explicit profile_md from properties if the model provided one;
        # otherwise synthesise a markdown block from structured fields.
        profile_md = p.get("profile_md") or (
            f"**Role**: {role}\n"
            f"**Wants**: {p.get('wants', '')}\n"
            f"**Fears**: {p.get('fears', '')}\n"
            f"**Arc**: {p.get('arc_stage', '')}\n"
            f"**Background**: {p.get('background', '')}\n"
            f"**Speech**: {p.get('speech_pattern', '')}"
        )
        session.add(
            Character(
                story_id=story.id,
                name=n.label,
                aliases=p.get("aliases", []),
                tier=tier,
                profile_md=profile_md,
            )
        )


def run(session: Session, story: Story, feedback: str | None = None) -> None:
    """Build (or rebuild with feedback) the new graph from the source graph.

    Reads the full source graph, calls the LLM with extended thinking to
    produce a creative transformation, then:
      1. Clears any previous new graph rows.
      2. Inserts new nodes/edges with graph_type="new".
      3. Derives story.plot_outline from EVENT nodes.
      4. Derives story.world_bible from world-building nodes.
      5. Rebuilds Character rows from CHARACTER nodes.
      6. Marks story.new_graph_built = True.
    """
    source_graph_text = context_builder.format_story_graph(
        session, story.id, graph_type="source"
    )
    system = load_prompt("new_graph_builder")
    user_content = (
        f"language: {story.language}\n"
        f"input_type: {story.input_type}\n"
        f"genre: {story.genre or '(AI decides)'}\n"
        f"total_chapters: {story.total_chapters}\n"
        f"words_per_chapter: {story.words_per_chapter}\n\n"
        f"## SOURCE GRAPH\n{source_graph_text}\n"
    )
    if feedback:
        user_content += (
            f"\n## FEEDBACK FROM VERIFIER — fix these specific nodes/edges:\n{feedback}\n"
        )

    output: StoryAnalyzerOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["new_graph_builder"],
        schema=StoryAnalyzerOutput,
        max_tokens=65536,
        thinking=True,  # creative transformation needs reasoning
    )

    _clear_new_graph(session, story.id)

    for node in output.nodes:
        session.add(
            StoryGraphNode(
                story_id=story.id,
                graph_type="new",
                node_key=node.id,
                node_type=node.node_type,
                label=node.label,
                properties=node.properties or {},
                chapter_introduced=node.chapter_introduced,
            )
        )
    session.flush()

    # Track inserted RELATION edges to deduplicate within the same LLM output.
    # Same pair + same rel_type active simultaneously = keep the one with later chapter_from.
    seen_active_relations: dict[tuple, StoryGraphEdge] = {}
    for edge in output.edges:
        # Build DB properties dict from typed fields (fall back to properties dict for generic edges)
        if edge.edge_type == "ARC_CHANGE":
            db_props = {
                "field": edge.arc_field,
                "old_val": edge.old_val,
                "new_val": edge.new_val,
            }
            db_label = f"{edge.old_val} → {edge.new_val}"
        elif edge.edge_type == "RELATION":
            db_props = {"rel_type": edge.rel_type, "strength": edge.strength}
            db_label = edge.label
        elif edge.edge_type == "PARTICIPATES":
            db_props = {"role": edge.role}
            db_label = edge.label
        elif edge.edge_type == "CAUSES":
            db_props = {"mechanism": edge.mechanism}
            db_label = "dẫn đến"
        elif edge.edge_type == "LOCATED_AT":
            db_props = {}
            db_label = edge.label
        else:
            db_props = edge.properties or {}
            db_label = edge.label

        if edge.edge_type == "RELATION" and edge.chapter_to is None:
            rel_type = edge.rel_type or ""
            key = (edge.source_id, edge.target_id, rel_type)
            if key in seen_active_relations:
                # Keep whichever has the later chapter_from
                if (edge.chapter_from or 0) > (seen_active_relations[key].chapter_from or 0):
                    seen_active_relations[key].chapter_from = edge.chapter_from
                    seen_active_relations[key].properties = db_props
                continue
            row = StoryGraphEdge(
                story_id=story.id,
                graph_type="new",
                source_key=edge.source_id,
                target_key=edge.target_id,
                edge_type=edge.edge_type,
                label=db_label,
                chapter_from=edge.chapter_from,
                chapter_to=edge.chapter_to,
                trigger_event_key=edge.trigger_event_id,
                condition=edge.condition,
                properties=db_props,
            )
            session.add(row)
            seen_active_relations[key] = row
        else:
            session.add(
                StoryGraphEdge(
                    story_id=story.id,
                    graph_type="new",
                    source_key=edge.source_id,
                    target_key=edge.target_id,
                    edge_type=edge.edge_type,
                    label=db_label,
                    chapter_from=edge.chapter_from,
                    chapter_to=edge.chapter_to,
                    trigger_event_key=edge.trigger_event_id,
                    condition=edge.condition,
                    properties=db_props,
                )
            )
    session.flush()

    # Populate downstream text artifacts so chapter_writer keeps working
    story.plot_outline = _build_plot_outline(session, story.id)
    story.world_bible = _build_world_bible(session, story.id)
    _rebuild_characters(session, story)
    story.new_graph_built = True
