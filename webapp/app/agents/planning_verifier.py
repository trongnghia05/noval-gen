"""Planning gate — verifies the 4 planning artifacts against professional
novel-craft criteria BEFORE any chapter is written, and auto-fixes what it
flags critical.

Per-artifact bounded retry (counted independently per file):
  1. verify all 4 artifacts
  2. for each artifact with a critical issue:
       - up to 3 feedback-guided REWRITES (the flagged issues are passed back
         to the artifact's own agent as `feedback`), re-verifying between each
       - if still critical after 3, one FROM-SCRATCH regen (no feedback)
       - if still critical after that, ACCEPT + log, move on
Characters are safe to wipe+rebuild here because no chapter references them yet
at the gate — the "keep initial characters" rule only applies once WRITING starts.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from .. import context_builder
from ..config import AGENT_MODELS, PROVIDER
from ..db.models import Character, PlanningVerifyLog, Story
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt
from ..schemas import PlanningVerifierOutput
from . import character_developer, plot_architect, story_analyzer, worldbuilder

MAX_REWRITES = 3  # feedback-guided rewrites per artifact before a from-scratch regen
# story_bible first, then the artifacts derived from it, then characters (reads both).
ARTIFACTS = ("story_bible", "plot_outline", "characters", "world")


def _verify(session: Session, story: Story) -> PlanningVerifierOutput:
    system = load_prompt("planning_verifier")
    user_content = f"""Ngôn ngữ: {story.language}
Loại input: {story.input_type}
total_chapters: {story.total_chapters}
words_per_chapter: {story.words_per_chapter}

## story-bible.md
---
{story.story_bible}
---

## plot-outline.md
---
{story.plot_outline}
---

## characters.md (hồ sơ đầy đủ)
---
{context_builder.format_characters(session, story.id)}
---

## world.md
---
{story.world_bible}
---
"""
    if story.input_type == "REWRITE":
        user_content += (
            "\n## Truyện gốc (đối chiếu — bản kế hoạch phải GIỮ đúng khung cốt truyện gốc)\n"
            f"---\n{story.source_content}\n---\n"
        )
    return generate_structured(
        PROVIDER,
        system=system,
        user_content=user_content,
        model=AGENT_MODELS["planning_verifier"],
        schema=PlanningVerifierOutput,
        max_tokens=8192,
        thinking=True,
    )


def _feedback_for(output: PlanningVerifierOutput, artifact: str) -> str:
    return "\n".join(
        f"- {i.description} → SỬA THÀNH: {i.suggestion}"
        for i in output.issues
        if i.artifact == artifact and i.severity == "critical"
    )


def _regenerate(session: Session, story: Story, artifact: str, feedback: str | None) -> None:
    if artifact == "story_bible":
        story.story_bible = story_analyzer.run(story, feedback=feedback)
    elif artifact == "plot_outline":
        story.plot_outline = plot_architect.run(story, feedback=feedback)
    elif artifact == "world":
        story.world_bible = worldbuilder.run(story, feedback=feedback)
    elif artifact == "characters":
        # Wipe + rebuild is safe pre-WRITING: nothing references these rows yet,
        # and character_developer re-inits the CSV graph from scratch.
        session.query(Character).filter_by(story_id=story.id).delete()
        character_developer.run(session, story, feedback=feedback)


def run(session: Session, story: Story) -> None:
    rewrites: dict[str, int] = defaultdict(int)  # feedback rewrites done per artifact
    fresh_done: set[str] = set()                 # artifacts that got their from-scratch regen

    while True:
        output = _verify(session, story)
        critical = {i.artifact for i in output.issues if i.severity == "critical"}

        # Decide this round's action per critical artifact (also used for the log).
        round_action: dict[str, str] = {}
        for art in ARTIFACTS:
            if art not in critical:
                continue
            if rewrites[art] < MAX_REWRITES:
                round_action[art] = f"rewrite_{rewrites[art] + 1}"
            elif art not in fresh_done:
                round_action[art] = "regenerated_fresh"
            else:
                round_action[art] = "accepted"

        # Append every issue found this round to the audit log.
        for issue in output.issues:
            action = round_action.get(issue.artifact, "logged_only") if issue.severity == "critical" else "logged_only"
            session.add(
                PlanningVerifyLog(
                    story_id=story.id,
                    artifact=issue.artifact,
                    severity=issue.severity,
                    description=issue.description,
                    suggestion=issue.suggestion,
                    action_taken=action,
                )
            )
        session.flush()

        # Artifacts we can still act on this round (dependency order preserved).
        actionable = [a for a in ARTIFACTS if a in critical and round_action[a] != "accepted"]
        if not actionable:
            return  # clean, or every critical artifact is exhausted → hard stop

        for art in actionable:
            if rewrites[art] < MAX_REWRITES:
                _regenerate(session, story, art, _feedback_for(output, art))
                rewrites[art] += 1
            else:  # exhausted feedback rewrites → one clean regen, no feedback
                _regenerate(session, story, art, None)
                fresh_done.add(art)
        session.flush()
