from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import length_calc, orchestrator
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..db.session import SessionLocal
from ..slug import generate_title, slugify

router = APIRouter()


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
