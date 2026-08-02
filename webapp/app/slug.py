import re

from .providers.base import LLMProvider

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
) -> str:
    system = (
        "You are a hit-title copywriter for viral web novels / short dramas "
        "(ShortTV / Dreame / GoodNovel style). Create ONE title for the story below.\n"
        "The title MUST be:\n"
        "- INSTANTLY CLEAR and easy to understand at a glance — never abstract, "
        "literary, cryptic or vague. A stranger should grasp the hook immediately. "
        "Avoid one-word mood nouns like 'Reckoning', 'Haven', 'Oath', 'Gambit', 'Echoes'.\n"
        "- HOOKY & TREND-WORTHY: signal the core trope / relationship / emotional "
        "promise that makes someone click — e.g. billionaire, CEO, ex-husband, "
        "revenge, contract/fake marriage, secret baby, rebirth/second chance, "
        "substitute bride, mafia, forbidden love. Name the DYNAMIC concretely.\n"
        "- EASY TO REMEMBER: punchy, concrete, emotional. Think titles like "
        "'Married to My Enemy', \"The Billionaire's Runaway Bride\", "
        "\"His Substitute Wife\", 'Rebirth: I Will Take Back Everything', "
        "'Divorcing My Cheating Husband'.\n"
        "- LENGTH: about 3 to 7 words. Short but complete enough to convey the hook "
        "(a bare 1-2 word abstract title is NOT acceptable).\n"
        "- Written in the SAME language as the story (English if the writing "
        "language is English).\n"
        "Return ONLY the title text — no explanation, no quotation marks."
    )
    user_content = (
        f"Ngon ngu: {language}\nLoai input: {input_type}\nThe loai: {genre or '(tu chon)'}\n\n"
        f"Noi dung:\n{source_content[:4000]}"
    )
    # Generous headroom even for a "just give me 2-6 words" task: reasoning
    # models spend part of max_tokens on invisible chain-of-thought before
    # the visible answer, so a tight budget here starves the real output.
    response = provider.generate(system=system, user_content=user_content, model=model, max_tokens=500)
    return response.text.strip().strip('"').strip("'")
