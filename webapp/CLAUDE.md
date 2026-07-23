# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A FastAPI + Claude API backend — a prototype for running the repo root's novel-generation pipeline from a web app instead of the Claude Code CLI. It reimplements the root project's `.claude/agents/*.md` sub-agents as Python functions that each make exactly one LLM call, and replaces the root `CLAUDE.md` orchestrator (an LLM re-reading `progress.json` every turn) with a deterministic Python state machine.

Phase 1 routes every agent through **OpenRouter** (`app/providers/openrouter.py`) rather than the Anthropic API directly, so one API key can front any model by OpenRouter slug (`anthropic/claude-opus-4.8`, `openai/gpt-5`, etc.) while the provider abstraction is exercised. This is independent of the repo root's system — see the root `CLAUDE.md` for that.

## Commands

Docker is the primary way to run this (no local Python required):

```bash
cp .env.example .env        # then set OPENROUTER_API_KEY (and optionally per-agent MODEL_* overrides)
docker compose up -d --build
docker compose logs -f api
docker compose down          # add -v to also wipe the SQLite volume
```

Without Docker (needs Python 3.10+ for `X | None` syntax):

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Either way, the API is self-documenting at `http://localhost:8000/docs` (FastAPI/Swagger) — use it to call `POST /api/stories`, `POST /api/stories/{id}/advance`, etc. by hand.

There is no test suite or linter configured in this sub-project yet.

## Architecture

**Provider abstraction** (`app/providers/base.py`): every agent calls `LLMProvider.generate(system, user_content, model, max_tokens, thinking, json_mode)` — never an SDK directly. `OpenRouterProvider` is the only implementation right now; adding a direct `AnthropicProvider` later means writing one new class and pointing `config.PROVIDER` at it, with zero changes to `app/agents/*.py`.

`OpenRouterProvider.generate` streams internally even though callers get a single `LLMResponse` back — non-streamed calls to slow/reasoning models risk the connection dying before a multi-minute response completes, surfacing as a truncated-JSON error. It also tolerates `LengthFinishReasonError` from the SDK's final-completion parser, since the accumulated stream deltas are already the text that matters.

**`app/config.py`**: one `PROVIDER` instance + `AGENT_MODELS`, a dict of OpenRouter model slugs keyed by agent name, each overridable via a `MODEL_<AGENT>` env var. This is how different agents can run on different models (e.g. a cheap model for `chapter_summarizer`, a stronger one for `chapter_writer`) without touching code.

**`app/orchestrator.py`**: `advance(session, story)` runs exactly one step and is safe to call repeatedly — the web equivalent of re-running "bắt đầu" to resume. Two things are non-obvious and load-bearing:
- A pending continuity/smart-planner checkpoint (`Story.last_checkpoint_chapter` vs. the last `done` chapter's number) is checked **before** looking for the next pending chapter. Checking "any chapters pending?" first would let a failed checkpoint call get silently skipped forever once every chapter is marked `done` — this was a real bug found by testing against a flaky free model, not a hypothetical.
- Chapter-writing and the checkpoint are separate steps/separate `advance()` calls on purpose, even though both can fire for the same chapter number — this keeps each HTTP call to roughly one LLM call's worth of latency instead of chaining three or four.

**`app/agents/*.py`**: one function per root-project sub-agent (`story_analyzer`, `plot_architect`, `character_developer`, `worldbuilder`, `chapter_writer`, `chapter_summarizer`, `continuity_editor`, `smart_planner`), each doing exactly one `provider.generate()` call plus reading/writing the DB. System prompts live in `app/prompts/*.md`, adapted from the root project's `.claude/agents/*.md` — same content, but reframed from "read this file path" to "the data is in the user message below."

Output handling splits by agent:
- `chapter_writer` returns free-form prose. Its first line must be a chapter heading with the chapter number (`# Chapter 3: ...`, `# Chương 3: ...` — the word for "chapter" varies by story language, so the parser in `chapter_writer.py` matches on the numeral, not a hardcoded word).
- Every other agent that feeds a DB table returns JSON, requested via `json_mode=True` and validated against `app/schemas.py` Pydantic models by `app/llm_json.py:generate_structured()`, which retries once (with the parse error appended to the prompt) if the model returns invalid JSON.

**`app/db/models.py`** replaces the root project's markdown memory files with real tables:
- `WorldState` has `UniqueConstraint(story_id, entity_type, entity_key, field)` — "overwrite, don't accumulate" is a DB constraint here, not a prompt instruction the model has to remember to follow.
- `StateLog` is the append-only audit trail (never overwritten).
- `ContinuityLog` and `SmartPlannerState` are one row per story — living checkpoint state, overwritten each time, not history logs.
- `Character.aliases` is populated once by `character_developer`'s structured output and is what `continuity_editor` cross-references to avoid flagging two names for the same person as a contradiction.

**`app/context_builder.py`** turns those DB rows back into the compact text blocks agents read (the Python equivalent of `world-state.md` / `chapter-summaries.md` / `continuity-log.md` in the CLI version).

**`app/length_calc.py`** mirrors the root `CLAUDE.md`'s dynamic length logic: for `REWRITE` input, `words_per_chapter` is derived from the source material's actual word/chapter density instead of the system default, so a short-chapter source doesn't get inflated to 4000 words/chapter.

## Known gotchas (found via actual testing, not theoretical)

- Reasoning models can spend their entire `max_tokens` budget on invisible reasoning tokens even for trivial tasks (e.g. a "give me a 2-6 word title" call needs a few hundred tokens of headroom, not 30) — give every call generous headroom, especially JSON-mode + `thinking=True` combined.
- Free-tier OpenRouter models can return `ResourceExhausted` / worker-limit errors under shared load; nothing in `generate_structured` retries on that class of error yet (only on bad JSON), so callers currently need to retry manually.
- The SQLite file lives at `/data/novelgen.db` inside the container (see the Dockerfile's `DATABASE_URL` and the `novelgen-data` volume in `docker-compose.yml`) — it persists across `docker compose down` but not `docker compose down -v`.
