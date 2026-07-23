"""CSV-based knowledge graph — character states, relationships, plot threads.

Lives at /data/graphs/{story_id}/ inside the Docker volume. Each story gets
its own subdirectory created by init_graph() after character_developer runs.

Design rules:
- characters.csv / relationships.csv / plot_threads.csv: overwrite in-place
- relationship_history.csv / timeline.csv: append-only, never truncated
- character_voices.md: written once, never touched again
"""
import csv
import os
from pathlib import Path

GRAPH_BASE = Path(os.getenv("GRAPH_DIR", "/data/graphs"))

# ── Column schemas ─────────────────────────────────────────────────────────────

CHAR_FIELDS = [
    "id", "name", "aliases", "role", "arc_status",
    "location", "emotional_state", "goals", "secrets",
    "speech_pattern", "last_seen_chapter",
]
REL_FIELDS = [
    "char_a", "char_b", "type", "strength", "status",
    "last_event", "last_updated_chapter",
]
REL_HIST_FIELDS = [
    "char_a", "char_b", "chapter", "event", "old_strength", "new_strength",
]
THREAD_FIELDS = [
    "id", "title", "type", "status",
    "introduced_chapter", "resolved_chapter",
    "involved_chars", "hint", "resolution_note",
]
TIMELINE_FIELDS = [
    "chapter", "story_time", "location", "characters", "summary",
]


# ── Internal helpers ───────────────────────────────────────────────────────────

def _dir(story_id: int) -> Path:
    d = GRAPH_BASE / str(story_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(story_id: int, name: str) -> Path:
    return _dir(story_id) / name


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _append(path: Path, fields: list[str], row: dict) -> None:
    exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)


# ── Init (called once after character_developer) ───────────────────────────────

def init_graph(story_id: int, characters: list[dict], voices_md: str) -> None:
    """Seed all CSV files. characters is a list of dicts with CHAR_FIELDS keys."""
    d = _dir(story_id)
    _write(d / "characters.csv", CHAR_FIELDS, characters)
    _write(d / "relationships.csv", REL_FIELDS, [])
    _write(d / "relationship_history.csv", REL_HIST_FIELDS, [])
    _write(d / "plot_threads.csv", THREAD_FIELDS, [])
    _write(d / "timeline.csv", TIMELINE_FIELDS, [])
    (d / "character_voices.md").write_text(voices_md, encoding="utf-8")


def graph_exists(story_id: int) -> bool:
    return _path(story_id, "characters.csv").exists()


# ── Readers ────────────────────────────────────────────────────────────────────

def get_characters(story_id: int) -> list[dict]:
    return _read(_path(story_id, "characters.csv"))


def get_relationships(story_id: int) -> list[dict]:
    return _read(_path(story_id, "relationships.csv"))


def get_open_plot_threads(story_id: int) -> list[dict]:
    return [r for r in _read(_path(story_id, "plot_threads.csv")) if r.get("status") == "open"]


def get_character_voices(story_id: int) -> str:
    p = _path(story_id, "character_voices.md")
    return p.read_text(encoding="utf-8") if p.exists() else ""


def get_recent_timeline(story_id: int, last_n: int = 8) -> list[dict]:
    rows = _read(_path(story_id, "timeline.csv"))
    return rows[-last_n:]


# ── Writers ────────────────────────────────────────────────────────────────────

def update_character_fields(story_id: int, char_id: str, updates: dict, chapter: int) -> None:
    """updates: {field: value} for one character."""
    rows = get_characters(story_id)
    for r in rows:
        if r["id"] == char_id:
            r.update(updates)
            r["last_seen_chapter"] = str(chapter)
            break
    _write(_path(story_id, "characters.csv"), CHAR_FIELDS, rows)


def upsert_relationship(
    story_id: int, char_a: str, char_b: str,
    rel_type: str, strength: float, status: str, event: str, chapter: int,
) -> None:
    rows = get_relationships(story_id)
    matched = False
    for r in rows:
        same = (r["char_a"] == char_a and r["char_b"] == char_b) or \
               (r["char_a"] == char_b and r["char_b"] == char_a)
        if same:
            old = r["strength"]
            r["type"] = rel_type
            r["strength"] = str(strength)
            r["status"] = status
            r["last_event"] = event
            r["last_updated_chapter"] = str(chapter)
            matched = True
            _append(_path(story_id, "relationship_history.csv"), REL_HIST_FIELDS, {
                "char_a": char_a, "char_b": char_b, "chapter": chapter,
                "event": event, "old_strength": old, "new_strength": strength,
            })
            break
    if not matched:
        rows.append({
            "char_a": char_a, "char_b": char_b, "type": rel_type,
            "strength": str(strength), "status": status,
            "last_event": event, "last_updated_chapter": str(chapter),
        })
        _append(_path(story_id, "relationship_history.csv"), REL_HIST_FIELDS, {
            "char_a": char_a, "char_b": char_b, "chapter": chapter,
            "event": f"[new] {event}", "old_strength": 0, "new_strength": strength,
        })
    _write(_path(story_id, "relationships.csv"), REL_FIELDS, rows)


def upsert_plot_thread(story_id: int, thread: dict) -> None:
    """thread must have 'id' key. Inserts or updates by id."""
    rows = _read(_path(story_id, "plot_threads.csv"))
    matched = False
    for r in rows:
        if r["id"] == thread["id"]:
            r.update({k: v for k, v in thread.items() if v is not None})
            matched = True
            break
    if not matched:
        rows.append({f: thread.get(f, "") for f in THREAD_FIELDS})
    _write(_path(story_id, "plot_threads.csv"), THREAD_FIELDS, rows)


def append_timeline(
    story_id: int, chapter: int, story_time: str,
    location: str, characters: str, summary: str,
) -> None:
    _append(_path(story_id, "timeline.csv"), TIMELINE_FIELDS, {
        "chapter": chapter, "story_time": story_time,
        "location": location, "characters": characters, "summary": summary,
    })


# ── Context formatters (for prompts) ──────────────────────────────────────────

def format_characters(story_id: int) -> str:
    rows = get_characters(story_id)
    if not rows:
        return "(graph not initialized)"
    lines = []
    for r in rows:
        strength_bar = ""
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
