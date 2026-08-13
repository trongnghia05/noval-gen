"""The story record itself: create, list, read, delete — and its title."""

import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core import storage
from ...services import graph, length_calc, story_state
from ...core.config import AGENT_MODELS, OUTPUT_BASE, PROVIDER
from ...db.models import (
    Chapter,
    ChapterSummary,
    ChapterTrace,
    ChapterVerifyLog,
    Character,
    CharacterState,
    ContinuityLog,
    Foreshadowing,
    PlanningVerifyLog,
    PlotThread,
    Relationship,
    SmartPlannerState,
    StateLog,
    Story,
    StoryGraphEdge,
    StoryGraphNode,
    StoryImage,
    TimelineEvent,
    WorldState,
)
from ...db.session import SessionLocal
from ...core.slug import generate_title, slugify
from ..common import image_urls, story_output_dir

router = APIRouter()
logger = logging.getLogger(__name__)

# Where live state used to live, before 0008 moved it into the database. Only used
# to sweep the leftover files when a pre-0008 story is deleted.
LEGACY_GRAPH_DIR = Path(os.getenv("GRAPH_DIR", "/data/graphs"))


# Every table that carries a story_id — deleted (children first) when a story is
# removed. Story itself is deleted last, separately.
_CHILD_MODELS = (
    ChapterSummary, ChapterTrace, ChapterVerifyLog, PlanningVerifyLog, WorldState,
    StateLog, Foreshadowing, Chapter, Character, StoryGraphNode, StoryGraphEdge,
    ContinuityLog, SmartPlannerState, StoryImage,
    # Live state, moved off CSV files in 0008. A table missing from this tuple is a
    # foreign-key violation on delete, not a silent leak — which is the good kind of
    # failure, but the list still has to be kept honest as tables are added.
    CharacterState, Relationship, PlotThread, TimelineEvent,
)


class CreateStoryRequest(BaseModel):
    language: str
    input_type: str  # IDEA | PREMISE | REWRITE
    genre: str | None = None
    source_title: str | None = None  # REWRITE: title of the original story being rewritten
    content: str
    desired_chapters: int | None = None
    desired_words: int | None = None


@router.post("/stories")
def create_story(req: CreateStoryRequest):
    if req.input_type not in ("IDEA", "PREMISE", "REWRITE"):
        raise HTTPException(400, "input_type phải là IDEA, PREMISE hoặc REWRITE")

    total_chapters, target_words, words_per_chapter = length_calc.compute_length(
        req.input_type, req.content, req.desired_chapters, req.desired_words
    )
    title = generate_title(
        PROVIDER,
        AGENT_MODELS["title_generator"],
        language=req.language,
        input_type=req.input_type,
        genre=req.genre,
        source_content=req.content,
    )
    base_slug = slugify(title)

    with SessionLocal() as session:
        slug = base_slug
        suffix = 2
        while session.query(Story).filter_by(slug=slug).first():
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        story = Story(
            title=title,
            slug=slug,
            language=req.language,
            input_type=req.input_type,
            genre=req.genre,
            source_title=(req.source_title or None) if req.input_type == "REWRITE" else None,
            source_content=req.content,
            total_chapters=total_chapters,
            target_words=target_words,
            words_per_chapter=words_per_chapter,
            phase="PLANNING",
        )
        session.add(story)
        session.flush()  # assigns story.id

        for number in range(1, total_chapters + 1):
            session.add(Chapter(story_id=story.id, number=number, status="pending"))
        session.commit()

        return {
            "id": story.id,
            "title": story.title,
            "slug": story.slug,
            "total_chapters": total_chapters,
            "target_words": target_words,
            "words_per_chapter": words_per_chapter,
        }


@router.delete("/stories/{story_id}")
def delete_story(story_id: int):
    """Delete a story and everything it owns: its DB rows, any leftover CSV state
    from before 0008, and the exported output folder. Refuses while a run is active."""
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        if story.is_running:
            raise HTTPException(409, "story is running — stop it first")
        slug = story.slug
        for model in _CHILD_MODELS:
            session.query(model).filter_by(story_id=story_id).delete(synchronize_session=False)
        session.delete(story)
        session.commit()

    # Files and objects (best-effort — the DB rows are already gone).
    if storage.enabled():
        # Everything under the story's own prefix, so the preview set goes too.
        storage.delete_prefix(f"{story_id}/")
    # The story's live state is DB rows now and went with the cascade above; this
    # only sweeps the CSV files a story generated before the move, which are kept
    # around as the rollback and would otherwise outlive the story.
    shutil.rmtree(LEGACY_GRAPH_DIR / str(story_id), ignore_errors=True)
    # Only remove an output folder this story actually owns (marker file), so a
    # slug collision never deletes another story's export.
    for cand in (OUTPUT_BASE / slug, OUTPUT_BASE / f"{slug}-{story_id}"):
        if (cand / f".story-{story_id}").exists():
            shutil.rmtree(cand, ignore_errors=True)
    return {"status": "deleted", "story_id": story_id}


class SuggestTitleRequest(BaseModel):
    notes: str = ""   # optional steer: "nhấn vào yếu tố mafia", "ngắn hơn"


class ApplyTitleRequest(BaseModel):
    title: str


@router.post("/stories/{story_id}/suggest-title")
def suggest_title(story_id: int, req: SuggestTitleRequest):
    """Draft a replacement title WITHOUT saving it, so it can be shown and accepted
    or thrown away. `notes` steers the wording."""
    from ..agents.new_graph_builder import relation_lines_for
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        try:
            relationships = "\n".join(relation_lines_for(session, story))
        except Exception:  # a story with no graph still deserves a title
            relationships = ""
        title = generate_title(
            PROVIDER, AGENT_MODELS["title_generator"],
            language=story.language, input_type="PREMISE", genre=story.genre,
            # The bible describes the RESKINNED world; the source content would name
            # the original's cast and pull the title back toward the book it replaced.
            source_content=story.story_bible or story.source_content or "",
            relationships=relationships, notes=req.notes,
        )
    return {"title": title, "current_title": story.title}


def _retitle_exports(out_dir: Path, new: str) -> list[str]:
    """Put the new title on the files already written to disk.

    Only the FIRST line of each file is touched, never a global replace: the title's
    words also occur in the prose, and rewriting those would corrupt the manuscript.
    The compiler always writes the title as line 1 — bare in summarize.txt, as an `# `
    heading in the markdown — so the line is identified by that shape rather than by
    matching the previous title. Matching the previous title fails exactly when it
    matters: a story renamed before this existed has files still carrying the name
    they were generated under, which is no longer the title being replaced.
    """
    changed = []
    for name, prefix in (("summarize.txt", ""), ("full.md", "# "), ("novel.md", "# ")):
        p = out_dir / name
        if not p.exists():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").split("\n")
        except OSError:
            continue
        if not lines or not lines[0].strip():
            continue
        first = lines[0].strip()
        # A heading, or summarize.txt's bare title line — never one of its labelled
        # fields ("Author: …", "Genre: …"), which is what the colon test excludes.
        if not (first.startswith("# ") or (prefix == "" and ":" not in first)):
            continue
        lines[0] = f"{prefix}{new}"
        try:
            p.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            continue
        changed.append(name)
    return changed


@router.patch("/stories/{story_id}/title")
def set_title(story_id: int, req: ApplyTitleRequest):
    """Accept a new title, and carry it into the files already exported.

    The slug is deliberately NOT recomputed: the export folder is named after it and
    claimed by a `.story-{id}` marker inside, so changing it would orphan the finished
    manuscript and its art. Download names come from the title instead — see
    `download_name` on the story endpoints.
    """
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "title must not be empty")
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        old, story.title = story.title, title
        session.commit()
        touched = _retitle_exports(story_output_dir(story), title)
        logger.info("[%s] title changed: %r -> %r (updated %s)",
                    story.slug, old, title, ", ".join(touched) or "no files")
        return {"title": story.title, "slug": story.slug, "previous_title": old,
                "download_name": slugify(story.title), "updated_files": touched}


@router.get("/stories")
def list_stories():
    with SessionLocal() as session:
        stories = session.query(Story).order_by(Story.created_at.desc()).all()
        return [
            {"id": s.id, "title": s.title, "slug": s.slug, "phase": s.phase,
             "download_name": slugify(s.title),
             "input_type": s.input_type, "source_title": s.source_title}
            for s in stories
        ]


@router.get("/stories/{story_id}")
def get_story(story_id: int):
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        chapters_done = (
            session.query(Chapter).filter_by(story_id=story_id, status="done").count()
        )
        return {
            "id": story.id,
            "title": story.title,
            "slug": story.slug,
            # What downloads are named. Derived from the CURRENT title, unlike `slug`
            # which is frozen at creation because the export folder is keyed on it —
            # so a renamed story stops handing out files under its old name.
            "download_name": slugify(story.title),
            # For a REWRITE, the title of the original this was reskinned from —
            # the detail page shows it so you can tell what a story came from.
            "input_type": story.input_type,
            "source_title": story.source_title or "",
            "phase": story.phase,
            "is_running": story.is_running,
            "stop_requested": story.stop_requested,
            "planning_verified": story.planning_verified,
            "chapters_done": chapters_done,
            "total_chapters": story.total_chapters,
            "current_words": story.current_words,
            "target_words": story.target_words,
            "words_per_chapter": story.words_per_chapter,
            # Signed, time-limited URLs — minted per request, never stored, because
            # they expire. What is stored is the object key on story_images.
            "images": image_urls(session, story_id, "live"),
            "preview_images": image_urls(session, story_id, "preview"),
            # Replaces the .running / .error marker files the playground used to read
            # off the mounted output dir, which object storage cannot provide.
            "image_job_running": bool(story.image_job_running),
            "image_job_error": story.image_job_error or "",
        }
