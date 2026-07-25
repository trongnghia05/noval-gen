from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import ChapterVerifierOutput, ChapterVerifyIssueOut


def check(
    session: Session,
    story: Story,
    chapter: Chapter,
    graph_context: str = "",
) -> list[ChapterVerifyIssueOut]:
    """Run continuity check on the just-written chapter.

    Returns all issues found (critical + minor). Logging and rewrite logic
    live in the orchestrator's verification loop — this function only checks.

    graph_context: formatted new-graph text (characters, relations, arc changes,
    causal chains) so the verifier can cross-check against structured graph state
    in addition to the flat world-state snapshot.
    """
    system = load_prompt("chapter_verifier")
    graph_section = (
        f"\n## Story graph (new) — structured state to cross-check against\n{graph_context}\n"
        if graph_context
        else ""
    )
    user_content = (
        f"chapter_number: {chapter.number}\n\n"
        f"## Nhân vật (đầy đủ)\n{context_builder.format_characters(session, story.id)}\n\n"
        f"## world-state hiện tại\n{context_builder.format_world_state(session, story.id)}\n\n"
        f"## Vấn đề continuity đang mở (từ lần rà soát sâu gần nhất, nếu có)\n"
        f"{context_builder.format_continuity_log(session, story.id)}\n"
        f"{graph_section}\n"
        f"## 3 chương gần nhất (bao gồm chương vừa viết, "
        f"Ch.{max(1, chapter.number - 2)}-{chapter.number})\n"
        f"{context_builder.last_n_chapters_text(session, story.id, chapter.number, n=3)}\n"
    )
    output: ChapterVerifierOutput = generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["chapter_verifier"],
        schema=ChapterVerifierOutput,
        max_tokens=8192,
        thinking=False,
    )
    return output.issues
