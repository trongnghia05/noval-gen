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
import re

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import (
    ArcChangeGroupEnrichOutput,
    ArcChangeSurfaceOut,
    CausesGroupEnrichOutput,
    CharacterGroupEnrichOutput,
    EventGroupEnrichOutput,
    GraphEnrichmentOutput,
    NameLexiconOutput,
    NewGraphSurfaceOutput,
    RelationGroupEnrichOutput,
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
    session.flush()


def build_characters_from_graph(session: Session, story: Story) -> None:
    """Deterministically build Character rows + CSV graph from the new graph.

    The new graph's CHARACTER nodes are the single source of truth for REWRITE —
    they carry fully-enriched role/wants/fears/arc/speech data. This avoids a
    second, independent LLM pass (character_developer) that could invent names or
    roles that drift from the graph. Also seeds the CSV knowledge graph
    (characters + voices + initial relationships) so chapter_writer and
    chapter_blueprinter read state consistent with the graph.
    """
    from .. import csv_graph

    _rebuild_characters(session, story)

    char_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .all()
    )
    node_label = {n.node_key: n.label for n in char_nodes}

    # ── CSV character rows ──────────────────────────────────────────────────
    graph_rows = []
    voice_lines = []
    seen: set[str] = set()
    for n in char_nodes:
        if n.label.strip().lower() in seen:
            continue
        seen.add(n.label.strip().lower())
        p = n.properties or {}
        graph_rows.append({
            "id": n.node_key,
            "name": n.label,
            "aliases": ", ".join(p.get("aliases", []) or []),
            "role": p.get("role", ""),
            "arc_status": "active",
            "location": p.get("initial_location", ""),
            "emotional_state": p.get("arc_stage", ""),
            "goals": p.get("wants", ""),
            "secrets": p.get("secrets", ""),
            "speech_pattern": p.get("speech_pattern", ""),
            "last_seen_chapter": "0",
        })
        vp = p.get("voice_profile") or p.get("speech_pattern")
        if vp:
            voice_lines.append(f"## {n.label}\n{vp}")

    voices_md = "\n\n".join(voice_lines) if voice_lines else "(chưa có dữ liệu giọng nói)"

    # ── Initial relationships (RELATION edges active before chapter 1) ──────
    rel_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="RELATION")
        .filter(
            (StoryGraphEdge.chapter_from.is_(None)) |
            (StoryGraphEdge.chapter_from <= 1)
        )
        .all()
    )
    rel_rows = []
    seen_pairs: set[tuple] = set()
    for e in rel_edges:
        a = node_label.get(e.source_key)
        b = node_label.get(e.target_key)
        if not a or not b:
            continue
        pair = tuple(sorted([a, b]))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        p = e.properties or {}
        rel_rows.append({
            "char_a": a,
            "char_b": b,
            "type": p.get("rel_type", e.label or ""),
            "strength": p.get("strength", "medium"),
            "status": "active",
            "last_event": e.condition or "",
            "last_updated_chapter": "0",
        })

    csv_graph.init_graph(story.id, graph_rows, voices_md, relationships=rel_rows)


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


# ── Phase 1.5: Python name substitution (deterministic) ──────────────────────

# Honorifics / titles that are never a character's distinctive given name — a
# name token equal to one of these is not worth substituting on its own.
_NAME_TITLES = {
    "dr", "mr", "mrs", "ms", "sr", "jr", "master", "grandmaster", "artificer",
    "librarian", "councillor", "apprentice", "healer", "lord", "lady", "the",
    "madam", "sir", "professor", "captain", "reverend", "matron", "miss", "mister",
}


def _is_clean_person_label(label: str) -> bool:
    """A label safe to mine given-name tokens from: a plain personal name, not a
    derived/possessive label like "Phineas' Father" or "Prenup (Phineas')" whose
    tokens refer to *another* entity and would poison the token → new-name map."""
    if any(ch in label for ch in "'\"()[]"):
        return False
    return 1 <= len(label.split()) <= 3


def _expand_with_name_tokens(
    label_map: dict[str, str],
    name_labels: set[str] | None = None,
) -> dict[str, str]:
    """Add given-name / distinctive-token entries to a full-label substitution map.

    Source labels are full names ("Juniper Kennedy") but prose references the
    character by a bare token ("Juniper"). Full-label-only substitution leaves
    those bare references untouched — the #1 source of un-reskinned name leaks.
    For each *clean personal* source label we add its distinctive tokens
    (len ≥ 4, not a title) mapping to the full new label, but drop any token that
    maps to more than one new label (e.g. a shared surname "Kennedy") to avoid
    ambiguous replacement. `name_labels` restricts which source labels contribute
    tokens (character names only) so possessive labels don't poison a given name.
    """
    token_targets: dict[str, set[str]] = {}
    for src, new in label_map.items():
        if name_labels is not None and src not in name_labels:
            continue
        if not _is_clean_person_label(src):
            continue
        for raw in src.split():
            tok = raw.strip(".,;:'\"()").strip()
            if len(tok) < 4 or tok.lower() in _NAME_TITLES:
                continue
            token_targets.setdefault(tok, set()).add(new)

    expanded = dict(label_map)
    for tok, targets in token_targets.items():
        if tok in expanded:
            continue  # a full label already owns this key
        if len(targets) == 1:
            expanded[tok] = next(iter(targets))
        else:
            # Ambiguous source token (e.g. a family surname "Branson" shared by
            # several characters). Don't drop it outright: if EVERY new target
            # label shares one distinctive token (the family got one new surname,
            # e.g. all "…Thorne"), map the source token to that shared token so
            # stray surname mentions in event/edge text are still reskinned.
            shared = _shared_token(targets)
            if shared:
                expanded[tok] = shared
    return expanded


def _shared_token(labels: set[str]) -> str | None:
    """The single distinctive token common to ALL labels, else None.

    Used so an ambiguous shared surname maps to the shared NEW surname rather than
    being dropped. Only returns when exactly one such common token exists (avoids
    guessing when the labels have nothing meaningful in common)."""
    token_sets: list[set[str]] = []
    original_case: dict[str, str] = {}
    for lab in labels:
        toks = set()
        for raw in lab.split():
            t = raw.strip(".,;:'\"()").strip()
            if len(t) < 4 or t.lower() in _NAME_TITLES:
                continue
            toks.add(t.lower())
            original_case.setdefault(t.lower(), t)
        token_sets.append(toks)
    if not token_sets:
        return None
    common = set.intersection(*token_sets)
    if len(common) == 1:
        return original_case[next(iter(common))]
    return None


def _sub_props(props: dict, sub) -> bool:
    """Substitute every string / list-of-string value in a properties dict.

    No field allowlist — any property may embed a source name (goal, significance,
    chapter_spirit, symbolic_meaning, …). Returns True if anything changed.
    Non-string scalars (ints, enums) are left untouched; a string enum with no
    source name is a harmless no-op. Mutates `props` in place.
    """
    changed = False
    for k, v in list(props.items()):
        if isinstance(v, str) and v:
            nv = sub(v)
            if nv != v:
                props[k] = nv
                changed = True
        elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            nv = [sub(x) for x in v]
            if nv != v:
                props[k] = nv
                changed = True
    return changed


def substitute_labels(
    session: Session,
    story_id: int,
    label_map: dict[str, str],
    name_labels: set[str] | None = None,
) -> int:
    """Replace source labels → new labels in every text field of the new graph.

    Deterministic, no LLM. Word-boundary matching so a short source label never
    corrupts an unrelated substring. Expands clean personal names to their bare
    given-name tokens (so "Juniper" is replaced, not just "Juniper Kennedy").
    Substitutes every string-valued property (no field allowlist). Returns the
    number of (expanded) label pairs.
    """
    if not label_map:
        return 0

    label_map = _expand_with_name_tokens(label_map, name_labels)
    # Longest source label first so multi-word names are replaced before any of
    # their component words. Each gets a compiled word-boundary pattern.
    pairs = sorted(label_map.items(), key=lambda x: len(x[0]), reverse=True)
    compiled = [
        (re.compile(r"(?<!\w)" + re.escape(src) + r"(?!\w)"), new)
        for src, new in pairs
    ]

    def sub(text: str | None) -> str | None:
        if not text:
            return text
        for pattern, new in compiled:
            text = pattern.sub(new, text)
        return text

    for node in session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="new").all():
        new_label = sub(node.label)
        if new_label and new_label != node.label:
            node.label = new_label
        props = dict(node.properties or {})
        if _sub_props(props, sub):
            node.properties = props

    for edge in session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="new").all():
        if edge.label:
            edge.label = sub(edge.label)
        if edge.condition:
            edge.condition = sub(edge.condition)
        props = dict(edge.properties or {})
        if _sub_props(props, sub):
            edge.properties = props

    session.flush()
    return len(pairs)


def _character_source_labels(session: Session, story_id: int) -> set[str]:
    """Source labels that are character names — the only labels allowed to
    contribute bare given-name tokens for substitution."""
    return {
        n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source", node_type="character").all()
    }


def _apply_lexicon_substitution(
    session: Session,
    story_id: int,
    lexicon: dict[str, str],          # node_key → new_label
) -> None:
    """Build source_label → new_label map from the lexicon and apply it."""
    source_labels = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source").all()
    }
    label_map: dict[str, str] = {}
    for key, src_label in source_labels.items():
        new_label = lexicon.get(key)
        if new_label and new_label != src_label:
            label_map[src_label] = new_label
    count = substitute_labels(
        session, story_id, label_map,
        name_labels=_character_source_labels(session, story_id),
    )
    logger.info("[story %d] Phase 1.5: applied %d name substitutions", story_id, count)


# ── Phase 2: Per-group LLM enrichment ─────────────────────────────────────────

def _enrich_characters(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
) -> None:
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .all()
    )
    if not nodes:
        return

    char_lines = []
    for n in nodes:
        p = n.properties or {}
        char_lines.append(
            f"[{n.node_key}] {n.label} | role: {p.get('role','')} | "
            f"arc: {p.get('arc_stage','')} | wants: {p.get('wants','')} | "
            f"fears: {p.get('fears','')} | background: {p.get('background','')}"
        )

    lexicon_block = "\n".join(f"  {k}: \"{v}\"" for k, v in sorted(lexicon.items()))
    system = load_prompt("graph_character_enricher")
    user_content = (
        f"language: {story.language}\n\n"
        f"## WORLD DESIGN\n{world_design_text}\n\n"
        f"## NAME LEXICON\n{lexicon_block}\n\n"
        f"## CHARACTERS\n" + "\n".join(char_lines)
    )
    output: CharacterGroupEnrichOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["graph_character_enricher"],
        schema=CharacterGroupEnrichOutput, max_tokens=48000, thinking=False,
    )
    node_map = {n.node_key: n for n in nodes}
    for surf in output.characters:
        node = node_map.get(surf.node_key)
        if not node:
            continue
        props = dict(node.properties or {})
        if surf.new_arc_stage:      props["arc_stage"]      = surf.new_arc_stage
        if surf.new_wants:          props["wants"]          = surf.new_wants
        if surf.new_fears:          props["fears"]          = surf.new_fears
        if surf.new_background:     props["background"]     = surf.new_background
        if surf.new_speech_pattern: props["speech_pattern"] = surf.new_speech_pattern
        if surf.new_voice_profile:  props["voice_profile"]  = surf.new_voice_profile
        node.properties = props
    session.flush()
    logger.info("[%s] Phase 2a: enriched %d characters", story.slug, len(output.characters))


def _enrich_events(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
) -> None:
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="event")
        .order_by(StoryGraphNode.chapter_introduced)
        .all()
    )
    if not nodes:
        return

    # Build char label map for context
    char_map = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character").all()
    }
    char_map_block = "\n".join(f"  {k}: {v}" for k, v in char_map.items())

    event_lines = []
    for n in nodes:
        p = n.properties or {}
        event_lines.append(
            f"[{n.node_key}] ch{n.chapter_introduced} {n.label} "
            f"| type:{p.get('event_type','')} | {p.get('summary','')[:120]}"
        )

    system = load_prompt("graph_event_enricher")
    user_content = (
        f"language: {story.language}\n\n"
        f"## WORLD DESIGN\n{world_design_text}\n\n"
        f"## CHARACTER LABEL MAP\n{char_map_block}\n\n"
        f"## EVENTS\n" + "\n".join(event_lines)
    )
    output: EventGroupEnrichOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["graph_event_enricher"],
        schema=EventGroupEnrichOutput, max_tokens=48000, thinking=False,
    )
    node_map = {n.node_key: n for n in nodes}
    for surf in output.events:
        node = node_map.get(surf.node_key)
        if not node:
            continue
        props = dict(node.properties or {})
        props["summary"] = surf.new_summary
        node.properties = props
    session.flush()
    logger.info("[%s] Phase 2b: enriched %d events", story.slug, len(output.events))


def _enrich_arc_changes(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
) -> None:
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="ARC_CHANGE")
        .order_by(StoryGraphEdge.chapter_from)
        .all()
    )
    if not edges:
        return

    char_map = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character").all()
    }
    char_map_block = "\n".join(f"  {k}: {v}" for k, v in char_map.items())

    arc_lines = []
    for e in edges:
        p = e.properties or {}
        char_name = char_map.get(e.source_key, e.source_key)
        arc_lines.append(
            f"[{e.source_key}] {char_name} ch{e.chapter_from}: "
            f"'{p.get('old_val','')}' → '{p.get('new_val','')}'"
        )

    system = load_prompt("graph_arc_enricher")
    user_content = (
        f"language: {story.language}\n\n"
        f"## WORLD DESIGN\n{world_design_text}\n\n"
        f"## CHARACTER LABEL MAP\n{char_map_block}\n\n"
        f"## ARC_CHANGES\n" + "\n".join(arc_lines)
    )
    output: ArcChangeGroupEnrichOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["graph_arc_enricher"],
        schema=ArcChangeGroupEnrichOutput, max_tokens=48000, thinking=False,
    )
    # Match by (source_key, chapter_from)
    edge_map: dict[tuple, StoryGraphEdge] = {}
    for e in edges:
        edge_map[(e.source_key, e.chapter_from)] = e

    for surf in output.arc_changes:
        edge = edge_map.get((surf.source_key, surf.chapter_from))
        if not edge:
            logger.warning("[%s] _enrich_arc_changes: no edge for (%s, ch%s)",
                           story.slug, surf.source_key, surf.chapter_from)
            continue
        props = dict(edge.properties or {})
        if surf.new_old_val: props["old_val"] = surf.new_old_val
        if surf.new_new_val: props["new_val"] = surf.new_new_val
        edge.properties = props
    session.flush()
    logger.info("[%s] Phase 2c: enriched %d arc_changes", story.slug, len(output.arc_changes))


def _enrich_relations(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
) -> None:
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="RELATION")
        .all()
    )
    if not edges:
        return

    char_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .all()
    )
    char_profiles = "\n".join(
        f"[{n.node_key}] {n.label} | role:{(n.properties or {}).get('role','')} | "
        f"arc:{(n.properties or {}).get('arc_stage','')[:60]}"
        for n in char_nodes
    )

    _BATCH = 30
    for batch_start in range(0, len(edges), _BATCH):
        batch = edges[batch_start: batch_start + _BATCH]
        char_map = {n.node_key: n.label for n in char_nodes}
        rel_lines = []
        for e in batch:
            p = e.properties or {}
            src = char_map.get(e.source_key, e.source_key)
            tgt = char_map.get(e.target_key, e.target_key)
            rel_lines.append(
                f"[{e.source_key}→{e.target_key}] ch{e.chapter_from} "
                f"'{src}' → '{tgt}' | rel_type:{p.get('rel_type','')} | label:{e.label}"
            )

        system = load_prompt("graph_relation_enricher")
        user_content = (
            f"language: {story.language}\n\n"
            f"## CHARACTER PROFILES\n{char_profiles}\n\n"
            f"## RELATIONS\n" + "\n".join(rel_lines)
        )
        output: RelationGroupEnrichOutput = generate_structured(
            PROVIDER, system=system, user_content=user_content,
            model=AGENT_MODELS["graph_relation_enricher"],
            schema=RelationGroupEnrichOutput, max_tokens=48000, thinking=False,
        )
        # Match by (source_key, target_key, chapter_from)
        edge_map: dict[tuple, StoryGraphEdge] = {}
        for e in batch:
            edge_map[(e.source_key, e.target_key, e.chapter_from)] = e

        for surf in output.relations:
            edge = edge_map.get((surf.source_key, surf.target_key, surf.chapter_from))
            if not edge:
                logger.warning("[%s] _enrich_relations: no edge for (%s→%s ch%s)",
                               story.slug, surf.source_key, surf.target_key, surf.chapter_from)
                continue
            props = dict(edge.properties or {})
            if surf.new_rel_type: props["rel_type"] = surf.new_rel_type
            edge.properties = props
            if surf.new_label:    edge.label = surf.new_label
        session.flush()
    logger.info("[%s] Phase 2d: enriched %d relations", story.slug, len(edges))


def _enrich_causes(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
) -> None:
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="CAUSES")
        .all()
    )
    if not edges:
        return

    event_map = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="event").all()
    }
    event_map_block = "\n".join(f"  {k}: {v}" for k, v in event_map.items())

    cause_lines = []
    for e in edges:
        p = e.properties or {}
        src_label = event_map.get(e.source_key, e.source_key)
        tgt_label = event_map.get(e.target_key, e.target_key)
        cause_lines.append(
            f"[{e.source_key}→{e.target_key}] '{src_label}' causes '{tgt_label}' "
            f"| mechanism: {p.get('mechanism','')[:100]} | label: {e.label}"
        )

    system = load_prompt("graph_causes_enricher")
    user_content = (
        f"language: {story.language}\n\n"
        f"## WORLD DESIGN\n{world_design_text}\n\n"
        f"## EVENT LABEL MAP\n{event_map_block}\n\n"
        f"## CAUSES\n" + "\n".join(cause_lines)
    )
    output: CausesGroupEnrichOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["graph_causes_enricher"],
        schema=CausesGroupEnrichOutput, max_tokens=48000, thinking=False,
    )
    edge_map = {(e.source_key, e.target_key): e for e in edges}
    for surf in output.causes:
        edge = edge_map.get((surf.source_key, surf.target_key))
        if not edge:
            logger.warning("[%s] _enrich_causes: no edge for (%s→%s)",
                           story.slug, surf.source_key, surf.target_key)
            continue
        props = dict(edge.properties or {})
        if surf.new_mechanism: props["mechanism"] = surf.new_mechanism
        edge.properties = props
        if surf.new_label: edge.label = surf.new_label
    session.flush()
    logger.info("[%s] Phase 2e: enriched %d causes", story.slug, len(output.causes))


# ── Phase 0: World design ──────────────────────────────────────────────────────

def _design_world(session: Session, story: Story) -> WorldDesignOutput:
    source_summary = _format_source_compact(session, story.id)
    system = load_prompt("world_designer")
    user_content = (
        f"language: {story.language}\n"
        f"genre_hint: {story.genre or '(derive from source)'}\n"
        f"total_chapters: {story.total_chapters}\n\n"
    )
    if story.source_spirit:
        user_content += (
            "## SOURCE SPIRIT (tone, plot arc — role-based, no source names)\n"
            f"{story.source_spirit}\n\n"
        )
    user_content += f"## SOURCE GRAPH\n{source_summary}\n"
    output: WorldDesignOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["world_designer"], schema=WorldDesignOutput,
        max_tokens=4096, thinking=True,
    )
    logger.info("[%s] Phase 0: world — %s / %s", story.slug, output.genre, output.setting)
    return output


# ── Phase 1b: Name lexicon ─────────────────────────────────────────────────────

# Capitalized words that routinely start sentences / titles in source event
# summaries but are NOT proper nouns — excluded from the forbidden-name set.
_COMMON_CAPS = {
    "the", "and", "but", "for", "with", "from", "into", "her", "his", "their",
    "she", "him", "them", "this", "that", "when", "after", "before", "while",
    "chapter", "then", "they", "who", "what", "how", "why", "where",
}


def _source_forbidden_names(session: Session, story_id: int) -> set[str]:
    """Every source proper-noun token the lexicon must NOT reuse as a new name.

    Two sources: (1) tokens of every source node label — high precision, these
    are definitely entity names; (2) capitalized words in source EVENT summaries
    — catches names that live only in prose (e.g. a husband whose node is labelled
    descriptively but who is named "Kyst" in the summaries). Over-inclusion is
    harmless: it only nudges the lexicon toward a different invented word.
    """
    forbidden: set[str] = set()
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source")
        .all()
    )
    for n in nodes:
        for raw in (n.label or "").split():
            tok = raw.strip(".,;:'\"()[]").strip().lower()
            if len(tok) >= 3 and tok not in _NAME_TITLES:
                forbidden.add(tok)
        if n.node_type == "event":
            summary = (n.properties or {}).get("summary", "") or ""
            for m in re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", summary):
                if m.lower() not in _COMMON_CAPS and m.lower() not in _NAME_TITLES:
                    forbidden.add(m.lower())
    return forbidden


def _lexicon_collisions(entries, forbidden: set[str]) -> list:
    """Entries whose new_label reuses a forbidden source proper-noun token."""
    bad = []
    for e in entries:
        for raw in (e.new_label or "").split():
            tok = raw.strip(".,;:'\"()[]").strip().lower()
            if len(tok) >= 4 and tok in forbidden:
                bad.append((e, tok))
                break
    return bad


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

    forbidden = _source_forbidden_names(session, story.id)
    forbidden_block = ", ".join(sorted(forbidden))

    system = load_prompt("name_lexicon")
    base_user = (
        f"language: {story.language}\n\n"
        f"## NEW WORLD DESIGN\n{world_design_text}\n\n"
        "## FORBIDDEN NAMES — every new name you invent MUST NOT contain any of these\n"
        "## source proper nouns (case-insensitive), not even as one word of a longer name:\n"
        f"{forbidden_block}\n\n"
        f"## SOURCE NODES TO RENAME\n" + "\n".join(lines)
    )
    if feedback:
        base_user += f"\n\n## FEEDBACK — these names were too similar to source, change them:\n{feedback}\n"

    output: NameLexiconOutput = generate_structured(
        PROVIDER, system=system, user_content=base_user,
        model=AGENT_MODELS["name_lexicon"], schema=NameLexiconOutput,
        max_tokens=4096, thinking=False,
    )

    # Validate + bounded regen: any new name reusing a forbidden source token is
    # re-requested with explicit instruction. This is the deterministic backstop
    # behind the prompt rule — the lexicon is the ONLY place new names are minted,
    # so a leak here poisons the whole story.
    for _attempt in range(2):
        bad = _lexicon_collisions(output.entries, forbidden)
        if not bad:
            break
        bad_desc = "; ".join(f"'{e.new_label}' (reuses source word '{tok}')" for e, tok in bad)
        logger.warning("[%s] Phase 1b: lexicon reused source names — regenerating: %s",
                       story.slug, bad_desc)
        retry_user = base_user + (
            "\n\n## HARD CONSTRAINT VIOLATION — the following invented names REUSE a "
            "source proper noun and are REJECTED. Re-issue the FULL lexicon with these "
            f"replaced by genuinely new names sharing NO word with the source:\n{bad_desc}\n"
        )
        output = generate_structured(
            PROVIDER, system=system, user_content=retry_user,
            model=AGENT_MODELS["name_lexicon"], schema=NameLexiconOutput,
            max_tokens=4096, thinking=False,
        )

    lexicon = {e.node_key: e.new_label for e in output.entries}
    leftover = _lexicon_collisions(output.entries, forbidden)
    if leftover:
        logger.warning("[%s] Phase 1b: %d source-name reuse(s) survived after retries",
                       story.slug, len(leftover))
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

    # Feedback rebuilds don't need thinking (the feedback already says exactly
    # what to fix); disabling it gives the full token budget to JSON output.
    # 65536 = Gemini 2.5 Flash max_output_tokens cap.
    output: NewGraphSurfaceOutput = generate_structured(
        PROVIDER, system=system, user_content=user_content,
        model=AGENT_MODELS["new_graph_builder"], schema=NewGraphSurfaceOutput,
        max_tokens=65536, thinking=(not feedback),
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
        max_tokens=16384, thinking=False,
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

    The output bible has two parts:
    1. LLM-generated narrative prose (premise, tone, characters, theme).
    2. Programmatically-appended structured sections (required for planning_verifier
       to pass for REWRITE without looping): "Bản đồ cốt truyện gốc (theo chương)"
       and "Sơ đồ quan hệ nhân vật". These are derived from the new graph's EVENT
       nodes and RELATION edges — guaranteed to be present every call, zero source-
       name contamination.
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
        # Feedback rebuild — existing story_bible is already clean; use as world context.
        # Strip off the programmatic sections we appended last time so the LLM doesn't
        # see its own structured output as "world context" prose.
        existing = story.story_bible or ""
        split_marker = "\n\n## Bản đồ cốt truyện gốc"
        world_block = existing.split(split_marker)[0]

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
            f"\n\n## CRITICAL FEEDBACK — must address in rewrite\n{feedback}\n"
        )

    response = PROVIDER.generate(
        system=system,
        user_content=user_content,
        model=AGENT_MODELS.get("story_bible_rewriter", AGENT_MODELS["world_designer"]),
        max_tokens=2048,
        thinking=False,
    )
    prose = response.text.strip()

    # ── Append required structured sections (planning_verifier checks these) ──

    # "Bản đồ cốt truyện gốc (theo chương)": one bullet per event node, ordered
    # by chapter. Derived from new graph — all names are new-world, no source leakage.
    plot_map_lines = ["## Bản đồ cốt truyện gốc (theo chương)\n"]
    for n in event_nodes:
        summary = ((n.properties or {}).get("summary") or "")[:150]
        plot_map_lines.append(f"- Chương {n.chapter_introduced}: **{n.label}** — {summary}")
    plot_map_section = "\n".join(plot_map_lines)

    # "Sơ đồ quan hệ nhân vật": from RELATION edges — mirrors the source structure
    # under new names, which planning_verifier cross-checks against characters.md.
    relation_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="RELATION")
        .all()
    )
    node_label_map = {n.node_key: n.label for n in char_nodes}
    rel_lines = ["## Sơ đồ quan hệ nhân vật\n"]
    for e in relation_edges:
        src = node_label_map.get(e.source_key, e.source_key)
        tgt = node_label_map.get(e.target_key, e.target_key)
        p = e.properties or {}
        rel_type = p.get("rel_type", "") or e.label or "RELATION"
        cond = (e.condition or "")[:100]
        rel_lines.append(f"- {src} ↔ {tgt}: {rel_type}" + (f" — {cond}" if cond else ""))
    rel_section = "\n".join(rel_lines) if len(rel_lines) > 1 else ""

    story.story_bible = prose + "\n\n" + plot_map_section + (("\n\n" + rel_section) if rel_section else "")
    logger.info("[%s] story_bible rewritten: %d chars (prose=%d, events=%d, relations=%d)",
                story.slug, len(story.story_bible), len(prose), len(event_nodes), len(relation_edges))


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
        # Only scan the prose portion — the programmatic structured sections
        # (## Bản đồ cốt truyện gốc, ## Sơ đồ quan hệ nhân vật) are built
        # from graph data and may legitimately reference minor source-character
        # names that were never assigned new-world equivalents in the lexicon.
        # Those names are caught by graph_verifier's reskin pass, not here.
        bible = story.story_bible or ""
        prose_only = bible.split("\n\n## Bản đồ cốt truyện gốc")[0]
        prose_lower = prose_only.lower()
        leaked = [name for name in source_labels if name.lower() in prose_lower]
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

    Pipeline:
      Phase 1   — Python copy of source graph structure
      Phase 1b  — LLM: build name lexicon (source_key → new_label)
      Phase 1.5 — Python: deterministic name substitution across all text fields
      Phase 2   — LLM per-group enrichment (characters / events / arc_changes /
                  relations / causes) — small focused calls, convergent
      Phase 3   — LLM: creative enrichment (new minor nodes/edges) — first run only
      Phase 3.5 — LLM: rewrite story_bible from new-graph names

    feedback: if set (from graph_verifier), world design is preserved but
    lexicon rebuilds + full Phase 2 re-runs. Phase 3 skipped on feedback.
    """
    logger.info("[%s] new_graph_builder: start (feedback=%s)", story.slug, bool(feedback))

    # Phase 1 — copy source structure
    _copy_source_structure(session, story.id)

    if not feedback:
        # Phase 0 — design the new world (first run only)
        world_design = _design_world(session, story)
        story.story_bible = world_design.narrative_summary
        world_design_text = world_design.narrative_summary
    else:
        world_design = None
        world_design_text = story.story_bible or ""

    # Phase 1b — name lexicon
    lexicon = _build_name_lexicon(session, story, world_design_text, feedback)

    # Phase 1.5 — Python name substitution (deterministic, convergent)
    _apply_lexicon_substitution(session, story.id, lexicon)

    # Phase 2 — per-group LLM enrichment (each call is focused and bounded)
    _enrich_characters(session, story, lexicon, world_design_text)
    _enrich_events(session, story, lexicon, world_design_text)
    _enrich_arc_changes(session, story, lexicon, world_design_text)
    _enrich_relations(session, story, lexicon, world_design_text)
    _enrich_causes(session, story, lexicon, world_design_text)

    # Phase 3 — creative enrichment (first run only)
    if not feedback:
        _enrich_graph(session, story)

    # Phase 3.5 — rewrite story_bible using new-graph names, then verify
    _rewrite_story_bible(session, story, world_design)
    _verify_story_bible(session, story, world_design)

    story.plot_outline = None
    story.world_bible = None
    story.planning_verified = False
    session.query(Character).filter_by(story_id=story.id).delete(synchronize_session="fetch")
    session.flush()
    story.new_graph_built = True
    logger.info("[%s] new_graph_builder: done", story.slug)
