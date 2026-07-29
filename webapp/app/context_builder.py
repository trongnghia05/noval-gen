"""Helpers that turn DB rows / CSV files into compact text blocks agents read —
the Python-side equivalent of world-state.md / chapter-summaries.md /
continuity-log.md / characters.md in the CLI version.
"""

from sqlalchemy.orm import Session

from .db.models import (
    Chapter,
    ChapterSummary,
    Character,
    ContinuityLog,
    Foreshadowing,
    SmartPlannerState,
    StoryGraphEdge,
    StoryGraphNode,
    WorldState,
)


def format_world_state(session: Session, story_id: int) -> str:
    rows = session.query(WorldState).filter_by(story_id=story_id).order_by(WorldState.entity_type, WorldState.entity_key).all()
    if not rows:
        return "(chưa có dữ liệu — đây là chương đầu tiên)"
    lines = []
    current_key = None
    for row in rows:
        key = (row.entity_type, row.entity_key)
        if key != current_key:
            lines.append(f"\n### {row.entity_type}: {row.entity_key}")
            current_key = key
        lines.append(f"- {row.field}: {row.value}")

    foreshadow = session.query(Foreshadowing).filter_by(story_id=story_id).all()
    if foreshadow:
        lines.append("\n### foreshadowing")
        for f in foreshadow:
            lines.append(
                f"- {f.fid}: {f.detail} (gieo Ch.{f.planted_chapter}, trạng thái: {f.status}"
                + (f", payoff Ch.{f.payoff_chapter}" if f.payoff_chapter else "")
                + ")"
            )
    return "\n".join(lines).strip()


def format_chapter_list(session: Session, story_id: int, current_chapter: int) -> str:
    """Compact chapter list for chapter_writer: number + title + 1-2 sentence summary.

    Gives the writer a panoramic view of the story so far and makes clear
    where the current chapter sits in the overall arc.
    """
    rows = (
        session.query(ChapterSummary)
        .filter_by(story_id=story_id)
        .order_by(ChapterSummary.chapter_number)
        .all()
    )
    chapters = session.query(Chapter).filter_by(story_id=story_id).all()
    title_map = {c.number: c.title for c in chapters if c.title}
    total = max((c.number for c in chapters), default=current_chapter)

    lines = [f"(Đang viết Ch.{current_chapter}/{total})\n"]
    for row in rows:
        title = title_map.get(row.chapter_number, "")
        title_part = f' "{title}"' if title else ""
        short = row.short_summary or (row.summary_text or "")[:120].replace("\n", " ")
        hook_part = f" | Hook: {row.hook}" if row.hook else ""
        lines.append(f"Ch{row.chapter_number:02d}{title_part}: {short}{hook_part}")
    if not rows:
        lines.append("(chưa có chương nào được viết)")
    return "\n".join(lines)


def format_chapter_summaries(session: Session, story_id: int) -> str:
    rows = (
        session.query(ChapterSummary)
        .filter_by(story_id=story_id)
        .order_by(ChapterSummary.chapter_number)
        .all()
    )
    if not rows:
        return "(chưa có chương nào được viết)"

    # Build title lookup from Chapter table
    chapters = (
        session.query(Chapter)
        .filter_by(story_id=story_id)
        .all()
    )
    title_map = {c.number: c.title for c in chapters if c.title}

    parts = []
    for row in rows:
        title = title_map.get(row.chapter_number, "")
        heading = f"## Chương {row.chapter_number}" + (f": {title}" if title else "")
        parts.append(f"{heading}\n{row.summary_text}")
    return "\n\n".join(parts)


def format_continuity_log(session: Session, story_id: int) -> str:
    log = session.query(ContinuityLog).filter_by(story_id=story_id).first()
    if not log or (not log.critical_issues and not log.minor_issues):
        return "Không có vấn đề continuity nào đang mở."
    lines = []
    if log.critical_issues:
        lines.append("CRITICAL:")
        for issue in log.critical_issues:
            lines.append(f"- {issue['description']} → {issue['suggestion']}")
    if log.minor_issues:
        lines.append("MINOR:")
        for issue in log.minor_issues:
            lines.append(f"- {issue['description']} → {issue['suggestion']}")
    return "\n".join(lines)


def format_characters(session: Session, story_id: int) -> str:
    rows = session.query(Character).filter_by(story_id=story_id).all()
    return "\n\n---\n\n".join(f"## {row.name} ({row.tier})\nAliases: {row.aliases}\n\n{row.profile_md}" for row in rows)


def format_character_aliases(session: Session, story_id: int) -> str:
    rows = session.query(Character).filter_by(story_id=story_id).all()
    return "\n".join(f"- {row.name}: {row.aliases}" for row in rows)


def format_gender_roster(session: Session, story_id: int) -> str:
    """name → gender for every character, from the new graph (the single source of
    truth). Fed to chapter_writer (use correct pronouns) and quality_reviewer (flag
    a character referred to with the wrong-gender pronoun). Gender is fixed by the
    source plot; it must never flip between chapters."""
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="new", node_type="character")
        .order_by(StoryGraphNode.node_key)
        .all()
    )
    lines = []
    for n in nodes:
        g = (n.properties or {}).get("gender", "unknown")
        lines.append(f"- {n.label}: {g}")
    return "\n".join(lines) if lines else "(no gender data)"


def format_smart_planner_adjustments(session: Session, story_id: int) -> str:
    state = session.query(SmartPlannerState).filter_by(story_id=story_id).first()
    if not state or not state.outline_adjustments:
        return "(chưa có điều chỉnh nào)"
    return state.outline_adjustments


def format_story_graph(session: Session, story_id: int, chapter_limit: int | None = None, graph_type: str = "source") -> str:
    """Render the story knowledge graph as compact text for agent prompts.

    graph_type: "source" (extracted from original) or "new" (creative transformation).
    chapter_limit: if set, only include nodes/edges introduced at or before
    that chapter (useful for chapter_writer to avoid spoilers from future nodes).
    """
    node_q = session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type=graph_type)
    if chapter_limit is not None:
        node_q = node_q.filter(
            (StoryGraphNode.chapter_introduced == None) |  # noqa: E711
            (StoryGraphNode.chapter_introduced <= chapter_limit)
        )
    nodes = node_q.order_by(StoryGraphNode.node_type, StoryGraphNode.chapter_introduced).all()

    if not nodes:
        return f"(story graph [{graph_type}] chưa được khởi tạo)"

    node_map = {n.node_key: n for n in nodes}
    valid_keys = set(node_map)

    edge_q = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type=graph_type)
        .filter(
            StoryGraphEdge.source_key.in_(valid_keys),
            StoryGraphEdge.target_key.in_(valid_keys),
        )
        .order_by(StoryGraphEdge.edge_type, StoryGraphEdge.chapter_from)
    )
    edges = edge_q.all()

    lines: list[str] = []

    # ── Characters ────────────────────────────────────────────────────
    chars = [n for n in nodes if n.node_type == "character"]
    if chars:
        lines.append("### NHÂN VẬT")
        for n in chars:
            p = n.properties or {}
            arc = p.get("arc_stage", "")
            wants = p.get("wants", "")
            fears = p.get("fears", "")
            role = p.get("role", "")
            detail = " | ".join(filter(None, [role, f"wants: {wants}" if wants else "", f"fears: {fears}" if fears else "", f"arc: {arc}" if arc else ""]))
            ch = f" [Ch.{n.chapter_introduced}]" if n.chapter_introduced else ""
            lines.append(f"  {n.node_key}{ch} {n.label}: {detail}")

    # ── Factions & Locations ──────────────────────────────────────────
    for ntype, header in [("faction", "PHE PHÁI"), ("location", "ĐỊA ĐIỂM")]:
        grp = [n for n in nodes if n.node_type == ntype]
        if grp:
            lines.append(f"\n### {header}")
            for n in grp:
                p = n.properties or {}
                desc = p.get("description") or p.get("goal") or ""
                ch = f" [Ch.{n.chapter_introduced}]" if n.chapter_introduced else ""
                lines.append(f"  {n.node_key}{ch} {n.label}: {desc}")

    # ── Events (ordered by chapter) ───────────────────────────────────
    events = sorted([n for n in nodes if n.node_type == "event"], key=lambda n: (n.chapter_introduced or 0))
    if events:
        lines.append("\n### SỰ KIỆN (theo thứ tự chương)")
        for n in events:
            p = n.properties or {}
            ch = f"Ch.{n.chapter_introduced}" if n.chapter_introduced else "pre"
            summary = p.get("summary", "")
            etype = p.get("event_type", "")
            tag = f"[{etype}] " if etype else ""
            lines.append(f"  {n.node_key} [{ch}]: {tag}{n.label}")
            if summary:
                lines.append(f"    → {summary}")

    # ── Objects & Themes ──────────────────────────────────────────────
    for ntype, header in [("object", "VẬT THỂ QUAN TRỌNG"), ("theme", "CHỦ ĐỀ")]:
        grp = [n for n in nodes if n.node_type == ntype]
        if grp:
            lines.append(f"\n### {header}")
            for n in grp:
                p = n.properties or {}
                desc = p.get("description") or p.get("central_question") or ""
                lines.append(f"  {n.node_key} {n.label}: {desc}")

    # ── Relations (temporal, grouped by pair) ─────────────────────────
    rel_edges = [e for e in edges if e.edge_type == "RELATION"]
    if rel_edges:
        lines.append("\n### QUAN HỆ NHÂN VẬT (theo thời gian)")
        for e in rel_edges:
            src = node_map.get(e.source_key)
            tgt = node_map.get(e.target_key)
            if not src or not tgt:
                continue
            p = e.properties or {}
            rtype = p.get("rel_type", e.edge_type)
            strength = p.get("strength")
            ch_range = f"Ch.{e.chapter_from}" if e.chapter_from else "?"
            if e.chapter_to:
                ch_range += f"→{e.chapter_to}"
            else:
                ch_range += "→∞"
            trigger = f" trigger={e.trigger_event_key}" if e.trigger_event_key else ""
            cond = f'\n    condition: "{e.condition}"' if e.condition else ""
            strength_str = f" strength={strength}" if strength is not None else ""
            lines.append(
                f"  {src.label} ──[{rtype}{strength_str} {ch_range}{trigger}]──► {tgt.label}"
                f"{cond}"
            )

    # ── Causal chains ─────────────────────────────────────────────────
    causal = [e for e in edges if e.edge_type == "CAUSES"]
    if causal:
        lines.append("\n### NHÂN QUẢ")
        for e in causal:
            src = node_map.get(e.source_key)
            tgt = node_map.get(e.target_key)
            if not src or not tgt:
                continue
            p = e.properties or {}
            mech = p.get("mechanism", e.label or "")
            lines.append(f"  {src.label} → {tgt.label}: {mech}")

    # ── Participates (character ↔ event) ──────────────────────────────
    part_edges = [e for e in edges if e.edge_type == "PARTICIPATES"]
    if part_edges:
        lines.append("\n### NHÂN VẬT THAM GIA SỰ KIỆN")
        for e in part_edges:
            src = node_map.get(e.source_key)
            tgt = node_map.get(e.target_key)
            if not src or not tgt:
                continue
            p = e.properties or {}
            role = p.get("role", e.label or "")
            ch = f" [Ch.{e.chapter_from}]" if e.chapter_from else ""
            lines.append(f"  {src.label}{ch} → {tgt.label} ({role})")

    # ── Foreshadowing ─────────────────────────────────────────────────
    foreshadow_edges = [e for e in edges if e.edge_type == "FORESHADOWS"]
    if foreshadow_edges:
        lines.append("\n### DỰ BÁO (FORESHADOWS)")
        for e in foreshadow_edges:
            src = node_map.get(e.source_key)
            tgt = node_map.get(e.target_key)
            if not src or not tgt:
                continue
            p = e.properties or {}
            hint = p.get("hint", e.label or "")
            lines.append(f"  {src.label} → {tgt.label}: {hint}")

    # ── Arc changes ───────────────────────────────────────────────────
    arc_edges = [e for e in edges if e.edge_type == "ARC_CHANGE"]
    if arc_edges:
        lines.append("\n### ARC THAY ĐỔI")
        for e in arc_edges:
            src = node_map.get(e.source_key)
            if not src:
                continue
            p = e.properties or {}
            field = p.get("field", "")
            old_v = p.get("old_val", "?")
            new_v = p.get("new_val", "?")
            ch = f"[Ch.{e.chapter_from}]" if e.chapter_from else ""
            trigger = f" (trigger: {e.trigger_event_key})" if e.trigger_event_key else ""
            lines.append(f"  {src.label} {ch}: {field} {old_v} → {new_v}{trigger}")

    return "\n".join(lines)


def format_chapter_subgraph(
    session: Session,
    story_id: int,
    chapter_number: int,
    graph_type: str = "new",
    max_depth: int = 1,
) -> str:
    """BFS subgraph centred on nodes participating in chapter_number.

    Layer 0: EVENT node for this chapter + CHARACTER nodes that PARTICIPATE in it.
    Layer 1: all edges touching those seed nodes → pulls in directly connected
             nodes (locations, other characters they relate to, causal events).
    Layer 2 (max_depth=2): edges of the nodes discovered in Layer 1.

    This keeps the verifier's context small and relevant — only the subgraph
    reachable from this chapter's participants, not the full story graph.
    """
    event_key = f"E{chapter_number:03d}"

    # ── Layer 0: seed nodes ────────────────────────────────────────────────────
    event_node = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type=graph_type, node_key=event_key)
        .first()
    )
    seed_keys: set[str] = {event_key} if event_node else set()

    part_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type=graph_type, edge_type="PARTICIPATES",
                   target_key=event_key)
        .all()
    )
    for e in part_edges:
        seed_keys.add(e.source_key)

    if not seed_keys:
        return f"(no graph nodes found for chapter {chapter_number})"

    # ── BFS up to max_depth ────────────────────────────────────────────────────
    visited_keys: set[str] = set(seed_keys)
    frontier: set[str] = set(seed_keys)
    collected_edges: list[StoryGraphEdge] = []

    for _ in range(max_depth):
        if not frontier:
            break
        layer_edges = (
            session.query(StoryGraphEdge)
            .filter_by(story_id=story_id, graph_type=graph_type)
            .filter(
                StoryGraphEdge.source_key.in_(frontier) |
                StoryGraphEdge.target_key.in_(frontier)
            )
            .all()
        )
        new_keys: set[str] = set()
        for e in layer_edges:
            collected_edges.append(e)
            if e.source_key not in visited_keys:
                new_keys.add(e.source_key)
            if e.target_key not in visited_keys:
                new_keys.add(e.target_key)
        visited_keys |= new_keys
        frontier = new_keys

    # ── Load all relevant nodes ────────────────────────────────────────────────
    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type=graph_type)
        .filter(StoryGraphNode.node_key.in_(visited_keys))
        .all()
    )
    node_map = {n.node_key: n for n in nodes}

    # Deduplicate edges
    seen_edge_ids: set[int] = set()
    unique_edges: list[StoryGraphEdge] = []
    for e in collected_edges:
        if e.id not in seen_edge_ids:
            seen_edge_ids.add(e.id)
            unique_edges.append(e)

    # ── Render ─────────────────────────────────────────────────────────────────
    lines: list[str] = [f"## Subgraph for Chapter {chapter_number} (depth={max_depth})"]

    for ntype, header in [
        ("event", "SỰ KIỆN"), ("character", "NHÂN VẬT"),
        ("location", "ĐỊA ĐIỂM"), ("faction", "PHE PHÁI"),
        ("object", "VẬT THỂ"), ("theme", "CHỦ ĐỀ"),
    ]:
        grp = [n for n in nodes if n.node_type == ntype]
        if not grp:
            continue
        lines.append(f"\n### {header}")
        for n in grp:
            p = n.properties or {}
            detail_parts = []
            for key in ("role", "arc_stage", "wants", "fears", "summary",
                        "description", "goal", "event_type"):
                if p.get(key):
                    detail_parts.append(f"{key}: {p[key]}")
            ch = f" [Ch.{n.chapter_introduced}]" if n.chapter_introduced else ""
            lines.append(f"  {n.node_key}{ch} {n.label}" +
                         (f" — {' | '.join(detail_parts)}" if detail_parts else ""))

    if unique_edges:
        lines.append("\n### QUAN HỆ & NHÂN QUẢ")
        for e in sorted(unique_edges, key=lambda x: (x.edge_type, x.chapter_from or 0)):
            src = node_map.get(e.source_key)
            tgt = node_map.get(e.target_key)
            src_label = src.label if src else e.source_key
            tgt_label = tgt.label if tgt else e.target_key
            p = e.properties or {}
            detail = p.get("rel_type") or p.get("mechanism") or p.get("role") or e.label or ""
            ch_info = f"Ch.{e.chapter_from}" if e.chapter_from else ""
            if e.chapter_to:
                ch_info += f"→{e.chapter_to}"
            elif e.chapter_from:
                ch_info += "→∞"
            tag = f"[{ch_info}] " if ch_info else ""
            lines.append(f"  {src_label} ──[{e.edge_type}: {detail}]──► {tgt_label} {tag}")

    return "\n".join(lines)


def format_node_subgraph(
    session: Session,
    story_id: int,
    node_key: str,
    graph_type: str = "new",
    max_depth: int = 1,
) -> str:
    """BFS subgraph centred on a specific node_key.

    Useful for repair context: given a broken node, surfaces all directly
    connected nodes and edges so the repair agent can see what it would affect.
    """
    seed_node = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type=graph_type, node_key=node_key)
        .first()
    )
    if not seed_node:
        return f"(node {node_key} not found in {graph_type} graph)"

    visited_keys: set[str] = {node_key}
    frontier: set[str] = {node_key}
    collected_edges: list[StoryGraphEdge] = []

    for _ in range(max_depth):
        if not frontier:
            break
        layer_edges = (
            session.query(StoryGraphEdge)
            .filter_by(story_id=story_id, graph_type=graph_type)
            .filter(
                StoryGraphEdge.source_key.in_(frontier) |
                StoryGraphEdge.target_key.in_(frontier)
            )
            .all()
        )
        new_keys: set[str] = set()
        for e in layer_edges:
            collected_edges.append(e)
            if e.source_key not in visited_keys:
                new_keys.add(e.source_key)
            if e.target_key not in visited_keys:
                new_keys.add(e.target_key)
        visited_keys |= new_keys
        frontier = new_keys

    nodes = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type=graph_type)
        .filter(StoryGraphNode.node_key.in_(visited_keys))
        .all()
    )
    node_map = {n.node_key: n for n in nodes}

    seen_ids: set[int] = set()
    unique_edges: list[StoryGraphEdge] = []
    for e in collected_edges:
        if e.id not in seen_ids:
            seen_ids.add(e.id)
            unique_edges.append(e)

    lines: list[str] = [f"## Subgraph around {node_key} ({graph_type}, depth={max_depth})"]
    for ntype, header in [
        ("event", "SỰ KIỆN"), ("character", "NHÂN VẬT"),
        ("location", "ĐỊA ĐIỂM"), ("faction", "PHE PHÁI"),
        ("object", "VẬT THỂ"), ("theme", "CHỦ ĐỀ"),
    ]:
        grp = [n for n in nodes if n.node_type == ntype]
        if not grp:
            continue
        lines.append(f"\n### {header}")
        for n in grp:
            p = n.properties or {}
            detail_parts = []
            for key in ("role", "arc_stage", "wants", "fears", "summary", "description", "goal", "event_type"):
                if p.get(key):
                    detail_parts.append(f"{key}: {p[key]}")
            marker = " ◄ TARGET" if n.node_key == node_key else ""
            ch = f" [Ch.{n.chapter_introduced}]" if n.chapter_introduced else ""
            lines.append(f"  {n.node_key}{ch} {n.label}{marker}" +
                         (f" — {' | '.join(detail_parts)}" if detail_parts else ""))

    if unique_edges:
        lines.append("\n### EDGES")
        for e in sorted(unique_edges, key=lambda x: (x.edge_type, x.chapter_from or 0)):
            src = node_map.get(e.source_key)
            tgt = node_map.get(e.target_key)
            p = e.properties or {}
            detail = p.get("rel_type") or p.get("mechanism") or p.get("role") or e.label or ""
            ch_info = f"Ch.{e.chapter_from}" if e.chapter_from else ""
            if e.chapter_to:
                ch_info += f"→{e.chapter_to}"
            elif e.chapter_from:
                ch_info += "→∞"
            tag = f"[{ch_info}] " if ch_info else ""
            lines.append(f"  {e.source_key} ──[{e.edge_type}: {detail}]──► {e.target_key} {tag}")

    return "\n".join(lines)


def format_chapter_context_from_graph(session: Session, story_id: int, chapter_number: int) -> str:
    """Build a compact context block for chapter_writer from the new graph.

    Queries the EVENT node for this chapter, the CHARACTER nodes that PARTICIPATE
    in it, their active RELATION edges, and the LOCATION where the event takes place.
    Falls back gracefully if the new graph hasn't been built yet.
    """
    event_key = f"E{chapter_number:03d}"
    event_node = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="new", node_key=event_key)
        .first()
    )
    if not event_node:
        return f"(new graph chưa có EVENT node {event_key})"

    lines: list[str] = []
    ep = event_node.properties or {}
    lines.append(f"## Kế hoạch chương {chapter_number}: {event_node.label}")
    if ep.get("summary"):
        lines.append(ep["summary"])
    if ep.get("event_type"):
        lines.append(f"Loại: {ep['event_type']} | Cảm xúc: {ep.get('emotional_weight', '')}")
    lines.append("")

    # Characters participating in this event via PARTICIPATES edges
    part_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="new", edge_type="PARTICIPATES", target_key=event_key)
        .all()
    )
    char_keys = [e.source_key for e in part_edges]
    if char_keys:
        lines.append("### Nhân vật tham gia")
        for e in part_edges:
            char = (
                session.query(StoryGraphNode)
                .filter_by(story_id=story_id, graph_type="new", node_key=e.source_key)
                .first()
            )
            if not char:
                continue
            cp = char.properties or {}
            role_in_event = (e.properties or {}).get("role", "")
            arc = cp.get("arc_stage", "")
            wants = cp.get("wants", "")
            profile = cp.get("profile_md", "")
            line = f"- **{char.label}** ({e.source_key})"
            if role_in_event:
                line += f" [{role_in_event}]"
            if arc:
                line += f" — arc: {arc}"
            if wants:
                line += f" | muốn: {wants}"
            lines.append(line)
            if profile:
                # Include brief profile excerpt (first 200 chars)
                brief = profile[:200].strip()
                if len(profile) > 200:
                    brief += "…"
                lines.append(f"  Profile: {brief}")

        # Active RELATION edges between participating characters at this chapter
        if len(char_keys) > 1:
            lines.append("")
            lines.append("### Quan hệ giữa các nhân vật (đang hoạt động)")
            active_rels = (
                session.query(StoryGraphEdge)
                .filter(
                    StoryGraphEdge.story_id == story_id,
                    StoryGraphEdge.graph_type == "new",
                    StoryGraphEdge.edge_type == "RELATION",
                    StoryGraphEdge.source_key.in_(char_keys),
                    StoryGraphEdge.target_key.in_(char_keys),
                    StoryGraphEdge.chapter_from <= chapter_number,
                )
                .filter(
                    (StoryGraphEdge.chapter_to.is_(None)) |
                    (StoryGraphEdge.chapter_to >= chapter_number)
                )
                .all()
            )
            for rel in active_rels:
                rp = rel.properties or {}
                rtype = rp.get("rel_type", rel.edge_type)
                strength = rp.get("strength")
                src_node = session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="new", node_key=rel.source_key).first()
                tgt_node = session.query(StoryGraphNode).filter_by(story_id=story_id, graph_type="new", node_key=rel.target_key).first()
                if not src_node or not tgt_node:
                    continue
                s = f"- {src_node.label} ↔ {tgt_node.label}: {rtype}"
                if strength is not None:
                    s += f" (strength={strength})"
                if rel.label:
                    s += f' "{rel.label}"'
                lines.append(s)
    lines.append("")

    # Location for this event via LOCATED_AT edge
    loc_edge = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="new", edge_type="LOCATED_AT", source_key=event_key)
        .first()
    )
    if loc_edge:
        loc_node = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story_id, graph_type="new", node_key=loc_edge.target_key)
            .first()
        )
        if loc_node:
            lp = loc_node.properties or {}
            lines.append(f"### Địa điểm: {loc_node.label}")
            if lp.get("description"):
                lines.append(lp["description"])
            lines.append("")

    return "\n".join(lines).strip()


def format_source_spirit_for_chapter(session, story, chapter_number: int) -> str:
    """Return source spirit block for chapter_writer (REWRITE only).

    Combines:
    - story.source_spirit: global tone + representative excerpts from the whole source
    - Per-chapter spirit + excerpts from the source EVENT node's properties

    Returns empty string for IDEA/PREMISE or if no spirit data is available.
    """
    if story.input_type != "REWRITE":
        return ""

    lines: list[str] = ["## Tinh thần truyện gốc (dùng làm chuẩn về tone, nhịp điệu, cảm xúc)"]

    if story.source_spirit:
        lines.append("\n### Tổng quan tone & phong cách (kèm trích đoạn mẫu)")
        lines.append(story.source_spirit)

    # Per-chapter spirit from source EVENT node
    event_key = f"E{chapter_number:03d}"
    event_node = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source", node_key=event_key)
        .first()
    )
    if event_node:
        p = event_node.properties or {}
        chapter_spirit = p.get("chapter_spirit", "")
        chapter_excerpts = p.get("chapter_excerpts", [])
        if chapter_spirit or chapter_excerpts:
            lines.append(f"\n### Tinh thần chương {chapter_number} (từ bản gốc — phải tái tạo cảm xúc này, không sao chép nội dung)")
            if chapter_spirit:
                lines.append(chapter_spirit)
            for excerpt in chapter_excerpts:
                if excerpt and excerpt.strip():
                    lines.append(f"\n> {excerpt.strip()}")

    if len(lines) == 1:
        return ""  # only header, no actual data
    return "\n".join(lines)


def last_n_chapters_text(session: Session, story_id: int, up_to_chapter: int, n: int = 5) -> str:
    rows = (
        session.query(Chapter)
        .filter(
            Chapter.story_id == story_id,
            Chapter.status == "done",
            Chapter.number > up_to_chapter - n,
            Chapter.number <= up_to_chapter,
        )
        .order_by(Chapter.number)
        .all()
    )
    return "\n\n---\n\n".join(f"# Chương {row.number}: {row.title}\n\n{row.content}" for row in rows)
