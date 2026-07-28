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
# 3 feedback + 1 from-scratch + 1 final verify = 5 iterations max per artifact.
# Derived so this cap stays correct if MAX_REWRITES ever changes.
MAX_ITERATIONS = MAX_REWRITES + 2
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
        # 8192 truncated the JSON mid-string on a large story (many issues across 4
        # artifacts) -> "Unterminated string" -> crash. Same headroom fix as the
        # blueprinter; output is structured findings, not prose, so this is plenty.
        max_tokens=16384,
        thinking=False,  # verification is pattern-matching, not creative reasoning
    )


def _feedback_for(output: PlanningVerifierOutput, artifact: str) -> str:
    return "\n".join(
        f"- {i.description} → SỬA THÀNH: {i.suggestion}"
        for i in output.issues
        if i.artifact == artifact and i.severity == "critical"
    )


def _regenerate(session: Session, story: Story, artifact: str, feedback: str | None) -> None:
    from .. import context_builder as cb

    if artifact == "story_bible":
        if story.input_type == "REWRITE":
            # For REWRITE, story_bible derives from new-graph names via
            # _rewrite_story_bible — never from re-analyzing the source content.
            # story_analyzer.run() would: (a) contaminate story_bible with source
            # character names, (b) wipe all source EVENT nodes accumulated during
            # the 30 graph_extract steps (they share graph_type="source" and
            # _clear_graph deletes the whole type).
            from .new_graph_builder import _rewrite_story_bible, _verify_story_bible
            _rewrite_story_bible(session, story, world_design=None, feedback=feedback)
            _verify_story_bible(session, story, world_design=None)
            # NOTE: We intentionally do NOT rebuild plot_outline/world_bible here.
            # After graph_verifier completes, all character names are fixed in the
            # new graph. _rewrite_story_bible() uses exactly those names each time,
            # so only the prose changes between rewrites (not the names). The
            # plot_outline/world_bible built by the orchestrator's dedicated steps
            # (which also use the same graph names) remain consistent without a
            # rebuild. Rebuilding them here caused repeated 48000-token
            # plot_architect responses across N iterations → OOM crashes.
            # If the verifier later flags plot_outline/world inconsistency, it
            # will dispatch _regenerate("plot_outline") or _regenerate("world")
            # in that specific iteration — one targeted call, not N×call.
        else:
            story_analyzer.run(session, story, feedback=feedback)
    elif artifact == "plot_outline":
        graph_type = "new" if story.new_graph_built else "source"
        graph_ctx = cb.format_story_graph(session, story.id, graph_type=graph_type)
        story.plot_outline = plot_architect.run(story, feedback=feedback, story_graph=graph_ctx)
    elif artifact == "world":
        graph_ctx = ""
        if story.new_graph_built:
            graph_ctx = cb.format_story_graph(session, story.id, graph_type="new")
        story.world_bible = worldbuilder.run(story, feedback=feedback, story_graph=graph_ctx)
    elif artifact == "characters":
        # Wipe + rebuild is safe pre-WRITING: nothing references these rows yet.
        # synchronize_session="fetch" loads objects into identity map before
        # deleting, so SQLAlchemy's unit-of-work doesn't treat them as "pending
        # inserts" on the next autoflush. The explicit flush ensures the DELETE
        # hits SQLite before new rows with the same names are added.
        session.query(Character).filter_by(story_id=story.id).delete(synchronize_session="fetch")
        session.flush()
        # REWRITE rebuilds deterministically from the graph (single source of
        # truth); IDEA/PREMISE re-runs the LLM character_developer with feedback.
        if story.new_graph_built:
            from .new_graph_builder import build_characters_from_graph
            build_characters_from_graph(session, story)
        else:
            character_developer.run(session, story, feedback=feedback)


def run(session: Session, story: Story) -> None:
    rewrites: dict[str, int] = defaultdict(int)  # feedback rewrites done per artifact
    fresh_done: set[str] = set()                 # artifacts that got their from-scratch regen

    for _iter in range(MAX_ITERATIONS):
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
        # Commit (not just flush) after each iteration so the next iteration
        # starts with a clean session — prevents stale identity-map objects from
        # causing autoflush UNIQUE violations when characters are rebuilt.
        session.commit()
