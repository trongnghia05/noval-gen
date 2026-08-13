"""Reading a story back out: the zip, the manuscript, one chapter, one trace."""

import io
import json
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ...core import storage
from ...services import orchestrator
from ...core.config import OUTPUT_BASE
from ...db.models import Chapter, ChapterTrace, Story, StoryImage
from ...db.session import SessionLocal
from ...core.slug import slugify
from ..common import story_output_dir

router = APIRouter()




@router.get("/stories/{story_id}/download-zip")
def download_zip(story_id: int):
    """Download the story as a .zip: one flat `ch-NNN.txt` per chapter (the same
    naming the compiler writes to the export dir), plus `full.md`, `summarize.txt`,
    the poster art under `image/`, and a per-chapter reproducibility record under
    `trace/ch-NNN.json`. Files are taken from the export dir when present, else
    built from the DB."""
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
        download_name = slugify(story.title) or story.slug
        out_dir = story_output_dir(story)

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

            # Poster art, kept under image/ so the zip unpacks into the layout the
            # export dir already uses, whether the bytes came from a bucket or a
            # folder. Written as bytes, not text — these are WebP/PNG.
            if storage.enabled():
                for row in (session.query(StoryImage)
                            .filter_by(story_id=story_id, state="live")
                            .order_by(StoryImage.stem).all()):
                    data = storage.get_bytes(row.object_key)
                    if data:
                        ext = row.object_key.rsplit(".", 1)[-1]
                        z.writestr(f"image/{row.stem}.{ext}", data)
            else:
                for img in sorted((out_dir / "image").glob("*")):
                    if img.is_file():
                        z.writestr(f"image/{img.name}", img.read_bytes())

            # Per-chapter reproducibility traces from the DB, each under trace/.
            for t in (session.query(ChapterTrace)
                      .filter_by(story_id=story_id)
                      .order_by(ChapterTrace.chapter_number).all()):
                z.writestr(
                    f"trace/ch-{t.chapter_number:03d}.json",
                    json.dumps(t.trace, ensure_ascii=False, indent=2),
                )

    return Response(
        buf.getvalue(),
        media_type="application/zip",
        # Named after the current title, not the frozen slug, so a renamed story
        # does not keep handing out files under the name it used to have.
        headers={"Content-Disposition": f'attachment; filename="{download_name}.zip"'},
    )



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



@router.get("/stories/{story_id}/chapters/{number}/trace")
def get_chapter_trace(story_id: int, number: int):
    """The reproducibility record for one chapter: every input the writer saw
    (snapshotted at write time) + the produced output. 404 until the chapter has
    been written."""
    with SessionLocal() as session:
        row = (
            session.query(ChapterTrace)
            .filter_by(story_id=story_id, chapter_number=number)
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "no trace for this chapter yet")
        return row.trace



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
        # The heading word follows the story's language — an English novel headed
        # "Chương" was a real leak here. orchestrator owns that mapping; the export
        # path already uses it, this endpoint had its own hardcoded Vietnamese.
        ch_word = orchestrator._chapter_word(story.language)
        manuscript = "\n\n---\n\n".join(
            f"# {ch_word} {c.number}: {c.title}\n\n{c.content}" for c in chapters
        )
        return {"title": story.title, "manuscript": manuscript}
