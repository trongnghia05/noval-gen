# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A FastAPI + Claude API backend — a prototype for running the repo root's novel-generation pipeline from a web app instead of the Claude Code CLI. It reimplements the root project's `.claude/agents/*.md` sub-agents as Python functions that each make exactly one LLM call, and replaces the root `CLAUDE.md` orchestrator (an LLM re-reading `progress.json` every turn) with a deterministic Python state machine.

All LLM calls are routed through **OpenRouter** (`app/providers/openrouter.py`) rather than the Anthropic API directly — one API key can front any model by OpenRouter slug (`anthropic/claude-opus-4-8`, `nvidia/nemotron-3-ultra-550b-a55b:free`, etc.). This is independent of the repo root's system — see the root `CLAUDE.md` for that.

## Commands

Docker is the primary way to run this (no local Python required):

```bash
cp .env.example .env        # set OPENROUTER_API_KEY, optionally MODEL_* overrides
docker compose up -d --build
docker compose logs -f api
docker compose down          # add -v to also wipe the SQLite + graph volumes
```

The API is at **`http://localhost:8001/docs`** (port 8001 on host → 8000 in container). Use the Swagger UI to call `POST /api/stories`, then `POST /api/stories/{id}/run` to generate the whole novel in the background (poll `GET /api/stories/{id}` for progress), or `POST /api/stories/{id}/advance` to step through it manually.

Without Docker (needs Python 3.10+):

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**DB schema changes require `docker compose down -v`** — there is no migration system. The SQLite file lives at `/data/novelgen.db` inside the container, persisting across `docker compose down` but not `-v`. The CSV knowledge graph files live at `/data/graphs/{story_id}/` (same `novelgen-data` volume).

There is no test suite or linter configured in this sub-project yet.

## Architecture

### Provider abstraction

`app/providers/base.py` defines `LLMProvider.generate(system, user_content, model, max_tokens, thinking, json_mode)` — agents never call an SDK directly. `OpenRouterProvider` is the only implementation. It streams internally even though callers get a single `LLMResponse` — this prevents connection timeouts on slow/reasoning models. It tolerates `LengthFinishReasonError` from the SDK's final-completion parser (the accumulated stream deltas are already the full text).

### Config

`app/config.py` exposes one `PROVIDER` instance and `AGENT_MODELS` — a dict of OpenRouter model slugs keyed by agent name. Each entry is overridable via a `MODEL_<AGENT>` env var (e.g. `MODEL_CHAPTER_WRITER`), so different agents can run on different models without touching code.

### Orchestrator

`app/orchestrator.py` is the single source of truth for both "what step comes next" and "what does step X do" — two other things dispatch through it without duplicating its logic:

- `_decide_next_step(session, story) -> str` — pure decision, no execution. Reads `Story.phase`/`Chapter.status` and returns one of `story_bible | plot_outline | characters | world | verify_planning | planning_complete | checkpoint | blueprint | write_chapter | complete`.
- `run_*_step(session, story) -> dict` — one function per step (`run_story_bible_step`, `run_checkpoint_step`, `run_write_chapter_step`, etc.), the actual execution bodies, collected in `_STEP_EXECUTORS`.
- `advance(session, story)` — the original single-step API: decides, dispatches, returns `{"phase", "step", ...}`. Still used by `POST /stories/{id}/advance` for manual/debug single-stepping — behavior unchanged.

Two load-bearing invariants in `_decide_next_step`, both apply to whichever path is driving it:

- **Checkpoint-before-pending**: a pending continuity/smart-planner checkpoint (`Story.last_checkpoint_chapter` vs. last `done` chapter) is checked **before** scanning for the next pending chapter. Scanning pending first would silently skip a failed checkpoint once all chapters are `done` — this was a real bug found during testing.
- **3 steps per chapter**: `pending → blueprinted → done` is three steps — blueprint, write+summarize, checkpoint.
- **Planning gate**: once all 4 planning artifacts exist but `Story.planning_verified` is still `False`, the next step is `verify_planning` (not `planning_complete`). Phase only flips to WRITING after the gate passes. Set-and-forget like `last_checkpoint_chapter`: the flag being `False` on resume re-runs the whole gate from scratch (`planning_verifier` only `flush()`es; the step commits once at the end).

### Continuous run (`app/graph.py`)

`POST /stories/{id}/run` is the primary way to generate a full novel now — it kicks off a LangGraph `StateGraph` in a FastAPI `BackgroundTasks` job and returns immediately; poll `GET /stories/{id}` (now includes `is_running`) for progress. This replaced the old model of a client repeatedly `POST`-ing `/advance` in a loop (that endpoint still works standalone, for manual single-stepping).

- The graph is a `router` node with conditional edges to one node per step (same names `_decide_next_step` returns); every work node edges back to `router`, and `complete` edges to `END`. Each node opens its own `SessionLocal()`, calls the matching `orchestrator._STEP_EXECUTORS[step]`, commits, closes.
- **No LangGraph checkpointer** — SQLite stays the only state store. Re-invoking the graph after a crash just re-derives "what's next" from `Story.phase`/`Chapter.status`, same as `/advance` always did. `recursion_limit` is set to 10000 (default 25 is nowhere near enough — a 25-chapter novel is 100+ router hops).
- **Auto-retry**: `graph._run_step_with_retry` catches `openai.APIError` (429/5xx/auth) and retries with backoff (5s/20s/60s) — this is what the old bash-loop-with-manual-retry gotcha needed. On retry it re-derives the step via `_decide_next_step` rather than blindly re-invoking the same executor, because a step can partially commit before failing (`write_chapter` commits the chapter, *then* summarizes it — a summarizer failure must not re-run `chapter_writer` on an already-`done` chapter).
- **`Story.is_running`**: guards against starting a second concurrent run for the same story; `POST /run` 409s if already running or already `COMPLETE`. Reset to `False` in a `try/finally` in `_run_story_background` regardless of success or exception, so a crash never leaves a story permanently locked.
- Exhausted retries (e.g. a real auth/rate-limit failure) propagate out of `run_story_to_completion`, are logged, and leave the DB exactly where the last successful commit left it — safe to resume with another `POST /run` (or `/advance` for manual stepping) once the underlying issue is fixed.

### Chapter status flow

```
pending → [blueprint_{N}] → blueprinted → [chapter_{N}] → done → [checkpoint_{N}] (every 5 or last)
```

Each transition is one step (one `/advance` call, or one graph node hop). The checkpoint step runs at chapter numbers divisible by 5 or at `total_chapters`, and only if `story.last_checkpoint_chapter` is behind.

### Agents (`app/agents/*.py`)

One Python function per root-project sub-agent. System prompts live in `app/prompts/*.md`.

| Agent | Output |
|-------|--------|
| `story_analyzer` | `story.story_bible` (TEXT) |
| `plot_architect` | `story.plot_outline` (TEXT) |
| `character_developer` | `Character` rows in DB |
| `worldbuilder` | `story.world_bible` (TEXT) |
| `planning_verifier` | `PlanningVerifyLog` rows; may regenerate any of the 4 planning artifacts; sets `story.planning_verified` |
| `chapter_blueprinter` | `chapter.blueprint` (JSON TEXT), sets status → `blueprinted` |
| `chapter_writer` | `chapter.title`, `chapter.content`, `chapter.word_count`, sets status → `done` |
| `chapter_verifier` | appends `ChapterVerifyLog` rows; may re-run `chapter_writer` in place |
| `quality_reviewer` | appends `ChapterVerifyLog` rows (dimension-tagged); may re-run `chapter_writer` in place |
| `chapter_summarizer` | `ChapterSummary` row, updates `WorldState` rows, appends `StateLog` |
| `continuity_editor` | updates `ContinuityLog` row for the story |
| `smart_planner` | updates `SmartPlannerState` row for the story |

**`chapter_blueprinter`** (added after initial build) runs before `chapter_writer` and creates a JSON blueprint stored in `chapter.blueprint` with: `purpose`, `act_position`, `emotional_arc_start/end`, `scenes[]` (each with `goal/conflict/outcome/disaster`), `hook`, `foreshadowing_to_plant`. It derives `act_position` from `chapter_number / total_chapters` ratio ("Act 1" / "Act 2a" / "Act 2b" / "Act 3").

**`chapter_writer`** has two paths:
1. **Scene-by-scene** (primary): if `chapter.blueprint` has `scenes`, it makes one LLM call per scene, passing all previous scenes as explicit context under `"CONTENT ALREADY WRITTEN THIS CHAPTER — do NOT repeat"`. Prevents the model from forgetting earlier scenes mid-generation (a real bug seen with 4000-word single-call generation at ~2000 words). A scene that comes back < 60% of its per-scene word target is expanded inline with a second call before moving to the next scene.
2. **Single-call fallback**: no blueprint, or exception in scene loop.

After all scenes are assembled, a final expand loop runs if the total is still < 85% of `words_per_chapter` (max 2 attempts). The expand prompt explicitly says "same order, no new events" to prevent restructuring.

**`chapter_verifier`** (added after initial build) runs after every single chapter — not every 5 like `continuity_editor` — as a fast, targeted gate: it reads the last 3 chapters (including the one just written), all `Character` profiles, the full `WorldState` snapshot (covers relationships/plot-threads/timeline too — everything under one `entity_type` column), and any open `ContinuityLog` issues, then checks only the just-written chapter against that established truth. Every issue found (critical or minor) is appended to `ChapterVerifyLog` — never overwritten, so the full history survives. A `critical` issue (real contradiction — a dead character reappearing, timeline/relationship reversal, identity mix-up) triggers exactly **one** automatic full rewrite of that chapter via `chapter_writer`, then moves on — no re-verify-after-rewrite loop, to keep cost/latency bounded and avoid infinite retry cycles. The flagged critical issues are passed to `chapter_writer` as a `feedback` block (prepended to its context) so the rewrite targets the specific contradictions instead of rewriting blind. `minor` issues are logged only, no rewrite. This is additive to — not a replacement for — the existing 5-chapter `continuity_editor`/`smart_planner` checkpoint, which does a deeper audit (5-chapter window, cross-checks `StateLog`) that a fast per-chapter check isn't meant to replace.

**`planning_verifier`** (added after initial build) is a one-time quality gate between planning and writing — it runs after all 4 planning artifacts exist, before phase flips to WRITING. It reads the full `story_bible`/`plot_outline`/`characters`/`world` (plus the source text for REWRITE) and checks them against professional novel-craft criteria (structure/pacing/setup-payoff, character want-vs-need/arc/motivation, world internal consistency, cross-artifact consistency, and — for REWRITE — that the original plot skeleton is preserved). Prompt: `app/prompts/planning_verifier.md`. Every issue is appended to `PlanningVerifyLog` (append-only history). Each issue names the single `artifact` responsible for the fix.

Its auto-fix loop is **per-artifact bounded** (counted independently per file): a `critical` issue triggers up to **3 feedback-guided rewrites** of that artifact's own agent (`plot_architect`, `story_analyzer`, etc.) — the flagged descriptions are passed back as `feedback` so the agent knows exactly what to fix — re-verifying between each; if still critical after 3, **one from-scratch regen** (no feedback); if still critical after that, the issue is **accepted + logged** and the pipeline moves on (hard stop — never an infinite loop). `characters` is safe to wipe+rebuild here (`session.query(Character)…delete()` then `character_developer.run`) because no chapter references those rows yet at the gate — the "keep initial characters stable" rule only applies once WRITING has started. Regeneration order follows dependency order (`story_bible → plot_outline → characters → world`).

**`quality_reviewer`** (added after initial build) runs after `chapter_verifier` and before `chapter_summarizer`, on **every** chapter. Two dimensions: **quality** (all input types) — truncated/unfinished prose, repetition, incoherence, chapter-boundary drift, leaked AI analysis text, word count far below target; and **originality** (REWRITE only) — compares the chapter against its matching source chapter (via `length_calc.split_source_chapters`, indexed by chapter number) and flags *surface* copying (leaked original names, copied phrasing, un-reskinned setting) while explicitly **not** penalizing shared plot (that's the intended REWRITE behavior). A `critical` issue triggers exactly one feedback-guided `chapter_writer` rewrite (bounded, no re-review loop), same contract as `chapter_verifier`. It reuses `ChapterVerifyLog` for history with the dimension tagged into the description (`[quality]` / `[originality]`) — no schema change. Prompt: `app/prompts/quality_reviewer.md`.

**`chapter_summarizer`** is called in the same `advance()` call as `chapter_writer`, but **after** `session.commit()` saves the chapter, and now also after `chapter_verifier` has had a chance to fix it. If the summarizer fails (e.g. rate limit), the chapter is already `done` in the DB with no `ChapterSummary` row — subsequent chapters will write with incomplete memory for that gap.

### JSON agents

Every agent other than `chapter_writer` returns structured data. `app/llm_json.py:generate_structured()` calls `provider.generate(..., json_mode=True)` and validates against a Pydantic schema from `app/schemas.py`. It retries once (with the parse error appended to the prompt) if the model returns invalid JSON.

### DB models (`app/db/models.py`)

- `WorldState` has `UniqueConstraint(story_id, entity_type, entity_key, field)` — "overwrite, don't accumulate" is a DB constraint, not a prompt instruction.
- `StateLog` is the append-only audit trail.
- `ContinuityLog` and `SmartPlannerState` are one row per story — living checkpoint state, overwritten each time.
- `Character.aliases` is populated once by `character_developer` and used by `continuity_editor` to avoid flagging two names for the same person as a contradiction.

### CSV Knowledge Graph (`app/csv_graph.py`)

A five-file graph in `/data/graphs/{story_id}/` that gives agents a compact, token-efficient view of world state:

| File | Mode | Schema |
|------|------|--------|
| `characters.csv` | overwrite in-place | name, role, status, location, emotional_state, arc_stage, aliases |
| `relationships.csv` | overwrite in-place | entity_a, entity_b, type, strength, trust, last_interaction |
| `plot_threads.csv` | overwrite in-place | thread_id, title, status, urgency, last_advanced_chapter |
| `relationship_history.csv` | append-only | chapter, entity_a, entity_b, event, strength_delta |
| `timeline.csv` | append-only | chapter, beat, characters_involved, location, consequence |

`character_voices.md` (one file per story) is written once by `character_blueprinter` and read by `chapter_writer`.

`csv_graph.graph_exists(story_id)` returns `False` until the graph is first populated; agents check this before including graph sections in their prompts.

### Context builder (`app/context_builder.py`)

Turns DB rows into the compact text blocks agents read — the Python equivalent of `world-state.md` / `chapter-summaries.md` / `continuity-log.md` in the CLI version.

### Length calculation (`app/length_calc.py`)

Mirrors the root `CLAUDE.md`'s dynamic length logic: for `REWRITE` input, `words_per_chapter` is derived from the source material's actual word/chapter density rather than the 4000-word system default.

### REWRITE plot preservation (chapter-by-chapter map)

For `REWRITE`, the goal is to **keep the source's plot/events/sequence but replace the surface** (character names, setting, prose). The mechanism is a plot map carried inside the story-bible — no extra DB column:

- **`story_analyzer`** (REWRITE branch) extracts a **chapter-by-chapter plot map** of the source (main event / conflict / turning point / consequence per source chapter) plus a **relationship map**, and appends both to `story.story_bible` — already re-skinned with the *new* invented names/setting but preserving the original event order. `max_tokens` scales with chapter count (the map is long).
- **`plot_architect`** receives `input_type` and, for REWRITE, **follows that map in order instead of the generic 24/28/24/24 three-act template** — mapping each map-chapter to an outline chapter (1-1 when `total_chapters` equals the source's chapter count). The three-act template applies only to IDEA/PREMISE.
- **`character_developer`** keeps the bible's relationship structure (new names, same relational bonds).
- **`planning_verifier`** cross-checks that the outline follows the map (and the source) in order; a divergence is a `critical` on `plot_outline` (or `story_bible` if the map itself is missing).

This closes a real gap: previously the source's detailed plot was lost after `story_analyzer` distilled it to a thin premise, and `plot_architect` regenerated a generic structure that only shared theme, not the actual sequence of events.

### Slug + title generation (`app/slug.py`)

`slugify(title)` handles Vietnamese diacritics (NFKD + `đ/Đ` special case). `generate_title(provider, model, ...)` calls the model with generous `max_tokens=500` headroom — reasoning models spend part of their budget on invisible chain-of-thought before outputting the actual title.

## Known gotchas (found via actual testing)

- **Non-Anthropic reasoning models leak reasoning text**: OpenRouter's `reasoning: {enabled: true}` only separates thinking tokens for Anthropic models. For other models (e.g. Nvidia Nemotron), reasoning arrives as plain `content.delta` and is captured verbatim in `response.text`. Observed in practice: when Nemotron detected a contradiction between the "already written" scenes and the blueprint for the next scene, it wrote out its internal reasoning ("The user wants me to write Scene 3, but Scene 2 already covered...") as plain prose in the chapter output starting at line 197. Partial mitigation: `chapter_writer.md` prompt instructs the model not to write analysis; a post-processing filter to strip such lines from scene output is **not yet implemented**.
- **Title generator returns verbose reasoning**: `generate_title()` in `app/slug.py` calls the model with `max_tokens=500` but **no** `thinking=True` — reasoning-capable models in non-thinking mode still occasionally return a multi-paragraph deliberation block instead of 2–6 words. The result is a story slug like `the-story-title-depends-on-the-following-considerations`. The call currently has no post-processing to truncate to the first line / first sentence.
- **Free-tier rate limits**: OpenRouter free models return `ResourceExhausted` under shared load. `generate_structured` retries only on bad JSON, not on HTTP 500/429 errors. The continuous run (`POST /run` → `app/graph.py`) now auto-retries these with backoff; the manual single-step `/advance` API still does not — callers driving it directly must retry manually (pattern: `$step--; sleep 15; continue` in a bash advance loop).
- **Summarizer failure after commit**: `session.commit()` is called between `chapter_writer.run()` and `chapter_summarizer.run()` in the orchestrator. A rate-limited summarizer leaves the chapter marked `done` with no `ChapterSummary` row — subsequent chapters write with a gap in their memory context. `graph.py`'s retry wrapper re-derives the next step before each retry (since `write_chapter` isn't safely re-runnable once the chapter commit landed), so it correctly avoids re-writing the chapter, but it also doesn't retry the summarizer specifically — this gap is unchanged, no auto-recovery exists yet.
- **Missing chapter 1 summary on resume**: If chapter 1's summarizer fails and chapter 2's blueprinter succeeds, resuming the advance loop finds chapter 2 as the next `pending` chapter and never re-runs chapter 1's summarizer.
- **Reasoning token budget starvation**: Reasoning models can spend their entire `max_tokens` on invisible chain-of-thought. Even a "give me a 2-6 word title" call needs headroom (500 tokens minimum, not 30). JSON-mode + `thinking=True` combined is especially prone — give generous budgets.
- **No migration system**: DB schema changes require `docker compose down -v`. Any in-progress story data is lost.
