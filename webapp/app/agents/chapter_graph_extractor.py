"""Per-source-chapter graph extraction for REWRITE mode.

Called by the orchestrator once per source chapter, in order. Each call:
  1. Reads the next unprocessed source chapter (detected by EVENT node count in DB).
  2. Formats the existing entity list (CHARACTER / LOCATION / FACTION / etc.) from DB.
  3. Asks the model to extract one EVENT node + edges for that chapter.
  4. Inserts results into StoryGraphNode / StoryGraphEdge tables.

Completeness guarantee: the orchestrator keeps dispatching this step until
  extracted EVENT count == story.source_chapter_count.
Each call is O(1 chapter) so it never hits token limits regardless of novel length.
"""

from sqlalchemy.orm import Session

from .. import length_calc
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Story, StoryGraphEdge, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterGraphOutput


def _format_entity_list(session: Session, story_id: int, up_to_chapter: int | None = None) -> str:
    """Compact text of non-event nodes so the model can reference them by ID.

    up_to_chapter: if set, only include nodes introduced at or before that chapter.
    Used during re-extraction to avoid leaking future-chapter entities.
    """
    q = session.query(StoryGraphNode).filter(
        StoryGraphNode.story_id == story_id,
        StoryGraphNode.graph_type == "source",
        StoryGraphNode.node_type != "event",
    )
    if up_to_chapter is not None:
        q = q.filter(StoryGraphNode.chapter_introduced <= up_to_chapter)
    nodes = q.order_by(StoryGraphNode.node_type, StoryGraphNode.node_key).all()
    if not nodes:
        return "(chưa có entity nào)"
    lines = []
    current_type = None
    for n in nodes:
        if n.node_type != current_type:
            current_type = n.node_type
            lines.append(f"\n[{current_type.upper()}]")
        p = n.properties or {}
        detail = ""
        if n.node_type == "character":
            detail = f"role={p.get('role','')} wants={p.get('wants','')} arc={p.get('arc_stage','')}"
        elif n.node_type == "location":
            detail = p.get("description", "")[:80]
        elif n.node_type == "faction":
            detail = p.get("goal", "")[:80]
        lines.append(f"  {n.node_key}  {n.label}: {detail}")
    return "\n".join(lines)


def _get_prev_event_key(session: Session, story_id: int, chapter_number: int) -> str | None:
    prev = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source", node_type="event", chapter_introduced=chapter_number - 1)
        .first()
    )
    return prev.node_key if prev else None


def _format_active_relations(session: Session, story_id: int) -> str:
    """Current active RELATION edges (chapter_to is NULL) and latest arc_stage per character.

    Passed to the model so it can determine whether a relationship has changed
    (and therefore needs a new RELATION edge) vs is unchanged (skip). Without
    this the model at chapter 30 has no way to know that C001-C002 moved from
    friendship to rivalry at chapter 15 — it would either duplicate the edge or
    create the wrong one.
    """
    active = (
        session.query(StoryGraphEdge)
        .filter(
            StoryGraphEdge.story_id == story_id,
            StoryGraphEdge.graph_type == "source",
            StoryGraphEdge.edge_type == "RELATION",
            StoryGraphEdge.chapter_to.is_(None),
        )
        .order_by(StoryGraphEdge.source_key, StoryGraphEdge.target_key)
        .all()
    )
    if not active:
        return "(chưa có quan hệ nào)"
    lines = []
    for e in active:
        rel = (e.properties or {}).get("rel_type", e.edge_type)
        strength = (e.properties or {}).get("strength", "")
        s = f"  {e.source_key}↔{e.target_key}: {rel}"
        if strength != "":
            s += f" (strength={strength})"
        if e.chapter_from:
            s += f" [từ Ch.{e.chapter_from}]"
        if e.label:
            s += f' "{e.label}"'
        lines.append(s)
    return "\n".join(lines)


def _sync_arc_change(session: Session, story_id: int, edge_source_key: str, props: dict) -> None:
    """After inserting an ARC_CHANGE edge, write new_val back into node.properties.

    This keeps entity_list arc_stage always up-to-date without re-querying
    all ARC_CHANGE edges on every chapter.
    """
    new_val = props.get("new_val")
    field = props.get("field", "arc_stage")
    if not new_val:
        return
    node = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="source", node_key=edge_source_key)
        .first()
    )
    if node:
        updated = dict(node.properties or {})
        updated[field] = new_val
        node.properties = updated


def _extract_chapter(session: Session, story: Story, chapter_number: int, chapter_text: str) -> None:
    """Core extraction logic for one source chapter. Assumes the EVENT node for
    chapter_number does NOT exist yet — call _delete_chapter_data() first when
    re-extracting."""
    entity_list = _format_entity_list(session, story.id, up_to_chapter=chapter_number - 1)
    active_relations = _format_active_relations(session, story.id)
    prev_event_key = _get_prev_event_key(session, story.id, chapter_number)

    system = load_prompt("chapter_graph_extractor")
    user_content = (
        f"story_language: {story.language}\n"
        f"chapter_number: {chapter_number}\n"
        f"total_source_chapters: {story.source_chapter_count}\n"
        f"new_character_name_context: các tên nhân vật ĐÃ được tái tạo trong entity list bên dưới — "
        f"dùng đúng tên đó, KHÔNG dùng tên gốc.\n"
        + (f"previous_event_key: {prev_event_key}\n" if prev_event_key else "previous_event_key: null (đây là chương đầu tiên)\n")
        + f"\n## ENTITY LIST (dùng đúng các ID này trong edges)\n{entity_list}\n"
        f"\n## QUAN HỆ ĐANG HOẠT ĐỘNG (trước chương {chapter_number})\n"
        f"Chỉ tạo RELATION edge mới khi quan hệ BẮT ĐẦU hoặc THAY ĐỔI trong chương này.\n"
        f"Nếu quan hệ không thay đổi so với danh sách dưới, KHÔNG tạo RELATION edge.\n"
        f"{active_relations}\n"
        f"\n## NỘI DUNG CHƯƠNG GỐC SỐ {chapter_number}\n---\n{chapter_text}\n---\n"
    )

    output: ChapterGraphOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_graph_extractor"],
        schema=ChapterGraphOutput,
        max_tokens=16384,
        thinking=False,
    )

    session.add(StoryGraphNode(
        story_id=story.id,
        graph_type="source",
        node_key=output.event.id,
        node_type="event",
        label=output.event.label,
        properties=output.event.properties or {},
        chapter_introduced=chapter_number,
    ))

    for node in output.new_nodes:
        existing = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story.id, graph_type="source", node_key=node.id)
            .first()
        )
        if not existing:
            session.add(StoryGraphNode(
                story_id=story.id,
                graph_type="source",
                node_key=node.id,
                node_type=node.node_type,
                label=node.label,
                properties=node.properties or {},
                chapter_introduced=chapter_number,
            ))
    session.flush()

    # Auto-create placeholder nodes for any node_key referenced in edges but
    # not yet defined. The LLM occasionally omits nodes from new_nodes despite
    # prompt instructions — this prevents verifier false-positives while
    # preserving the relationship data.
    _NODE_TYPE_BY_PREFIX = {"C": "character", "L": "location", "F": "faction",
                            "T": "theme", "O": "object", "E": "event"}
    referenced_keys = {e.source_id for e in output.edges} | {e.target_id for e in output.edges}
    for key in referenced_keys:
        if not key:
            continue
        existing = (
            session.query(StoryGraphNode)
            .filter_by(story_id=story.id, graph_type="source", node_key=key)
            .first()
        )
        if not existing:
            prefix = key[0].upper() if key else ""
            node_type = _NODE_TYPE_BY_PREFIX.get(prefix, "character")
            session.add(StoryGraphNode(
                story_id=story.id,
                graph_type="source",
                node_key=key,
                node_type=node_type,
                label=key,  # placeholder label = key itself
                properties={},
                chapter_introduced=chapter_number,
            ))
    session.flush()

    for edge in output.edges:
        # For RELATION edges: if same pair + same rel_type is already active (chapter_to=null),
        # just update chapter_from to the current chapter instead of inserting a duplicate.
        if edge.edge_type == "RELATION" and edge.chapter_to is None:
            rel_type = (edge.properties or {}).get("rel_type", "")
            active_same_pair = (
                session.query(StoryGraphEdge)
                .filter(
                    StoryGraphEdge.story_id == story.id,
                    StoryGraphEdge.graph_type == "source",
                    StoryGraphEdge.edge_type == "RELATION",
                    StoryGraphEdge.source_key == edge.source_id,
                    StoryGraphEdge.target_key == edge.target_id,
                    StoryGraphEdge.chapter_to.is_(None),
                )
                .all()
            )
            matched = next(
                (e for e in active_same_pair if (e.properties or {}).get("rel_type", "") == rel_type),
                None,
            )
            if matched:
                matched.chapter_from = edge.chapter_from
                matched.properties = edge.properties or {}
                continue

        session.add(StoryGraphEdge(
            story_id=story.id,
            graph_type="source",
            source_key=edge.source_id,
            target_key=edge.target_id,
            edge_type=edge.edge_type,
            label=edge.label,
            chapter_from=edge.chapter_from,
            chapter_to=edge.chapter_to,
            trigger_event_key=edge.trigger_event_id,
            condition=edge.condition,
            properties=edge.properties or {},
        ))
        if edge.edge_type == "ARC_CHANGE":
            _sync_arc_change(session, story.id, edge.source_id, edge.properties or {})


def _delete_chapter_data(session: Session, story_id: int, chapter_number: int) -> None:
    """Remove the EVENT node and all edges introduced at chapter_number.

    Also reverts any ARC_CHANGE effects (arc_stage synced into node.properties)
    so the entity list reflects the state at chapter_number - 1 before re-extraction.
    """
    # Revert ARC_CHANGE effects before deleting the edges that carried them.
    arc_edges = (
        session.query(StoryGraphEdge)
        .filter_by(story_id=story_id, graph_type="source", edge_type="ARC_CHANGE")
        .filter(StoryGraphEdge.chapter_from == chapter_number)
        .all()
    )
    for edge in arc_edges:
        props = edge.properties or {}
        old_val = props.get("old_val")
        field = props.get("field", "arc_stage")
        if old_val:
            node = (
                session.query(StoryGraphNode)
                .filter_by(story_id=story_id, graph_type="source", node_key=edge.source_key)
                .first()
            )
            if node:
                updated = dict(node.properties or {})
                updated[field] = old_val
                node.properties = updated

    # Delete all edges introduced at this chapter.
    session.query(StoryGraphEdge).filter(
        StoryGraphEdge.story_id == story_id,
        StoryGraphEdge.graph_type == "source",
        StoryGraphEdge.chapter_from == chapter_number,
    ).delete(synchronize_session=False)

    # Delete ALL nodes introduced at this chapter (event + character/location/etc.).
    # Without this, ghost nodes from a previous failed extraction persist in the DB
    # and cause "referenced but not defined" false-positives on the next attempt.
    session.query(StoryGraphNode).filter(
        StoryGraphNode.story_id == story_id,
        StoryGraphNode.graph_type == "source",
        StoryGraphNode.chapter_introduced == chapter_number,
    ).delete(synchronize_session=False)

    session.flush()


def run(session: Session, story: Story) -> int:
    """Extract graph data for the next unprocessed source chapter.
    Returns the chapter number just processed.
    """
    source_chapters = length_calc.split_source_chapters(story.source_content)
    extracted_count = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source", node_type="event")
        .count()
    )
    chapter_number = extracted_count + 1
    _extract_chapter(session, story, chapter_number, source_chapters[extracted_count])
    return chapter_number


def run_for_chapter(session: Session, story: Story, chapter_number: int) -> int:
    """Re-extract a specific source chapter after wiping its existing data.

    Safe to call only if chapters AFTER chapter_number are NOT yet extracted —
    i.e., entity state from chapter_number+1 onward has not been applied yet.
    source_graph_verifier calls this chapter-by-chapter in order, so this
    invariant always holds.
    """
    source_chapters = length_calc.split_source_chapters(story.source_content)
    _delete_chapter_data(session, story.id, chapter_number)
    _extract_chapter(session, story, chapter_number, source_chapters[chapter_number - 1])
    return chapter_number
