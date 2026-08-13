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

from . import _common
from ..services import context_builder
from ..core.config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story, StoryGraphEdge, StoryGraphNode
from ..core.slug import generate_title, slugify
from ..schemas import (
    ArcChangeGroupEnrichOutput,
    ArcChangeSurfaceOut,
    CausesGroupEnrichOutput,
    CharacterGroupEnrichOutput,
    EventGroupEnrichOutput,
    GraphEnrichmentOutput,
    GraphEnrichVerifyOutput,
    NameLexiconOutput,
    NewGraphSurfaceOutput,
    RelationGroupEnrichOutput,
    StoryBibleLeakCheckOutput,
    WorldDesignOutput,
    WorldNameCheckOutput,
)

logger = logging.getLogger(__name__)

# The cast roles the new graph is allowed to use. Anything else is rejected on the way
# in, so downstream code can rely on the value rather than pattern-matching prose.
_VALID_ROLES = {"protagonist", "antagonist", "love_interest", "supporting", "minor"}

_ROLE_TIERS = {
    "protagonist":   "core",
    "love_interest": "core",
    "antagonist":    "important",
    "supporting":    "important",
    "minor":         "secondary",
}


def _tier_for_role(role: str) -> str:
    """Character tier from cast role, tolerant of a role that isn't in _VALID_ROLES.

    Exact matching used to be the rule, and every value outside a hard-coded tuple
    fell through to the bottom tier in silence — so `male_lead` and `minor_antagonist`
    both landed as background characters, as did anyone the extraction gave no role at
    all. Tier drives who reaches the cover art and the per-chapter context, so getting
    this wrong quietly demotes the leads. Substring matching keeps such variants near
    the right tier; order matters, since `minor_antagonist` must read as minor.
    """
    r = (role or "").strip().lower()
    if r in _ROLE_TIERS:
        return _ROLE_TIERS[r]
    if not r:
        return "secondary"
    if "minor" in r:
        return "secondary"
    if "protagonist" in r or "lead" in r or "heroine" in r or "hero" in r:
        return "core"
    if "love" in r or "romantic" in r:
        return "core"
    if "antagonist" in r or "villain" in r or "rival" in r or "supporting" in r:
        return "important"
    return "secondary"


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
        role = p.get("role", "")
        tier = _tier_for_role(role)
        profile_md = p.get("profile_md") or (
            f"**Role**: {role}\n"
            f"**Gender**: {p.get('gender', '')}\n"
            f"**Appearance**: {p.get('appearance', '')}\n"
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
    from ..services import story_state

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
            "gender": p.get("gender", "unknown"),
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

    voices_md = "\n\n".join(voice_lines) if voice_lines else "(no voice data yet)"

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

    story_state.init_graph(story.id, graph_rows, voices_md, relationships=rel_rows)


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
            line += (f" | gender: {p.get('gender', 'unknown')}"
                     f" | role: {p.get('role', '')}"
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
    "mistress", "dame", "elder",
}


def _name_tokens(label: str) -> list[str]:
    """Name words with titles + punctuation stripped, lowercased. Position-agnostic —
    does NOT try to tell which token is the given name vs the surname (that ordering
    differs by language: 'Given Surname' in English, 'Surname Given' in Vietnamese/
    East-Asian names). Callers reason over the SET of words, never their position."""
    out = []
    for raw in (label or "").split():
        t = raw.strip(".,;:'\"()[]").lower()
        if t and t not in _NAME_TITLES:
            out.append(t)
    return out


def _sorted_name_key(label: str) -> str:
    """A language/order-independent identity key for a person's name: the set of its
    name words (titles removed), sorted. Two labels match IFF they are the same set of
    words — so a shared SURNAME between family members ('Isolde Ashworth' vs 'Silas
    Ashworth') does NOT match, but a truly identical full name does. This is the ONLY
    thing that counts as a name collision."""
    return " ".join(sorted(_name_tokens(label)))


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
    compiled = []
    for src, new in pairs:
        pat = re.compile(r"(?<!\w)" + re.escape(src) + r"(?!\w)")
        # IDEMPOTENCY: skip a self-wrapping pair where the NEW label contains the SRC
        # token (e.g. src="Elena", new="Architect Elena Nexus"). Applying it wraps the
        # label with another layer every time substitute_labels runs (it runs in Phase
        # 1.5 AND again in verify_graph's reskin), producing runaway labels like
        # "Architect Architect ... Elena ... Nexus Nexus". Normal pairs (Kael→Roric)
        # are kept, so re-sweeping freshly-leaked source names still works.
        if pat.search(new):
            logger.warning("[%s] substitute_labels: skip self-wrapping %r → %r",
                           story_id, src, new)
            continue
        compiled.append((pat, new))

    def sub(text: str | None) -> str | None:
        if not text:
            return text
        orig = text
        for pattern, new in compiled:
            text = pattern.sub(new, text)
        # Safety net: substitution must never blow up a field's size. Any pathological
        # growth (a runaway we didn't foresee) leaves the field untouched rather than
        # writing a multi-megabyte blob that crashes the DB.
        if len(text) > max(500, 4 * len(orig)):
            logger.warning("[%s] substitute_labels: runaway growth on a field "
                           "(%d→%d chars) — leaving it unchanged", story_id, len(orig), len(text))
            return orig
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

def _style_contract(session: Session, story: Story, world_design_text: str) -> str:
    """The shared style anchor every Phase 2 enrichment call receives.

    Phase 2 is five separate LLM calls, and each one used to see a different slice of
    the world: only the character pass got the source's tonal energy, and the relation
    pass was handed `world_design_text` and never used it — so it described
    relationships without knowing the era, region or genre they sat in. The result was
    a graph enriched in several unrelated voices, which chapter_writer then had to
    reconcile.

    The cast block is what keeps names, gender and heritage coherent. Names are minted
    in Phase 1b, appearance in Phase 2, and nothing connected the two — which is how a
    lead ended up with a French surname and an unexplained East Asian heritage. Every
    call now sees both at once.
    """
    parts = [f"## WORLD DESIGN\n{world_design_text}"]

    if story.source_spirit:
        parts.append(
            "## TONAL ENERGY (match the ENERGY LEVEL — lively/funny vs grim — in this "
            f"world's idiom; do NOT copy source names or wording)\n{story.source_spirit}"
        )

    cast_lines = []
    for n in (session.query(StoryGraphNode)
              .filter_by(story_id=story.id, graph_type="new", node_type="character")
              .order_by(StoryGraphNode.node_key)):
        p = n.properties or {}
        bits = [f"[{n.node_key}] {n.label}"]
        for key, prefix in (("role", "role"), ("gender", "gender")):
            if p.get(key):
                bits.append(f"{prefix}:{p[key]}")
        if p.get("appearance"):
            bits.append(f"looks:{p['appearance'][:120]}")
        cast_lines.append(" | ".join(bits))
    if cast_lines:
        parts.append(
            "## CAST (names are FIXED — use these exact labels; a character's heritage, "
            "gender and register must stay consistent with the name and looks shown "
            "here in every field you write)\n" + "\n".join(cast_lines)
        )

    # Relationships belong in the shared contract, not just in the pass that writes
    # them. Without this the character pass assigned each age independently and put a
    # heroine in her early thirties beside the best friend she grew up with, who came
    # out in her early twenties — a contradiction only visible if you can see the tie.
    relations = relation_lines_for(session, story)
    if relations:
        parts.append(
            "## RELATIONSHIPS (every recorded fact, grouped by pair — permanent ties "
            "and passing moods are mixed together). Anything you write about a "
            "character must be consistent with these: peers of one generation are "
            "close in age, a parent is a generation above their child, and a tie "
            "stated here cannot be contradicted.\n" + "\n".join(relations)
        )

    return "\n\n".join(parts) + "\n\n"



# Per-enricher TASK BRIEF handed to the unified verifier so it checks against THIS
# enricher's actual goals/rules, not a generic list.
_ENRICH_BRIEFS = {
    "characters": (
        "Rewrote each CHARACTER's psychological surface for the new world: arc_stage, "
        "wants, fears, background, speech_pattern, and a rich voice_profile. RULES: "
        "preserve role + arc DIRECTION; use ONLY each node's exact label as its name; "
        "make voices MAXIMALLY DISTINCT across the cast; no source paraphrase."
    ),
    "events": (
        "Rewrote each EVENT's summary so it reads as a native new-world beat. RULES: "
        "keep the same plot function/sequence; reference characters only by their exact "
        "roster labels; no source paraphrase."
    ),
    "arc_changes": (
        "Rewrote each character ARC_CHANGE (old_val → new_val) for the new world. "
        "RULES: preserve the change DIRECTION; the character is referenced by its exact "
        "roster label; states must be coherent for that character's role."
    ),
    "relations": (
        "Rewrote each RELATION between two characters (rel_type + label). RULES: the "
        "relationship must fit both characters' roles/arcs and the plot; reference the "
        "two characters only by their exact roster labels."
    ),
    "causes": (
        "Rewrote each CAUSES link between two events (mechanism + label). RULES: the "
        "cause→effect must be logically sound in the new world; reference events by "
        "their labels; no source paraphrase."
    ),
}


def _build_name_roster(session: Session, story_id: int) -> str:
    """The authoritative name list for the verifier: node_key → label for every
    character and event. A character's name is EXACTLY its label; nothing else counts."""
    chars = (session.query(StoryGraphNode)
             .filter_by(story_id=story_id, graph_type="new", node_type="character")
             .order_by(StoryGraphNode.node_key).all())
    events = (session.query(StoryGraphNode)
              .filter_by(story_id=story_id, graph_type="new", node_type="event")
              .order_by(StoryGraphNode.node_key).all())
    lines = ["CHARACTERS:"]
    lines += [f"  {n.node_key}: {n.label}" for n in chars]
    if events:
        lines.append("EVENTS:")
        lines += [f"  {n.node_key}: {n.label}" for n in events]
    return "\n".join(lines)


def _verify_enrichment(
    session: Session, story: Story, group: str, rendered: str, world_design_text: str,
) -> list:
    """Ask the unified verifier to check one enriched group. Returns a list of issue
    objects (empty ⇒ clean). On LLM failure, returns [] (fail-open — the deterministic
    reconcile pass downstream is the final naming backstop)."""
    try:
        user_content = (
            f"language: {story.language}\n\n"
            f"## ENRICHER TASK\n{_ENRICH_BRIEFS.get(group, group)}\n\n"
            f"## NAME ROSTER\n{_build_name_roster(session, story.id)}\n\n"
            f"## WORLD DESIGN\n{world_design_text}\n\n"
            f"## ENRICHED OUTPUT\n{rendered}"
        )
        out: GraphEnrichVerifyOutput = _common.call_agent(
            "graph_enrich_verifier",
            user_content=user_content,
            model_fallback="graph_character_enricher",
            schema=GraphEnrichVerifyOutput,
            max_tokens=8000,
            thinking=False,
        )
        return list(out.issues or [])
    except Exception as exc:
        logger.warning("[%s] enrich-verify(%s) failed: %s — accepting", story.slug, group, exc)
        return []


def _format_enrich_feedback(issues: list) -> str:
    """Render verifier issues into a feedback block for the re-enrichment call."""
    lines = ["## VERIFIER FEEDBACK — fix EVERY issue below on the listed items only:"]
    for i in issues:
        tgt = f"[{i.target}] " if getattr(i, "target", "") else ""
        lines.append(f"- ({i.dimension}) {tgt}{i.problem} → FIX: {i.fix}")
    return "\n".join(lines)


def _in_scope(only_keys: set | None, *keys: str) -> bool:
    """True if an item should be (re-)enriched this pass: no filter (first pass, all),
    or any of the item's keys (node_key, 'src→tgt', or an endpoint) is flagged."""
    if not only_keys:
        return True
    return any(k in only_keys for k in keys)


def _issue_keys(issues: list) -> set:
    """The keys the verifier flagged, exactly as it wrote them.

    Targets are NOT expanded. An earlier version split 'C001→C003' into its two
    endpoints as well, on the theory that this pinned down the flagged items — it did
    the opposite. `_in_scope` matches an edge on either endpoint, so one flagged
    relation dragged in every other relation touching either character, and since the
    protagonist appears in nearly all of them a single issue rewrote the whole group:
    38 of 39 edges re-enriched for 11 flagged. The clean ones came back broken and the
    loop oscillated (12 → 10 → 6 → 12 → 15) instead of converging.

    Nothing is lost by keeping targets verbatim: `_in_scope` is handed the endpoints
    AND the composite key, so a bare 'C001' still selects everything touching C001
    when the verifier means the whole character.
    """
    return {t for i in issues if (t := (getattr(i, "target", "") or "").strip())}


def _render_enriched_group(session: Session, story: Story, group: str) -> str:
    """Render the CURRENT (post-enrich) DB state of one group for the verifier — the
    whole group every pass, so a targeted re-enrich of a few items is still checked in
    full context."""
    sid = story.id
    if group in ("characters", "events"):
        ntype = "character" if group == "characters" else "event"
        nodes = (session.query(StoryGraphNode)
                 .filter_by(story_id=sid, graph_type="new", node_type=ntype)
                 .order_by(StoryGraphNode.node_key).all())
        lines = []
        for n in nodes:
            p = n.properties or {}
            if group == "characters":
                lines.append(
                    f"[{n.node_key}] label={n.label} | arc: {p.get('arc_stage','')} | "
                    f"wants: {p.get('wants','')} | fears: {p.get('fears','')} | "
                    f"background: {p.get('background','')} | voice: {str(p.get('voice_profile',''))[:200]}")
            else:
                lines.append(f"[{n.node_key}] label={n.label} | summary: {p.get('summary','')}")
        return "\n".join(lines)
    # edge groups
    char_map = {n.node_key: n.label for n in session.query(StoryGraphNode)
                .filter_by(story_id=sid, graph_type="new", node_type="character").all()}
    ev_map = {n.node_key: n.label for n in session.query(StoryGraphNode)
              .filter_by(story_id=sid, graph_type="new", node_type="event").all()}
    etype = {"arc_changes": "ARC_CHANGE", "relations": "RELATION", "causes": "CAUSES"}[group]
    edges = session.query(StoryGraphEdge).filter_by(story_id=sid, graph_type="new", edge_type=etype).all()
    lines = []
    for e in edges:
        p = e.properties or {}
        if group == "arc_changes":
            # Keyed per ARC-CHANGE, not per character. A lead owns twenty of these
            # (one per chapter their arc moves), so a bare 'C001' target meant one
            # flagged beat rewrote all twenty — including the nineteen that were fine.
            lines.append(f"[{e.source_key}@ch{e.chapter_from}] "
                         f"{char_map.get(e.source_key, e.source_key)} "
                         f"ch{e.chapter_from}: '{p.get('old_val','')}' → '{p.get('new_val','')}'")
        elif group == "relations":
            lines.append(f"[{e.source_key}→{e.target_key}] '{char_map.get(e.source_key, e.source_key)}' → "
                         f"'{char_map.get(e.target_key, e.target_key)}' | rel_type:{p.get('rel_type','')} | label:{e.label}")
        else:  # causes
            lines.append(f"[{e.source_key}→{e.target_key}] '{ev_map.get(e.source_key, e.source_key)}' causes "
                         f"'{ev_map.get(e.target_key, e.target_key)}' | mechanism: {p.get('mechanism','')} | label: {e.label}")
    return "\n".join(lines)


def _enrich_with_verify(story: Story, group: str, enrich_fn, session: Session,
                        world_design_text: str, max_attempts: int = 5) -> None:
    """Run an enricher, then verify → re-enrich ONLY the flagged items with feedback
    until clean (max 5 attempts).

    Targeted re-enrichment is essential: re-running the enricher over the WHOLE group
    each pass does not converge (fixing a flagged node rewrites — and often breaks —
    the clean ones). enrich_fn(feedback, only_keys) rewrites only the flagged items and
    leaves the rest untouched; the verifier then re-checks the full group from DB."""
    enrich_fn(None, None)   # first pass: enrich all
    for attempt in range(1, max_attempts + 1):
        rendered = _render_enriched_group(session, story, group)
        issues = _verify_enrichment(session, story, group, rendered, world_design_text)
        if not issues:
            logger.info("[%s] Phase 2 verify(%s): clean (attempt %d)", story.slug, group, attempt)
            return
        logger.warning("[%s] Phase 2 verify(%s) attempt %d: %d issue(s): %s",
                       story.slug, group, attempt, len(issues),
                       "; ".join(f"{i.dimension}:{i.target}" for i in issues[:6]))
        if attempt == max_attempts:
            logger.warning("[%s] Phase 2 verify(%s): %d issue(s) survived after %d attempts — accepted",
                           story.slug, group, len(issues), max_attempts)
            return
        enrich_fn(_format_enrich_feedback(issues), _issue_keys(issues))


def _enrich_characters(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
    feedback: str | None = None, only_keys: set | None = None,
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
        new_label = lexicon.get(n.node_key, n.label)
        char_lines.append(
            f"[{n.node_key}] NEW NAME: {new_label} | current_gender: {p.get('gender','unknown')} | "
            f"role: {p.get('role','')} | "
            f"arc: {p.get('arc_stage','')} | wants: {p.get('wants','')} | "
            f"fears: {p.get('fears','')} | background: {p.get('background','')}"
        )

    lexicon_block = "\n".join(f"  {k}: \"{v}\"" for k, v in sorted(lexicon.items()))
    user_content = (
        f"language: {story.language}\n\n"
        f"{_style_contract(session, story, world_design_text)}"
        f"## NAME LEXICON\n{lexicon_block}\n\n"
        f"## CHARACTERS\n" + "\n".join(char_lines)
    )
    if only_keys:
        user_content += (f"\n\n## RE-ENRICH ONLY THESE node_keys: {', '.join(sorted(only_keys))}\n"
                         "Return ONLY these entries; leave every other character exactly as-is.")
    if feedback:
        user_content += f"\n\n{feedback}"
    output: CharacterGroupEnrichOutput = _common.call_agent(
        "graph_character_enricher",
        user_content=user_content,
        schema=CharacterGroupEnrichOutput,
        max_tokens=48000,
        thinking=False,
    )
    node_map = {n.node_key: n for n in nodes}
    applied = 0
    for surf in output.characters:
        node = node_map.get(surf.node_key)
        if not node or not _in_scope(only_keys, surf.node_key):
            continue
        props = dict(node.properties or {})
        if surf.new_arc_stage:      props["arc_stage"]      = surf.new_arc_stage
        if surf.new_wants:          props["wants"]          = surf.new_wants
        if surf.new_fears:          props["fears"]          = surf.new_fears
        if surf.new_background:     props["background"]     = surf.new_background
        if surf.new_speech_pattern: props["speech_pattern"] = surf.new_speech_pattern
        if surf.new_voice_profile:  props["voice_profile"]  = surf.new_voice_profile
        if surf.new_appearance:     props["appearance"]     = surf.new_appearance
        if surf.new_gender and surf.new_gender.strip().lower() in ("male", "female", "nonbinary"):
            props["gender"] = surf.new_gender.strip().lower()
        if surf.new_role and surf.new_role.strip().lower() in _VALID_ROLES:
            props["role"] = surf.new_role.strip().lower()
        node.properties = props
        applied += 1
    session.flush()

    # The source graph is not a reliable source of roles — extraction drops the key
    # often enough that a protagonist can arrive with no role at all — so the new
    # graph must stand on its own here. Log rather than raise: a bad cast shape is
    # worth seeing in the logs, but not worth killing a run over.
    roles = [(n.properties or {}).get("role", "") for n in nodes]
    leads = roles.count("protagonist")
    if leads != 1:
        logger.warning("[%s] Phase 2a: %d protagonists among %d characters (want exactly 1) — "
                       "roles=%s", story.slug, leads, len(nodes), sorted(set(roles)))
    if not any(r in ("antagonist", "love_interest") for r in roles):
        logger.warning("[%s] Phase 2a: no antagonist or love_interest in the cast", story.slug)
    logger.info("[%s] Phase 2a: enriched %d characters%s", story.slug, applied,
                f" (targeted {len(only_keys)})" if only_keys else "")


def _enrich_events(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
    feedback: str | None = None, only_keys: set | None = None,
) -> None:
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="event")
        .order_by(StoryGraphNode.chapter_introduced)
        .all()
    )
    if not nodes:
        return ""

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

    user_content = (
        f"language: {story.language}\n\n"
        f"{_style_contract(session, story, world_design_text)}"
        f"## CHARACTER LABEL MAP\n{char_map_block}\n\n"
        f"## EVENTS\n" + "\n".join(event_lines)
    )
    if only_keys:
        user_content += (f"\n\n## RE-ENRICH ONLY THESE node_keys: {', '.join(sorted(only_keys))}\n"
                         "Return ONLY these entries; leave every other event exactly as-is.")
    if feedback:
        user_content += f"\n\n{feedback}"
    output: EventGroupEnrichOutput = _common.call_agent(
        "graph_event_enricher",
        user_content=user_content,
        schema=EventGroupEnrichOutput,
        max_tokens=48000,
        thinking=False,
    )
    node_map = {n.node_key: n for n in nodes}
    applied = 0
    for surf in output.events:
        node = node_map.get(surf.node_key)
        if not node or not _in_scope(only_keys, surf.node_key):
            continue
        props = dict(node.properties or {})
        props["summary"] = surf.new_summary
        node.properties = props
        applied += 1
    session.flush()
    logger.info("[%s] Phase 2b: enriched %d events%s", story.slug, applied,
                f" (targeted {len(only_keys)})" if only_keys else "")


def _enrich_arc_changes(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
    feedback: str | None = None, only_keys: set | None = None,
) -> None:
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="ARC_CHANGE")
        .order_by(StoryGraphEdge.chapter_from)
        .all()
    )
    if not edges:
        return ""

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
        # Same key shape the verifier is shown, so a target it reports comes straight
        # back here and selects exactly the arc-change it meant.
        arc_lines.append(
            f"[{e.source_key}@ch{e.chapter_from}] {char_name} ch{e.chapter_from}: "
            f"'{p.get('old_val','')}' → '{p.get('new_val','')}'"
        )

    user_content = (
        f"language: {story.language}\n\n"
        f"{_style_contract(session, story, world_design_text)}"
        f"## CHARACTER LABEL MAP\n{char_map_block}\n\n"
        f"## ARC_CHANGES\n" + "\n".join(arc_lines)
    )
    if only_keys:
        user_content += (f"\n\n## RE-ENRICH ONLY THESE arc-changes: {', '.join(sorted(only_keys))}\n"
                         "The keys are the bracketed ids above. Return ONLY those; "
                         "leave every other arc-change exactly as-is.")
    if feedback:
        user_content += f"\n\n{feedback}"
    output: ArcChangeGroupEnrichOutput = _common.call_agent(
        "graph_arc_enricher",
        user_content=user_content,
        schema=ArcChangeGroupEnrichOutput,
        max_tokens=48000,
        thinking=False,
    )
    # Match by (source_key, chapter_from)
    edge_map: dict[tuple, StoryGraphEdge] = {}
    for e in edges:
        edge_map[(e.source_key, e.chapter_from)] = e

    applied = 0
    for surf in output.arc_changes:
        edge = edge_map.get((surf.source_key, surf.chapter_from))
        if not edge:
            logger.warning("[%s] _enrich_arc_changes: no edge for (%s, ch%s)",
                           story.slug, surf.source_key, surf.chapter_from)
            continue
        # Both granularities: the composite key hits one arc-change, a bare character
        # key still selects all of theirs — so a verifier that reports the old shape
        # degrades to the previous behaviour instead of silently skipping the fix.
        if not _in_scope(only_keys, surf.source_key,
                         f"{surf.source_key}@ch{surf.chapter_from}"):
            continue
        props = dict(edge.properties or {})
        if surf.new_old_val: props["old_val"] = surf.new_old_val
        if surf.new_new_val: props["new_val"] = surf.new_new_val
        edge.properties = props
        applied += 1
    session.flush()
    logger.info("[%s] Phase 2c: enriched %d arc_changes%s", story.slug, applied,
                f" (targeted {len(only_keys)})" if only_keys else "")


def _enrich_relations(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
    feedback: str | None = None, only_keys: set | None = None,
) -> None:
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="RELATION")
        .all()
    )
    if only_keys:
        edges = [e for e in edges
                 if _in_scope(only_keys, e.source_key, e.target_key,
                              f"{e.source_key}→{e.target_key}")]
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

    char_map = {n.node_key: n.label for n in char_nodes}
    applied = 0
    _BATCH = 30
    for batch_start in range(0, len(edges), _BATCH):
        batch = edges[batch_start: batch_start + _BATCH]
        rel_lines = []
        for e in batch:
            p = e.properties or {}
            src = char_map.get(e.source_key, e.source_key)
            tgt = char_map.get(e.target_key, e.target_key)
            rel_lines.append(
                f"[{e.source_key}→{e.target_key}] ch{e.chapter_from} "
                f"'{src}' → '{tgt}' | rel_type:{p.get('rel_type','')} | label:{e.label}"
            )

        user_content = (
            f"language: {story.language}\n\n"
            f"{_style_contract(session, story, world_design_text)}"
            f"## CHARACTER PROFILES\n{char_profiles}\n\n"
            f"## RELATIONS\n" + "\n".join(rel_lines)
        )
        if only_keys:
            user_content += ("\n\n## These are the ONLY relations to re-enrich — "
                             "return exactly these, corrected.")
        if feedback:
            user_content += f"\n\n{feedback}"
        output: RelationGroupEnrichOutput = _common.call_agent(
            "graph_relation_enricher",
            user_content=user_content,
            schema=RelationGroupEnrichOutput,
            max_tokens=48000,
            thinking=False,
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
            applied += 1
        session.flush()
    logger.info("[%s] Phase 2d: enriched %d relations%s", story.slug, applied,
                f" (targeted {len(only_keys)})" if only_keys else "")


def _enrich_causes(
    session: Session, story: Story,
    lexicon: dict[str, str], world_design_text: str,
    feedback: str | None = None, only_keys: set | None = None,
) -> None:
    edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new", edge_type="CAUSES")
        .all()
    )
    if only_keys:
        edges = [e for e in edges
                 if _in_scope(only_keys, e.source_key, e.target_key,
                              f"{e.source_key}→{e.target_key}")]
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

    user_content = (
        f"language: {story.language}\n\n"
        f"{_style_contract(session, story, world_design_text)}"
        f"## EVENT LABEL MAP\n{event_map_block}\n\n"
        f"## CAUSES\n" + "\n".join(cause_lines)
    )
    if only_keys:
        user_content += ("\n\n## These are the ONLY causal links to re-enrich — "
                         "return exactly these, corrected.")
    if feedback:
        user_content += f"\n\n{feedback}"
    output: CausesGroupEnrichOutput = _common.call_agent(
        "graph_causes_enricher",
        user_content=user_content,
        schema=CausesGroupEnrichOutput,
        max_tokens=48000,
        thinking=False,
    )
    edge_map = {(e.source_key, e.target_key): e for e in edges}
    applied = 0
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
        applied += 1
    session.flush()
    logger.info("[%s] Phase 2e: enriched %d causes%s", story.slug, applied,
                f" (targeted {len(only_keys)})" if only_keys else "")


# ── Phase 0: World design ──────────────────────────────────────────────────────

# Words allowed to be capitalized in world design without counting as an invented
# proper name (sentence starters, genre/era words, generic role/place nouns).
_WD_ALLOWED_CAPS = {
    "the", "a", "an", "and", "but", "for", "with", "from", "into", "she", "he",
    "her", "his", "their", "they", "them", "this", "that", "when", "after", "before",
    "while", "then", "who", "what", "how", "why", "where", "in", "on", "at", "of",
    "to", "as", "it", "no", "not", "act", "chapter", "i", "ii", "iii",
    # genre / era / register descriptors commonly capitalized mid-phrase
    "dark", "fantasy", "gothic", "romance", "noir", "empire", "dynasty", "kingdom",
    "victorian", "steampunk", "mafia", "triad", "shanghai", "tokyo", "london",
}


def _world_design_text(wd: WorldDesignOutput) -> str:
    fields = [wd.setting, wd.time_period, wd.genre, wd.tone,
              wd.protagonist_archetype, wd.antagonist_archetype,
              wd.thematic_core, wd.narrative_summary] + list(wd.location_concepts or [])
    return "\n".join(f for f in fields if f)


def _regex_two_word_names(text: str) -> set[str]:
    """Cheap backstop: two-word Capitalized phrases are almost always names, minus
    obvious genre/era pairs. Union'd with the LLM checker so nothing slips."""
    out = set()
    for m in re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text):
        a, b = m.split()
        if a.lower() in _WD_ALLOWED_CAPS and b.lower() in _WD_ALLOWED_CAPS:
            continue
        out.add(m)
    return out


def _world_design_name_leaks(story: Story, wd: WorldDesignOutput) -> list[str]:
    """Invented proper names still in the world design (should be name-free).
    Decided by an LLM proofreader — it understands context (a genre label like
    'Revenge Thriller' is NOT a name; 'Grand Guildhall' IS) and non-ASCII names,
    which a regex cannot. No regex union: it false-flagged genre pairs and caused
    pointless regenerations."""
    text = _world_design_text(wd)
    try:
        out: WorldNameCheckOutput = _common.call_agent(
            "world_name_check",
            user_content=f'language: {story.language}\n\n## WORLD DESIGN\n{text}\n',
            model_fallback="name_lexicon",
            schema=WorldNameCheckOutput,
            max_tokens=2048,
            thinking=False,
        )
        return sorted({n.strip() for n in (out.proper_names or []) if n and n.strip()})
    except Exception as exc:
        logger.warning("[%s] Phase 0 name-check LLM failed (%s) — skipping", story.slug, exc)
        return []


def _design_world(session: Session, story: Story, feedback: str | None = None,
                  max_attempts: int = 5) -> WorldDesignOutput:
    """Design the new world, then LOOP a strict name-check: regenerate until the
    world design contains NO invented proper names (people/places/clans), so the
    draft narrative can't seed a name that later collides with the lexicon.

    `feedback` (from a downstream verifier) is fed back in so the world is REWRITTEN
    to address it — a feedback loop that improves the world across rebuilds, rather
    than freezing the first attempt."""
    source_summary = _format_source_compact(session, story.id)
    base = (
        f"language: {story.language}\n"
        f"genre_hint: {story.genre or '(derive from source)'}\n"
        f"total_chapters: {story.total_chapters}\n\n"
    )
    if story.source_spirit:
        base += ("## SOURCE SPIRIT (tone, plot arc — role-based, no source names)\n"
                 f"{story.source_spirit}\n\n")
    base += f"## SOURCE GRAPH\n{source_summary}\n"
    if feedback:
        base += ("\n\n## FEEDBACK FROM VERIFICATION — the previous world/graph had "
                 "these problems; redesign the world to FIX them (you may shift "
                 "setting, tone, or coherence as needed), while keeping it name-free:\n"
                 f"{feedback}\n")

    user_content = base
    output = None
    for attempt in range(max_attempts):
        output = _common.call_agent(
            "world_designer",
            user_content=user_content,
            schema=WorldDesignOutput,
            max_tokens=4096,
            thinking=True,
        )
        leaks = _world_design_name_leaks(story, output)
        if not leaks:
            break
        logger.warning("[%s] Phase 0: world design still has proper names %s — regenerating",
                       story.slug, leaks[:12])
        user_content = base + (
            "\n\n## REJECTED — your previous output contained INVENTED PROPER NAMES, "
            "which is forbidden. Rewrite EVERY field using only roles/descriptions "
            "(the protagonist, the rival clan, the riverside teahouse). Remove these "
            f"names entirely: {', '.join(leaks[:20])}\n"
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


def _lexicon_duplicate_labels(entries) -> list:
    """Entries whose FULL new name is identical to an earlier entry's (word-set, so
    case- and order-independent). A shared surname between family/clan members is NOT
    a collision — only a truly identical full name is, since that is the only case
    that makes two different characters indistinguishable. Returns [(entry, reason)]
    for regeneration."""
    bad = []
    seen: dict[str, str] = {}       # normalized full name → node_key
    for e in entries:
        key = _sorted_name_key(e.new_label)
        if not key:
            continue
        if key in seen:
            bad.append((e, f"identical full name to {seen[key]}"))
        else:
            seen[key] = e.node_key
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

    output: NameLexiconOutput = _common.call_agent(
        "name_lexicon",
        user_content=base_user,
        schema=NameLexiconOutput,
        max_tokens=4096,
        thinking=False,
    )

    # Validate + bounded regen. The lexicon is the ONLY place names are minted, so
    # two rules are enforced with deterministic backstops behind the prompt:
    #   1. no new name reuses a forbidden SOURCE proper noun (leak)
    #   2. every new name is UNIQUE — no identical labels, no two characters sharing
    #      a distinctive given-name token (collision like 'Elder Lysander' x2)
    for _attempt in range(3):
        leaks = _lexicon_collisions(output.entries, forbidden)
        dups = _lexicon_duplicate_labels(output.entries)
        if not leaks and not dups:
            break
        parts = []
        if leaks:
            parts.append("REUSE a source proper noun: " +
                         "; ".join(f"'{e.new_label}' (source word '{tok}')" for e, tok in leaks))
        if dups:
            parts.append("COLLIDE with another new name: " +
                         "; ".join(f"'{e.new_label}' [{e.node_key}] — {why}" for e, why in dups))
        violation = "\n".join(parts)
        logger.warning("[%s] Phase 1b: lexicon violations — regenerating:\n%s",
                       story.slug, violation)
        retry_user = base_user + (
            "\n\n## HARD CONSTRAINT VIOLATIONS — the following invented names are REJECTED. "
            "Re-issue the FULL lexicon: each name must share NO word with the source AND "
            "no two entries may have an IDENTICAL full name. (Family members SHARING a "
            "surname is REQUIRED, not a violation — keep each family on one shared "
            f"surname with distinct given names.):\n{violation}\n"
        )
        output = _common.call_agent(
            "name_lexicon",
            user_content=retry_user,
            schema=NameLexiconOutput,
            max_tokens=4096,
            thinking=False,
        )

    lexicon = {e.node_key: e.new_label for e in output.entries}
    leftover = _lexicon_collisions(output.entries, forbidden) + _lexicon_duplicate_labels(output.entries)
    if leftover:
        logger.warning("[%s] Phase 1b: %d name violation(s) survived after retries",
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
    output: NewGraphSurfaceOutput = _common.call_agent(
        "new_graph_builder",
        user_content=user_content,
        schema=NewGraphSurfaceOutput,
        max_tokens=65536,
        thinking=not feedback,
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
    user_content = (
        f"language: {story.language}\n"
        f"total_chapters: {story.total_chapters}\n\n"
        f"## NEW GRAPH (after surface rename)\n{new_graph_text}\n"
    )
    output: GraphEnrichmentOutput = _common.call_agent(
        "graph_enricher",
        user_content=user_content,
        schema=GraphEnrichmentOutput,
        max_tokens=16384,
        thinking=False,
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

def _edge_facts(edge: StoryGraphEdge) -> list[str]:
    """Every human-readable fact on an edge, as `key: value` where a key exists.

    Reads ALL of `properties`, not just `rel_type`. The enrichers are not consistent
    about which key they use — kinship turns up under `relationship`, and some edges
    carry no `rel_type` at all — so keying on one name silently loses the fact.
    """
    facts = []
    for k, v in (edge.properties or {}).items():
        if isinstance(v, str) and v.strip():
            facts.append(f"{k}: {v.strip()}")
    for v in (edge.label, edge.condition):
        if v and v.strip():
            facts.append(v.strip())
    return facts


def relation_lines_for(session: Session, story: Story) -> list[str]:
    """Every recorded relationship fact, grouped by character pair.

    This deliberately does NOT decide which fact matters — the model reading it does.
    An earlier version tried to pick one line per pair and lost the thing it existed
    to protect: for a story premised on "she fake-dates her ex's step-brother", the
    pair's own line came out as "Ethan holds contempt for Julian" with no mention of
    step-siblings, so the bible invented a different relationship and the title
    dropped the hook. Picking by length failed because a structural fact is terse by
    nature; classifying by a kinship keyword list failed differently, because such a
    list is never finished — grandmother, godfather, foster and stepmother-in-law all
    have to be predicted in advance, and whatever is missed is silently discarded.

    Grouping is lossless, so no prediction is needed. Duplicates within a pair are
    collapsed (the graph stores one edge per chapter that touches a relationship, so
    the same phrase recurs many times), which is safe — it removes repetition, never
    a distinct fact.

    Direction is dropped on purpose: a relationship map wants one entry per pair, and
    both directions carry the same fact.
    """
    char_labels = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode).filter_by(
            story_id=story.id, graph_type="new", node_type="character")
    }
    by_pair: dict[tuple[str, str], list[str]] = {}
    for e in session.query(StoryGraphEdge).filter_by(
            story_id=story.id, graph_type="new", edge_type="RELATION"):
        src, tgt = char_labels.get(e.source_key), char_labels.get(e.target_key)
        if not src or not tgt or src == tgt:
            continue
        key = (src, tgt) if src <= tgt else (tgt, src)
        seen = by_pair.setdefault(key, [])
        for fact in _edge_facts(e):
            if fact not in seen:
                seen.append(fact)

    lines = []
    for (a, b), facts in sorted(by_pair.items()):
        if not facts:
            continue
        lines.append(f"- {a} ↔ {b}:")
        lines.extend(f"    • {f[:200]}" for f in facts)
    return lines


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
    relation_lines = relation_lines_for(session, story)
    relation_block = "\n".join(relation_lines)
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
        "The CHARACTER RELATIONSHIPS block lists every recorded fact per pair, mixing "
        "PERMANENT ties (family, marriage, exes, guardianship — including ones no "
        "single word covers, e.g. raised in the same household) with the CURRENT "
        "emotional state (affection, contempt, rivalry), in no particular order. Work "
        "out which facts are permanent and state EVERY one of them explicitly in the "
        "prose — those are what make a premise forbidden or high-stakes. If the love "
        "interest is the antagonist's step-brother, say so in a sentence. A reader of "
        "this bible must never have to infer who is whose sibling, spouse, ex or "
        "parent, and must never be told a tie the block does not contain. "
        "Return ONLY the story bible prose — no headings, no commentary."
    )
    user_content = (
        f"language: {story.language}\n\n"
        f"## WORLD DESIGN\n{world_block}\n\n"
        f"## NEW CHARACTERS (use EXACTLY these names — no others)\n{char_block}\n\n"
        f"## CHARACTER RELATIONSHIPS — every recorded fact, grouped by pair.\n"
        f"## Permanent ties and current mood are mixed together; sort them out and "
        f"put every permanent tie into the prose.\n"
        f"{relation_block or '(none recorded)'}\n\n"
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

    # Stamp the era on the very first line. world_design carries `time_period`, but it
    # was only ever used to WRITE this prose and the prose has no reason to repeat it —
    # so across eight finished stories not one bible mentioned its own era anywhere.
    # Everything downstream then had to guess: chapter_writer drifted into "the healer's
    # parchment" for a pregnancy test in a modern corporate story, and the poster art
    # dressed a 19th-century cast in modern tuxedos. It goes FIRST because
    # image_generator only reads the opening 3000 characters.
    era = (world_design.time_period if world_design else "").strip()
    if not era:
        m = re.search(r"^ERA:[ \t]*(.+)$", story.story_bible or "", re.MULTILINE)
        era = m.group(1).strip() if m else ""
    if era:
        prose = f"ERA: {era}\n\n{prose}"

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
    # Same deduped lines the prose writer was given, so the two can't disagree.
    rel_section = ("## Sơ đồ quan hệ nhân vật\n\n" + relation_block) if relation_lines else ""

    story.story_bible = prose + "\n\n" + plot_map_section + (("\n\n" + rel_section) if rel_section else "")
    logger.info("[%s] story_bible rewritten: %d chars (prose=%d, events=%d, relations=%d)",
                story.slug, len(story.story_bible), len(prose), len(event_nodes), len(relation_lines))


def _confirm_story_bible_leaks(story: Story, candidates: list[str], prose: str) -> list[str]:
    """Stage 2 of the leak check: given Python's whole-word source-name CANDIDATES,
    ask an LLM which are ACTUAL source-name leaks vs false positives (a coincidental
    common word, or a name the new world legitimately reused). On any failure, treat
    all candidates as leaks — conservative, so a real leak is never silently kept."""
    if not candidates:
        return []
    try:
        out: StoryBibleLeakCheckOutput = _common.call_agent(
            "story_bible_leak_check",
            user_content=f"language: {story.language}\n\n## CANDIDATES\n{', '.join(candidates)}\n\n## STORY-BIBLE PROSE\n{prose}\n",
            model_fallback="name_lexicon",
            schema=StoryBibleLeakCheckOutput,
            max_tokens=2048,
            thinking=False,
        )
        confirmed = {n.strip().lower() for n in (out.real_leaks or []) if n and n.strip()}
        # Keep only names that were actually candidates — the LLM must not add new ones.
        return [c for c in candidates if c.lower() in confirmed]
    except Exception as exc:
        logger.warning("[%s] story_bible leak judge failed (%s) — treating all %d "
                       "candidate(s) as leaks", story.slug, exc, len(candidates))
        return candidates


def _verify_story_bible(
    session: Session,
    story: Story,
    world_design: WorldDesignOutput | None = None,
    max_retries: int = 10,
) -> None:
    """Check story_bible for leaked source character names; retry rewrite if found.

    Two stages: Python word-boundary scan finds candidate source names appearing as
    WHOLE words, then an LLM judges which candidates are genuine leaks vs false
    positives (common word / legit new-world reuse). Only confirmed leaks retry."""
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
        # Stage 1 (Python, cheap, deterministic): word-boundary match so a source name
        # is flagged only as a WHOLE word — "Kael" no longer matches inside a new name
        # like "Kaelen". Same rule as substitute_labels (word-boundary).
        candidates = [
            name for name in source_labels
            if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", prose_only, re.IGNORECASE)
        ]
        # Stage 2 (LLM judge, only when there are candidates): keep just the genuine
        # source-name leaks; drop false positives (common word / legit reuse).
        leaked = _confirm_story_bible_leaks(story, candidates, prose_only)
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

    # Phase 0 — design the new world. Always run: on a feedback rebuild the world is
    # REDESIGNED to address the verifier's feedback (a feedback loop that improves
    # the world), not frozen. The name-check loop inside keeps it name-free either way.
    world_design = _design_world(session, story, feedback=feedback)
    story.story_bible = world_design.narrative_summary
    world_design_text = world_design.narrative_summary

    # Phase 1b — name lexicon. Feed the lexicon EVERY name the world design already
    # coined (narrative prose + the named locations/archetypes it lists), not just
    # the narrative, so it can adopt them ALL (rule 0) and become the single source
    # of names — no entity ends up with one name in the world design and a different
    # one in the lexicon.
    if world_design is not None:
        lexicon_world_text = (
            f"{world_design.narrative_summary}\n\n"
            f"Named locations already coined: {', '.join(world_design.location_concepts)}\n"
            f"Protagonist archetype: {world_design.protagonist_archetype}\n"
            f"Antagonist archetype: {world_design.antagonist_archetype}"
        )
    else:
        lexicon_world_text = world_design_text
    lexicon = _build_name_lexicon(session, story, lexicon_world_text, feedback)

    # Phase 1.5 — Python name substitution (deterministic, convergent)
    _apply_lexicon_substitution(session, story.id, lexicon)

    # Phase 2 — per-group LLM enrichment, each gated by the unified enrich-verifier
    # (verify → re-enrich with feedback until clean, max 5 attempts per group).
    for group, fn in (
        ("characters",  _enrich_characters),
        ("events",      _enrich_events),
        ("arc_changes", _enrich_arc_changes),
        ("relations",   _enrich_relations),
        ("causes",      _enrich_causes),
    ):
        _enrich_with_verify(
            story, group,
            lambda fb, keys, _fn=fn: _fn(session, story, lexicon, world_design_text,
                                         feedback=fb, only_keys=keys),
            session, world_design_text,
        )

    # Phase 3 — creative enrichment (first run only)
    if not feedback:
        _enrich_graph(session, story)

    # Naming is settled UPSTREAM now — the lexicon assigns every name, Phase 1.5
    # substitution applies them deterministically across all text, the enrichers are
    # constrained to use only exact labels, and verify_graph's reskin pass sweeps any
    # leaked SOURCE name. So the old Phase 3.4 name-reconcile pass was removed: it was
    # a backstop for enrichers inventing names (now prevented) whose orphan-name guess
    # (regex-replacing "stray" capitalized phrases) was unreliable and, in one run,
    # ran away — inflating a node's text to ~1e9 chars until SQLite rejected the write.
    story.plot_outline = None
    story.world_bible = None
    story.planning_verified = False
    session.query(Character).filter_by(story_id=story.id).delete(synchronize_session="fetch")
    session.flush()
    story.new_graph_built = True
    logger.info("[%s] new_graph_builder: done", story.slug)


def finalize_after_verify(session: Session, story: Story) -> None:
    """Called by the orchestrator AFTER verify_graph (graph_verifier + its surface
    rewriter) has run — i.e. when the new graph is FINAL. Does the last name pass and
    derives story_bible from that final graph, so story_bible / plot_outline / world /
    characters (all built from here on) share ONE canonical name set.

    Order matters: verify_graph is the last step that touches graph CONTENT, so every
    artifact derivation must happen here, not inside run() (which finishes before
    verify_graph). This is what fixed the 3-names-for-one-character desync (story_bible
    said 'Lyra Vane', plot said 'Kira Valorant', labels said 'Seraphina Nova') —
    everything now derives from the FINAL graph's labels."""
    # Derive story_bible from the FINAL graph (names are settled by the lexicon +
    # substitution + label-constrained enrichers; verify_graph's reskin already swept
    # any leaked source name). _verify_story_bible still guards story_bible itself.
    _rewrite_story_bible(session, story, world_design=None)
    _verify_story_bible(session, story)
    # Retitle for the new world from the final story_bible.
    try:
        new_title = generate_title(
            PROVIDER, AGENT_MODELS["title_generator"],
            language=story.language, input_type="PREMISE",
            genre=story.genre, source_content=story.story_bible or "",
            relationships="\n".join(relation_lines_for(session, story)),
        )
        if new_title and new_title.strip():
            logger.info("[%s] retitled for new world: %r -> %r",
                        story.slug, story.title, new_title.strip())
            story.title = new_title.strip()
            # Keep the slug (used for the output folder name) in sync with the final
            # title — otherwise the folder keeps the stale pre-retitle slug and no
            # longer matches the story's name. Collision with another story's folder
            # is still resolved at export time via the .story-{id} ownership marker.
            new_slug = slugify(story.title)
            if new_slug:
                story.slug = new_slug
    except Exception as exc:
        logger.warning("[%s] retitle failed, keeping original: %s", story.slug, exc)
    session.flush()
    logger.info("[%s] finalize_after_verify: story_bible derived from final graph", story.slug)
