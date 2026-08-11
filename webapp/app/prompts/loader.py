from functools import lru_cache
from pathlib import Path

from ..market import market_block
from ..schema_hints import _resolve_schema_placeholders

PROMPTS_DIR = Path(__file__).parent

# Agents whose output has to belong to the target market, and therefore get the market
# contract appended to their system prompt. Keep this list honest: an agent that mints
# names, designs the world, describes a person's look, or draws the cover needs it —
# a summariser or a continuity checker does not, and the extra tokens buy nothing.
_MARKET_AWARE = {
    "world_designer",            # decides the setting
    "name_lexicon",              # mints every proper name in the story
    "graph_character_enricher",  # writes each character's appearance
    "image_prompt",              # describes the people on the cover
    "image_prompt_verifier",     # checks that description
}


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    # Substitute schema placeholders with hints generated from the Pydantic models
    # — the single source of truth. `{{schema:ModelName}}` → annotated output
    # contract; `{{plot_outline_schema}}`/`{{world_bible_schema}}` → compact input
    # shape. Change a model and every prompt referencing it updates automatically.
    text = _resolve_schema_placeholders(text)
    if name in _MARKET_AWARE:
        text = f"{text}\n\n{market_block()}\n"
    return text
