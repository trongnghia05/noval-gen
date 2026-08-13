"""Poster art: regenerate, then accept or discard the pending set."""

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ...core import storage
from ...db.models import Story, StoryImage
from ...db.session import SessionLocal
from ..common import preview_dir, story_image_dir

router = APIRouter()
logger = logging.getLogger(__name__)


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


# Progress used to be published as marker FILES, which worked only because the
# playground had the output dir mounted and could watch them appear. Object storage
# cannot be mounted, so the state lives on the story row and the UI reads it from
# GET /stories/{id} like everything else.
def _regen_images_job(story_id: int, which: str, notes: str, seed: int | None,
                      target: str, preview: bool):
    """Generate in the background, always clearing the running flag — a flag left set
    would leave the UI waiting for art that is never coming."""
    from .. import image_generator
    try:
        with SessionLocal() as session:
            story = session.get(Story, story_id)
            if story:
                image_generator.generate(session, story, Path(target), only=which,
                                         notes=notes, seed=seed, preview=preview)
                session.commit()
    except Exception as exc:  # noqa: BLE001 — the UI reads this to show the error
        logger.exception("[story %s] background image regen failed", story_id)
        with SessionLocal() as session:
            story = session.get(Story, story_id)
            if story:
                story.image_job_error = str(exc)[:500]
                session.commit()
    finally:
        with SessionLocal() as session:
            story = session.get(Story, story_id)
            if story:
                story.image_job_running = False
                session.commit()


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
        image_dir = story_image_dir(story)
        image_dir.mkdir(parents=True, exist_ok=True)
        target = image_dir
        if req.preview:
            target = preview_dir(story)
            if not storage.enabled():
                # File mode only. With a bucket the preview set is its own set of
                # rows, and the identity reference is read from the LIVE row — so
                # there is nothing to seed and nothing to copy.
                shutil.rmtree(target, ignore_errors=True)
                target.mkdir(parents=True, exist_ok=True)
                for p in image_dir.glob("*"):
                    if p.is_file():
                        shutil.copy2(p, target / p.name)
            else:
                # Replacing an unaccepted preview: drop the previous one first so a
                # partial regeneration cannot leave a mixed set behind.
                _drop_preview(session, story)

        if req.background:
            story.image_job_running = True
            story.image_job_error = None
            session.commit()
            background_tasks.add_task(_regen_images_job, story_id, req.which,
                                      req.notes, req.seed, str(target), req.preview)
            return {"status": "started", "which": req.which, "preview": req.preview}

        from .. import image_generator
        try:
            written = image_generator.generate(
                session, story, target, only=req.which,
                notes=req.notes, seed=req.seed, preview=req.preview,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001 — surface any gen failure to the client
            logger.exception("[%s] regenerate-images failed", story.slug)
            raise HTTPException(500, f"image generation failed: {exc}")
    return {"which": req.which, "written": written, "preview": req.preview}


def _drop_preview(session, story: Story) -> int:
    """Delete the pending set — rows and objects. Returns how many went."""
    rows = session.query(StoryImage).filter_by(story_id=story.id, state="preview").all()
    for row in rows:
        storage.delete(row.object_key)
        session.delete(row)
    session.flush()
    return len(rows)


@router.post("/stories/{story_id}/images/accept")
def accept_preview_images(story_id: int):
    """Replace the live art with the previewed set, then drop the preview.

    With a bucket this is one UPDATE inside one transaction. The file version was a
    loop of moves that could half-fail and leave a set that was part new, part old.
    """
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")

        if not storage.enabled():
            preview, image_dir = preview_dir(story), story_image_dir(story)
            files = [p for p in preview.glob("*") if p.is_file()] if preview.is_dir() else []
            if not files:
                raise HTTPException(409, "no preview images to accept")
            image_dir.mkdir(parents=True, exist_ok=True)
            for p in files:
                shutil.move(str(p), str(image_dir / p.name))
            shutil.rmtree(preview, ignore_errors=True)
            logger.info("[%s] accepted %d preview image(s)", story.slug, len(files))
            return {"accepted": [p.name for p in files]}

        pending = session.query(StoryImage).filter_by(story_id=story.id, state="preview").all()
        if not pending:
            raise HTTPException(409, "no preview images to accept")

        # The keys being retired, collected before the rows are repointed. Deleting
        # them only after the commit means a crash mid-way leaves an unreferenced
        # object rather than a row pointing at an object that is already gone.
        retired = [
            r.object_key
            for r in session.query(StoryImage)
                       .filter(StoryImage.story_id == story.id,
                               StoryImage.state == "live",
                               StoryImage.stem.in_([p.stem for p in pending])).all()
        ]
        for row in (session.query(StoryImage)
                    .filter(StoryImage.story_id == story.id,
                            StoryImage.state == "live",
                            StoryImage.stem.in_([p.stem for p in pending])).all()):
            session.delete(row)
        session.flush()
        for row in pending:
            row.state = "live"
        session.commit()

        for key in retired:
            storage.delete(key)
        logger.info("[%s] accepted %d preview image(s)", story.slug, len(pending))
        return {"accepted": [p.stem for p in pending]}


@router.post("/stories/{story_id}/images/discard")
def discard_preview_images(story_id: int):
    """Throw the previewed set away; the live art is untouched."""
    with SessionLocal() as session:
        story = session.get(Story, story_id)
        if not story:
            raise HTTPException(404, "story not found")
        if storage.enabled():
            n = _drop_preview(session, story)
            session.commit()
            return {"status": "discarded", "dropped": n}
        shutil.rmtree(preview_dir(story), ignore_errors=True)
        return {"status": "discarded"}
