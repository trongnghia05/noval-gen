"""Deterministic prose checks (no LLM) — cheap immersion-breaking-leak gate.

Catches production/meta references that leaked into the fiction (e.g. "from
Chapter 16", "the blueprint", "scene 3"), which break reader immersion. Runs in
the chapter verify loop next to dialogue_check; a hit is critical → rewrite.
"""

import re

from sqlalchemy.orm import Session

from ..db.models import Chapter, Story
from ..schemas import QualityReviewIssueOut

# Patterns that should never appear in narrative prose. Case-insensitive.
# NOTE: we deliberately do NOT ban the bare word "scene"/"summary" — they occur
# naturally in prose ("a scene of ruin"). Only meta-specific forms are flagged.
_META_PATTERNS = [
    re.compile(r"\bchapter\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bchương\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bscene\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bblueprint\b", re.IGNORECASE),
    re.compile(r"\bbeat[_\s]type\b", re.IGNORECASE),
    re.compile(r"\bstate[_\s]delta\b", re.IGNORECASE),
    re.compile(r"\bthe (?:plot )?outline\b", re.IGNORECASE),
    re.compile(r"\bthe summary\b", re.IGNORECASE),
]


def check(session: Session, story: Story, chapter: Chapter) -> list[QualityReviewIssueOut]:
    text = chapter.content or ""
    if not text:
        return []
    # Skip the first line — it is the legitimate "# Chapter X: Title" heading.
    lines = text.split("\n")
    body = "\n".join(lines[1:]) if lines and lines[0].lstrip().startswith("#") else text

    hits: list[str] = []
    for pat in _META_PATTERNS:
        for m in pat.finditer(body):
            hits.append(m.group(0))
    if not hits:
        return []

    uniq = sorted(set(h.strip() for h in hits))
    return [QualityReviewIssueOut(
        dimension="quality",
        severity="critical",
        description=(
            "Prose references production/meta artifacts that break immersion: "
            + ", ".join(f'"{h}"' for h in uniq[:8])
            + ". Rewrite these to describe the actual in-story content instead "
            "(e.g. recall the event itself, not 'Chapter 16')."
        ),
        suggestion="Remove all meta references; never name chapter numbers, scenes, "
                   "blueprint, or outline inside the narrative.",
    )]
