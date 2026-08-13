"""Copy the live story state out of /data/graphs/<story_id>/*.csv into the database.

Run once after 0008_story_state_tables. Idempotent: a story that already has rows
is skipped, so re-running after a partial pass costs nothing. The CSV files are
LEFT IN PLACE as the rollback, and as the thing the verification script compares
the new rendering against.

`relationship_history.csv` is deliberately NOT copied — see the migration's note.

    docker compose exec api python scripts/migrate_csv_graph_to_db.py [--dry-run] [id...]
"""

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.db.models import (           # noqa: E402
    CharacterState, PlotThread, Relationship, Story, TimelineEvent,
)
from app.db.session import SessionLocal      # noqa: E402
from app.services.story_state import _i, _s  # noqa: E402

GRAPH_BASE = Path(os.getenv("GRAPH_DIR", "/data/graphs"))


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ids = [int(a) for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    session = SessionLocal()

    q = session.query(Story).order_by(Story.id)
    if ids:
        q = q.filter(Story.id.in_(ids))

    moved = skipped = 0
    for story in q.all():
        d = GRAPH_BASE / str(story.id)
        if not d.is_dir():
            continue
        if session.query(CharacterState).filter_by(story_id=story.id).first():
            print(f"[{story.id}] {story.slug}: da co du lieu trong DB — bo qua")
            skipped += 1
            continue

        chars = _read(d / "characters.csv")
        rels = _read(d / "relationships.csv")
        threads = _read(d / "plot_threads.csv")
        timeline = _read(d / "timeline.csv")
        voices_p = d / "character_voices.md"
        voices = voices_p.read_text(encoding="utf-8") if voices_p.exists() else ""

        print(f"[{story.id}] {story.slug}: {len(chars)} nhan vat, {len(rels)} quan he, "
              f"{len(threads)} tuyen, {len(timeline)} timeline, voices {len(voices)} ky tu"
              + (" (dry-run)" if dry else ""))
        if dry:
            continue

        # Insert in file order — that order is what the formatters render, so it is
        # part of the contract, not an accident.
        for c in chars:
            session.add(CharacterState(
                story_id=story.id, char_id=c.get("id"), name=c.get("name"),
                gender=c.get("gender"), aliases=c.get("aliases"), role=c.get("role"),
                arc_status=c.get("arc_status"), location=c.get("location"),
                emotional_state=c.get("emotional_state"), goals=c.get("goals"),
                secrets=c.get("secrets"), speech_pattern=c.get("speech_pattern"),
                last_seen_chapter=_i(c.get("last_seen_chapter")),
            ))
        for r in rels:
            session.add(Relationship(
                story_id=story.id, char_a=r.get("char_a"), char_b=r.get("char_b"),
                type=r.get("type"), strength=_s(r.get("strength")),
                status=r.get("status"), last_event=r.get("last_event"),
                last_updated_chapter=_i(r.get("last_updated_chapter")),
            ))
        for t in threads:
            session.add(PlotThread(
                story_id=story.id, thread_id=t.get("id"), title=t.get("title"),
                type=t.get("type"), status=t.get("status"),
                introduced_chapter=_i(t.get("introduced_chapter")),
                resolved_chapter=_i(t.get("resolved_chapter")),
                involved_chars=t.get("involved_chars"), hint=t.get("hint"),
                resolution_note=t.get("resolution_note"),
            ))
        for e in timeline:
            session.add(TimelineEvent(
                story_id=story.id, chapter=_i(e.get("chapter")),
                story_time=e.get("story_time"), location=e.get("location"),
                characters=e.get("characters"), summary=e.get("summary"),
            ))
        story.character_voices = voices
        session.commit()
        moved += 1

    print(f"\nxong: {moved} truyen chuyen, {skipped} bo qua")


main()
