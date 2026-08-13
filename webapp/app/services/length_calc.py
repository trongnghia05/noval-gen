import re

# Matches "Chapter 12", "CHƯƠNG 5", "Chương 5:", "# Chương 5: ..." at the
# start of a line (optionally behind a markdown heading marker — our own
# GET /manuscript output and most pasted source material use "# Chapter N"),
# and optionally behind a leading chapter-index number glued to the marker —
# e.g. scraped sources dumped as "#1 Chapter 1 - Title" rather than "# Chapter 1".
# — used only to estimate the source novel's chapter count for REWRITE mode.
_CHAPTER_MARKER_RE = re.compile(r"(?im)^\s*#{0,6}\s*\d*\s*(chapter|chương)\s+\d+")

DEFAULT_TOTAL_CHAPTERS = 25
DEFAULT_TARGET_WORDS = 100_000
DEFAULT_WORDS_PER_CHAPTER = 4_000


def split_source_chapters(source_content: str) -> list[str]:
    """Split a source novel into per-chapter chunks by chapter markers, used by
    quality_reviewer to compare a rewritten chapter against its source chapter.
    Each chunk is the marker line plus its body up to the next marker. No marker
    found → one chunk with the whole text (or empty list if the text is blank)."""
    matches = list(_CHAPTER_MARKER_RE.finditer(source_content))
    if not matches:
        stripped = source_content.strip()
        return [stripped] if stripped else []
    chunks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source_content)
        chunks.append(source_content[m.start():end].strip())
    return chunks


def compute_length(
    input_type: str,
    source_content: str,
    desired_chapters: int | None,
    desired_words: int | None,
) -> tuple[int, int, int]:
    """Returns (total_chapters, target_words, words_per_chapter).

    Mirrors the logic in CLAUDE.md's "BUOC KHOI DONG" step: for REWRITE,
    density is derived from the source material's own word/chapter ratio
    instead of the system default, so a short-chapter source doesn't get
    inflated to 4000 words/chapter.
    """
    if input_type == "REWRITE":
        source_words = len(source_content.split())
        source_chapter_count = len(_CHAPTER_MARKER_RE.findall(source_content)) or 1
        source_words_per_chapter = max(1, round(source_words / source_chapter_count))

        if desired_chapters:
            total_chapters = desired_chapters
            words_per_chapter = source_words_per_chapter
            target_words = total_chapters * words_per_chapter
        elif desired_words:
            target_words = desired_words
            words_per_chapter = source_words_per_chapter
            total_chapters = max(1, round(desired_words / words_per_chapter))
        else:
            total_chapters = source_chapter_count
            words_per_chapter = source_words_per_chapter
            target_words = total_chapters * words_per_chapter
        return total_chapters, target_words, words_per_chapter

    # IDEA / PREMISE — no source density to measure, use the system default.
    if desired_chapters:
        total_chapters = desired_chapters
        words_per_chapter = DEFAULT_WORDS_PER_CHAPTER
        target_words = total_chapters * words_per_chapter
    elif desired_words:
        target_words = desired_words
        total_chapters = max(1, round(desired_words / DEFAULT_WORDS_PER_CHAPTER))
        words_per_chapter = round(desired_words / total_chapters)
    else:
        total_chapters = DEFAULT_TOTAL_CHAPTERS
        target_words = DEFAULT_TARGET_WORDS
        words_per_chapter = DEFAULT_WORDS_PER_CHAPTER
    return total_chapters, target_words, words_per_chapter
