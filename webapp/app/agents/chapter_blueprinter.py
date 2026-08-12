import json

from sqlalchemy.orm import Session

from .. import context_builder, csv_graph
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story, StoryGraphNode
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterBlueprintOutput


def _act_position(chapter_number: int, total_chapters: int) -> str:
    ratio = chapter_number / total_chapters
    if ratio <= 0.25:
        return "Act 1"
    if ratio <= 0.50:
        return "Act 2a"
    if ratio <= 0.75:
        return "Act 2b"
    return "Act 3"


def _recent_beats(session: Session, story_id: int, before_chapter: int, n: int = 3) -> str:
    """beat_type + state_delta of the last n blueprinted chapters — the anti-repeat
    window the blueprinter must not echo (kept to a short recent window for now)."""
    rows = (
        session.query(Chapter)
        .filter(Chapter.story_id == story_id, Chapter.number < before_chapter,
                Chapter.blueprint.isnot(None))
        .order_by(Chapter.number.desc())
        .limit(n)
        .all()
    )
    lines = []
    for c in reversed(rows):
        try:
            bp = json.loads(c.blueprint)
        except Exception:
            continue
        lines.append(
            f"- Ch{c.number}: beat_type={bp.get('beat_type', '?')} | "
            f"state_delta={bp.get('state_delta', '?')}"
        )
    return "\n".join(lines) or "(no previous chapters)"


MOTIF_CAP = 3  # a motif tag may recur at most this many times before it must be dropped/escalated


def _canonical_motif_tag(tag: str) -> str:
    """Deterministic backstop canonicalization: lowercase, collapse whitespace and
    separators. Catches case/spacing variants of the same tag; semantic near-dupes
    (different words, same meaning) are handled by the LLM's match-or-reuse rule."""
    return " ".join(tag.lower().replace("-", " ").replace("_", " ").split())


def _motif_ledger(session: Session, story_id: int, before_chapter: int) -> tuple[str, dict]:
    """Cumulative motif tally across ALL prior chapters' blueprints (single source of
    truth = Chapter.blueprint JSON; no separate store). Returns a human-readable block
    for the prompt plus a {normalized_tag: (display_tag, count, [chapters])} map used to
    merge exact/case-variant tags deterministically after generation."""
    rows = (
        session.query(Chapter)
        .filter(Chapter.story_id == story_id, Chapter.number < before_chapter,
                Chapter.blueprint.isnot(None))
        .order_by(Chapter.number.asc())
        .all()
    )
    tally: dict[str, list] = {}  # norm -> [display, count, [chapters]]
    for c in rows:
        try:
            used = json.loads(c.blueprint).get("motifs_used", []) or []
        except Exception:
            continue
        for tag in used:
            if not isinstance(tag, str) or not tag.strip():
                continue
            key = _canonical_motif_tag(tag)
            if key in tally:
                tally[key][1] += 1
                tally[key][2].append(c.number)
            else:
                tally[key] = [tag.strip(), 1, [c.number]]
    if not tally:
        return "(no motifs yet — this is the first chapter)", tally
    lines = []
    for _, (disp, cnt, chaps) in sorted(tally.items(), key=lambda kv: -kv[1][1]):
        flag = ("  ← AT ITS CEILING: no flat repeat — DROP it or ESCALATE into a "
                "different kind of expression") if cnt >= MOTIF_CAP else ""
        lines.append(f"  - {disp} ({cnt}×: ch{','.join(map(str, chaps))}){flag}")
    return "\n".join(lines), tally


def _new_label_for_key(session: Session, story_id: int, key: str) -> str:
    n = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story_id, graph_type="new", node_key=key)
        .first()
    )
    return n.label if n else ""


def _resolve_pov_from_source(session: Session, story: Story, chapter_number: int) -> tuple[str, list[str]]:
    """Faithful per-chapter POV for a REWRITE.

    chapter_graph_extractor records, on each source EVENT E{N}: properties['pov'] =
    the primary POV character's node_key, and properties['pov_others'] = keys of any
    ADDITIONAL POV holders when the source chapter switches POV mid-chapter. The new
    graph preserves node_keys (labels reskinned), so the same keys on graph_type='new'
    give the reskinned names. Returns (primary_label, all_labels): all_labels has >1
    entry only for a genuine multi-POV chapter. ('', []) when no POV was captured.
    """
    if story.input_type != "REWRITE":
        return "", []
    ev = (
        session.query(StoryGraphNode)
        .filter_by(story_id=story.id, graph_type="source", node_key=f"E{chapter_number:03d}")
        .first()
    )
    props = (ev.properties or {}) if ev else {}
    primary_key = (props.get("pov") or "").strip()
    if not primary_key:
        return "", []
    keys = [primary_key] + [k for k in (props.get("pov_others") or []) if k and k != primary_key]
    labels, seen = [], set()
    for k in keys:
        lbl = _new_label_for_key(session, story.id, k)
        if lbl and lbl not in seen:
            seen.add(lbl)
            labels.append(lbl)
    if not labels:
        return "", []
    return labels[0], labels


def run(session: Session, story: Story, chapter: Chapter) -> None:
    system = load_prompt("chapter_blueprinter")
    act = _act_position(chapter.number, story.total_chapters)

    graph_section = ""
    if csv_graph.graph_exists(story.id):
        graph_section = f"""
## character-graph (current states)
{csv_graph.format_characters(story.id)}

## relationships
{csv_graph.format_relationships(story.id)}

## open plot threads
{csv_graph.format_open_threads(story.id)}

## recent timeline
{csv_graph.format_recent_timeline(story.id)}
"""

    spirit_section = ""
    if story.input_type == "REWRITE" and story.source_spirit:
        spirit_section = (
            "\n## Source spirit — POV & tone (use this to assign pov_character)\n"
            + story.source_spirit
            + "\n"
        )

    db_graph_section = ""
    if story.new_graph_built:
        db_graph_section = (
            "\n## chapter graph constraints"
            " (PARTICIPATES = who must appear, LOCATED_AT = where, ARC_CHANGE = arc shifts to trigger)\n"
            + context_builder.format_chapter_subgraph(
                session, story.id, chapter.number, graph_type="new", max_depth=1
            )
            + "\n"
        )

    ledger_block, ledger_map = _motif_ledger(session, story.id, chapter.number)

    user_content = f"""chapter_number: {chapter.number}
total_chapters: {story.total_chapters}
act_position: {act}
language: {story.language}
{spirit_section}{graph_section}{db_graph_section}
## recent chapters' beat_type + state_delta (DO NOT repeat these — advance beyond them)
{_recent_beats(session, story.id, chapter.number)}

## motif ledger — tags used so far (REUSE them, don't spawn variants)
For each repeatable beat/motif in this chapter: if it means the same as a tag below, copy that tag string EXACTLY into `motifs_used` (so the counts aggregate); coin a NEW tag only for a genuinely new motif. A tag at its ceiling ({MOTIF_CAP}×) must NOT be repeated flat — drop it, or escalate it into a different kind of expression.
{ledger_block}

## chapter-summaries (story so far)
{context_builder.format_chapter_summaries(session, story.id)}

## continuity-log (open issues to address or avoid)
{context_builder.format_continuity_log(session, story.id)}

## plot-outline (what this chapter should cover)
{story.plot_outline}
"""

    blueprint = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS.get("chapter_blueprinter", AGENT_MODELS["chapter_writer"]),
        schema=ChapterBlueprintOutput,
        # 4096 truncated the JSON mid-string once Gemini's thinking tokens ate
        # into the budget (unterminated-string parse failures). The blueprint
        # itself is small; the headroom is for the reasoning pass. See the
        # "reasoning token budget starvation" gotcha in webapp/CLAUDE.md.
        max_tokens=16384,
        thinking=True,
    )

    # Faithful POV: override the LLM's pov_character guess with the source's actual
    # per-chapter POV holder, mapped to its reskinned name. Keeps the rewrite's POV
    # alternation identical to the source instead of an approximation.
    bp = blueprint.model_dump()
    primary_pov, all_pov = _resolve_pov_from_source(session, story, chapter.number)
    if primary_pov:
        bp["pov_character"] = primary_pov
        # Only carry the full list for a genuine multi-POV chapter; single-POV keeps
        # pov_characters empty so the strong single-POV writer/guard path is unchanged.
        bp["pov_characters"] = all_pov if len(all_pov) > 1 else []

    # Motif backstop: snap each returned tag to the existing display spelling when it
    # normalizes to a tag already in the ledger (catches case/space/hyphen variants
    # the LLM might reintroduce), and drop exact-dupes within this chapter. Keeps the
    # cumulative count honest without a separate store.
    canon = {norm: disp for norm, (disp, _c, _ch) in ledger_map.items()}
    cleaned, seen = [], set()
    for tag in (bp.get("motifs_used") or []):
        if not isinstance(tag, str) or not tag.strip():
            continue
        key = _canonical_motif_tag(tag)
        display = canon.get(key, tag.strip())
        if key not in seen:
            seen.add(key)
            cleaned.append(display)
    bp["motifs_used"] = cleaned

    chapter.blueprint = json.dumps(bp, ensure_ascii=False)
    chapter.status = "blueprinted"
