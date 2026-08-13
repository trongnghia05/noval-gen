import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMProvider

T = TypeVar("T", bound=BaseModel)


MAX_STRUCTURED_RETRIES = 3


def generate_structured(
    provider: LLMProvider,
    *,
    system: str,
    user_content: str,
    model: str,
    schema: type[T],
    max_tokens: int = 4096,
    thinking: bool = False,
) -> T:
    """Call the provider in JSON mode and validate the result against a
    Pydantic schema. Retries up to MAX_STRUCTURED_RETRIES times, appending
    the parse/validation error to the prompt each time so the model can
    self-correct — covers both syntax errors and semantic violations (e.g.
    duplicate node labels caught by model_validators).
    """
    last_error: Exception | None = None
    content = user_content
    for attempt in range(MAX_STRUCTURED_RETRIES):
        response = provider.generate(
            system=system,
            user_content=content,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
            json_mode=True,
            response_schema=schema,
        )
        try:
            return schema.model_validate(json.loads(response.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            content = (
                f"{user_content}\n\n---\n"
                f"Previous response did not pass schema validation "
                f"(attempt {attempt + 1}/{MAX_STRUCTURED_RETRIES}, error: {exc}). "
                f"Reply AGAIN with a single valid JSON object — no markdown fences, "
                f"no preamble. Fix the exact error described above."
            )
    raise ValueError(
        f"Model did not return valid JSON after {MAX_STRUCTURED_RETRIES} attempts: {last_error}"
    )
