"""Driving the pipeline: one step at a time, or the whole novel in the background."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ...services import graph, orchestrator
from ...db.models import Story
from ...db.session import SessionLocal

router = APIRouter()
logger = logging.getLogger(__name__)


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
