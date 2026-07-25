"""Per-chapter quality gate (all input types) plus, for REWRITE, an originality
check against the matching source chapter. Runs after chapter_verifier and
before chapter_summarizer. Reuses ChapterVerifyLog for history (the dimension
is tagged into the description) — no schema change needed.
"""

from sqlalchemy.orm import Session

from .. import length_calc
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import QualityReviewIssueOut, QualityReviewerOutput


def _source_chapter_for(story: Story, chapter_number: int) -> str | None:
    if story.input_type != "REWRITE" or not story.source_content:
        return None
    chapters = length_calc.split_source_chapters(story.source_content)
    if 1 <= chapter_number <= len(chapters):
        return chapters[chapter_number - 1]
    return None


def check(session: Session, story: Story, chapter: Chapter) -> list[QualityReviewIssueOut]:
    """Run quality + originality check on the just-written chapter.

    Returns all issues found. Logging and rewrite logic live in the
    orchestrator's verification loop — this function only checks.
    """
    system = load_prompt("quality_reviewer")
    source_chapter = _source_chapter_for(story, chapter.number)

    user_content = (
        f"input_type: {story.input_type}\n"
        f"chapter_number: {chapter.number}\n"
        f"words_per_chapter (mục tiêu): {story.words_per_chapter}\n"
        f"word_count thực tế: {chapter.word_count}\n\n"
        f"## Chương vừa viết (tiêu đề: {chapter.title})\n"
        f"---\n{chapter.content}\n---\n"
    )
    if source_chapter is not None:
        user_content += (
            f"\n## Chương gốc tương ứng — kiểm ĐỘ GIỐNG BỀ MẶT\n"
            f"(Giống mạch truyện/tình tiết là ĐÚNG chủ đích — KHÔNG phạt. "
            f"Chỉ phạt khi rò tên gốc / chép câu / bê nguyên bối cảnh.)\n"
            f"---\n{source_chapter}\n---\n"
        )

    output: QualityReviewerOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["quality_reviewer"],
        schema=QualityReviewerOutput,
        max_tokens=8192,
        thinking=False,
    )
    return output.issues
