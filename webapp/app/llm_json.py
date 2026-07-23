import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .providers.base import LLMProvider

T = TypeVar("T", bound=BaseModel)


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
    Pydantic schema. Retries once with the parse error appended to the
    prompt if the first response isn't valid JSON / doesn't match the
    schema — models occasionally wrap JSON in prose despite instructions.
    """
    last_error: Exception | None = None
    content = user_content
    for attempt in range(2):
        response = provider.generate(
            system=system,
            user_content=content,
            model=model,
            max_tokens=max_tokens,
            thinking=thinking,
            json_mode=True,
        )
        try:
            return schema.model_validate(json.loads(response.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            content = (
                f"{user_content}\n\n---\nLần trả lời trước không phải JSON hợp lệ theo schema "
                f"yêu cầu (lỗi: {exc}). Trả lời LẠI, DUY NHẤT một object JSON hợp lệ, không có "
                f"markdown code fence, không có lời dẫn."
            )
    raise ValueError(f"Model did not return valid JSON after 2 attempts: {last_error}")
