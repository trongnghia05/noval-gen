"""Build the NEW story graph from the source graph — three phases.

Phase 0 (LLM): design the new world — setting, genre, tone, protagonist/antagonist
  archetypes, location concepts. Output stored in story.story_bible.

Phase 1 (Python): copy ALL nodes and edges from source graph verbatim,
  preserving exact structure (chapter_from, chapter_to, edge types, timing).

Phase 1b (LLM): build a name lexicon (source_label → new_label) for all
  characters, locations, factions, and objects. Names decided once upfront.

Phase 2 (LLM): write rich content for every node and edge using the world
  design + lexicon as anchors. No name invention — focus purely on quality.

Phase 3 (LLM): creative enrichment — add minor characters, objects, themes,
  foreshadowing without touching the plot structure.

After all phases: rebuilds Character rows so downstream agents work unchanged.
story.plot_outline and story.world_bible are NOT set here — plot_architect and
worldbuilder run as separate orchestrator steps after graph_verifier.
"""

import logging

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import (
    GraphEnrichmentOutput,
    NameLexiconOutput,
    NewGraphSurfaceOutput,
    WorldDesignOutput,
)

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _clear_new_graph(session: Session, story_id: int) -> None:
    session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="new").delete(synchronize_session="fetch")
    session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="new").delete(synchronize_session="fetch")


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


def _format_source_compact(session: Session, story_id: int) -> str:
    """Compact source-node listing for world_designer and name_lexicon."""
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source")
        .order_by(StoryGraphNode.node_type, StoryGraphNode.chapter_introduced)
        .all()
    )
    lines = []
    for n in nodes:
        p = n.properties or {}
        line = f"[{n.node_key}] {n.node_type.upper()}: '{n.label}'"
        if n.node_type == "character":
            line += (f" | role: {p.get('role', '')}"
                     f" | wants: {p.get('wants', '')}"
                     f" | arc: {p.get('arc_stage', '')}")
        elif n.node_type == "event":
            summary = (p.get("summary") or "")[:100]
            line += f" | ch{n.chapter_introduced} | {p.get('event_type', '')} | {summary}"
        elif n.node_type == "location":
            desc = (p.get("description") or "")[:80]
            line += f" | {desc}"
        lines.append(line)
    return "\n".join(lines)


def _format_source_for_surface(session: Session, story_id: int) -> str:
    """Detailed source-node listing for the surface content call (Phase 2)."""
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
        elif e.edge_type == "ARC_CHANGE":
            old_v = p.get("old_val", "")
            new_v = p.get("new_val", "")
            edge_lines.append(f"[{e.source_key}] ARC_CHANGE ch{e.chapter_from}: {old_v} → {new_v}")

    parts = ["## NODES\n" + "\n".join(node_lines)]
    if edge_lines:
        parts.append("## KEY EDGES (text fields to rewrite)\n" + "\n".join(edge_lines[:60]))
    return "\n\n".join(parts)


# ── Phase 1: Python structure copy ────────────────────────────────────────────

def _copy_source_structure(session: Session, story_id: int) -> None:
    _clear_new_graph(session, story_id)
    source_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source")
        .all()
    )
    for n in source_nodes:
        session.add(StoryGraphNode(
            story_id=story_id, graph_type="new",
            node_key=n.node_key, node_type=n.node_type,
            label=n.label, properties=dict(n.properties or {}),
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
            story_id=story_id, graph_type="new",
            source_key=e.source_key, target_key=e.target_key,
            edge_type=e.edge_type, label=e.label or "",
            chapter_from=e.chapter_from, chapter_to=e.chapter_to,
            trigger_event_key=e.trigger_event_key, condition=e.condition,
            properties=dict(e.properties or {}),
        ))
    session.flush()
    logger.info("[story %d] Phase 1: copied %d nodes, %d edges",
                story_id, len(source_nodes), len(source_edges))


# ── Phase 0: World design ──────────────────────────────────────────────────────

def _design_world(session: Session, story: Story) -> WorldDesignOutput:
    source_summary = _format_source_compact(session, story.id)
    system = load_prompt("world_designer")
    user_content = (
        f"language: {story.language}\n"
        f"genre_hint: {story.genre or '(derive from source)'}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## SOURCE GRAPH\n{source_summary}\n"
    )
    output: WorldDesignOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["world_designer"], schema=WorldDesignOutput,
        max_tokens=4096, thinking=True,
    )
    logger.info("[%s] Phase 0: world — %s / %s", story.slug, output.genre, output.setting)
    return output


# ── Phase 1b: Name lexicon ─────────────────────────────────────────────────────

def _build_name_lexicon(
    session: Session, story: Story,
    world_design_text: str,
    feedback: str | None = None,
) -> dict[str, str]:
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source")
        .all()
    )
    nameable = [n for n in nodes if n.node_type in ("character", "location", "faction", "object")]
    lines = []
    for n in nameable:
        p = n.properties or {}
        role = p.get("role", "")
        lines.append(f"[{n.node_key}] {n.node_type.upper()}: '{n.label}'" + (f" | {role}" if role else ""))

    system = load_prompt("name_lexicon")
    user_content = (
        f"language: {story.language}\n\n"
        f"## NEW WORLD DESIGN\n{world_design_text}\n\n"
        f"## SOURCE NODES TO RENAME\n" + "\n".join(lines)
    )
    if feedback:
        user_content += f"\n\n## FEEDBACK — these names were too similar to source, change them:\n{feedback}\n"

    output: NameLexiconOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["name_lexicon"], schema=NameLexiconOutput,
        max_tokens=4096, thinking=False,
    )
    lexicon = {e.node_key: e.new_label for e in output.entries}
    logger.info("[%s] Phase 1b: lexicon — %d names | %s", story.slug, len(lexicon), output.world_note)
    return lexicon


# ── Phase 2: LLM surface content ──────────────────────────────────────────────

def _rename_surface(
    session: Session, story: Story,
    lexicon: dict[str, str],
    world_design_text: str,
    feedback: str | None = None,
) -> None:
    source_summary = _format_source_for_surface(session, story.id)
    lexicon_block = "\n".join(f'  {k}: "{v}"' for k, v in sorted(lexicon.items()))

    system = load_prompt("new_graph_builder")
    user_content = (
        f"language: {story.language}\n"
        f"genre: {story.genre or '(see world design)'}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## WORLD DESIGN\n{world_design_text}\n\n"
        f"## NAME LEXICON (use EXACTLY these names)\n{lexicon_block}\n\n"
        f"## SOURCE NODES TO REIMAGINE\n{source_summary}\n"
    )
    if feedback:
        user_content += f"\n## FEEDBACK — fix these specific issues:\n{feedback}\n"

    output: NewGraphSurfaceOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["new_graph_builder"], schema=NewGraphSurfaceOutput,
        max_tokens=32768, thinking=True,
    )

    node_map = {
        n.node_key: n
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new").all()
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
        else:
            if surf.new_description: props["description"] = surf.new_description
        node.properties = props

    for surf in output.edge_surfaces:
        edge = (
            session.query(StoryGraphEdge)
            .filter_by(story_id=story.id, graph_type="new",
                       source_key=surf.source_key, target_key=surf.target_key,
                       edge_type=surf.edge_type)
            .first()
        )
        if not edge:
            continue
        if surf.new_label:     edge.label = surf.new_label
        if surf.new_condition: edge.condition = surf.new_condition
        props = dict(edge.properties or {})
        if surf.new_mechanism: props["mechanism"] = surf.new_mechanism
        if surf.new_old_val:   props["old_val"]   = surf.new_old_val
        if surf.new_new_val:   props["new_val"]   = surf.new_new_val
        edge.properties = props

    session.flush()
    logger.info("[%s] Phase 2: surface written %d nodes, %d edges",
                story.slug, len(output.node_surfaces), len(output.edge_surfaces))


# ── Phase 3: LLM creative enrichment ─────────────────────────────────────────

def _enrich_graph(session: Session, story: Story) -> None:
    new_graph_text = context_builder.format_story_graph(session, story.id, graph_type="new")
    system = load_prompt("graph_enricher")
    user_content = (
        f"language: {story.language}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## NEW GRAPH (after surface rename)\n{new_graph_text}\n"
    )
    output: GraphEnrichmentOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["graph_enricher"], schema=GraphEnrichmentOutput,
        max_tokens=8192, thinking=False,
    )
    for node in output.new_nodes:
        session.add(StoryGraphNode(
            story_id=story.id, graph_type="new", node_key=node.id,
            node_type=node.node_type, label=node.label,
            properties=node.properties or {}, chapter_introduced=node.chapter_introduced,
        ))
    session.flush()
    for edge in output.new_edges:
        db_props = {"role": edge.role} if edge.edge_type == "PARTICIPATES" else (edge.properties or {})
        session.add(StoryGraphEdge(
            story_id=story.id, graph_type="new",
            source_key=edge.source_id, target_key=edge.target_id,
            edge_type=edge.edge_type, label=edge.label or "",
            chapter_from=edge.chapter_from, chapter_to=edge.chapter_to,
            trigger_event_key=edge.trigger_event_id, condition=edge.condition,
            properties=db_props,
        ))
    session.flush()
    logger.info("[%s] Phase 3: +%d nodes, +%d edges — %s",
                story.slug, len(output.new_nodes), len(output.new_edges), output.enrichment_note)


# ── Phase 3.5: Rewrite story_bible from new-graph names ──────────────────────

def _rewrite_story_bible(
    session: Session,
    story: Story,
    world_design: WorldDesignOutput | None = None,
    feedback: str | None = None,
) -> None:
    """Rewrite story.story_bible using new-graph names — no source-name contamination.

    world_design: provided on first run; None on feedback rebuilds (uses current
    story_bible as world context instead).
    feedback: list of forbidden source names to explicitly avoid.
    """
    char_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .order_by(StoryGraphNode.chapter_introduced)
        .all()
    )
    event_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="event")
        .order_by(StoryGraphNode.chapter_introduced)
        .all()
    )

    char_block = "\n".join(
        "[{key}] {label} | role: {role} | wants: {wants} | arc: {arc}".format(
            key=n.node_key, label=n.label,
            role=(n.properties or {}).get("role", ""),
            wants=(n.properties or {}).get("wants", ""),
            arc=(n.properties or {}).get("arc_stage", ""),
        )
        for n in char_nodes
    )
    event_block = "\n".join(
        "ch{ch}: {label} — {summary}".format(
            ch=n.chapter_introduced, label=n.label,
            summary=((n.properties or {}).get("summary") or "")[:100],
        )
        for n in event_nodes
    )

    if world_design is not None:
        world_block = (
            f"Setting: {world_design.setting}\n"
            f"Time period: {world_design.time_period}\n"
            f"Genre: {world_design.genre}\n"
            f"Tone: {world_design.tone}\n"
            f"Thematic core: {world_design.thematic_core}\n"
            f"Key locations: {', '.join(world_design.location_concepts)}\n"
            f"Protagonist archetype: {world_design.protagonist_archetype}\n"
            f"Antagonist archetype: {world_design.antagonist_archetype}"
        )
    else:
        # Feedback rebuild — existing story_bible is already clean; use as world context
        world_block = story.story_bible or ""

    system = (
        "You are a story bible writer for a novel project. "
        "Write a concise, vivid story bible (300-500 words) using ONLY the provided "
        "new-world information and character names. "
        "Never reference source/original character names, company names, or settings. "
        "Return ONLY the story bible prose — no headings, no commentary."
    )
    user_content = (
        f"language: {story.language}\n\n"
        f"## WORLD DESIGN\n{world_block}\n\n"
        f"## NEW CHARACTERS (use EXACTLY these names — no others)\n{char_block}\n\n"
        f"## KEY PLOT EVENTS (in order)\n{event_block}\n"
    )
    if feedback:
        user_content += (
            f"\n\n## CRITICAL — FORBIDDEN SOURCE NAMES (must not appear anywhere)\n{feedback}\n"
        )

    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS.get("story_bible_rewriter", AGENT_MODELS["world_designer"]),
        max_tokens=2048,
        thinking=False,
    )
    story.story_bible = response.text.strip()
    logger.info("[%s] story_bible rewritten: %d chars", story.slug, len(story.story_bible))


def _verify_story_bible(
    session: Session,
    story: Story,
    world_design: WorldDesignOutput | None = None,
    max_retries: int = 10,
) -> None:
    """Check story_bible for leaked source character names; retry rewrite if found."""
    source_labels = [
        n.label for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source", node_type="character")
        .all()
    ]

    for attempt in range(max_retries + 1):
        bible_lower = story.story_bible.lower()
        leaked = [name for name in source_labels if name.lower() in bible_lower]
        if not leaked:
            logger.info("[%s] story_bible verify: clean (attempt %d)", story.slug, attempt)
            return
        logger.warning("[%s] story_bible leaked source names (attempt %d): %s",
                       story.slug, attempt, leaked)
        if attempt < max_retries:
            _rewrite_story_bible(
                session, story, world_design,
                feedback="FORBIDDEN — do not use any of: " + ", ".join(leaked),
            )

    logger.warning("[%s] story_bible still has source names after %d retries — accepted",
                   story.slug, max_retries)


# ── Public entry point ────────────────────────────────────────────────────────

def run(session: Session, story: Story, feedback: str | None = None) -> None:
    """Build (or rebuild) the new graph.

    feedback: if set (from graph_verifier reskin issues), world design is
    preserved but name lexicon rebuilds with feedback context and Phase 2
    reruns. Phase 3 is skipped on feedback rebuilds.
    """
    logger.info("[%s] new_graph_builder: start (feedback=%s)", story.slug, bool(feedback))

    # Phase 1 — copy structure (always, even on feedback rebuild)
    _copy_source_structure(session, story.id)

    if not feedback:
        # Phase 0 — design the world (first run only)
        world_design = _design_world(session, story)
        # Store narrative_summary temporarily; _rewrite_story_bible replaces it below
        story.story_bible = world_design.narrative_summary
        world_design_text = world_design.narrative_summary
    else:
        world_design = None
        # Feedback rebuild — story_bible already rewritten cleanly on first run
        world_design_text = story.story_bible or ""

    # Phase 1b — name lexicon (re-runs on feedback with context)
    lexicon = _build_name_lexicon(session, story, world_design_text, feedback)

    # Phase 2 — write surface content
    _rename_surface(session, story, lexicon, world_design_text, feedback)

    # Phase 3 — enrichment (skip on feedback rebuild)
    if not feedback:
        _enrich_graph(session, story)

    # Phase 3.5 — rewrite story_bible using new-graph names, then verify
    _rewrite_story_bible(session, story, world_design)
    _verify_story_bible(session, story, world_design)

    _rebuild_characters(session, story)
    story.new_graph_built = True
    logger.info("[%s] new_graph_builder: done", story.slug)
