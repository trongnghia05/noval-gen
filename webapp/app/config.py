import os
from pathlib import Path

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
    "story_analyzer":           os.getenv("MODEL_STORY_ANALYZER",           _DEFAULT_MODEL),
    "chapter_graph_extractor":  os.getenv("MODEL_CHAPTER_GRAPH_EXTRACTOR",  _DEFAULT_MODEL),
    "entity_resolver":          os.getenv("MODEL_ENTITY_RESOLVER",          _DEFAULT_MODEL),
    "plot_architect":           os.getenv("MODEL_PLOT_ARCHITECT",            _DEFAULT_MODEL),
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
    "world_designer":         os.getenv("MODEL_WORLD_DESIGNER",          _DEFAULT_MODEL),
    "name_lexicon":           os.getenv("MODEL_NAME_LEXICON",            _DEFAULT_MODEL),
    "story_bible_leak_check": os.getenv("MODEL_STORY_BIBLE_LEAK_CHECK",  _DEFAULT_MODEL),
    "new_graph_builder":      os.getenv("MODEL_NEW_GRAPH_BUILDER",       _DEFAULT_MODEL),
    "graph_enricher":         os.getenv("MODEL_GRAPH_ENRICHER",          _DEFAULT_MODEL),
    "graph_verifier":         os.getenv("MODEL_GRAPH_VERIFIER",          _DEFAULT_MODEL),
    "graph_surface_rewriter": os.getenv("MODEL_GRAPH_SURFACE_REWRITER",  _DEFAULT_MODEL),
    "graph_character_enricher": os.getenv("MODEL_GRAPH_CHARACTER_ENRICHER", _DEFAULT_MODEL),
    "graph_event_enricher":     os.getenv("MODEL_GRAPH_EVENT_ENRICHER",     _DEFAULT_MODEL),
    "graph_arc_enricher":       os.getenv("MODEL_GRAPH_ARC_ENRICHER",       _DEFAULT_MODEL),
    "graph_relation_enricher":  os.getenv("MODEL_GRAPH_RELATION_ENRICHER",  _DEFAULT_MODEL),
    "graph_causes_enricher":    os.getenv("MODEL_GRAPH_CAUSES_ENRICHER",    _DEFAULT_MODEL),
    "graph_enrich_verifier":    os.getenv("MODEL_GRAPH_ENRICH_VERIFIER",    _DEFAULT_MODEL),
    "novel_metadata":         os.getenv("MODEL_NOVEL_METADATA",          _DEFAULT_MODEL),
    "image_prompt":           os.getenv("MODEL_IMAGE_PROMPT",            _DEFAULT_MODEL),
}

# Text-to-image model for cover / thumbnail generation. Uses a Gemini image model
# via generate_content (Vertex Imagen / generate_images is not enabled on this
# project). Override with IMAGE_MODEL.
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-3-pro-image")

# Image models are not published in every region: gemini-3-pro-image exists ONLY in
# `global`, while the text models run in GOOGLE_CLOUD_LOCATION (us-central1). So image
# calls get their own client/region — see VertexProvider.image_client.
# Beware: models.get() resolves gemini-3-pro-image in us-central1 and then
# generate_content 404s there, so a metadata probe is not proof a region works.
IMAGE_LOCATION = os.getenv("IMAGE_LOCATION", "global")

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./novelgen.db")
OUTPUT_BASE = Path(os.getenv("OUTPUT_DIR", "/data/output"))
