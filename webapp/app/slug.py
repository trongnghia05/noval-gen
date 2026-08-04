import logging
import re

from .prompts.loader import load_prompt
from .providers.base import LLMProvider

logger = logging.getLogger(__name__)

_VIETNAMESE_D = str.maketrans({"đ": "d", "Đ": "D"})  # đ / Đ don't decompose under NFKD


def slugify(title: str, max_len: int = 40) -> str:
    import unicodedata

    ascii_str = unicodedata.normalize("NFKD", title.translate(_VIETNAMESE_D))
    ascii_str = ascii_str.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_str).strip("-").lower()
    return slug[:max_len].strip("-") or "untitled"


def generate_title(
    provider: LLMProvider,
    model: str,
    *,
    language: str,
    input_type: str,
    genre: str | None,
    source_content: str,
    relationships: str | None = None,
) -> str:
    system = load_prompt("title_generator")
    # `relationships` matters more than its size suggests. source_content is truncated
    # at 4000 chars, and the story bible's relationship map sits at the END of a
    # ~14k-char document — so the title writer never reached it and had to guess who
    # was whose brother. One story shipped as "My Fake Fiance's Brother" when the fake
    # fiancé WAS the brother; the title named the villain instead of the love interest.
    rel_block = (
        f"\n\nQuan he nhan vat (chinh xac — dung suy dien tu ten ho):\n{relationships.strip()}"
        if relationships and relationships.strip() else ""
    )
    user_content = (
        f"Ngon ngu: {language}\nLoai input: {input_type}\nThe loai: {genre or '(tu chon)'}"
        f"{rel_block}\n\n"
        f"Noi dung:\n{source_content[:4000]}"
    )
    # Generous headroom even for a "just give me 2-6 words" task: reasoning
    # models spend part of max_tokens on invisible chain-of-thought before
    # the visible answer, so a tight budget here starves the real output.
    response = provider.generate(system=system, user_content=user_content, model=model, max_tokens=500)
    return _clean_title(response.text)


def _clean_title(raw: str) -> str:
    """Turn whatever the model returned into a usable title.

    Two failure modes this guards against, both seen in practice:
    a reasoning model answering with a paragraph of deliberation instead of a title,
    and a two-part "Hook: Second Hook" title. The colon form is banned in the prompt
    but the model still reaches for it, and a long title is what makes the image
    model misspell it on the poster."""
    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if not lines:
        return ""

    def _looks_like_a_title(s: str) -> bool:
        s = s.strip('"').strip("'").lstrip("#").strip()
        return bool(s) and len(s.split()) <= 8 and not s.endswith((".", "?", "!", ":"))

    # A reasoning model puts its deliberation FIRST and the answer LAST, so scan from
    # the bottom for the last line that actually looks like a title. Falling back to
    # the first line would keep "The best title depends on the following…".
    line = next((ln for ln in reversed(lines) if _looks_like_a_title(ln)), lines[0])
    line = line.strip('"').strip("'").lstrip("#").strip()
    line = re.sub(r"^(?:title|tiêu đề)\s*[:\-]\s*", "", line, flags=re.IGNORECASE).strip()

    # Drop the subtitle: keep whichever side of the colon carries more of the hook.
    if ":" in line:
        parts = [p.strip() for p in line.split(":") if p.strip()]
        if parts:
            best = max(parts, key=lambda p: len(p.split()))
            logger.info("title: dropped subtitle %r -> %r", line, best)
            line = best

    return line.strip('"').strip("'").strip()
