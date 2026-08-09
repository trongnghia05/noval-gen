import logging
import shutil

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import csv_graph, graph, length_calc, orchestrator
from ..config import AGENT_MODELS, OUTPUT_BASE, PROVIDER
from ..db.models import (
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


@router.get("/stories")
def list_stories():
    with SessionLocal() as session:
        stories = session.query(Story).order_by(Story.created_at.desc()).all()
        return [{"id": s.id, "title": s.title, "slug": s.slug, "phase": s.phase} for s in stories]


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
