"""Helpers that turn DB rows into the compact text blocks agents read —
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
