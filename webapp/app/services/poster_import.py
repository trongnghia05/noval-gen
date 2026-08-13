"""Poster art for novels that already exist — a separate feature from generation.

`input/novels/<genre>/<id>.json` + `<id>.bible.json` already carry almost everything
the poster pipeline needs: title, synopsis, genre, tags, setting, world rules, and a
cast with names, roles and physical descriptions. Two things are missing — the era,
and which way the power runs between the leads — so one small LLM call over the 5KB
bible supplies those rather than re-reading 300KB of prose.

Everything then goes into the DB rows `image_generator` already reads, and
`image_generator.generate()` is called **unmodified**. Nothing in this file changes
how generated stories work; it only feeds the existing machine from a second source.

Output follows the layout the novels already use for their covers:

    <out_root>/<genre>/<id>.webp
    <out_root>/<genre>/<id>-thumb1.webp
    <out_root>/<genre>/<id>-thumb2.webp
"""

import json
import logging
import shutil
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import image_generator
from ..core.config import AGENT_MODELS, PROVIDER
from ..db.models import Character, Story, StoryGraphEdge, StoryGraphNode
from ..llm.structured import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import NovelMetadataOut

logger = logging.getLogger(__name__)


# ── what the small LLM call returns ────────────────────────────────────────────

class PosterDynamic(BaseModel):
    source: str      # the one who acts, pursues, controls
    target: str      # the one acted upon
    dynamic: str     # what flows from source to target, and how target responds


class PosterCastGender(BaseModel):
    name: str
    gender: str      # male | female | nonbinary


class PosterFacts(BaseModel):
    era: str
    dynamics: list[PosterDynamic] = []
    genders: list[PosterCastGender] = []


# ── reading the novel files ────────────────────────────────────────────────────

def read_novel(novel_json: Path) -> tuple[dict, dict]:
    """Return (novel metadata, story bible) for one novel id."""
    data = json.loads(novel_json.read_text(encoding="utf-8"))
    bible_path = novel_json.with_suffix("").with_suffix(".bible.json")
    if not bible_path.exists():
        bible_path = novel_json.parent / f"{novel_json.stem}.bible.json"
    bible = json.loads(bible_path.read_text(encoding="utf-8")) if bible_path.exists() else {}
    return data.get("novel", {}), bible


def poster_facts(bible: dict) -> PosterFacts:
    """The era and the directional power dynamics — the only two things the bible
    does not already state. Reads the bible only, never the chapter prose."""
    cast = bible.get("characters", []) or []
    payload = {
        "premise": bible.get("premise", {}),
        "world": bible.get("world", {}),
        "characters": [
            {k: c.get(k) for k in ("name", "role", "appearance", "voice", "canonFacts")}
            for c in cast
        ],
    }
    return generate_structured(
        PROVIDER, system=load_prompt("poster_facts"),
        user_content=json.dumps(payload, ensure_ascii=False, indent=2),
        model=AGENT_MODELS.get("story_analyzer"),
        schema=PosterFacts, max_tokens=4096, thinking=False,
    )


# ── shaping it into the rows image_generator reads ─────────────────────────────

# image_generator ranks cast by these exact strings, so the bible's freer wording
# ("love interest", "ally / hidden identity") has to be mapped onto them.
_ROLE_MAP = {
    "protagonist": "protagonist", "hero": "protagonist", "heroine": "protagonist",
    "love interest": "love_interest", "love_interest": "love_interest",
    "antagonist": "antagonist", "villain": "antagonist", "rival": "antagonist",
    "ally": "supporting", "mentor": "supporting", "friend": "supporting",
    "supporting": "supporting",
}
_ROLE_TIER = {"protagonist": "core", "love_interest": "core",
              "antagonist": "important", "supporting": "secondary"}


def _map_role(raw: str) -> str:
    """Free-text role from an imported novel → one of this system's role values.

    A lookup, not a normalisation: the source writes whatever it likes ("ally /
    hidden identity"), and _ROLE_MAP decides which of our fixed roles that is.
    """
    r = (raw or "").strip().lower()
    for key, value in _ROLE_MAP.items():          # substring: "ally / hidden identity"
        if key in r:
            return value
    return "minor"


def _story_bible(novel: dict, bible: dict, era: str) -> str:
    """`_world_block` reads the era off an `ERA:` first line and takes everything
    before the first `## ` heading as the premise, so this is written to that shape."""
    p = bible.get("premise", {}) or {}
    parts = [f"ERA: {era}", "", novel.get("synopsis", "") or p.get("logline", "")]
    for label, key in (("Tone", "tone"), ("Themes", "themeLine"),
                       ("Tropes", "tropeStack"), ("Heat level", "spiceLevel")):
        if p.get(key):
            parts.append(f"{label}: {p[key]}")
    return "\n".join(parts)


def _world_bible(bible: dict) -> str:
    p, w = bible.get("premise", {}) or {}, bible.get("world", {}) or {}
    out = []
    if p.get("setting"):
        out.append(f"## Setting\n{p['setting']}")
    if w.get("rules"):
        out.append("## How this world works\n" + "\n".join(f"- {r}" for r in w["rules"]))
    if w.get("glossary"):
        out.append("## Things in it\n" + "\n".join(
            f"- {k}: {v}" for k, v in w["glossary"].items()))
    return "\n\n".join(out)


def import_novel(session: Session, novel_json: Path, out_root: Path,
                 only: str | None = None, notes: str = "",
                 seed: int | None = None) -> list[Path]:
    """Import one novel and generate its poster art. Returns the files written.

    `notes` and `seed` pass straight through to the image generator — free-text
    steering for this run, and a fixed art-direction draw for repeatability.
    """
    novel, bible = read_novel(novel_json)
    novel_id = novel_json.stem
    genre_dir = novel_json.parent.name
    facts = poster_facts(bible)
    genders = {g.name: g.gender for g in facts.genders}

    story = session.query(Story).filter_by(slug=novel_id).one_or_none()
    if story is None:
        story = Story(slug=novel_id)
        session.add(story)
    story.title = novel.get("title") or novel_id
    story.language = novel.get("language") or "en"
    story.input_type = "IMPORT"
    story.genre = novel.get("genre") or genre_dir
    story.total_chapters = novel.get("chapterCount") or 1
    story.target_words = novel.get("wordCount") or 0
    story.words_per_chapter = (story.target_words // story.total_chapters) or 1
    story.phase = "COMPLETE"          # keeps it out of the resume scan
    story.story_bible = _story_bible(novel, bible, facts.era)
    story.world_bible = _world_bible(bible)
    session.flush()

    # Wipe anything a previous import of this novel left, so re-importing is clean.
    for model in (Character, StoryGraphNode, StoryGraphEdge):
        session.query(model).filter_by(story_id=story.id).delete()

    keys: dict[str, str] = {}
    for i, c in enumerate(bible.get("characters", []) or [], start=1):
        name, role = c.get("name"), _map_role(c.get("role"))
        if not name:
            continue
        key = f"C{i:03d}"
        keys[name] = key
        gender = genders.get(name, "")
        session.add(Character(
            story_id=story.id, name=name, tier=_ROLE_TIER.get(role, "minor"),
            profile_md=(f"**Role**: {role} **Gender**: {gender} "
                        f"**Appearance**: {c.get('appearance', '')}"),
        ))
        session.add(StoryGraphNode(
            story_id=story.id, graph_type="new", node_key=key, node_type="character",
            label=name, properties={"role": role},
        ))

    for d in facts.dynamics:
        a, b = keys.get(d.source), keys.get(d.target)
        if not a or not b or a == b:
            continue          # a dynamic naming someone not in the cast is unusable
        session.add(StoryGraphEdge(
            story_id=story.id, graph_type="new", source_key=a, target_key=b,
            edge_type="RELATION", label=d.dynamic, condition=d.dynamic,
            properties={"rel_type": "power dynamic"},
        ))
    session.commit()

    meta = NovelMetadataOut(
        author=novel.get("author") or "Unknown",
        tags=list(novel.get("tags") or []) or [story.genre],
        logline=novel.get("synopsis") or "",
        summary=novel.get("synopsis") or "",
    )

    dest = out_root / genre_dir
    dest.mkdir(parents=True, exist_ok=True)
    staging = dest / f".{novel_id}.tmp"
    staging.mkdir(exist_ok=True)
    try:
        with _import_prompts():
            written = image_generator.generate(session, story, staging, meta=meta,
                                               only=only, notes=notes, seed=seed)
        results = []
        for name in written:
            stem = Path(name).stem
            suffix = "" if stem == "cover" else f"-thumb{stem[-1]}"
            target = dest / f"{novel_id}{suffix}{Path(name).suffix}"
            shutil.move(str(staging / name), str(target))
            results.append(target)
        return results
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ── the import flow's own poster prompts, without touching the shared ones ─────

# The generation-side prompts assume a present-day American romance: thumb2 is
# specified as "the hottest image of the three", the wardrobe menu runs from black-tie
# to red-carpet, and the female lead is deliberately cast at 18-20. Applied to this
# catalogue — apocalypse survival, cultivation, litRPG, epic fantasy — that produced a
# protagonist and his estranged SISTER embracing in evening wear in a red velvet
# lounge, for a book set in a frozen mall fortress.
#
# These two files are that pair rewritten for books that already exist: the story's
# own world sets the wardrobe and the location, ages come from the cast list, and
# thumb2 stages whatever the relationship actually is. Swapping them in also drops the
# market contract, since neither is registered as market-aware.
#
# The cost of not editing the shared files: this is a FORK. A lesson learned on one
# side has to be copied to the other by hand.
_PROMPT_SWAP = {
    "image_prompt": "poster_image_prompt",
    "image_prompt_verifier": "poster_image_verifier",
}


class _import_prompts:
    """Point image_generator's prompt loading at the import-flow variants."""

    def __enter__(self):
        self._saved = image_generator.load_prompt
        image_generator.load_prompt = (
            lambda name: load_prompt(_PROMPT_SWAP.get(name, name))
        )

    def __exit__(self, *exc):
        image_generator.load_prompt = self._saved
        return False
