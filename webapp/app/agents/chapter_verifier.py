from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, ChapterVerifyLog, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterVerifierOutput
from . import chapter_writer


def run(session: Session, story: Story, chapter: Chapter) -> bool:
    """Checks the just-written chapter against established characters/world
    state. Every issue found is appended to ChapterVerifyLog. A critical
    issue triggers exactly one automatic rewrite of this chapter (via
    chapter_writer) before returning — bounded, no re-verify loop.

    Returns True if the chapter was rewritten.
    """
    system = load_prompt("chapter_verifier")
    user_content = f"""chapter_number: {chapter.number}

## Nhân vật (đầy đủ)
{context_builder.format_characters(session, story.id)}

## world-state hiện tại
{context_builder.format_world_state(session, story.id)}

## Vấn đề continuity đang mở (từ lần rà soát sâu gần nhất, nếu có)
{context_builder.format_continuity_log(session, story.id)}

## 3 chương gần nhất (bao gồm chương vừa viết, Ch.{max(1, chapter.number - 2)}-{chapter.number})
{context_builder.last_n_chapters_text(session, story.id, chapter.number, n=3)}
"""
    output = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_verifier"],
        schema=ChapterVerifierOutput,
        max_tokens=8192,
        thinking=True,
    )

    has_critical = any(issue.severity == "critical" for issue in output.issues)
    action_taken = "rewritten" if has_critical else "logged_only"
    for issue in output.issues:
        session.add(
            ChapterVerifyLog(
                story_id=story.id,
                chapter_number=chapter.number,
                severity=issue.severity,
                description=issue.description,
                suggestion=issue.suggestion,
                action_taken=action_taken,
            )
        )

    if has_critical:
        feedback = "\n".join(
            f"- {issue.description} → SỬA THÀNH: {issue.suggestion}"
            for issue in output.issues
            if issue.severity == "critical"
        )
        chapter_writer.run(session, story, chapter, feedback=feedback)

    return has_critical
