import io
import logging
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .. import csv_graph, graph, length_calc, orchestrator
from ..config import AGENT_MODELS, OUTPUT_BASE, PROVIDER
from ..db.models import (
    AppSetting,
    Chapter,
    ChapterSummary,
    ChapterVerifyLog,
    Character,
    ContinuityLog,
    Foreshadowing,
    PlanningVerifyLog,
    SmartPlannerState,
    StateLog,
    Story,
    StoryGraphEdge,
    StoryGraphNode,
    WorldState,
)
from ..db.session import SessionLocal
from ..slug import generate_title, slugify

# Every table that carries a story_id — deleted (children first) when a story is
# removed. Story itself is deleted last, separately.
_CHILD_MODELS = (
    ChapterSummary, ChapterVerifyLog, PlanningVerifyLog, WorldState, StateLog,
    Foreshadowing, Chapter, Character, StoryGraphNode, StoryGraphEdge,
    ContinuityLog, SmartPlannerState,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateStoryRequest(BaseModel):
    language: str
    input_type: str  # IDEA | PREMISE | REWRITE
    genre: str | None = None
    source_title: str | None = None  # REWRITE: title of the original story being rewritten
    content: str
    desired_chapters: int | None = None
    desired_words: int | None = None


class SettingRequest(BaseModel):
    value: str = ""


_SETTING_KEYS = {"cms_upload_url"}


@router.get("/settings/{key}")
def get_setting(key: str):
    if key not in _SETTING_KEYS:
        raise HTTPException(404, "setting not found")
    with SessionLocal() as session:
        setting = session.get(AppSetting, key)
        return {"key": key, "value": setting.value if setting else ""}


@router.put("/settings/{key}")
def put_setting(key: str, req: SettingRequest):
    if key not in _SETTING_KEYS:
        raise HTTPException(404, "setting not found")
    with SessionLocal() as session:
        setting = session.get(AppSetting, key)
        if setting is None:
            setting = AppSetting(key=key)
            session.add(setting)
        setting.value = req.value.strip()
        session.commit()
        return {"key": key, "value": setting.value}


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


@router.post("/stories/{story_id}/advance")
def advance_story(story_id: int):
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        return orchestrator.advance(session, story)


def _run_story_background(story_id: int) -> None:
    try:
        graph.run_story_to_completion(story_id)
    except graph.RunStopped:
        logger.info("Run for story %s stopped by request", story_id)
    except Exception:
        logger.exception("Background run failed for story %s", story_id)
    finally:
        with SessionLocal() as session:
            story = session.get(Story, story_id)
            if story is not None:
                story.is_running = False
                story.stop_requested = False
                session.commit()


@router.post("/stories/{story_id}/run")
def run_story(story_id: int, background_tasks: BackgroundTasks):
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        if story.is_running:
            raise HTTPException(409, "story is already running")
        if story.phase == "COMPLETE":
            raise HTTPException(409, "story is already complete")
        story.is_running = True
        story.stop_requested = False  # clear any stale stop from a previous run
        session.commit()

    background_tasks.add_task(_run_story_background, story_id)
    return {"status": "started", "story_id": story_id}


@router.post("/stories/{story_id}/stop")
def stop_story(story_id: int):
    """Request a running generation to halt at the next step boundary. The story
    stays fully resumable — POST /run continues from where it stopped."""
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        if not story.is_running:
            raise HTTPException(409, "story is not running")
        story.stop_requested = True
        session.commit()
    return {"status": "stopping", "story_id": story_id}


@router.delete("/stories/{story_id}")
def delete_story(story_id: int):
    """Delete a story and everything it owns: all DB rows, the CSV knowledge
    graph, and the exported output folder. Refuses while a run is active."""
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

    # Files (best-effort — DB row is already gone).
    shutil.rmtree(csv_graph.GRAPH_BASE / str(story_id), ignore_errors=True)
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


@router.patch("/stories/{story_id}/title")
def set_title(story_id: int, req: ApplyTitleRequest):
    """Accept a new title. The slug is deliberately left alone: the export folder is
    named after it and is claimed by a `.story-{id}` marker inside, so renaming here
    would orphan the finished manuscript and its art."""
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(400, "title must not be empty")
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        old, story.title = story.title, title
        session.commit()
        logger.info("[%s] title changed: %r -> %r", story.slug, old, title)
        return {"title": story.title, "slug": story.slug, "previous_title": old}


class RegenImagesRequest(BaseModel):
    which: str = "all"  # all | cover | thumbnail1 | thumbnail2
    # Free-text steering for this run — "warmer, put her in red", or per-image with a
    # "cover:" / "thumb1:" / "thumb2:" prefix. Overrides the randomly drawn art
    # direction; never the story's era, its cast, or the title rules.
    notes: str = ""
    # Fixes the art-direction draw so a run can be repeated. Omit and the menus are
    # drawn fresh, which is why two runs of the same story look different.
    seed: int | None = None
    # Write to image/.preview/ instead of over the live art, so the result can be
    # looked at before it replaces anything. Accept or discard it afterwards.
    preview: bool = False
    # Return as soon as the work is queued instead of holding the request open for
    # the minute or two it takes. The caller watches the preview folder fill up.
    background: bool = False


def _story_output_dir(story: Story):
    """This story's export dir, resolved via the same ownership marker the compiler
    uses so a slug collision can't point at another story's folder."""
    folder = story.slug or f"story-{story.id}"
    for cand in (OUTPUT_BASE / folder, OUTPUT_BASE / f"{folder}-{story.id}"):
        if (cand / f".story-{story.id}").exists():
            return cand
    return OUTPUT_BASE / folder


def _story_image_dir(story: Story):
    return _story_output_dir(story) / "image"


def _preview_dir(story: Story):
    """Where a regenerated set waits to be accepted. A dot-prefixed name so the
    poster readers, which glob `image/*`, never pick it up as live art."""
    return _story_image_dir(story) / ".preview"


# Progress is published as marker FILES rather than through an endpoint, because the
# playground already mounts the output dir read-only: it can watch a poster appear the
# moment it is written and needs no polling API to do it.
def _regen_images_job(story_id: int, which: str, notes: str, seed: int | None,
                      target: str, marker: str):
    """Generate in the background, clearing the marker whatever happens — a marker
    left behind would leave the UI waiting for art that is never coming."""
    from .. import image_generator
    mark = Path(marker)
    try:
        with SessionLocal() as session:
            story = session.get(Story, story_id)
            if story:
                image_generator.generate(session, story, Path(target), only=which,
                                         notes=notes, seed=seed)
    except Exception as exc:  # noqa: BLE001 — the UI reads this file to show the error
        logger.exception("[story %s] background image regen failed", story_id)
        try:
            mark.with_name(".error").write_text(str(exc)[:500], encoding="utf-8")
        except OSError:
            pass
    finally:
        mark.unlink(missing_ok=True)


@router.get("/stories/{story_id}/download-zip")
def download_zip(story_id: int):
    """Download the story as a .zip: one flat `ch-NNN.txt` per chapter (the same
    naming the compiler writes to the export dir), plus `full.md`, `summarize.txt`
    and the poster art under `image/`. Files are taken from the export dir when
    present, else built from the DB."""
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        chapters = (
            session.query(Chapter)
            .filter_by(story_id=story_id, status="done")
            .order_by(Chapter.number)
            .all()
        )
        if not chapters:
            raise HTTPException(409, "no completed chapters to download yet")
        slug = story.slug
        out_dir = _story_output_dir(story)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for c in chapters:
                fname = f"ch-{c.number:03d}.txt"
                disk = out_dir / fname
                if disk.exists():  # exact file the compiler already wrote
                    z.writestr(fname, disk.read_text(encoding="utf-8", errors="ignore"))
                else:  # not compiled yet — build the same shape from the DB
                    heading = f"Chapter {c.number}: {c.title or ''}".rstrip(": ").strip()
                    z.writestr(fname, f"{heading}\n\n{c.content or ''}\n")

            full = out_dir / "full.md"
            if full.exists():
                z.writestr("full.md", full.read_text(encoding="utf-8", errors="ignore"))
            else:  # not compiled yet — assemble a minimal full.md from the DB
                parts = [f"# {story.title}\n"]
                for c in chapters:
                    parts.append(f"# Chapter {c.number}: {c.title or ''}\n\n{c.content or ''}")
                z.writestr("full.md", "\n\n---\n\n".join(parts) + "\n")

            summ = out_dir / "summarize.txt"
            if summ.exists():
                z.writestr("summarize.txt", summ.read_text(encoding="utf-8", errors="ignore"))

            # Poster art, kept at the same path it has on disk so the zip unpacks
            # into the layout the export dir already uses. Written as bytes, not
            # text — these are WebP/PNG.
            for img in sorted((out_dir / "image").glob("*")):
                if img.is_file():
                    z.writestr(f"image/{img.name}", img.read_bytes())

    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@router.post("/stories/{story_id}/regenerate-images")
def regenerate_images(story_id: int, req: RegenImagesRequest,
                      background_tasks: BackgroundTasks):
    """Re-run poster art: `which` = all | cover | thumbnail1 | thumbnail2.
    Regenerating a thumbnail reuses the existing cover as the identity reference.
    `notes` steers this run in free text; `seed` makes the art direction repeatable.
    `preview` writes to image/.preview/ so the result can be judged before it replaces
    the live art — accept or discard it with the endpoints below.
    Synchronous — runs in FastAPI's threadpool; can take a minute or two."""
    valid = {"all", "cover", "thumbnail1", "thumbnail2"}
    if req.which not in valid:
        raise HTTPException(400, f"which must be one of {sorted(valid)}")
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        if story.phase != "COMPLETE":
            raise HTTPException(409, "images are only (re)generated once the story is COMPLETE")
        image_dir = _story_image_dir(story)
        image_dir.mkdir(parents=True, exist_ok=True)
        target = image_dir
        if req.preview:
            target = _preview_dir(story)
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            # Seed the preview with the current art. Two reasons: regenerating one
            # thumbnail needs the cover present as an identity reference, and it makes
            # the preview a COMPLETE set, so accepting it is a straight move.
            for p in image_dir.glob("*"):
                if p.is_file():
                    shutil.copy2(p, target / p.name)

        if req.background:
            marker = target / ".running"
            (target / ".error").unlink(missing_ok=True)
            marker.write_text(req.which, encoding="utf-8")
            background_tasks.add_task(_regen_images_job, story_id, req.which,
                                      req.notes, req.seed, str(target), str(marker))
            return {"status": "started", "which": req.which, "preview": req.preview}

        from .. import image_generator
        try:
            written = image_generator.generate(
                session, story, target, only=req.which,
                notes=req.notes, seed=req.seed,
            )
        except Exception as exc:  # noqa: BLE001 — surface any gen failure to the client
            logger.exception("[%s] regenerate-images failed", story.slug)
            raise HTTPException(500, f"image generation failed: {exc}")
    return {"which": req.which, "written": written, "preview": req.preview}


@router.post("/stories/{story_id}/images/accept")
def accept_preview_images(story_id: int):
    """Replace the live art with the previewed set, then drop the preview."""
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        preview, image_dir = _preview_dir(story), _story_image_dir(story)
        files = [p for p in preview.glob("*") if p.is_file()] if preview.is_dir() else []
        if not files:
            raise HTTPException(409, "no preview images to accept")
        image_dir.mkdir(parents=True, exist_ok=True)
        for p in files:
            shutil.move(str(p), str(image_dir / p.name))
        shutil.rmtree(preview, ignore_errors=True)
        logger.info("[%s] accepted %d preview image(s)", story.slug, len(files))
        return {"accepted": [p.name for p in files]}


@router.post("/stories/{story_id}/images/discard")
def discard_preview_images(story_id: int):
    """Throw the previewed set away; the live art is untouched."""
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        shutil.rmtree(_preview_dir(story), ignore_errors=True)
        return {"status": "discarded"}


@router.get("/stories")
def list_stories():
    with SessionLocal() as session:
        stories = session.query(Story).order_by(Story.created_at.desc()).all()
        return [
            {"id": s.id, "title": s.title, "slug": s.slug, "phase": s.phase,
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
        }


@router.get("/stories/{story_id}/chapters/{number}")
def get_chapter(story_id: int, number: int):
    with SessionLocal() as session:
        chapter = session.query(Chapter).filter_by(story_id=story_id, number=number).first()
        if not chapter:
            raise HTTPException(404, "chapter not found")
        return {
            "number": chapter.number,
            "title": chapter.title,
            "content": chapter.content,
            "word_count": chapter.word_count,
            "status": chapter.status,
        }


@router.get("/stories/{story_id}/export")
def export_manuscript(story_id: int):
    """Download the compiled novel as a single markdown file.

    Only available once the story is COMPLETE (run_complete_step saves the file).
    Returns the file as a downloadable text/markdown attachment.
    """
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        out_path = OUTPUT_BASE / str(story_id) / "novel.md"
        if not out_path.exists():
            raise HTTPException(404, "compiled manuscript not found — story must be COMPLETE first")
        return FileResponse(
            str(out_path),
            media_type="text/markdown",
            filename=f"{story.slug}.md",
        )


@router.get("/stories/{story_id}/manuscript")
def get_manuscript(story_id: int):
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        chapters = (
            session.query(Chapter)
            .filter_by(story_id=story_id, status="done")
            .order_by(Chapter.number)
            .all()
        )
        manuscript = "\n\n---\n\n".join(
            f"# Chương {c.number}: {c.title}\n\n{c.content}" for c in chapters
        )
        return {"title": story.title, "manuscript": manuscript}
