import json

from ..config import AGENT_MODELS, PROVIDER
from ..llm_json import generate_structured
from ..db.models import Story
from ..prompts.loader import load_prompt
from ..schemas import PlotOutlineOut

# A single generation can be cut off by the output-token ceiling before all N
# chapters are produced. Rather than accept a short outline, keep asking the model
# to continue from the last chapter until every chapter is covered.
_MAX_CONTINUATIONS = 6


def _highest_chapter(outline: PlotOutlineOut) -> int:
    return max((c.number for c in outline.chapters), default=0)


def _merge(base: PlotOutlineOut, more: PlotOutlineOut) -> PlotOutlineOut:
    """Append continuation chapters, keeping one entry per chapter number, sorted."""
    by_num = {c.number: c for c in base.chapters}
    for c in more.chapters:
        by_num.setdefault(c.number, c)
    base.chapters = [by_num[n] for n in sorted(by_num)]
    return base


def run(story: Story, feedback: str | None = None, story_graph: str = "") -> str:
    """Produce the plot outline as a structured JSON string (PlotOutlineOut).

    Stored verbatim in story.plot_outline. Downstream agents receive this JSON
    string as context (the creative planning logic is unchanged — only the output
    format moved from markdown to JSON)."""
    system = load_prompt("plot_architect")
    graph_section = (
        f"\n## Story Knowledge Graph (dùng EVENT nodes làm xương sống cho REWRITE)\n{story_graph}\n"
        if story_graph else ""
    )
    base = f"""input_type: {story.input_type}
total_chapters (N): {story.total_chapters}
words_per_chapter: {story.words_per_chapter}
Ngôn ngữ: {story.language}

story-bible.md (tóm tắt ngắn):
---
{story.story_bible}
---
{graph_section}"""
    if feedback:
        base += (
            "\n## LỖI TỪ VÒNG KIỂM TRA TRƯỚC — bắt buộc khắc phục, giữ nguyên phần đã đúng\n"
            f"{feedback}\n"
        )

    # A detailed per-chapter outline runs ~1200+ output tokens/chapter, and the
    # model's dynamic thinking draws from the same budget — a small cap truncates
    # long novels mid-chapter. Scale generously, stay under the hard output ceiling.
    max_tokens = min(60000, max(8192, story.total_chapters * 1600))

    outline: PlotOutlineOut = generate_structured(
        PROVIDER, system=system, user_content=base,
        model=AGENT_MODELS["plot_architect"], schema=PlotOutlineOut,
        max_tokens=max_tokens, thinking=True,
    )

    # Completeness loop: if fewer than N chapters came back (truncation), ask the
    # model to continue from where it left off. Bounded so it can't spin forever.
    attempts = 0
    while _highest_chapter(outline) < story.total_chapters and attempts < _MAX_CONTINUATIONS:
        attempts += 1
        last = _highest_chapter(outline)
        written = json.dumps(outline.model_dump(), ensure_ascii=False)
        continuation_prompt = base + f"""
## OUTLINE ĐÃ VIẾT (đã tới hết Chương {last}) — cần VIẾT TIẾP, KHÔNG lặp lại phần đã có
---
{written}
---

Outline trên bị dừng ở Chương {last}, CHƯA đủ {story.total_chapters} chương. Hãy viết TIẾP
từ **Chương {last + 1}** cho đến hết **Chương {story.total_chapters}**, cùng cấu trúc JSON.
CHỈ trả về các chương từ {last + 1} trở đi trong `chapters` (title/arc_overview có thể để trống),
KHÔNG lặp lại các chương đã viết.
"""
        more: PlotOutlineOut = generate_structured(
            PROVIDER, system=system, user_content=continuation_prompt,
            model=AGENT_MODELS["plot_architect"], schema=PlotOutlineOut,
            max_tokens=max_tokens, thinking=True,
        )
        if _highest_chapter(more) <= last:
            break  # continuation added no new chapter — stop rather than spin
        outline = _merge(outline, more)

    return json.dumps(outline.model_dump(), ensure_ascii=False, indent=2)
