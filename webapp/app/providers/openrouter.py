import os

from openai import LengthFinishReasonError, OpenAI

from .base import LLMProvider, LLMResponse


class OpenRouterProvider(LLMProvider):
    """Phase 1 provider: OpenRouter is OpenAI-API-compatible, so this is
    just the `openai` SDK pointed at OpenRouter's base_url. One API key
    gives access to Claude/GPT/Gemini/Llama/etc. via the `model` string
    (e.g. "anthropic/claude-opus-4.8", "openai/gpt-5").
    """

    def __init__(self, api_key: str | None = None):
        # Falls back to an empty string rather than raising at import time —
        # the key is only actually needed once generate() makes a request.
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key or os.getenv("OPENROUTER_API_KEY", ""),
        )

    def generate(
        self,
        *,
        system: str,
        user_content: str,
        model: str,
        max_tokens: int = 4096,
        thinking: bool = False,
        json_mode: bool = False,
    ) -> LLMResponse:
        extra_body: dict = {}
        if thinking:
            extra_body["reasoning"] = {"enabled": True}

        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        # Streamed rather than a single blocking call: slow/reasoning models
        # can take 1-2+ minutes, and a non-streamed request of that length
        # risks the connection being cut before the full body arrives
        # (observed as a truncated-JSON error from the OpenAI client).
        # Streaming keeps the connection alive with a steady trickle of bytes.
        text_parts: list[str] = []
        with self.client.chat.completions.stream(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            extra_body=extra_body or None,
            **kwargs,
        ) as stream:
            for event in stream:
                if event.type == "content.delta":
                    text_parts.append(event.delta)
            try:
                # Only used to populate LLMResponse.raw for debugging — the
                # actual text already came from the deltas above. The SDK
                # refuses to build this if finish_reason == "length" (budget
                # ran out, often to reasoning tokens on "thinking" models),
                # but that shouldn't discard content we already collected.
                raw = stream.get_final_completion().model_dump()
            except LengthFinishReasonError:
                raw = {"finish_reason": "length"}
        return LLMResponse(text="".join(text_parts), raw=raw)
