"""Deterministic POV-person pre-check — no LLM.

The source-fidelity fix makes REWRITE chapters first-person with an alternating
`pov_character`. Over a long run the writer tends to *drift* back to third-person
narration in later chapters (observed: a first-person source rendered 16/20 first
person, with a third-person stretch near the end). This catches that drift the same
way dialogue_check catches missing dialogue: strip the dialogue, then compare
first- vs third-person pronoun frequency in the *narration*. If the source is
first-person and the chapter's narration is dominantly third-person, that's a
critical drift → one targeted rewrite via the existing _verify_chapter_loop.

Only fires when we can be confident: REWRITE + source_spirit POV says first-person
+ blueprint set a pov_character + third-person clearly dominates a non-trivial
narration. A first-person chapter naturally contains "he/she" about *other*
characters, so we require third to clearly outweigh first, not merely appear.
"""

import json
import re

from pydantic import BaseModel
from typing import Literal

from sqlalchemy.orm import Session

from . import _common
from ..config import AGENT_MODELS
from ..db.models import Chapter, Story
from ..schemas import QualityReviewIssueOut

# Dialogue spans to strip before counting narration pronouns.
_QUOTE_SPANS = [
    re.compile(r"“(.+?)”", re.S),
    re.compile(r'"(.+?)"', re.S),
    re.compile(r"«(.+?)»", re.S),
]

# First- vs third-person narration markers (English primary; a few Vietnamese
# first-person forms so a VN-language rewrite isn't mis-flagged as third).
_FIRST = re.compile(r"\b(i|me|my|mine|myself|we|us|our|ours|tôi|mình|tớ)\b", re.I)
_THIRD = re.compile(r"\b(he|she|him|her|his|hers|himself|herself)\b", re.I)

_FIRST_PERSON_HINT = re.compile(r"first[\s-]?person|ngôi\s*(thứ\s*)?(nhất|1)", re.I)


def _expects_first_person(story: Story) -> bool:
    ss = story.source_spirit or ""
    # Look only at the POV: section so a stray mention elsewhere doesn't trigger it.
    for line in ss.splitlines():
        if line.strip().upper().startswith("POV"):
            return bool(_FIRST_PERSON_HINT.search(line))
    return False


def _strip_dialogue(text: str) -> str:
    for pat in _QUOTE_SPANS:
        text = pat.sub(" ", text)
    return text


class _PovClassifyOut(BaseModel):
    person: Literal["first", "third"]  # narration's grammatical person for the POV character


def _llm_confirms_third(story: Story, chapter: Chapter, pov: str) -> bool:
    """Cheap LLM confirmation, run ONLY after the code pre-filter fires. Guards
    against a false positive — a valid first-person chapter that merely contains
    lots of he/she about *other* characters. The code count can't tell "narrated
    as 'I'" from "narrated about the POV char in third person"; the LLM can.
    On any error, fall back to the code verdict (assume the flag stands)."""
    system = (
        "You classify narrative point of view. Ignore text inside quotation marks "
        "(dialogue). Look ONLY at the narration. Decide whether the narration is written "
        "in FIRST person (the narrator IS the POV character, using 'I/me/my') or THIRD "
        "person (the narrator refers to the POV character by name or as he/she). "
        "Other characters appearing as he/she does NOT make it third person — what "
        "matters is how the POV character themselves is narrated. Answer with person only."
    )
    user = f"POV character: {pov}\n\nChapter narration:\n{chapter.content[:6000]}"
    try:
        out = _common.call_agent(
            "pov_check", system=system, user_content=user,
            model_fallback="quality_reviewer",
            schema=_PovClassifyOut, max_tokens=200, thinking=False,
        )
        return out.person == "third"
    except Exception:
        return True  # can't confirm -> trust the code flag rather than silently skip


def check(session: Session, story: Story, chapter: Chapter) -> list[QualityReviewIssueOut]:
    if story.input_type != "REWRITE" or not chapter.content or not chapter.blueprint:
        return []
    if not _expects_first_person(story):
        return []
    try:
        bp = json.loads(chapter.blueprint)
    except Exception:
        return []

    multi = [p for p in (bp.get("pov_characters") or []) if p]
    is_multi = len(multi) > 1
    pov_desc = ", ".join(multi) if is_multi else (bp.get("pov_character") or "").strip()
    if not pov_desc:
        return []

    issues: list[QualityReviewIssueOut] = []

    # Multi-POV: a real POV switch must be marked with a '---' section break. Zero
    # breaks means the chapter almost certainly collapsed to a single POV (the exact
    # failure the multi-POV path exists to prevent). The writer is explicitly told to
    # use '---', so requiring it here is safe.
    if is_multi and len(re.findall(r"(?m)^\s*-{3,}\s*$", chapter.content)) == 0:
        issues.append(QualityReviewIssueOut(
            dimension="pov",
            severity="critical",
            description=(
                f"Multi-POV chapter (POV holders: {pov_desc}) has no '---' section "
                f"break — the POV switch is missing; the chapter likely collapsed to a "
                f"single POV."
            ),
            suggestion=(
                f"Split the chapter into segments, one per POV holder ({pov_desc}), each "
                f"separated by a '---' line and written in that character's first person."
            ),
        ))

    # Both single- and multi-POV: the source is first-person, so the NARRATION must be
    # first-person dominant either way (each multi-POV segment is still first person).
    # A clear third-person majority means it drifted to third person.
    narration = _strip_dialogue(chapter.content)
    first = len(_FIRST.findall(narration))
    third = len(_THIRD.findall(narration))
    if third >= 12 and third > first * 1.5:
        # Code is only a cheap pre-filter; confirm with the LLM before paying for a
        # rewrite, so a first-person chapter merely he/she-heavy about OTHER characters
        # isn't rewritten by mistake.
        if _llm_confirms_third(story, chapter, pov_desc):
            issues.append(QualityReviewIssueOut(
                dimension="pov",
                severity="critical",
                description=(
                    f"POV drift: source is first-person (POV: {pov_desc}) but the "
                    f"narration reads third-person (I/my={first}, he/she={third})."
                ),
                suggestion=(
                    "Rewrite in FIRST PERSON: "
                    + (f"each segment from its POV holder's point of view ('I/my'), "
                       f"never naming the current POV character or using he/she."
                       if is_multi else
                       f"every narrative sentence about {pov_desc} must use 'I/my', not "
                       f"the character's name or he/she.")
                ),
            ))
    return issues
