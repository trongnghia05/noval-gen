"""Deterministic dialogue pre-check — the cheap gate before the LLM dialogue judge.

Reads the chapter's blueprint dialogue plan and the written prose and flags only
the *obvious, countable* failures (no LLM): a dialogue-heavy chapter that came back
with almost no dialogue, or a chapter that planned speakers but has no quoted speech
at all. Nuanced judgments — thin/off-voice dialogue, invalid characters, whether the
planned intent was achieved — are left to the LLM pass in quality_reviewer.

Returns QualityReviewIssueOut objects (dimension="dialogue") so they slot straight
into the existing _verify_chapter_loop alongside chapter_verifier / quality_reviewer.
"""

import json
import re

from sqlalchemy.orm import Session

from ..db.models import Chapter, Story
from ..schemas import QualityReviewIssueOut

# Fraction of chapter characters that should sit inside quotation marks, per
# planned intensity. `sparse` has no floor — a solitary-introspection chapter is
# allowed to have little or no dialogue by design.
_RATIO_FLOOR = {"heavy": 0.30, "balanced": 0.12, "sparse": 0.0}

# Straight and curly double-quote pairs used across languages.
_QUOTE_SPANS = [
    re.compile(r"“(.+?)”", re.S),  # “ … ”
    re.compile(r'"(.+?)"', re.S),             # " … "
    re.compile(r"«(.+?)»", re.S),  # « … »
]


def _quoted_char_ratio(text: str) -> tuple[float, int]:
    """(fraction of chars inside quotes, number of quoted spans)."""
    if not text:
        return 0.0, 0
    quoted = 0
    spans = 0
    for pat in _QUOTE_SPANS:
        for m in pat.finditer(text):
            quoted += len(m.group(1))
            spans += 1
    return (quoted / len(text), spans)


def check(session: Session, story: Story, chapter: Chapter) -> list[QualityReviewIssueOut]:
    if not chapter.blueprint or not chapter.content:
        return []
    try:
        bp = json.loads(chapter.blueprint)
    except Exception:
        return []

    intensity = (bp.get("dialogue_intensity") or "balanced").lower()
    planned_speakers = {
        s for scene in bp.get("scenes", [])
        for s in (scene.get("speaking_characters") or [])
    }
    ratio, spans = _quoted_char_ratio(chapter.content)
    issues: list[QualityReviewIssueOut] = []

    # A chapter that planned dialogue but produced essentially none.
    if planned_speakers and spans == 0:
        issues.append(QualityReviewIssueOut(
            dimension="dialogue",
            severity="critical",
            description=(
                f"Blueprint planned dialogue for {sorted(planned_speakers)} but the chapter "
                f"contains no quoted speech at all."
            ),
            suggestion="Rewrite so the planned speakers actually converse, in their distinct voices.",
        ))
        return issues  # ratio check below is redundant once there's zero dialogue

    # Dialogue-heavy/balanced chapter that came back far too narration-heavy.
    floor = _RATIO_FLOOR.get(intensity, 0.12)
    if floor > 0 and planned_speakers and ratio < floor:
        issues.append(QualityReviewIssueOut(
            dimension="dialogue",
            severity="critical",
            description=(
                f"Dialogue intensity is '{intensity}' (floor {floor:.0%}) but only "
                f"{ratio:.0%} of the chapter is dialogue — too narration-heavy."
            ),
            suggestion="Dramatize the planned exchanges as live dialogue instead of narrating them.",
        ))

    return issues
