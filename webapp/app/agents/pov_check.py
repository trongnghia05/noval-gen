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

from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Chapter, Story
from ..llm_json import generate_structured
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
        out = generate_structured(
            PROVIDER, system=system, user_content=user,
            model=AGENT_MODELS.get("pov_check", AGENT_MODELS["quality_reviewer"]),
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
        pov = (json.loads(chapter.blueprint).get("pov_character") or "").strip()
    except Exception:
        pov = ""
    if not pov:
        return []

    narration = _strip_dialogue(chapter.content)
    first = len(_FIRST.findall(narration))
    third = len(_THIRD.findall(narration))

    # Require a clear third-person majority over a non-trivial base, so a valid
    # first-person chapter with lots of "he/she" about others is not flagged.
    if third >= 12 and third > first * 1.5:
        # Code is only a cheap pre-filter here; confirm with the LLM before paying
        # for a rewrite, so a first-person chapter that's merely he/she-heavy about
        # other characters isn't rewritten by mistake.
        if not _llm_confirms_third(story, chapter, pov):
            return []
        return [QualityReviewIssueOut(
            dimension="pov",
            severity="critical",
            description=(
                f"POV drift: source is first-person and this chapter's POV holder is "
                f"{pov}, but the narration reads third-person (I/my={first}, "
                f"he/she={third})."
            ),
            suggestion=(
                f"Rewrite entirely in FIRST PERSON from {pov}'s point of view — every "
                f"narrative sentence about {pov} must use 'I/my', not the character's "
                f"name or he/she."
            ),
        )]
    return []
