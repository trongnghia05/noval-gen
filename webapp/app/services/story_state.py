"""The live state a chapter is written against: where everyone is, how each pair
stands, which threads are open, what happened when.

Distinct from the two other stores it is easy to confuse it with:

- `story_graph_*` is the PLAN — which event belongs to which chapter, who takes
  part, what causes what. Built during planning and frozen after it is verified.
- `world_state` is a free-form fact sheet the summarizer also writes, read by the
  CHECKING agents (chapter_verifier, continuity_editor, smart_planner).

This module serves the WRITING agents (blueprinter, writer, reviser). It used to
be five CSV files under /data/graphs/<story_id>/, which put half the story's memory
outside the database — nothing was queryable, deleting a story meant remembering a
directory as well, and the two halves could drift with nothing to notice.

## The contract that must not move

Every `get_*` returns rows as dicts of STRINGS keyed by the old CSV column names,
and every `format_*` returns the exact text it used to. Those strings go straight
into prompts, so a changed space or a `None` where an empty string used to be is a
changed prompt and therefore a different novel. Ints are rendered back with
`_s()`, and rows come back in insertion order because that is the order a file
preserved.
"""

import logging

from sqlalchemy.orm import Session

from ..db.models import (
    CharacterState,
    PlotThread,
    Relationship,
    StateLog,
    Story,
    TimelineEvent,
)
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# Kept so callers and the migration script can still name the columns.
CHAR_FIELDS = [
    "id", "name", "gender", "aliases", "role", "arc_status",
    "location", "emotional_state", "goals", "secrets",
    "speech_pattern", "last_seen_chapter",
]
REL_FIELDS = [
    "char_a", "char_b", "type", "strength", "status",
    "last_event", "last_updated_chapter",
]
THREAD_FIELDS = [
    "id", "title", "type", "status",
    "introduced_chapter", "resolved_chapter",
    "involved_chars", "hint", "resolution_note",
]
TIMELINE_FIELDS = ["chapter", "story_time", "location", "characters", "summary"]


def _s(v) -> str:
    """A DB value as the CSV would have held it: never None, always a string."""
    return "" if v is None else str(v)


def _i(v) -> int | None:
    """A chapter number from whatever the caller passed. Anything unparseable
    becomes NULL rather than raising — a bad number must not stop a chapter."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# ── Row adapters ──────────────────────────────────────────────────────────────

def _char_row(r: CharacterState) -> dict:
    return {
        "id": _s(r.char_id), "name": _s(r.name), "gender": _s(r.gender),
        "aliases": _s(r.aliases), "role": _s(r.role), "arc_status": _s(r.arc_status),
        "location": _s(r.location), "emotional_state": _s(r.emotional_state),
        "goals": _s(r.goals), "secrets": _s(r.secrets),
        "speech_pattern": _s(r.speech_pattern),
        "last_seen_chapter": _s(r.last_seen_chapter),
    }


def _rel_row(r: Relationship) -> dict:
    return {
        "char_a": _s(r.char_a), "char_b": _s(r.char_b), "type": _s(r.type),
        "strength": _s(r.strength), "status": _s(r.status),
        "last_event": _s(r.last_event),
        "last_updated_chapter": _s(r.last_updated_chapter),
    }


def _thread_row(r: PlotThread) -> dict:
    return {
        "id": _s(r.thread_id), "title": _s(r.title), "type": _s(r.type),
        "status": _s(r.status),
        "introduced_chapter": _s(r.introduced_chapter),
        "resolved_chapter": _s(r.resolved_chapter),
        "involved_chars": _s(r.involved_chars), "hint": _s(r.hint),
        "resolution_note": _s(r.resolution_note),
    }


def _timeline_row(r: TimelineEvent) -> dict:
    return {
        "chapter": _s(r.chapter), "story_time": _s(r.story_time),
        "location": _s(r.location), "characters": _s(r.characters),
        "summary": _s(r.summary),
    }


# ── Seeding ───────────────────────────────────────────────────────────────────

def init_graph(
    story_id: int,
    characters: list[dict],
    voices_md: str,
    relationships: list[dict] | None = None,
) -> None:
    """Seed the state for a story. Replaces the cast and the opening relationships.

    Unlike the CSV version, this does NOT wipe plot threads and the timeline. That
    truncation was harmless only because both callers happen to run during planning;
    a re-seed after writing had started would have silently erased the live state.
    """
    with SessionLocal() as session:
        session.query(CharacterState).filter_by(story_id=story_id).delete()
        session.query(Relationship).filter_by(story_id=story_id).delete()
        session.flush()
        for c in characters:
            session.add(CharacterState(
                story_id=story_id, char_id=c.get("id"), name=c.get("name"),
                gender=c.get("gender"), aliases=c.get("aliases"), role=c.get("role"),
                arc_status=c.get("arc_status"), location=c.get("location"),
                emotional_state=c.get("emotional_state"), goals=c.get("goals"),
                secrets=c.get("secrets"), speech_pattern=c.get("speech_pattern"),
                last_seen_chapter=_i(c.get("last_seen_chapter")),
            ))
        for r in relationships or []:
            session.add(Relationship(
                story_id=story_id, char_a=r.get("char_a"), char_b=r.get("char_b"),
                type=r.get("type"), strength=_s(r.get("strength")),
                status=r.get("status"), last_event=r.get("last_event"),
                last_updated_chapter=_i(r.get("last_updated_chapter")),
            ))
        story = session.get(Story, story_id)
        if story:
            story.character_voices = voices_md
        session.commit()
    logger.info("[story %d] state seeded: %d characters, %d relationships",
                story_id, len(characters), len(relationships or []))


def graph_exists(story_id: int) -> bool:
    with SessionLocal() as session:
        return session.query(CharacterState).filter_by(story_id=story_id).first() is not None


# ── Readers ───────────────────────────────────────────────────────────────────

def get_characters(story_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = (session.query(CharacterState).filter_by(story_id=story_id)
                .order_by(CharacterState.id).all())
        return [_char_row(r) for r in rows]


def get_relationships(story_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = (session.query(Relationship).filter_by(story_id=story_id)
                .order_by(Relationship.id).all())
        return [_rel_row(r) for r in rows]


def get_open_plot_threads(story_id: int) -> list[dict]:
    with SessionLocal() as session:
        rows = (session.query(PlotThread).filter_by(story_id=story_id, status="open")
                .order_by(PlotThread.id).all())
        return [_thread_row(r) for r in rows]


def get_character_voices(story_id: int) -> str:
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        return (story.character_voices or "") if story else ""


def get_recent_timeline(story_id: int, last_n: int = 8) -> list[dict]:
    with SessionLocal() as session:
        rows = (session.query(TimelineEvent).filter_by(story_id=story_id)
                .order_by(TimelineEvent.id).all())
        return [_timeline_row(r) for r in rows[-last_n:]]


# ── Writers ───────────────────────────────────────────────────────────────────

def update_character_fields(story_id: int, char_id: str, updates: dict, chapter: int) -> None:
    """updates: {field: value} for one character."""
    with SessionLocal() as session:
        row = (session.query(CharacterState)
               .filter_by(story_id=story_id, char_id=char_id).first())
        if row is None:
            return                      # unknown id: the CSV version also did nothing
        for field, value in updates.items():
            if field in ("id", "last_seen_chapter") or not hasattr(row, field):
                continue
            setattr(row, field, value)
        row.last_seen_chapter = chapter
        session.commit()


def upsert_relationship(
    story_id: int, char_a: str, char_b: str,
    rel_type: str, strength: float, status: str, event: str, chapter: int,
) -> None:
    with SessionLocal() as session:
        rows = session.query(Relationship).filter_by(story_id=story_id).all()
        row = next(
            (r for r in rows
             if (r.char_a == char_a and r.char_b == char_b)
             or (r.char_a == char_b and r.char_b == char_a)),
            None,
        )
        old = _s(row.strength) if row else "0"
        if row is None:
            row = Relationship(story_id=story_id, char_a=char_a, char_b=char_b)
            session.add(row)
            event_note = f"[new] {event}"
        else:
            event_note = event
        row.type, row.strength, row.status = rel_type, _s(strength), status
        row.last_event, row.last_updated_chapter = event, chapter

        # Where relationship_history.csv used to go. That file was written every
        # chapter and read by nothing; state_log is the trail continuity_editor
        # actually consults, and it can carry the same fact.
        session.add(StateLog(
            story_id=story_id, chapter_number=chapter,
            entity=f"{char_a}↔{char_b}", field="relationship_strength",
            old_value=old, new_value=_s(strength), reason=event_note,
        ))
        session.commit()


def upsert_plot_thread(story_id: int, thread: dict) -> None:
    """thread must have 'id'. Inserts or updates by id; a None value leaves the
    stored one alone, matching how the CSV version merged."""
    with SessionLocal() as session:
        row = (session.query(PlotThread)
               .filter_by(story_id=story_id, thread_id=thread["id"]).first())
        if row is None:
            row = PlotThread(story_id=story_id, thread_id=thread["id"])
            session.add(row)
            for f in THREAD_FIELDS:
                if f == "id":
                    continue
                v = thread.get(f, "")
                setattr(row, f, _i(v) if f.endswith("_chapter") else v)
        else:
            for f, v in thread.items():
                if f == "id" or v is None:
                    continue
                setattr(row, f, _i(v) if f.endswith("_chapter") else v)
        session.commit()


def append_timeline(
    story_id: int, chapter: int, story_time: str,
    location: str, characters: str, summary: str,
) -> None:
    with SessionLocal() as session:
        session.add(TimelineEvent(
            story_id=story_id, chapter=chapter, story_time=story_time,
            location=location, characters=characters, summary=summary,
        ))
        session.commit()


# ── Context formatters (for prompts) ──────────────────────────────────────────
# Byte-for-byte what the CSV version produced. These strings are prompt text.

def format_characters(story_id: int) -> str:
    rows = get_characters(story_id)
    if not rows:
        return "(graph not initialized)"
    lines = []
    for r in rows:
        lines.append(
            f"[{r['id']}] **{r['name']}** — {r['role']} | arc: {r['arc_status']}\n"
            f"  location: {r['location']}\n"
            f"  mood: {r['emotional_state']}\n"
            f"  goals: {r['goals']}\n"
            f"  secrets: {r['secrets']}\n"
            f"  speech: {r['speech_pattern']}\n"
            f"  last seen: ch.{r['last_seen_chapter']}"
        )
    return "\n\n".join(lines)


def format_relationships(story_id: int) -> str:
    rows = get_relationships(story_id)
    if not rows:
        return "(no relationships yet)"
    lines = []
    for r in rows:
        try:
            s = float(r["strength"])
            bar = "█" * int(abs(s) * 5)
            sign = "+" if s >= 0 else "-"
        except (ValueError, TypeError):
            bar, sign = "?", "?"
        lines.append(
            f"{r['char_a']} ↔ {r['char_b']}  [{sign}{bar}] {r['strength']}  "
            f"type:{r['type']}  status:{r['status']}\n"
            f"  last: \"{r['last_event']}\" (ch.{r['last_updated_chapter']})"
        )
    return "\n".join(lines)


def format_open_threads(story_id: int) -> str:
    threads = get_open_plot_threads(story_id)
    if not threads:
        return "(no open plot threads)"
    lines = []
    for t in threads:
        line = (
            f"[{t['id']}] {t['title']}  type:{t['type']}  "
            f"opened:ch.{t['introduced_chapter']}  chars:{t['involved_chars']}"
        )
        if t.get("hint"):
            line += f"\n  hint: {t['hint']}"
        lines.append(line)
    return "\n".join(lines)


def format_recent_timeline(story_id: int) -> str:
    rows = get_recent_timeline(story_id)
    if not rows:
        return "(no timeline yet)"
    return "\n".join(
        f"ch.{r['chapter']} | {r['story_time']} | {r['location']} → {r['summary']}"
        for r in rows
    )
