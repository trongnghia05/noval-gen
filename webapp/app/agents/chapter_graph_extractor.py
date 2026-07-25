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


def _format_entity_list(session: Session, story_id: int) -> str:
    """Compact text of all non-event nodes so the model can reference them by ID."""
    nodes = (
        session.query(StoryGraphNode)
        .filter(
            StoryGraphNode.story_id == story_id,
            StoryGraphNode.graph_type == "source",
            StoryGraphNode.node_type != "event",
        )
        .order_by(StoryGraphNode.node_type, StoryGraphNode.node_key)
        .all()
    )
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


def run(session: Session, story: Story) -> int:
    """Extract graph data for the next unprocessed source chapter.
    Returns the chapter number just processed.
    """
    source_chapters = length_calc.split_source_chapters(story.source_content)

    # Next unprocessed = number of source EVENT nodes already in DB (0-indexed offset).
    extracted_count = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source", node_type="event")
        .count()
    )
    chapter_number = extracted_count + 1   # 1-indexed
    chapter_text = source_chapters[extracted_count]

    entity_list = _format_entity_list(session, story.id)
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

    # One chapter = small bounded output: 1 event + ~10 edges = ~1500 tokens.
    output: ChapterGraphOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_graph_extractor"],
        schema=ChapterGraphOutput,
        max_tokens=4096,
        thinking=False,   # extraction task, no creative reasoning needed
    )

    # Insert EVENT node for this chapter.
    session.add(StoryGraphNode(
        story_id=story.id,
        graph_type="source",
        node_key=output.event.id,
        node_type="event",
        label=output.event.label,
        properties=output.event.properties or {},
        chapter_introduced=chapter_number,
    ))

    # Insert any newly discovered entity nodes.
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

    # Insert edges; sync denormalized arc_stage cache for ARC_CHANGE edges.
    for edge in output.edges:
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

    return chapter_number
