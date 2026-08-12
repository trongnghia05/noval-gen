"""Pieces every agent shares, so they cannot drift apart.

Each of these was written out separately in the agents that needed it, which meant a
change had to be made in four or five places at once — and one of them silently kept
the old wording until someone noticed.
"""

from typing import TypeVar

from pydantic import BaseModel

from ..config import AGENT_MODELS, PROVIDER
from ..llm_json import generate_structured
from ..prompts.loader import load_prompt

T = TypeVar("T", bound=BaseModel)


def call_agent(
    prompt: str,
    *,
    user_content: str,
    schema: type[T],
    max_tokens: int,
    thinking: bool = False,
    model: str | None = None,
    model_fallback: str | None = None,
    system: str | None = None,
) -> T:
    """Run one agent: load its prompt, pick its model, call the provider in JSON mode.

    Thirty-eight call sites each assembled this by hand — the same PROVIDER, the same
    load_prompt, the same AGENT_MODELS lookup — which is how max_tokens ended up
    scattered from 200 to 65536 with no pattern, and how two crashes happened from a
    cap set too low for JSON mode with thinking on.

    `prompt` names the file in app/prompts and, by default, the AGENT_MODELS key. The
    three overrides exist because several agents deliberately break that link:

    - `model`: an explicit model id. chapter_reviser runs on the WRITER's model, since
      it is repairing the writer's prose; graph_repair and source_graph_verifier run on
      the graph verifier's.
    - `model_fallback`: an AGENT_MODELS key to use when `prompt` has no entry of its
      own. Keeps the small helper agents working without their own config line.
    - `system`: prompt text that does not come from a file at all — chapter_writer's
      finalize pass builds its system message in code.

    max_tokens is required, never defaulted: the right value depends on how much
    structured output the agent emits and whether thinking eats into the budget, and a
    silent default is exactly how the truncation bugs happened.
    """
    if model is None:
        if model_fallback:
            model = AGENT_MODELS.get(prompt, AGENT_MODELS[model_fallback])
        else:
            model = AGENT_MODELS[prompt]
    return generate_structured(
        PROVIDER,
        system=system if system is not None else load_prompt(prompt),
        user_content=user_content,
        model=model,
        schema=schema,
        max_tokens=max_tokens,
        thinking=thinking,
    )

# Prepended to the user content when a planning agent is re-run with the planning
# verifier's findings. Identical wording in character_developer, plot_architect,
# story_analyzer and worldbuilder, because they all answer the same verifier.
#
# NOT the same as chapter_writer's rewrite header: that one carries a chapter
# verifier's continuity errors into a chapter rewrite. Different verifier, different
# loop, different instruction — deliberately separate.
FEEDBACK_HEADER = (
    "\n## ISSUES FROM THE PREVIOUS VERIFICATION PASS — you must fix these, and "
    "leave everything already correct untouched\n"
)
