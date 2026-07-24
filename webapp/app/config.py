import os

from dotenv import load_dotenv

from .providers.openrouter import OpenRouterProvider

load_dotenv()

# One provider instance for every agent — agent code never imports a provider
# SDK directly. Select via LLM_PROVIDER: "openrouter" (default, OpenAI-compatible
# OpenRouter) or "vertex" (Google Vertex AI / Gemini, auth via a service-account
# JSON in GOOGLE_APPLICATION_CREDENTIALS). VertexProvider is imported lazily so
# google-genai is only needed when actually running on Vertex.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter").lower()
if LLM_PROVIDER == "vertex":
    from .providers.vertex import VertexProvider

    PROVIDER = VertexProvider()
else:
    PROVIDER = OpenRouterProvider()

# Model per agent — override via env vars without touching code. The DEFAULT
# is provider-aware so an agent with no explicit MODEL_* override never falls
# back to a slug the active provider can't resolve (e.g. an "anthropic/..."
# OpenRouter slug reaching Vertex → 404). On Vertex every default is a bare
# Gemini model id; on OpenRouter it's a provider/model slug.
_DEFAULT_MODEL = "gemini-2.5-flash" if LLM_PROVIDER == "vertex" else "anthropic/claude-opus-4.8"

AGENT_MODELS = {
    "story_analyzer":      os.getenv("MODEL_STORY_ANALYZER",      _DEFAULT_MODEL),
    "plot_architect":      os.getenv("MODEL_PLOT_ARCHITECT",       _DEFAULT_MODEL),
    "character_developer": os.getenv("MODEL_CHARACTER_DEVELOPER",  _DEFAULT_MODEL),
    "worldbuilder":        os.getenv("MODEL_WORLDBUILDER",         _DEFAULT_MODEL),
    "chapter_blueprinter": os.getenv("MODEL_CHAPTER_BLUEPRINTER",  _DEFAULT_MODEL),
    "chapter_writer":      os.getenv("MODEL_CHAPTER_WRITER",       _DEFAULT_MODEL),
    "chapter_summarizer":  os.getenv("MODEL_CHAPTER_SUMMARIZER",   _DEFAULT_MODEL),
    "continuity_editor":   os.getenv("MODEL_CONTINUITY_EDITOR",    _DEFAULT_MODEL),
    "smart_planner":       os.getenv("MODEL_SMART_PLANNER",        _DEFAULT_MODEL),
    "chapter_verifier":    os.getenv("MODEL_CHAPTER_VERIFIER",     _DEFAULT_MODEL),
    "planning_verifier":   os.getenv("MODEL_PLANNING_VERIFIER",    _DEFAULT_MODEL),
    "quality_reviewer":    os.getenv("MODEL_QUALITY_REVIEWER",     _DEFAULT_MODEL),
    "title_generator":     os.getenv("MODEL_TITLE_GENERATOR",      _DEFAULT_MODEL),
}

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./novelgen.db")
