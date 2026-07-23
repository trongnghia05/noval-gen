import os

from dotenv import load_dotenv

from .providers.openrouter import OpenRouterProvider

load_dotenv()

# Phase 1: single OpenRouter provider for every agent. Swapping any agent
# to a direct AnthropicProvider later only requires changing the value
# here (or per-agent, if PROVIDER becomes a dict) — agent code never
# imports a provider SDK directly.
PROVIDER = OpenRouterProvider()

# Model per agent — override via env vars without touching code.
# Model IDs are OpenRouter slugs (provider/model).
AGENT_MODELS = {
    "story_analyzer":      os.getenv("MODEL_STORY_ANALYZER",      "anthropic/claude-opus-4.8"),
    "plot_architect":      os.getenv("MODEL_PLOT_ARCHITECT",       "anthropic/claude-opus-4.8"),
    "character_developer": os.getenv("MODEL_CHARACTER_DEVELOPER",  "anthropic/claude-opus-4.8"),
    "worldbuilder":        os.getenv("MODEL_WORLDBUILDER",         "anthropic/claude-opus-4.8"),
    "chapter_blueprinter": os.getenv("MODEL_CHAPTER_BLUEPRINTER",  "anthropic/claude-opus-4.8"),
    "chapter_writer":      os.getenv("MODEL_CHAPTER_WRITER",       "anthropic/claude-opus-4.8"),
    "chapter_summarizer":  os.getenv("MODEL_CHAPTER_SUMMARIZER",   "anthropic/claude-haiku-4.5"),
    "continuity_editor":   os.getenv("MODEL_CONTINUITY_EDITOR",    "anthropic/claude-opus-4.8"),
    "smart_planner":       os.getenv("MODEL_SMART_PLANNER",        "anthropic/claude-opus-4.8"),
    "title_generator":     os.getenv("MODEL_TITLE_GENERATOR",      "anthropic/claude-haiku-4.5"),
}

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./novelgen.db")
