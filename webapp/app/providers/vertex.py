import logging
import os
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from .base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

_RATE_LIMIT_SLEEP = 60  # seconds to wait on 429 before retrying
_MAX_RETRIES = 3


class VertexProvider(LLMProvider):
    """Google Vertex AI provider (Gemini models) via the google-genai SDK.

    Auth is Application Default Credentials: point GOOGLE_APPLICATION_CREDENTIALS
    at the service-account JSON (mounted into the container at /auth/...). The
    `model` string is a bare Vertex model id, e.g. "gemini-2.5-flash".

    Like OpenRouterProvider this streams internally and returns a single
    LLMResponse — a long reasoning generation kept as one blocking call risks
    the connection dropping before the full body arrives.
    """

    def __init__(self, project: str | None = None, location: str | None = None):
        self.client = genai.Client(
            vertexai=True,
            project=project or os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=location or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
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
        # thinking_budget=-1 lets Gemini 2.5 Flash decide dynamically; 0 disables
        # thinking entirely. We never surface thought text (include_thoughts left
        # off), so only the answer parts arrive in the stream.
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else None,
            thinking_config=types.ThinkingConfig(thinking_budget=-1 if thinking else 0),
        )

        for attempt in range(_MAX_RETRIES + 1):
            text_parts: list[str] = []
            last_chunk = None
            try:
                for chunk in self.client.models.generate_content_stream(
                    model=model,
                    contents=user_content,
                    config=config,
                ):
                    last_chunk = chunk
                    try:
                        delta = chunk.text
                    except Exception:
                        delta = None
                    if delta:
                        text_parts.append(delta)
                return LLMResponse(text="".join(text_parts), raw=last_chunk)
            except genai_errors.ClientError as exc:
                if exc.code == 429 and attempt < _MAX_RETRIES:
                    logger.warning(
                        "Vertex AI 429 RESOURCE_EXHAUSTED (attempt %d/%d), sleeping %ds before retry",
                        attempt + 1, _MAX_RETRIES, _RATE_LIMIT_SLEEP,
                    )
                    time.sleep(_RATE_LIMIT_SLEEP)
                    continue
                raise
