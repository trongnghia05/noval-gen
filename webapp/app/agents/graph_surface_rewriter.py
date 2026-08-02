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
from ..schemas import GraphSurfaceRepairOutput, GraphVerifyIssueOut, NewEdgeForRepairOut

logger = logging.getLogger(__name__)


_RESKIN_DIRECTIVE = (
    "## MODE: RESKIN-ORIGINALITY REWRITE\n"
    "The listed nodes/edges are too close to the SOURCE (near-verbatim copy or "
    "direct translation). Rewrite each flagged field (summary / mechanism / "
    "description / label / arc) COMPLETELY in fresh wording of THIS new world.\n"
    "- KEEP: the same events, the same causal function (which event causes which), "
    "the same participants, and the same chapter timing. Do NOT change story logic.\n"
    "- CHANGE: the sentence structure entirely (no translating/mirroring the source), "
    "AND the concrete surface details — the MEANS, props, setting and imagery — to "
    "things native to this world (e.g. a spied-on camera → a scrying ritual; a prenup "
    "→ a blood-oath covenant). This is a copyright-safety rewrite: the result must not "
    "read as a paraphrase of the source.\n\n"
)


def run(
    session: Session,
    story: Story,
    issues: list[GraphVerifyIssueOut],
    reason: str = "logic",
) -> None:
    """Rewrite surface content for flagged nodes/edges.

    reason="logic": repair narrative-logic problems.
    reason="reskin": rewrite content that is too close to the source (originality/
    copyright), changing wording AND surface detail while preserving story logic.
    Only patches the listed items — structure (chapter_from, chapter_to, etc.) is
    never touched.
    """
    new_graph_text = context_builder.format_story_graph(session, story.id, graph_type="new")

    issue_lines = [
        f"- [{i.node_key or 'general'}] {i.edge_desc or ''} | {i.description} → Fix: {i.suggestion}"
        for i in issues
    ]
    issues_text = "\n".join(issue_lines)

    system = load_prompt("graph_surface_rewriter")
    directive = _RESKIN_DIRECTIVE if reason == "reskin" else ""
    user_content = (
        f"language: {story.language}\n\n"
        f"{directive}"
        f"## NEW STORY GRAPH\n{new_graph_text}\n\n"
        f"## ISSUES TO FIX\n{issues_text}\n"
    )

    output: GraphSurfaceRepairOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["graph_surface_rewriter"],
        schema=GraphSurfaceRepairOutput,
        max_tokens=16384,
        thinking=False,
    )

    all_nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new")
        .all()
    )
    node_map = {n.node_key: n for n in all_nodes}

    # Node-id normalization: the verifier/rewriter sometimes returns a node's NAME
    # (label, e.g. "Seraphina") instead of its node_key ("C001"), which made the
    # patch miss entirely. Map label→key (case-insensitive) so either form resolves.
    label_to_key = {(n.label or "").strip().lower(): n.node_key for n in all_nodes if n.label}

    def _resolve_key(raw: str) -> str | None:
        if not raw:
            return None
        if raw in node_map:
            return raw
        return label_to_key.get(raw.strip().lower())

    for patch in output.node_patches:
        key = _resolve_key(patch.node_key)
        node = node_map.get(key) if key else None
        if not node:
            logger.warning("[%s] graph_surface_rewriter: node %s not found", story.slug, patch.node_key)
            continue
        # NAMES ARE FROZEN: only the lexicon (source entities) and creative enrichment
        # (new entities) may set a node's name. This step fixes CONTENT only — it must
        # never rename an existing entity, so patch.new_label is deliberately ignored
        # for nodes. (Applying it caused the protagonist to be renamed post-reconcile
        # — "Kira Valorant"/"Lyra Vane" — desyncing the derived story_bible/plot.)
        props = dict(node.properties or {})
        if patch.new_summary:      props["summary"]     = patch.new_summary
        if patch.new_profile_md:   props["profile_md"]  = patch.new_profile_md
        if patch.new_description:  props["description"] = patch.new_description
        if patch.new_arc_stage:    props["arc_stage"]   = patch.new_arc_stage
        if patch.new_background:   props["background"]  = patch.new_background
        if patch.new_wants:        props["wants"]       = patch.new_wants
        if patch.new_fears:        props["fears"]       = patch.new_fears
        node.properties = props

    for patch in output.edge_patches:
        # Resolve source/target too (verifier may hand back names, not keys).
        src_key = _resolve_key(patch.source_key) or patch.source_key
        tgt_key = _resolve_key(patch.target_key) or patch.target_key
        edge = (
            session.query(StoryGraphEdge)
            .filter_by(
                story_id=story.id, graph_type="new",
                source_key=src_key, target_key=tgt_key,
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
                           source_key=src_key, target_key=tgt_key)
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
        if patch.new_condition:
            edge.condition = patch.new_condition
        _edge_prop_updates = {
            "mechanism": patch.new_mechanism,
            "rel_type": patch.new_rel_type,
            "old_val": patch.new_old_val,   # ARC_CHANGE
            "new_val": patch.new_new_val,   # ARC_CHANGE
        }
        if any(v for v in _edge_prop_updates.values()):
            props = dict(edge.properties or {})
            for k, v in _edge_prop_updates.items():
                if v:
                    props[k] = v
            edge.properties = props

    # Create missing edges from add_edges list
    existing_keys: set[tuple] = {
        (e.source_key, e.target_key, e.edge_type)
        for e in session.query(StoryGraphEdge)
        .filter_by(story_id=story.id, graph_type="new").all()
    }
    added = 0
    for new_edge in output.add_edges:
        key = (new_edge.source_key, new_edge.target_key, new_edge.edge_type)
        if key in existing_keys:
            logger.info("[%s] graph_surface_rewriter: edge %s→%s %s already exists — skipped",
                        story.slug, new_edge.source_key, new_edge.target_key, new_edge.edge_type)
            continue
        db_props: dict = {}
        if new_edge.edge_type == "PARTICIPATES" and new_edge.role:
            db_props["role"] = new_edge.role
        elif new_edge.edge_type == "RELATION" and new_edge.rel_type:
            db_props["rel_type"] = new_edge.rel_type
        session.add(StoryGraphEdge(
            story_id=story.id, graph_type="new",
            source_key=new_edge.source_key, target_key=new_edge.target_key,
            edge_type=new_edge.edge_type, label=new_edge.label or "",
            chapter_from=new_edge.chapter_from,
            properties=db_props,
        ))
        existing_keys.add(key)
        added += 1

    session.flush()
    logger.info("[%s] graph_surface_rewriter: patched %d nodes, %d edges, +%d new edges — %s",
                story.slug, len(output.node_patches), len(output.edge_patches),
                added, output.repair_note)
