"""Per-chapter quality gate (all input types) plus, for REWRITE, an originality
check against the matching source chapter. Runs after chapter_verifier and
before chapter_summarizer. A critical issue triggers exactly one feedback-guided
rewrite (bounded, no re-review loop) — same contract as chapter_verifier.

Reuses ChapterVerifyLog for history (the dimension is tagged into the
description) so no schema change is needed.
"""

from sqlalchemy.orm import Session

from .. import length_calc
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, ChapterVerifyLog, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import QualityReviewerOutput
from . import chapter_writer


def _source_chapter_for(story: Story, chapter_number: int) -> str | None:
    """The source chapter a REWRITE chapter should be compared against for
    surface-similarity. None for non-REWRITE, or when the source has no
    matching chapter (e.g. target chapter count exceeds the source's)."""
    if story.input_type != "REWRITE" or not story.source_content:
        return None
    chapters = length_calc.split_source_chapters(story.source_content)
    if 1 <= chapter_number <= len(chapters):
        return chapters[chapter_number - 1]
    return None


def run(session: Session, story: Story, chapter: Chapter) -> bool:
    system = load_prompt("quality_reviewer")
    source_chapter = _source_chapter_for(story, chapter.number)

    user_content = f"""input_type: {story.input_type}
chapter_number: {chapter.number}
words_per_chapter (mục tiêu): {story.words_per_chapter}
word_count thực tế: {chapter.word_count}

## Chương vừa viết (tiêu đề: {chapter.title})
---
{chapter.content}
---
"""
    if source_chapter is not None:
        user_content += f"""
## Chương gốc tương ứng — kiểm ĐỘ GIỐNG BỀ MẶT
(Giống mạch truyện/tình tiết là ĐÚNG chủ đích — KHÔNG phạt. Chỉ phạt khi rò tên gốc / chép câu / bê nguyên bối cảnh.)
---
{source_chapter}
---
"""

    output = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["quality_reviewer"],
        schema=QualityReviewerOutput,
        max_tokens=8192,
        thinking=True,
    )

    has_critical = any(i.severity == "critical" for i in output.issues)
    action = "rewritten" if has_critical else "logged_only"
    for issue in output.issues:
        session.add(
            ChapterVerifyLog(
                story_id=story.id,
                chapter_number=chapter.number,
                severity=issue.severity,
                description=f"[{issue.dimension}] {issue.description}",
                suggestion=issue.suggestion,
                action_taken=action,
            )
        )

    if has_critical:
        feedback = "\n".join(
            f"- ({i.dimension}) {i.description} → SỬA: {i.suggestion}"
            for i in output.issues
            if i.severity == "critical"
        )
        chapter_writer.run(session, story, chapter, feedback=feedback)

    return has_critical
