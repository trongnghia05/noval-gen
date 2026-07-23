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
        "Ban dat ten truyen ngan gon (2-6 tu), cung ngon ngu voi truyen (hoac tieng Anh "
        "neu ngon ngu viet la tieng Anh), dua tren noi dung duoc cung cap. "
        "CHI tra ve ten truyen, khong giai thich, khong dau ngoac kep."
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
