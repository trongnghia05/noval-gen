"""Verify the new story graph on three axes:

1. NARRATIVE LOGIC (all input types): check that surface content is coherent —
   CAUSES mechanisms make sense, ARC_CHANGE events justify arc shifts, overall
   story has a clear shape. Critical → graph_surface_rewriter (targeted patch).

2. RESKIN QUALITY (REWRITE only): surface is genuinely different from source —
   no copied names, no near-verbatim summaries. Critical → Python name
   substitution using the reverse lexicon derived from existing DB nodes
   (deterministic, convergent — never triggers a full graph rebuild).

3. ENRICHMENT VALIDITY (always): Phase 3 additions don't violate constraints —
   no EVENT nodes added, no CAUSES/ARC_CHANGE from enrichment nodes. Critical →
   remove the offending enrichment node/edge directly.

4. CAST ROLES (always): every character carries a role from the allowed set, and
   the cast has exactly one protagonist. Critical → deterministic Python repair
   from graph connectivity — a role is a property assignment, not prose, so the
   surface rewriter cannot fix it.

Max 10 iterations. Sets story.new_graph_verified = True when done or exhausted.
"""

import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from . import _common
from .. import context_builder
from ..db.models import PlanningVerifyLog, Story, StoryGraphEdge, StoryGraphNode
from ..schemas import GraphVerifierOutput
from . import graph_surface_rewriter
from .new_graph_builder import (
    _VALID_ROLES,
    _character_source_labels,
    _tier_for_role,
    substitute_labels,
)

MAX_ITERATIONS = 10
# Cap on how many near-verbatim nodes/edges get an LLM content-rewrite per
# iteration (prioritising the flagged ones) so a 60-issue iteration doesn't fan
# out into 60 rewrite calls. No early-stop: the loop still runs all iterations.
_MAX_RESKIN_REWRITE = 25
logger = logging.getLogger(__name__)


def _apply_reskin_substitution(session: Session, story: Story) -> None:
    """Replace leaked source labels with new labels — derived from DB, no LLM.

    Builds a source_label → new_label map by matching node_keys between the
    source and new graphs, then delegates to the shared substitute_labels()
    (word-boundary safe, covers aliases). Deterministic and convergent.
    """
    source_map = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source").all()
    }
    new_map = {
        n.node_key: n.label
        for n in session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new").all()
        if n.node_type in ("character", "location", "faction", "object")
    }
    label_map: dict[str, str] = {}
    for key, src_label in source_map.items():
        new_label = new_map.get(key)
        if new_label and new_label != src_label:
            label_map[src_label] = new_label

    count = substitute_labels(
        session, story.id, label_map,
        name_labels=_character_source_labels(session, story.id),
    )
    logger.info("[%s] reskin substitution: %d label pairs applied", story.slug, count)


def _repair_cast_roles(session: Session, story: Story) -> list[str]:
    """Force the cast into a usable shape: valid roles, exactly one protagonist.

    Deterministic rather than another LLM call, because this must converge — and
    because connectivity is a better protagonist signal than anything a model can
    re-derive here: the lead is in more RELATION edges than anyone else, by a wide
    margin. Returns a list of the changes made, for the log.

    Runs after the character enricher has already assigned roles, so in the normal
    case it finds nothing to do; it exists for when the enricher returns a role
    outside the allowed set, or none at all.
    """
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="new", node_type="character")
        .all()
    )
    if not nodes:
        return []

    degree: dict[str, int] = defaultdict(int)
    for e in session.query(StoryGraphEdge).filter_by(
            story_id=story.id, graph_type="new", edge_type="RELATION"):
        degree[e.source_key] += 1
        degree[e.target_key] += 1

    changes: list[str] = []

    def set_role(node, role, why):
        props = dict(node.properties or {})
        old = props.get("role", "")
        if old == role:
            return
        props["role"] = role
        node.properties = props
        changes.append(f"{node.label}: {old or '(none)'} -> {role} ({why})")

    mean_degree = (sum(degree.get(n.node_key, 0) for n in nodes) / len(nodes)) or 0

    # 1. Bring every role into the allowed set. A role that is merely worded oddly
    #    ("male_lead", "minor_antagonist") still carries intent, so it maps by tier.
    #    An EMPTY role carries none, and must not be guessed as `minor`: extraction
    #    drops the key on leads as readily as on bit-players, and writing `minor`
    #    onto the protagonist is worse than leaving it blank. Connectivity decides
    #    those instead.
    for n in nodes:
        role = ((n.properties or {}).get("role") or "").strip().lower()
        if role in _VALID_ROLES:
            continue
        if role:
            tier = _tier_for_role(role)
            set_role(n, {"core": "protagonist", "important": "supporting"}.get(tier, "minor"),
                     f"invalid role {role!r}")
        else:
            deg = degree.get(n.node_key, 0)
            set_role(n, "supporting" if deg >= mean_degree else "minor",
                     f"no role, {deg} relations vs cast mean {mean_degree:.1f}")

    # 2. Exactly one protagonist, and it is the character the graph revolves around.
    #    Degree separates the leads sharply — in a real cast the top character had 57
    #    relations, the next 46, the fourth only 7 — so the most-connected character
    #    takes the part. Another character already marked protagonist becomes the
    #    love_interest rather than being demoted out of the leads: in a romance that
    #    is what a second lead actually is, and it keeps them in the `core` tier.
    winner = max(nodes, key=lambda n: degree.get(n.node_key, 0))
    for n in nodes:
        if n is not winner and (n.properties or {}).get("role") == "protagonist":
            set_role(n, "love_interest", "second lead, only one protagonist allowed")
    set_role(winner, "protagonist", f"most connected, {degree.get(winner.node_key, 0)} relations")

    # 3. A cast with nobody opposing the lead reads as no conflict at all, and leaves
    #    the antagonist sitting in a lower tier than the plot gives them. When the
    #    role is missing entirely, the best-connected remaining character is the
    #    opposing force — third place, after the lead and the love interest.
    if not any((n.properties or {}).get("role") == "antagonist" for n in nodes):
        rest = [n for n in nodes
                if (n.properties or {}).get("role") not in ("protagonist", "love_interest")]
        if rest:
            foe = max(rest, key=lambda n: degree.get(n.node_key, 0))
            if degree.get(foe.node_key, 0) > 0:
                set_role(foe, "antagonist",
                         f"no antagonist in cast, {degree.get(foe.node_key, 0)} relations")

    if changes:
        session.flush()
    return changes


def _remove_enrichment_nodes(session: Session, story_id: int, node_keys: list[str]) -> None:
    """Remove invalid enrichment nodes and their edges."""
    for key in node_keys:
        session.query(StoryGraphEdge).filter_by(story_id=story_id, graph_type="new").filter(
            (StoryGraphEdge.source_key == key) | (StoryGraphEdge.target_key == key)
        ).delete(synchronize_session="fetch")
        session.query(StoryGraphNode).filter_by(
            story_id=story_id, graph_type="new", node_key=key
        ).delete(synchronize_session="fetch")
    session.flush()


def _deduplicate_nodes(session: Session, story_id: int) -> int:
    """Remove duplicate new-graph nodes (same node_type + label) before LLM verification.

    graph_surface_rewriter can only PATCH properties — it cannot delete nodes.
    Keeping duplicates causes the LLM verifier to flag them every iteration,
    burning cycles. This Python pass detects duplicates by (node_type, label),
    keeps the lowest node_key (first-created), redirects edges from duplicates
    to the survivor, then deletes duplicates. Returns the count removed.
    """
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="new")
        .all()
    )
    groups: dict[tuple, list] = defaultdict(list)
    for n in nodes:
        groups[(n.node_type, n.label.strip().lower())].append(n)

    removed = 0
    for (_ntype, _label), group in groups.items():
        if len(group) <= 1:
            continue
        group.sort(key=lambda n: n.node_key)
        survivor = group[0]
        for dup in group[1:]:
            session.query(StoryGraphEdge).filter_by(
                story_id=story_id, graph_type="new", source_key=dup.node_key
            ).update({"source_key": survivor.node_key}, synchronize_session="fetch")
            session.query(StoryGraphEdge).filter_by(
                story_id=story_id, graph_type="new", target_key=dup.node_key
            ).update({"target_key": survivor.node_key}, synchronize_session="fetch")
            # Drop self-loops created by the redirect
            session.query(StoryGraphEdge).filter_by(
                story_id=story_id, graph_type="new",
                source_key=survivor.node_key, target_key=survivor.node_key,
            ).delete(synchronize_session="fetch")
            session.query(StoryGraphNode).filter_by(
                story_id=story_id, graph_type="new", node_key=dup.node_key
            ).delete(synchronize_session="fetch")
            logger.info("[story %d] deduplicate: removed %s '%s' (duplicate of %s)",
                        story_id, dup.node_key, dup.label, survivor.node_key)
            removed += 1

    if removed:
        session.flush()
    return removed


def run(session: Session, story: Story) -> None:
    """Verify the new graph. Routes critical issues to the right repair agent."""
    # Pre-pass: remove label duplicates before the LLM sees them.
    # graph_surface_rewriter can only patch — it cannot delete — so duplicates
    # would survive every iteration and waste MAX_ITERATIONS LLM calls.
    dup_count = _deduplicate_nodes(session, story.id)
    if dup_count:
        logger.info("[%s] graph_verifier: pre-pass removed %d duplicate nodes",
                    story.slug, dup_count)

    source_graph_text = ""
    if story.input_type == "REWRITE":
        source_graph_text = context_builder.format_story_graph(
            session, story.id, graph_type="source"
        )
        logger.info("[%s] graph_verifier: REWRITE — checking narrative_logic + reskin + enrichment",
                    story.slug)
    else:
        logger.info("[%s] graph_verifier: checking narrative_logic + enrichment (input_type=%s)",
                    story.slug, story.input_type)

    for iteration in range(MAX_ITERATIONS):
        new_graph_text = context_builder.format_story_graph(
            session, story.id, graph_type="new"
        )
        user_content = (
            f"language: {story.language}\n"
            f"input_type: {story.input_type}\n"
            f"total_chapters: {story.total_chapters}\n\n"
            f"## NEW STORY GRAPH\n{new_graph_text}\n"
        )
        if source_graph_text:
            user_content += (
                f"\n## SOURCE GRAPH (for RESKIN QUALITY check)\n{source_graph_text}\n"
            )

        output: GraphVerifierOutput = _common.call_agent(
        "graph_verifier",
        user_content=user_content,
        schema=GraphVerifierOutput,
        max_tokens=48000,
        thinking=False,
    )

        critical = [i for i in output.issues if i.severity == "critical"]
        logger.info("[%s] graph_verifier iter%d: %d issues (%d critical) | %s",
                    story.slug, iteration + 1, len(output.issues), len(critical),
                    output.verdict_note)
        for issue in output.issues:
            logger.info("  [%s][%s] node=%s | %s | fix: %s",
                        issue.severity.upper(), issue.check_type, issue.node_key,
                        issue.description, issue.suggestion)

        for issue in output.issues:
            session.add(PlanningVerifyLog(
                story_id=story.id,
                artifact="graph",
                severity=issue.severity,
                description=f"[{issue.check_type}] {issue.description}",
                suggestion=issue.suggestion,
                action_taken=(
                    f"{issue.check_type}_repair_iter_{iteration + 1}"
                    if issue.severity == "critical"
                    else "logged_only"
                ),
            ))
        session.flush()

        if not critical:
            break

        narrative_critical = [i for i in critical if i.check_type == "narrative_logic"]
        reskin_critical    = [i for i in critical if i.check_type == "reskin"]
        enrichment_critical = [i for i in critical if i.check_type == "enrichment"]

        if narrative_critical:
            logger.info("[%s] graph_verifier: surface-rewriting %d narrative_logic issues",
                        story.slug, len(narrative_critical))
            graph_surface_rewriter.run(session, story, narrative_critical)
            session.flush()

        if reskin_critical:
            # Two-part reskin fix:
            #  (a) leaked NAMES → deterministic Python substitution (cheap, exact).
            #  (b) near-verbatim CONTENT (summary/mechanism/description that is a
            #      direct translation of the source) → surface rewrite, since
            #      substitution only swaps names and can't fix copied phrasing.
            logger.info("[%s] graph_verifier: reskin fix — name substitution + content rewrite for %d issues",
                        story.slug, len(reskin_critical))
            _apply_reskin_substitution(session, story)
            session.flush()
            reskin_targeted = [i for i in reskin_critical if i.node_key][:_MAX_RESKIN_REWRITE]
            if reskin_targeted:
                logger.info("[%s] graph_verifier: reskin content-rewrite for %d node(s)",
                            story.slug, len(reskin_targeted))
                graph_surface_rewriter.run(session, story, reskin_targeted, reason="reskin")
                session.flush()

        if enrichment_critical:
            bad_keys = [i.node_key for i in enrichment_critical if i.node_key]
            if bad_keys:
                logger.info("[%s] graph_verifier: removing %d invalid enrichment nodes: %s",
                            story.slug, len(bad_keys), bad_keys)
                _remove_enrichment_nodes(session, story.id, bad_keys)

    # Cast roles are repaired unconditionally, not only when the model flags them:
    # the check is cheap, deterministic, and a graph with no protagonist silently
    # mis-tiers the whole cast downstream (cover art, per-chapter context, naming).
    role_changes = _repair_cast_roles(session, story)
    if role_changes:
        logger.warning("[%s] graph_verifier: cast roles repaired — %s",
                       story.slug, "; ".join(role_changes))

    story.new_graph_verified = True
    logger.info("[%s] graph_verifier: done — new_graph_verified=True", story.slug)
