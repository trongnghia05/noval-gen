import logging
import re

from ..prompts.loader import load_prompt
from ..llm.base import LLMProvider

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
    notes: str | None = None,
) -> str:
    system = load_prompt("title_generator")
    # `relationships` matters more than its size suggests. source_content is truncated
    # at 4000 chars, and the story bible's relationship map sits at the END of a
    # ~14k-char document — so the title writer never reached it and had to guess who
    # was whose brother. One story shipped as "My Fake Fiance's Brother" when the fake
    # fiancé WAS the brother; the title named the villain instead of the love interest.
    rel_block = (
        "\n\nQuan he nhan vat — moi du kien ghi nhan, nhom theo cap. Quan he VINH VIEN "
        "(gia dinh, hon nhan, nguoi yeu cu) va trang thai NHAT THOI (yeu, han, ganh dua) "
        "nam lan nhau; tu phan biet lay. Chi dung nhung gi co o day, khong suy dien tu ho ten:\n"
        f"{relationships.strip()}"
        if relationships and relationships.strip() else ""
    )
    # A human asking for a specific angle ("nhấn vào yếu tố mafia", "ngắn hơn").
    # Placed last so it is the freshest thing in context, and marked as outranking the
    # writer's own judgement — but never the title RULES, which _title_problem still
    # enforces in code afterwards whatever was asked for.
    note_block = (
        "\n\nYEU CAU RIENG cua nguoi dung cho lan dat ten nay — uu tien cao hon lua "
        "chon cua ban, nhung KHONG duoc pha cac quy tac ve do dai va cau truc tieu de:\n"
        f"{notes.strip()}"
        if notes and notes.strip() else ""
    )
    user_content = (
        f"Ngon ngu: {language}\nLoai input: {input_type}\nThe loai: {genre or '(tu chon)'}"
        f"{rel_block}\n\n"
        f"Noi dung:\n{source_content[:4000]}"
        f"{note_block}"
    )
    # Generous headroom even for a "just give me 2-6 words" task: reasoning
    # models spend part of max_tokens on invisible chain-of-thought before
    # the visible answer, so a tight budget here starves the real output.
    title = ""
    for attempt in range(2):
        response = provider.generate(system=system, user_content=user_content,
                                     model=model, max_tokens=500)
        title = _clean_title(response.text)
        problem = _title_problem(title)
        if not problem:
            return title
        logger.warning("title attempt %d rejected (%s): %r", attempt + 1, problem, title)
        user_content += (
            f"\n\nLan truoc ban tra ve: \"{title}\" — BI TU CHOI vi: {problem}\n"
            f"Hay viet lai mot tieu de khac, sua dung loi nay."
        )
    # Never block a run over a title; a flawed one is fixable later, a crash is not.
    logger.warning("title still flawed after retries, accepting: %r", title)
    return title


# Imperative openers from the command mould. "Claim My …" is the specific way that
# mould breaks: it drops the heroine out of her own title and hands the power to
# whoever is being addressed.
_COMMAND_VERBS = ("claim", "break", "ruin", "own", "tame", "wreck", "crave", "keep",
                  "breed", "take", "reject")


def _title_problem(title: str) -> str | None:
    """Return why a title is unusable, or None if it passes.

    A code-side gate because prompt rules alone kept being ignored: one story shipped
    as "Claim My Magnate, Architect", which mixes two moulds, swaps `Me` for `My` so
    the heroine is no longer the one speaking, and fills the vocative with her OWN
    profession. Each of those was already forbidden in the prompt.
    """
    words = title.split()
    if not 2 <= len(words) <= 8:
        return f"do dai {len(words)} tu, phai trong khoang 3-6 tu"
    if words[0].lower().strip(",") in _COMMAND_VERBS and words[1].lower() in ("my", "the", "his", "her"):
        return ("dung khuon menh lenh nhung sau dong tu phai la 'Me' (nhan vat nu "
                "moi gia loi), khong phai 'My/The/His/Her'")
    if title.count("'s") + title.count("’s") >= 2:
        return "co hai so huu cach lien tiep, doc rat luc cuc"
    if ":" in title:
        return "co dau hai cham / tieu de phu"
    return None


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
