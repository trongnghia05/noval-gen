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

The API is at **`http://localhost:8001/docs`** (port 8001 on host → 8000 in container). Use the Swagger UI to call `POST /api/stories`, `POST /api/stories/{id}/advance`, etc. by hand.

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

`app/orchestrator.py`: `advance(session, story)` runs exactly one step and returns `{"phase", "step", ...}`. Callers repeat until `phase == "COMPLETE"`. Two load-bearing invariants:

- **Checkpoint-before-pending**: a pending continuity/smart-planner checkpoint (`Story.last_checkpoint_chapter` vs. last `done` chapter) is checked **before** scanning for the next pending chapter. Scanning pending first would silently skip a failed checkpoint once all chapters are `done` — this was a real bug found during testing.
- **3 advances per chapter**: `pending → blueprinted → done` maps to three separate `advance()` calls — blueprint, write+summarize, checkpoint — keeping each HTTP call to roughly one LLM call's latency.

### Chapter status flow

```
pending → [blueprint_{N}] → blueprinted → [chapter_{N}] → done → [checkpoint_{N}] (every 5 or last)
```

Each transition is one `advance()` call. The checkpoint step runs at chapter numbers divisible by 5 or at `total_chapters`, and only if `story.last_checkpoint_chapter` is behind.

### Agents (`app/agents/*.py`)

One Python function per root-project sub-agent. System prompts live in `app/prompts/*.md`.

| Agent | Output |
|-------|--------|
| `story_analyzer` | `story.story_bible` (TEXT) |
| `plot_architect` | `story.plot_outline` (TEXT) |
| `character_developer` | `Character` rows in DB |
| `worldbuilder` | `story.world_bible` (TEXT) |
| `chapter_blueprinter` | `chapter.blueprint` (JSON TEXT), sets status → `blueprinted` |
| `chapter_writer` | `chapter.title`, `chapter.content`, `chapter.word_count`, sets status → `done` |
| `chapter_summarizer` | `ChapterSummary` row, updates `WorldState` rows, appends `StateLog` |
| `continuity_editor` | updates `ContinuityLog` row for the story |
| `smart_planner` | updates `SmartPlannerState` row for the story |

**`chapter_blueprinter`** (added after initial build) runs before `chapter_writer` and creates a JSON blueprint stored in `chapter.blueprint` with: `purpose`, `act_position`, `emotional_arc_start/end`, `scenes[]` (each with `goal/conflict/outcome/disaster`), `hook`, `foreshadowing_to_plant`. It derives `act_position` from `chapter_number / total_chapters` ratio ("Act 1" / "Act 2a" / "Act 2b" / "Act 3").

**`chapter_writer`** has two paths:
1. **Scene-by-scene** (primary): if `chapter.blueprint` has `scenes`, it makes one LLM call per scene, passing all previous scenes as explicit context under `"CONTENT ALREADY WRITTEN THIS CHAPTER — do NOT repeat"`. Prevents the model from forgetting earlier scenes mid-generation (a real bug seen with 4000-word single-call generation at ~2000 words). A scene that comes back < 60% of its per-scene word target is expanded inline with a second call before moving to the next scene.
2. **Single-call fallback**: no blueprint, or exception in scene loop.

After all scenes are assembled, a final expand loop runs if the total is still < 85% of `words_per_chapter` (max 2 attempts). The expand prompt explicitly says "same order, no new events" to prevent restructuring.

**`chapter_summarizer`** is called in the same `advance()` call as `chapter_writer`, but **after** `session.commit()` saves the chapter. If the summarizer fails (e.g. rate limit), the chapter is already `done` in the DB with no `ChapterSummary` row — subsequent chapters will write with incomplete memory for that gap.

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

### Slug + title generation (`app/slug.py`)

`slugify(title)` handles Vietnamese diacritics (NFKD + `đ/Đ` special case). `generate_title(provider, model, ...)` calls the model with generous `max_tokens=500` headroom — reasoning models spend part of their budget on invisible chain-of-thought before outputting the actual title.

## Known gotchas (found via actual testing)

- **Non-Anthropic reasoning models leak reasoning text**: OpenRouter's `reasoning: {enabled: true}` only separates thinking tokens for Anthropic models. For other models (e.g. Nvidia Nemotron), reasoning arrives as plain `content.delta` and is captured verbatim in `response.text`. Observed in practice: when Nemotron detected a contradiction between the "already written" scenes and the blueprint for the next scene, it wrote out its internal reasoning ("The user wants me to write Scene 3, but Scene 2 already covered...") as plain prose in the chapter output starting at line 197. Partial mitigation: `chapter_writer.md` prompt instructs the model not to write analysis; a post-processing filter to strip such lines from scene output is **not yet implemented**.
- **Title generator returns verbose reasoning**: `generate_title()` in `app/slug.py` calls the model with `max_tokens=500` but **no** `thinking=True` — reasoning-capable models in non-thinking mode still occasionally return a multi-paragraph deliberation block instead of 2–6 words. The result is a story slug like `the-story-title-depends-on-the-following-considerations`. The call currently has no post-processing to truncate to the first line / first sentence.
- **Free-tier rate limits**: OpenRouter free models return `ResourceExhausted` under shared load. `generate_structured` retries only on bad JSON, not on HTTP 500/429 errors — callers must retry manually. Pattern: `$step--; sleep 15; continue` in a bash advance loop.
- **Summarizer failure after commit**: `session.commit()` is called between `chapter_writer.run()` and `chapter_summarizer.run()` in the orchestrator. A rate-limited summarizer leaves the chapter marked `done` with no `ChapterSummary` row — subsequent chapters write with a gap in their memory context. No auto-recovery exists yet.
- **Missing chapter 1 summary on resume**: If chapter 1's summarizer fails and chapter 2's blueprinter succeeds, resuming the advance loop finds chapter 2 as the next `pending` chapter and never re-runs chapter 1's summarizer.
- **Reasoning token budget starvation**: Reasoning models can spend their entire `max_tokens` on invisible chain-of-thought. Even a "give me a 2-6 word title" call needs headroom (500 tokens minimum, not 30). JSON-mode + `thinking=True` combined is especially prone — give generous budgets.
- **No migration system**: DB schema changes require `docker compose down -v`. Any in-progress story data is lost.
