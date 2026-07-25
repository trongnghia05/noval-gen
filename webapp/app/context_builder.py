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


def format_chapter_summaries(session: Session, story_id: int) -> str:
    rows = (
        session.query(ChapterSummary)
        .filter_by(story_id=story_id)
        .order_by(ChapterSummary.chapter_number)
        .all()
    )
    if not rows:
        return "(chưa có chương nào được viết)"
    return "\n\n".join(f"## Chương {row.chapter_number}\n{row.summary_text}" for row in rows)


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
