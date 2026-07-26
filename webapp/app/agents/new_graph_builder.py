"""Build the NEW story graph from the source graph — three phases.

Phase 1 (Python): copy ALL nodes and edges from source graph verbatim,
  preserving exact structure (chapter_from, chapter_to, edge types, timing).

Phase 2 (LLM): rename every piece of visible text — character names,
  locations, event summaries, profiles, mechanism text — while the structure
  stays locked. Output: NewGraphSurfaceOutput (labels + text fields only).

Phase 3 (LLM): creative enrichment — add minor characters, objects, themes,
  foreshadowing threads that make the world feel original without touching
  the plot structure. Output: GraphEnrichmentOutput.

After all three phases: derives story.plot_outline, story.world_bible, and
Character rows so downstream agents keep working unchanged.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import GraphEnrichmentOutput, GraphNodeOut, NewGraphSurfaceOutput

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _clear_new_graph(session: Session, story_id: int) -> None:
    session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="new").delete(synchronize_session="fetch")
    session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="new").delete(synchronize_session="fetch")


def _build_plot_outline(session: Session, story_id: int) -> str:
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
    session.query(Character).filter_by(story_id=story.id).delete(synchronize_session="fetch")
    session.flush()
    char_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .all()
    )
    seen_names: set[str] = set()
    for n in char_nodes:
        name_key = n.label.strip().lower()
        if name_key in seen_names:
            logger.warning("[story %d] _rebuild_characters: duplicate label '%s' (node %s) — skipped",
                           story.id, n.label, n.node_key)
            continue
        seen_names.add(name_key)
        p = n.properties or {}
        role = p.get("role", "minor")
        tier = (
            "core" if role == "protagonist"
            else ("important" if role in ("antagonist", "supporting") else "secondary")
        )
        profile_md = p.get("profile_md") or (
            f"**Role**: {role}\n"
            f"**Wants**: {p.get('wants', '')}\n"
            f"**Fears**: {p.get('fears', '')}\n"
            f"**Arc**: {p.get('arc_stage', '')}\n"
            f"**Background**: {p.get('background', '')}\n"
            f"**Speech**: {p.get('speech_pattern', '')}"
        )
        session.add(Character(
            story_id=story.id,
            name=n.label,
            aliases=p.get("aliases", []),
            tier=tier,
            profile_md=profile_md,
        ))


# ── Phase 1: Python structure copy ────────────────────────────────────────────

def _copy_source_structure(session: Session, story_id: int) -> None:
    """Copy all source nodes/edges to new graph, preserving structure exactly."""
    _clear_new_graph(session, story_id)

    source_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source")
        .all()
    )
    for n in source_nodes:
        session.add(StoryGraphNode(
            story_id=story_id,
            graph_type="new",
            node_key=n.node_key,
            node_type=n.node_type,
            label=n.label,                        # placeholder; Phase 2 overwrites
            properties=dict(n.properties or {}),  # placeholder; Phase 2 overwrites
            chapter_introduced=n.chapter_introduced,
        ))
    session.flush()

    source_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source")
        .all()
    )
    for e in source_edges:
        session.add(StoryGraphEdge(
            story_id=story_id,
            graph_type="new",
            source_key=e.source_key,
            target_key=e.target_key,
            edge_type=e.edge_type,
            label=e.label or "",
              # structural metadata — locked from source:
            chapter_from=e.chapter_from,
            chapter_to=e.chapter_to,
            trigger_event_key=e.trigger_event_key,
            condition=e.condition,
            properties=dict(e.properties or {}),
        ))
    session.flush()
    logger.info("[story %d] Phase 1: copied %d nodes, %d edges from source",
                story_id, len(source_nodes), len(source_edges))


# ── Phase 2: LLM surface rename ────────────────────────────────────────────────

def _format_source_for_surface(session: Session, story_id: int) -> str:
    """Build a compact source-node listing for the LLM surface prompt."""
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source")
        .order_by(StoryGraphNode.node_type, StoryGraphNode.chapter_introduced)
        .all()
    )
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source")
        .all()
    )

    node_lines = []
    for n in nodes:
        p = n.properties or {}
        line = f"[{n.node_key}] {n.node_type.upper()}: '{n.label}'"
        if n.node_type == "character":
            line += (f" | wants: {p.get('wants', '')}"
                     f" | fears: {p.get('fears', '')}"
                     f" | arc: {p.get('arc_stage', '')}"
                     f" | role: {p.get('role', '')}")
        elif n.node_type == "event":
            summary = (p.get("summary") or "")[:120]
            line += (f" | ch{n.chapter_introduced}"
                     f" | type: {p.get('event_type', '')}"
                     f" | weight: {p.get('emotional_weight', '')}"
                     f" | {summary}")
        elif n.node_type == "location":
            desc = (p.get("description") or "")[:80]
            line += f" | {desc}"
        node_lines.append(line)

    # Include only text-bearing edges (CAUSES mechanism, RELATION condition)
    edge_lines = []
    for e in edges:
        p = e.properties or {}
        if e.edge_type == "CAUSES":
            mech = (p.get("mechanism") or "")[:100]
            edge_lines.append(f"[{e.source_key}→{e.target_key}] CAUSES ch{e.chapter_from}: {mech}")
        elif e.edge_type == "RELATION" and e.condition:
            edge_lines.append(
                f"[{e.source_key}↔{e.target_key}] RELATION {p.get('rel_type', '')} "
                f"ch{e.chapter_from}-{e.chapter_to}: {e.condition[:80]}"
            )

    parts = ["## NODES\n" + "\n".join(node_lines)]
    if edge_lines:
        parts.append("## KEY EDGES (text only — structure is preserved)\n" + "\n".join(edge_lines[:40]))
    return "\n\n".join(parts)


def _rename_surface(session: Session, story: Story, feedback: str | None = None) -> str:
    """Phase 2: ask LLM to rename all surface content; apply to new graph nodes/edges."""
    source_summary = _format_source_for_surface(session, story.id)

    system = load_prompt("new_graph_builder")
    user_content = (
        f"language: {story.language}\n"
        f"genre: {story.genre or '(AI decides)'}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## SOURCE NODES TO REIMAGINE\n{source_summary}\n"
    )
    if feedback:
        user_content += f"\n## FEEDBACK — fix these specific issues:\n{feedback}\n"

    output: NewGraphSurfaceOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["new_graph_builder"],
        schema=NewGraphSurfaceOutput,
        max_tokens=32768,
        thinking=True,
    )

    node_map = {
        n.node_key: n
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new")
        .all()
    }

    for surf in output.node_surfaces:
        node = node_map.get(surf.node_key)
        if not node:
            continue
        node.label = surf.new_label
        props = dict(node.properties or {})
        if node.node_type == "character":
            if surf.new_profile_md:     props["profile_md"]     = surf.new_profile_md
            if surf.new_wants:          props["wants"]          = surf.new_wants
            if surf.new_fears:          props["fears"]          = surf.new_fears
            if surf.new_arc_stage:      props["arc_stage"]      = surf.new_arc_stage
            if surf.new_background:     props["background"]     = surf.new_background
            if surf.new_speech_pattern: props["speech_pattern"] = surf.new_speech_pattern
        elif node.node_type == "event":
            if surf.new_summary: props["summary"] = surf.new_summary
        else:  # location, faction, theme, object
            if surf.new_description: props["description"] = surf.new_description
        node.properties = props

    for surf in output.edge_surfaces:
        edge = (
            session.query(StoryGraphEdge)
            .filter_by(
                story_id=story.id, graph_type="new",
                source_key=surf.source_key, target_key=surf.target_key,
                edge_type=surf.edge_type,
            )
            .first()
        )
        if not edge:
            continue
        if surf.new_label:
            edge.label = surf.new_label
        if surf.new_condition:
            edge.condition = surf.new_condition
        props = dict(edge.properties or {})
        if surf.new_mechanism:  props["mechanism"] = surf.new_mechanism
        if surf.new_old_val:    props["old_val"]   = surf.new_old_val
        if surf.new_new_val:    props["new_val"]   = surf.new_new_val
        edge.properties = props

    session.flush()
    logger.info("[%s] Phase 2: renamed surface for %d nodes, %d edges",
                story.slug, len(output.node_surfaces), len(output.edge_surfaces))
    return output.narrative_summary


# ── Phase 3: LLM creative enrichment ─────────────────────────────────────────

def _enrich_graph(session: Session, story: Story) -> None:
    """Phase 3: add minor characters, objects, foreshadowing without touching plot."""
    new_graph_text = context_builder.format_story_graph(session, story.id, graph_type="new")

    system = load_prompt("graph_enricher")
    user_content = (
        f"language: {story.language}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## NEW GRAPH (after surface rename)\n{new_graph_text}\n"
    )

    output: GraphEnrichmentOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["graph_enricher"],
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
        # Build properties from typed fields
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

    logger.info("[%s] Phase 3: enriched with %d nodes, %d edges — %s",
                story.slug, len(output.new_nodes), len(output.new_edges),
                output.enrichment_note)


# ── Public entry point ────────────────────────────────────────────────────────

def run(session: Session, story: Story, feedback: str | None = None) -> None:
    """Build (or rebuild) the new graph in three phases.

    feedback: if set (from graph_verifier reskin issues), passed to Phase 2
    to guide the surface rename toward fixing the flagged problems.
    On feedback rebuild, Phase 1 re-copies the source structure from scratch.
    """
    logger.info("[%s] new_graph_builder: start (feedback=%s)", story.slug, bool(feedback))

    # Phase 1 — always re-copy structure from source (even on feedback rebuild,
    # to ensure structure stays clean)
    _copy_source_structure(session, story.id)

    # Phase 2 — LLM rename surface
    narrative_summary = _rename_surface(session, story, feedback)
    story.story_bible = narrative_summary

    # Phase 3 — creative enrichment (skip on feedback rebuild to save cost;
    # verifier will re-run and can flag enrichment issues separately)
    if not feedback:
        _enrich_graph(session, story)

    # Derive downstream text artifacts
    story.plot_outline = _build_plot_outline(session, story.id)
    story.world_bible = _build_world_bible(session, story.id)
    _rebuild_characters(session, story)
    story.new_graph_built = True
    logger.info("[%s] new_graph_builder: done", story.slug)
