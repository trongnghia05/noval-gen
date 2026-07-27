from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    text: str
    raw: Any = None


class LLMProvider(ABC):
    """Provider-agnostic interface every agent calls through.

    Concrete providers (OpenRouter now, direct Anthropic later) translate
    the generic ``thinking``/``json_mode`` flags into whatever the
    underlying API actually expects.
    """

    @abstractmethod
    def generate(
        self,
        *,
        system: str,
        user_content: str,
        model: str,
        max_tokens: int = 4096,
        thinking: bool = False,
        json_mode: bool = False,
        response_schema: type | None = None,
    ) -> LLMResponse:
        ...

    def generate_image(
        self,
        *,
        prompt: str,
        model: str,
        aspect_ratio: str = "1:1",
    ) -> bytes:
        """Return raw image bytes (PNG/JPEG) for a text-to-image prompt.

        Optional capability — only providers that support image generation
        override this. Callers must handle NotImplementedError."""
        raise NotImplementedError(f"{type(self).__name__} does not support image generation")
